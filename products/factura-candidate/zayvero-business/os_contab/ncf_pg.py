"""BLOQUE CONTABILIDAD (P1b, 2026-10-13) — Persistencia NCF en PostgreSQL.

- `AlmacenNCF`: emisión de NCF con numeración secuencial sobre PG
  (migración 019). Recibe un cursor y un `company_id`; si además recibe
  `auditar`, las escrituras generan eventos de auditoría (`os_eventos`,
  tipo 'ncf' — ya incluido en el CHECK desde la migración 013).
- `tx_ncf()`: transacción de escritura con lock por empresa (MISMO patrón
  que `os_empleados/almacen.py::tx_empleados`): primero consulta
  `tx_ambiente()` y, si el hilo ya corre dentro de un `mutar_os` para la
  misma empresa, PARTICIPA en esa transacción en vez de abrir una conexión
  anidada (la conexión anidada se interbloquea con el lock de empresa).
- `lectura_ncf()`: conexión de solo lectura (sin lock) para consultas.

ADVERTENCIA FISCAL: persistencia de ORGANIZACIÓN INTERNA. Cada salida
lleva la marca "SINTÉTICO — no válido ante la DGII" / "NO CERTIFICADO
DGII" (constantes de os_contab.itbis). El OS no declara impuestos ni
emite comprobantes fiscales válidos.

Toda lectura filtra por company_id. Los dicts devueltos son
JSON-serializables.
"""
from __future__ import annotations

import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone

from .errores import ErrorContab, NoEncontrado  # noqa: E402
from .itbis import (  # noqa: E402
    ErrorITBIS, TIPOS_NCF, _TIPO_RE,
    AVISO_NCF_SINTETICO, AVISO_NO_CERTIFICADO,
)


# --- Serialización -----------------------------------------------------------
def _js(v):
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "isoformat"):  # date
        return v.isoformat()
    return v


