"""Persistencia e-CF en PostgreSQL — esquema propio `os_ecf_*`.

Fase 1 local con datos sintéticos. Sin DDL en runtime: el esquema se
crea únicamente con `os_ecf.migrar.migrar()`.

- `AlmacenECF.reservar_e_ncf`: reserva atómica con `SELECT ... FOR UPDATE`
  sobre `os_ecf_secuencias` (clave canónica rnc_norm + tipo + origen);
  los concurrentes se serializan y cada número se asigna una sola vez
  (patrón de `os_contab/ncf_pg.py`). Exige emisor autorizado (#3) y
  vincula la clave de idempotencia a su contexto
  (rnc_norm, tipo, origen, operacion) (#5).
- `guardar`: upsert por id con verificación previa de propiedad (#1);
  exige emisor autorizado (#3); si el comprobante no trae e-NCF, lo
  reserva con clave_idempotencia = operacion = "comprobante:<id>" (#7).
- `guardar_evento`: verifica propiedad del comprobante (#2).
- `eventos`: `company_id` obligatorio, siempre filtra (#2).
- TODA lectura filtra por `company_id` (aislamiento multiempresa).
- Los dicts devueltos son JSON-serializables.

AVISO: SIMULACIÓN — NO VÁLIDO ANTE DGII.
"""
from __future__ import annotations

import json
import os
from datetime import datetime
from decimal import Decimal

import psycopg

from .modelos import (
    Comprobante, ErrorECF, ESTADOS, ESTADOS_EMITIDOS, ESTADOS_INMUTABLES,
    TIPOS_ECF, formatear_e_ncf, parsear_e_ncf, validar_rnc,
    validar_transicion_estado,
)


def _resolver_dsn(dsn: str | None) -> str:
    valor = dsn or os.environ.get("ZAYVERO_OS_DATABASE_URL")
    if not valor:
        raise ErrorECF(
            "sin DSN: defina ZAYVERO_OS_DATABASE_URL o pase el parámetro dsn")
    return valor


def _js(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, Decimal):
        return str(v)
    return v


def _fila_dict(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [{c: _js(v) for c, v in zip(cols, fila)}
            for fila in cur.fetchall()]


def _una(cur) -> dict | None:
    filas = _fila_dict(cur)
    return filas[0] if filas else None


_COLUMNAS = (
    "id, company_id, tipo, e_ncf, encf_original, emisor_rnc, emisor_rnc_norm,"
    " emisor_razon, receptor_rnc, receptor_razon, moneda, estado, modo,"
    " subtotal, descuento, itbis, total, datos, creado_en, fecha_emision"
)


class AlmacenECF:
    """Operaciones de persistencia e-CF sobre PostgreSQL."""

    def __init__(self, dsn: str | None = None):
        self._dsn = _resolver_dsn(dsn)

    # ---- emisores autorizados (#3) -------------------------------------------
    def autorizar_emisor(self, company_id: str, rnc: str) -> dict:
        """Registra (company_id, rnc_norm) como emisor autorizado.

        Idempotente. En PRODUCCIÓN la autorización la otorga el
        onboarding con verificación del RNC del contribuyente; NUNCA
        auto-registro abierto (ver docs/ecf-emisores-autorizados.md).
        En fase 1 (datos sintéticos) los tests autorizan explícitamente.
        """
        if not company_id:
            raise ErrorECF("company_id es obligatorio")
        rnc_norm = validar_rnc(rnc)
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO os_ecf_emisores_autorizados"
                    " (company_id, rnc_norm) VALUES (%s,%s)"
                    " ON CONFLICT (company_id, rnc_norm) DO NOTHING",
                    (company_id, rnc_norm))
        return {"company_id": company_id, "rnc_norm": rnc_norm}

    def _exigir_emisor_autorizado(self, cur, company_id: str,
                                 rnc_norm: str) -> None:
        """ErrorECF si (company_id, rnc_norm) no está autorizado (#3)."""
        cur.execute(
            "SELECT 1 FROM os_ecf_emisores_autorizados"
            " WHERE company_id=%s AND rnc_norm=%s",
            (company_id, rnc_norm))
        if not cur.fetchone():
            raise ErrorECF(
                "empresa no autorizada para operar con ese RNC")

    # ---- secuencias (e-NCF) -------------------------------------------------
    def reservar_e_ncf(self, company_id: str, rnc_emisor: str,
                       tipo: str, origen: str = "simulado",
                       clave_idempotencia: str | None = None,
                       operacion: str = "manual") -> str:
        """Reserva el siguiente e-NCF para el EMISOR FISCAL (RNC normalizado).

        Decisión ejecutiva (issue #11): la secuencia es por (rnc_norm, tipo,
        origen), NO por empresa ni global:
          - Dos RNC distintos pueden usar el mismo número.
          - Dos empresas/sucursales con el MISMO RNC comparten secuencia:
            jamás duplican.
          - `origen` separa el namespace SIMULADO del futuro FISCAL real:
            el mock jamás consume rango fiscal.

        Cierre P0 (orden ejecutiva 2026-10-13):
          - #3: exige que (company_id, rnc_norm) esté autorizado.
          - #5: la clave de idempotencia queda vinculada a su contexto
            (rnc_norm, tipo, origen, operacion); reutilizarla con un
            contexto distinto es ErrorECF. `operacion` identifica la
            operación lógica, p.ej. "comprobante:<id>" o "manual".
            Filas anteriores a la migración 005 (operacion NULL) se
            tratan como operacion='manual' (legado documentado).

        Concurrencia: con `clave_idempotencia` se toma primero un
        advisory lock transaccional sobre la clave y luego el bloqueo
        pesimista de la secuencia; N procesos concurrentes con la misma
        clave reciben el MISMO e-NCF y la secuencia avanza exactamente 1.
        """
        if not company_id:
            raise ErrorECF("company_id es obligatorio")
        rnc = validar_rnc(rnc_emisor)
        tipo = (tipo or "").strip()
        if tipo not in TIPOS_ECF:
            raise ErrorECF(f"tipo e-CF desconocido: {tipo!r}")
        if not TIPOS_ECF[tipo]["habilitado"]:
            raise ErrorECF(f"tipo {tipo} no habilitado en fase 1")
        origen = (origen or "simulado").strip().lower()
        if origen not in ("simulado", "fiscal"):
            raise ErrorECF(f"origen inválido: {origen!r}")
        clave = (clave_idempotencia or "").strip() or None
        operacion = (operacion or "manual").strip() or "manual"
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                if clave:
                    # Serializa a todos los que compitan por la misma clave,
                    # aunque pidan (rnc_norm, tipo, origen) distintos.
                    cur.execute(
                        "SELECT pg_advisory_xact_lock(hashtext(%s))",
                        (clave,))
                self._exigir_emisor_autorizado(cur, company_id, rnc)
                cur.execute(
                    "INSERT INTO os_ecf_secuencias"
                    " (company_id, rnc_emisor, rnc_norm, tipo, origen, ultimo)"
                    " VALUES (%s,%s,%s,%s,%s,0)"
                    " ON CONFLICT (rnc_norm, tipo, origen) DO NOTHING",
                    (company_id, rnc, rnc, tipo, origen))
                cur.execute(
                    "SELECT ultimo FROM os_ecf_secuencias"
                    " WHERE rnc_norm=%s AND tipo=%s AND origen=%s"
                    " FOR UPDATE",
                    (rnc, tipo, origen))
                fila = cur.fetchone()
                if clave:
                    cur.execute(
                        "SELECT rnc_norm, tipo, origen, operacion, e_ncf"
                        " FROM os_ecf_reservas WHERE clave=%s",
                        (clave,))
                    fr = cur.fetchone()
                    if fr:
                        # Reintento: el número ya asignado, sin consumir otro.
                        # #5: el contexto debe coincidir con el que creó
                        # la clave; si difiere, es reutilización indebida.
                        ctx_guardado = (fr[0], fr[1], fr[2],
                                        fr[3] or "manual")
                        if ctx_guardado != (rnc, tipo, origen, operacion):
                            raise ErrorECF(
                                "reutilización indebida de clave de"
                                " idempotencia")
                        return fr[4]
                nuevo = fila[0] + 1
                cur.execute(
                    "UPDATE os_ecf_secuencias SET ultimo=%s"
                    " WHERE rnc_norm=%s AND tipo=%s AND origen=%s",
                    (nuevo, rnc, tipo, origen))
                e_ncf = formatear_e_ncf(tipo, nuevo)
                if clave:
                    cur.execute(
                        "INSERT INTO os_ecf_reservas"
                        " (clave, rnc_norm, tipo, origen, operacion, e_ncf)"
                        " VALUES (%s,%s,%s,%s,%s,%s)"
                        " ON CONFLICT (clave) DO NOTHING",
                        (clave, rnc, tipo, origen, operacion, e_ncf))
        return e_ncf

    # ---- comprobantes -------------------------------------------------------
    def guardar(self, c: Comprobante) -> dict:
        """Upsert por id. Si el comprobante no trae e-NCF, lo reserva.

        Reglas (issue #11):
        - `emisor_rnc_norm` se deriva del RNC; la unicidad del e-NCF es
          por emisor fiscal, no global ni por empresa.
        - Regla cronológica oficial: un comprobante en estado emitido no
          puede tener fecha_emision anterior a la máxima ya emitida para
          el mismo (rnc_norm, tipo).
        - Un comprobante ya emitido no se edita: solo se permiten
          transiciones de estado (el contenido fiscal es inmutable).
        - Orden ejecutiva P0 #10: en estado inmutable se compara el
          contenido fiscal COMPLETO (columnas + receptor_rnc +
          fecha_emision + JSONB `datos` salvo `estado`/`modo`).
        - Orden ejecutiva P0 #11: todo cambio de estado en un
          comprobante existente debe estar en TRANSICIONES_VALIDAS
          (modelos.py); si no, ErrorECF.
        - Orden ejecutiva P0 #1: si el id ya existe con OTRO company_id,
          ErrorECF antes del upsert (nunca se sobrescribe en silencio
          ni se "mueve" de empresa).
        - Orden ejecutiva P0 #3: (company_id, rnc_norm) debe estar
          autorizado, traiga el comprobante e-NCF propio o no.
        - Orden ejecutiva P0 #7: sin e-NCF propio, la reserva usa
          clave_idempotencia = operacion = "comprobante:<id>": todo
          número consumido tiene su fila de reserva.
        """
        if not c.id:
            raise ErrorECF("comprobante sin id")
        if not c.company_id:
            raise ErrorECF("comprobante sin company_id")
        rnc_norm = validar_rnc(c.emisor.rnc)
        if c.e_ncf:
            parsear_e_ncf(c.e_ncf)
        # P0 #3: (company_id, rnc_norm) debe estar autorizado, traiga el
        # comprobante e-NCF propio o no.
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                self._exigir_emisor_autorizado(cur, c.company_id, rnc_norm)
        if not c.e_ncf:
            # P0 #7: todo número consumido tiene su fila de reserva.
            c.e_ncf = self.reservar_e_ncf(
                c.company_id, c.emisor.rnc, c.tipo,
                clave_idempotencia=f"comprobante:{c.id}",
                operacion=f"comprobante:{c.id}")
        t = c.totales
        datos = json.dumps(c.to_dict(), ensure_ascii=False)
        try:
            creado = datetime.fromisoformat(c.creado_en)
            f_emision = datetime.fromisoformat(c.fecha_emision)
        except ValueError:
            raise ErrorECF(f"fecha inválida: {c.creado_en!r}")
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                # P0 #1: propiedad del id ANTES del upsert. Si el id
                # existe con otro company_id, se rechaza: jamás
                # silencioso (ni se "mueve" de empresa).
                cur.execute(
                    "SELECT company_id FROM os_ecf_comprobantes"
                    " WHERE id=%s FOR UPDATE",
                    (c.id,))
                prop = cur.fetchone()
                if prop and prop[0] != c.company_id:
                    raise ErrorECF("id pertenece a otra empresa")
                cur.execute(
                    "SELECT estado FROM os_ecf_comprobantes"
                    " WHERE id=%s AND company_id=%s",
                    (c.id, c.company_id))
                fila = cur.fetchone()
                if fila:
                    # P0 #11: todo cambio de estado en un comprobante
                    # existente debe estar en TRANSICIONES_VALIDAS.
                    validar_transicion_estado(fila[0], c.estado)
                    if fila[0] in ESTADOS_INMUTABLES:
                        self._verificar_transicion_permitida(cur, c, fila[0])
                if c.estado in ESTADOS_EMITIDOS:
                    cur.execute(
                        "SELECT MAX(fecha_emision) FROM os_ecf_comprobantes"
                        " WHERE emisor_rnc_norm=%s AND tipo=%s"
                        "   AND estado = ANY(%s) AND id <> %s",
                        (rnc_norm, c.tipo, list(ESTADOS_EMITIDOS), c.id))
                    max_fecha = cur.fetchone()[0]
                    if max_fecha is not None and f_emision < max_fecha:
                        raise ErrorECF(
                            "regla cronológica: fecha_emision anterior a"
                            " comprobantes ya emitidos del mismo emisor+tipo")
                cur.execute(
                    """INSERT INTO os_ecf_comprobantes
                       (id, company_id, tipo, e_ncf, encf_original,
                        emisor_rnc, emisor_rnc_norm, emisor_razon,
                        receptor_rnc, receptor_razon, moneda, estado, modo,
                        subtotal, descuento, itbis, total, datos,
                        creado_en, fecha_emision)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               %s,%s,%s,%s,%s::jsonb,%s,%s)
                       ON CONFLICT (id) DO UPDATE SET
                           company_id=EXCLUDED.company_id,
                           tipo=EXCLUDED.tipo, e_ncf=EXCLUDED.e_ncf,
                           encf_original=EXCLUDED.encf_original,
                           emisor_rnc=EXCLUDED.emisor_rnc,
                           emisor_rnc_norm=EXCLUDED.emisor_rnc_norm,
                           emisor_razon=EXCLUDED.emisor_razon,
                           receptor_rnc=EXCLUDED.receptor_rnc,
                           receptor_razon=EXCLUDED.receptor_razon,
                           moneda=EXCLUDED.moneda, estado=EXCLUDED.estado,
                           modo=EXCLUDED.modo, subtotal=EXCLUDED.subtotal,
                           descuento=EXCLUDED.descuento,
                           itbis=EXCLUDED.itbis, total=EXCLUDED.total,
                           datos=EXCLUDED.datos,
                           creado_en=EXCLUDED.creado_en,
                           fecha_emision=EXCLUDED.fecha_emision""",
                    (c.id, c.company_id, c.tipo, c.e_ncf,
                     c.encf_original or "",
                     c.emisor.rnc, rnc_norm, c.emisor.razon_social,
                     c.receptor.rnc, c.receptor.razon_social,
                     c.moneda, c.estado, c.modo,
                     str(t.subtotal), str(t.descuento_global), str(t.itbis),
                     str(t.total), datos, creado, f_emision))
        return self.obtener(c.company_id, c.id)

    @staticmethod
    def _verificar_transicion_permitida(cur, c: Comprobante,
                                       estado_actual: str) -> None:
        """Un comprobante emitido solo admite transiciones de estado.

        P0 #10 (emitido inmutable ampliado): se compara el contenido
        fiscal COMPLETO — columnas canónicas + `receptor_rnc`,
        `fecha_emision` y el JSONB `datos` íntegro, excluyendo solo las
        claves `estado` y `modo` (lo único que puede variar en un
        documento inmutable). Cambiar el receptor, una descripción de
        línea, la fecha o cualquier dato fiscal se rechaza aunque los
        totales coincidan; el contenido se corrige con nota de
        crédito/débito, nunca por edición directa.
        """
        cur.execute(
            "SELECT e_ncf, emisor_rnc_norm, tipo, subtotal, descuento,"
            "       itbis, total, moneda, receptor_rnc, fecha_emision, datos"
            " FROM os_ecf_comprobantes"
            " WHERE id=%s",
            (c.id,))
        f = cur.fetchone()
        t = c.totales
        fecha_nueva = datetime.fromisoformat(c.fecha_emision)
        fecha_actual = f[9]
        fiscal_actual = (f[0], f[1], f[2], str(f[3]), str(f[4]),
                         str(f[5]), str(f[6]), f[7], f[8],
                         fecha_actual.isoformat()
                         if hasattr(fecha_actual, "isoformat")
                         else str(fecha_actual))
        fiscal_nuevo = (c.e_ncf, validar_rnc(c.emisor.rnc), c.tipo,
                        str(t.subtotal), str(t.descuento_global),
                        str(t.itbis), str(t.total), c.moneda,
                        c.receptor.rnc, fecha_nueva.isoformat())
        if fiscal_actual != fiscal_nuevo:
            raise ErrorECF(
                "comprobante emitido: el contenido fiscal es inmutable"
                f" (estado {estado_actual!r}; use nota de crédito/débito)")
        # JSONB `datos` completo: normaliza comparando dicts Python y
        # excluye solo `estado` y `modo`.
        datos_actual = f[10] if isinstance(f[10], dict) else json.loads(f[10])
        datos_nuevo = json.loads(json.dumps(c.to_dict(), ensure_ascii=False))
        for d in (datos_actual, datos_nuevo):
            d.pop("estado", None)
            d.pop("modo", None)
        if datos_actual != datos_nuevo:
            raise ErrorECF(
                "comprobante emitido: el contenido fiscal es inmutable"
                f" (estado {estado_actual!r}; use nota de crédito/débito)")

    def obtener(self, company_id: str,
                comprobante_id: str) -> dict | None:
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_COLUMNAS} FROM os_ecf_comprobantes"
                    " WHERE company_id=%s AND id=%s",
                    (company_id, comprobante_id))
                return _una(cur)

    def obtener_por_encf(self, company_id: str,
                         e_ncf: str) -> dict | None:
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_COLUMNAS} FROM os_ecf_comprobantes"
                    " WHERE company_id=%s AND e_ncf=%s",
                    (company_id, (e_ncf or "").strip().upper()))
                return _una(cur)

    def listar(self, company_id: str,
               estado: str | None = None) -> list[dict]:
        if estado is not None and estado not in ESTADOS:
            raise ErrorECF(f"estado inválido: {estado!r}")
        sql = (f"SELECT {_COLUMNAS} FROM os_ecf_comprobantes"
               " WHERE company_id=%s")
        params: list = [company_id]
        if estado is not None:
            sql += " AND estado=%s"
            params.append(estado)
        sql += " ORDER BY creado_en DESC, id DESC"
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return _fila_dict(cur)

    # ---- eventos ------------------------------------------------------------
    def guardar_evento(self, comprobante_id: str, company_id: str,
                       de_estado: str | None, a_estado: str,
                       actor: str = "", detalle: dict | None = None,
                       hash: str = "") -> dict:
        """Registra una transición de estado (bitácora auditada).

        P0 #2: el comprobante debe existir Y pertenecer a `company_id`;
        si no, ErrorECF (un solo mensaje para no revelar existencia
        entre empresas).
        """
        if not comprobante_id:
            raise ErrorECF("comprobante_id es obligatorio")
        if not company_id:
            raise ErrorECF("company_id es obligatorio")
        if de_estado is not None and de_estado not in ESTADOS:
            raise ErrorECF(f"de_estado inválido: {de_estado!r}")
        if a_estado not in ESTADOS:
            raise ErrorECF(f"a_estado inválido: {a_estado!r}")
        if not hash:
            raise ErrorECF("hash es obligatorio")
        det = json.dumps(detalle or {}, ensure_ascii=False)
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT company_id FROM os_ecf_comprobantes"
                    " WHERE id=%s",
                    (comprobante_id,))
                prop = cur.fetchone()
                if not prop or prop[0] != company_id:
                    raise ErrorECF(
                        "comprobante no existe o pertenece a otra empresa")
                cur.execute(
                    """INSERT INTO os_ecf_eventos
                       (comprobante_id, company_id, de_estado, a_estado,
                        actor, detalle, hash)
                       VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s)
                       RETURNING id, creado_en""",
                    (comprobante_id, company_id, de_estado, a_estado,
                     actor or "", det, hash))
                eid, creado = cur.fetchone()
        return {"id": eid, "comprobante_id": comprobante_id,
                "company_id": company_id, "de_estado": de_estado,
                "a_estado": a_estado, "actor": actor or "",
                "detalle": detalle or {}, "hash": hash,
                "creado_en": creado.isoformat()}

    def eventos(self, comprobante_id: str,
                company_id: str) -> list[dict]:
        """Bitácora del comprobante en orden cronológico.

        P0 #2: `company_id` es OBLIGATORIO y siempre filtra: una empresa
        nunca lee eventos de comprobantes de otra.
        """
        if not comprobante_id:
            raise ErrorECF("comprobante_id es obligatorio")
        if not company_id:
            raise ErrorECF("company_id es obligatorio")
        sql = ("SELECT id, comprobante_id, company_id, de_estado, a_estado,"
               " actor, detalle, hash, creado_en FROM os_ecf_eventos"
               " WHERE comprobante_id=%s AND company_id=%s"
               " ORDER BY id")
        with psycopg.connect(self._dsn) as conn:
            with conn.cursor() as cur:
                cur.execute(sql, (comprobante_id, company_id))
                return _fila_dict(cur)