def _fila_dict(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [{c: _js(v) for c, v in zip(cols, fila)}
            for fila in cur.fetchall()]


def _una(cur) -> dict | None:
    filas = _fila_dict(cur)
    return filas[0] if filas else None


def _validar_tipo(tipo: str) -> str:
    tipo = (tipo or "").upper().strip()
    if not _TIPO_RE.match(tipo) or tipo not in TIPOS_NCF:
        raise ErrorITBIS(
            f"tipo NCF inválido: {tipo} "
            f"(válidos: {', '.join(sorted(TIPOS_NCF))})")
    return tipo


def _formatear(tipo: str, secuencial: int) -> str:
    return f"{tipo}{secuencial:08d}"


# --- Eventos de auditoría ------------------------------------------------------
def _evento(company_id: str, tipo: str, actor: str, ref_id: str,
            detalle: dict) -> dict:
    return {
        "evento_id": uuid.uuid4().hex,
        "company_id": company_id,
        "tipo": tipo,
        "actor": actor or "",
        "ref_id": ref_id or "",
        "detalle": detalle or {},
        "resultado": "exito",
    }


class _TxPG:
    """Transacción del bloque NCF sobre una conexión PG propia."""

    def __init__(self, conn, company_id: str):
        self.conn = conn
        self.cur = conn.cursor()
        self.company_id = company_id
        self._eventos: list[dict] = []

    def auditar(self, tipo: str, actor: str, ref_id: str,
                detalle: dict | None = None) -> None:
        from os_persist.auditoria import TIPOS_EVENTO
        if tipo not in TIPOS_EVENTO:
            raise ErrorContab(f"tipo de evento inválido: {tipo!r}")
        self._eventos.append(
            _evento(self.company_id, tipo, actor, ref_id, detalle or {}))

    def commit(self) -> None:
        from os_persist.auditoria import insertar_evento
        for ev in self._eventos:
            insertar_evento(self.cur, ev)
        self._eventos = []
        self.conn.commit()

    def rollback(self) -> None:
        self._eventos = []
        self.conn.rollback()


class _TxAmbiente:
    """Adaptador re-entrante: participa en la transacción ambiente de
    mutar_os. No abre conexión, no toma lock, no hace commit/rollback
    (los hace el mutar_os exterior). La auditoría usa la cola global de
    mutar_os vía os_persist.auditoria.auditar (se inserta pre-commit en
    la misma transacción)."""

    def __init__(self, cur, company_id: str):
        self.cur = cur
        self.company_id = company_id

    def auditar(self, tipo: str, actor: str, ref_id: str,
                detalle: dict | None = None) -> None:
        from os_persist.auditoria import auditar, TIPOS_EVENTO
        if tipo not in TIPOS_EVENTO:
            raise ErrorContab(f"tipo de evento inválido: {tipo!r}")
        auditar(self.company_id, tipo, actor, ref_id, detalle or {})


@contextmanager
def tx_ncf(company_id: str):
    """Transacción de escritura con lock por empresa (patrón tx_empleados).

    RE-ENTRANCIA: si el hilo ya corre dentro de un mutar_os para la misma
    empresa, PARTICIPA en esa transacción en vez de abrir una conexión
    anidada — la conexión anidada se interbloquea con el lock de empresa
    (deadlock por transactionid indetectable por PG).

    Uso:
        with tx_ncf(cid) as tx:
            alm = AlmacenNCF(tx.cur, cid, tx.auditar)
            alm.emitir("B02", "venta mostrador", "u-1")
        # commit automático al salir sin excepción; rollback si falla
    """
    if not company_id:
        raise ErrorContab("company_id es obligatorio")
    from os_persist.tx import tx_ambiente
    amb_cid, amb_cur = tx_ambiente()
    if amb_cid == company_id and amb_cur is not None:
        yield _TxAmbiente(amb_cur, company_id)
        return
    from os_persist.pg import connect
    conn = connect()
    tx = None
    try:
        conn.autocommit = False
        tx = _TxPG(conn, company_id)
        tx.cur.execute(
            "INSERT INTO os_companies (company_id) VALUES (%s)"
            " ON CONFLICT (company_id) DO NOTHING", (company_id,))
        tx.cur.execute(
            "INSERT INTO os_company_lock (company_id) VALUES (%s)"
            " ON CONFLICT (company_id) DO NOTHING", (company_id,))
        tx.cur.execute(
            "SELECT version FROM os_company_lock WHERE company_id=%s"
            " FOR UPDATE", (company_id,))
        yield tx
        tx.commit()
    except Exception:
        if tx is not None:
            tx.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def lectura_ncf(company_id: str):
    """Conexión de solo lectura (sin lock). Yields (conn, AlmacenNCF)."""
    if not company_id:
        raise ErrorContab("company_id es obligatorio")
    from os_persist.pg import connect
    conn = connect()
    try:
        conn.autocommit = True
        yield conn, AlmacenNCF(conn.cursor(), company_id)
    finally:
        conn.close()


class AlmacenNCF:
    """Operaciones NCF sobre PostgreSQL (migración 019).

    `cur`: cursor psycopg. `auditar`: callable(tipo, actor, ref_id,
    detalle) o None (en lectura no se audita).
    """

    def __init__(self, cur, company_id: str, auditar=None):
        if not company_id:
            raise ErrorContab("company_id es obligatorio")
        self.cur = cur
        self.c = company_id
        self._auditar = auditar

    def _audit(self, tipo: str, actor: str, ref_id: str,
               detalle: dict | None = None) -> None:
        if self._auditar is not None:
            self._auditar(tipo, actor, ref_id, detalle or {})

    # ---- rangos (secuencias) ------------------------------------------------
    def crear_rango(self, tipo: str, desde: int, hasta: int,
                    actor: str = "") -> dict:
        tipo = _validar_tipo(tipo)
        try:
            d, h = int(desde), int(hasta)
        except (TypeError, ValueError):
            raise ErrorITBIS("desde/hasta deben ser enteros")
        if d <= 0 or h < d:
            raise ErrorITBIS(f"rango inválido: desde={desde} hasta={hasta}")
        cur = self.cur
        cur.execute(
            """INSERT INTO os_ncf_secuencias
                   (company_id, tipo, siguiente, hasta, descripcion)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (company_id, tipo) DO NOTHING
               RETURNING siguiente, hasta""",
            (self.c, tipo, d, h, TIPOS_NCF[tipo]))
        if cur.rowcount == 0:
            cur.execute(
                "SELECT siguiente, hasta FROM os_ncf_secuencias"
                " WHERE company_id=%s AND tipo=%s", (self.c, tipo))
            ex = cur.fetchone()
            raise ErrorITBIS(
                f"ya existe un rango para {tipo} ({ex[0]}-{ex[1]}); cierre "
                "o amplíe el existente antes de crear otro")
        self._audit("ncf", actor, tipo,
                    {"accion": "crear_rango_sintetico",
                     "desde": d, "hasta": h, "sintetico": True})
        return {**self._rango_dict(tipo), "ok": True, "sintetico": True}

    def _rango_dict(self, tipo: str) -> dict:
        self.cur.execute(
            "SELECT tipo, siguiente, hasta, descripcion, actualizada_en"
            " FROM os_ncf_secuencias WHERE company_id=%s AND tipo=%s",
            (self.c, tipo))
        r = _una(self.cur)
        if r is None:
            raise NoEncontrado(
                f"sin rango NCF para {tipo}: cree el rango primero")
        disponibles = max(0, r["hasta"] - r["siguiente"] + 1)
        return {"tipo": r["tipo"], "descripcion": r["descripcion"],
                "desde": None, "siguiente": r["siguiente"],
                "hasta": r["hasta"], "disponibles": disponibles,
                "aviso": AVISO_NCF_SINTETICO}

    def rangos(self) -> list[dict]:
        self.cur.execute(
            "SELECT tipo, siguiente, hasta, descripcion"
            " FROM os_ncf_secuencias WHERE company_id=%s"
            " ORDER BY tipo", (self.c,))
        out = []
        for r in _fila_dict(self.cur):
            disponibles = max(0, r["hasta"] - r["siguiente"] + 1)
            out.append({"tipo": r["tipo"],
                        "descripcion": r["descripcion"],
                        "siguiente": r["siguiente"], "hasta": r["hasta"],
                        "disponibles": disponibles,
                        "aviso": AVISO_NCF_SINTETICO})
        return out

    # ---- emisión (bloqueo pesimista: sin duplicados ni huecos) -------------
    def emitir(self, tipo: str, concepto: str = "", actor: str = "",
               clave_idempotencia: str | None = None) -> dict:
        tipo = _validar_tipo(tipo)
        clave = (clave_idempotencia or "").strip() or None
        cur = self.cur
        # Idempotencia: la clave ya emitió -> devolver el mismo NCF sin
        # consumir un número nuevo.
        if clave:
            cur.execute(
                "SELECT numero, tipo, secuencial, concepto, estado,"
                " emitido_por, emitido_en FROM os_ncf_emitidos"
                " WHERE company_id=%s AND clave_idempotencia=%s",
                (self.c, clave))
            ex = _una(cur)
            if ex:
                return {**self._emitido_dict(ex), "ok": True,
                        "idempotente": True, "repetido": True,
                        "sintetico": True, "aviso": AVISO_NCF_SINTETICO}
        # Bloqueo pesimista sobre la fila de la secuencia: los emisores
        # concurrentes se serializan aquí; cada número se asigna una vez.
        cur.execute(
            "SELECT siguiente, hasta FROM os_ncf_secuencias"
            " WHERE company_id=%s AND tipo=%s FOR UPDATE",
            (self.c, tipo))
        sec = cur.fetchone()
        if sec is None:
            raise NoEncontrado(
                f"sin rango NCF para {tipo}: cree el rango primero")
        siguiente, hasta = sec[0], sec[1]
        if siguiente > hasta:
            raise ErrorITBIS(
                f"rango {tipo} agotado: solicite un rango nuevo")
        numero = _formatear(tipo, siguiente)
        cur.execute(
            """INSERT INTO os_ncf_emitidos
                   (company_id, numero, tipo, secuencial, concepto,
                    estado, clave_idempotencia, emitido_por)
               VALUES (%s,%s,%s,%s,%s,'emitido',%s,%s)
               ON CONFLICT (company_id, numero) DO NOTHING""",
            (self.c, numero, tipo, siguiente, (concepto or "").strip(),
             clave, actor or ""))
        if cur.rowcount == 0:
            # No debería ocurrir (el FOR UPDATE serializa), pero si la
            # clave estaba presente se devuelve el existente.
            if clave:
                cur.execute(
                    "SELECT numero, tipo, secuencial, concepto, estado,"
                    " emitido_por, emitido_en FROM os_ncf_emitidos"
                    " WHERE company_id=%s AND clave_idempotencia=%s",
                    (self.c, clave))
                ex = _una(cur)
                if ex:
                    return {**self._emitido_dict(ex), "ok": True,
                            "idempotente": True, "repetido": True,
                            "sintetico": True, "aviso": AVISO_NCF_SINTETICO}
            raise ErrorContab(
                f"colisión inesperada al emitir {numero}")
        cur.execute(
            "UPDATE os_ncf_secuencias SET siguiente=%s,"
            " actualizada_en=now()"
            " WHERE company_id=%s AND tipo=%s",
            (siguiente + 1, self.c, tipo))
        self._audit("ncf", actor, numero,
                    {"accion": "emitir_sintetico", "tipo": tipo,
                     "secuencial": siguiente,
                     "concepto": (concepto or "").strip(),
                     "sintetico": True})
        return {"ok": True, "ncf": numero, "tipo": tipo,
                "secuencial": siguiente,
                "concepto": (concepto or "").strip(),
                "estado": "emitido", "idempotente": False,
                "sintetico": True, "aviso": AVISO_NCF_SINTETICO,
                "disponibles_restantes": max(0, hasta - siguiente)}

    # ---- lectura ------------------------------------------------------------
    def _emitido_dict(self, r: dict) -> dict:
        d = {"ncf": r["numero"], "tipo": r["tipo"],
             "secuencial": r["secuencial"],
             "concepto": r.get("concepto") or "",
             "estado": r.get("estado") or "emitido",
             "emitido_por": r.get("emitido_por") or "",
             "emitido_en": r.get("emitido_en")}
        if r.get("estado") == "anulado":
            d["motivo_anulacion"] = r.get("motivo_anulacion") or ""
            d["anulado_por"] = r.get("anulado_por") or ""
            d["anulado_en"] = r.get("anulado_en")
        return d

    def obtener(self, numero: str) -> dict | None:
        numero = (numero or "").strip().upper()
        self.cur.execute(
            "SELECT numero, tipo, secuencial, concepto, estado,"
            " emitido_por, emitido_en, anulado_por, anulado_en,"
            " motivo_anulacion FROM os_ncf_emitidos"
            " WHERE company_id=%s AND numero=%s",
            (self.c, numero))
        r = _una(self.cur)
        return self._emitido_dict(r) if r else None

    def emitidos(self, tipo: str | None = None,
                 estado: str | None = None) -> list[dict]:
        sql = ("SELECT numero, tipo, secuencial, concepto, estado,"
               " emitido_por, emitido_en, anulado_por, anulado_en,"
               " motivo_anulacion FROM os_ncf_emitidos"
               " WHERE company_id=%s")
        params: list = [self.c]
        if tipo:
            sql += " AND tipo=%s"
            params.append(_validar_tipo(tipo))
        if estado:
            if estado not in ("emitido", "anulado"):
                raise ErrorContab(f"estado inválido: {estado}")
            sql += " AND estado=%s"
            params.append(estado)
        sql += " ORDER BY tipo, secuencial"
        self.cur.execute(sql, params)
        return [self._emitido_dict(r) for r in _fila_dict(self.cur)]

    def documento(self, numero: str) -> dict:
        """Vista de documento fiscal para un NCF emitido.

        Claramente marcado como NO CERTIFICADO DGII: es un documento de
        organización interna, no un comprobante fiscal válido.
        """
        r = self.obtener(numero)
        if r is None:
            raise NoEncontrado(f"NCF inexistente: {numero}")
        return {
            "ok": True,
            "documento": "COMPROBANTE FISCAL — DOCUMENTO DE PRUEBA INTERNA",
            "certificacion_dgii": "NO CERTIFICADO DGII",
            "numero": r["ncf"],
            "tipo": r["tipo"],
            "descripcion_tipo": TIPOS_NCF[r["tipo"]],
            "secuencial": r["secuencial"],
            "concepto": r["concepto"],
            "estado": r["estado"],
            "emitido_por": r["emitido_por"],
            "emitido_en": r["emitido_en"],
            "motivo_anulacion": r.get("motivo_anulacion") or "",
            "anulado_por": r.get("anulado_por") or "",
            "anulado_en": r.get("anulado_en"),
            "sintetico": True,
            "aviso": AVISO_NCF_SINTETICO,
            "aviso_fiscal": AVISO_NO_CERTIFICADO,
        }

    # ---- anulación (auditada; el número queda reservado) --------------------
    def anular(self, numero: str, motivo: str = "",
               actor: str = "") -> dict:
        numero = (numero or "").strip().upper()
        if not numero:
            raise ErrorContab("numero es obligatorio")
        cur = self.cur
        cur.execute(
            "UPDATE os_ncf_emitidos SET estado='anulado',"
            " anulado_por=%s, anulado_en=now(), motivo_anulacion=%s"
            " WHERE company_id=%s AND numero=%s AND estado='emitido'",
            (actor or "", (motivo or "").strip(), self.c, numero))
        if cur.rowcount == 1:
            self._audit("ncf", actor, numero,
                        {"accion": "anular",
                         "motivo": (motivo or "").strip(),
                         "nota": "el número queda reservado; no se reutiliza"})
            r = self.obtener(numero)
            return {"ok": True, **r, "sintetico": True,
                    "aviso": AVISO_NCF_SINTETICO}
        cur.execute(
            "SELECT estado FROM os_ncf_emitidos"
            " WHERE company_id=%s AND numero=%s", (self.c, numero))
        fila = cur.fetchone()
        if fila is None:
            raise NoEncontrado(f"NCF inexistente: {numero}")
        raise ErrorContab(f"el NCF {numero} ya está anulado")
