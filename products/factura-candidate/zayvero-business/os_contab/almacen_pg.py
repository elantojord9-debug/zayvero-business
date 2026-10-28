"""BLOQUE CONTABILIDAD — Persistencia PostgreSQL (P1a, issue #6).

Presupuestos y extractos/conciliaciones en PG (migración 018), con el
mismo patrón que `os_empleados/almacen.py`:

- `tx_contab(company_id)`: transacción de escritura con lock por empresa.
  RE-ENTRANTE: si el hilo ya corre dentro de un `mutar_os` para la misma
  empresa, PARTICIPA en esa transacción (vía `os_persist.tx.tx_ambiente`)
  en vez de abrir una conexión anidada — la conexión anidada se
  interbloquea con el lock de empresa (deadlock real ya ocurrido).
  En ese caso no hace commit/rollback propios: los hace mutar_os.
- `lectura_contab(company_id)`: cursor de solo lectura (sin lock);
  participa en la transacción ambiente si la hay.
- `AlmacenContabPG`: operaciones de almacenamiento puras sobre un cursor
  (sin validaciones de dominio: esas viven en los registros).
- `RegistroPresupuestosPG` / `RegistroConciliacionPG`: misma API pública
  que los registros en memoria; heredan la lógica de dominio
  (ejecución/proyección, cotejo) y cambian solo el almacenamiento.
  Idempotencia: reintentar con la misma clave natural no duplica
  (ON CONFLICT DO NOTHING + relectura).

Todo con company_id (aislamiento). Datos sintéticos en local.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import date, datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from .errores import ErrorContab, NoEncontrado  # noqa: E402
from .presupuestos import (  # noqa: E402
    RegistroPresupuestos, Presupuesto, ErrorPresupuesto)
from .conciliacion import (  # noqa: E402
    RegistroConciliacion, LineaExtracto, AVISO_SIMULADO, ESTADOS,
    _cotejar_linea, _parsear_csv_extracto, ErrorConciliacion)


# --- Serialización -----------------------------------------------------------
def _js(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if isinstance(v, date):
        return v.isoformat()
    return v


def _filas_dict(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [{c: _js(v) for c, v in zip(cols, fila)}
            for fila in cur.fetchall()]


def _una(cur) -> dict | None:
    filas = _filas_dict(cur)
    return filas[0] if filas else None


def _num_s(v) -> str:
    """Decimal -> str sin ceros insignificantes ('10000.00' -> '10000').

    Paridad de formato con el registro en memoria (que conserva el
    texto de entrada); mismo criterio que el `_s` de presupuestos.py.
    """
    s = format(_dec(v).normalize(), "f")
    return s if s not in ("", "-") else "0"


_COLS_PRES = ("presupuesto_id, company_id, categoria, periodo, limite, "
              "moneda, creado_en, actualizado_en")
_COLS_LIN = ("linea_id, company_id, extracto_id, nro_linea, fecha, "
             "descripcion, monto, moneda, referencia, estado, "
             "contraparte_id, marca_manual, creado_en, actualizado_en")
_COLS_CONC = ("conciliacion_id, company_id, extracto_id, entrada_hash, "
              "creada_en, lineas_total, resumen, resultado, simulado")


def _presupuesto_desde_fila(f: dict) -> Presupuesto:
    return Presupuesto(
        presupuesto_id=f["presupuesto_id"], company_id=f["company_id"],
        categoria=f["categoria"], periodo=f["periodo"],
        limite=_num_s(f["limite"]), moneda=f["moneda"])


def _linea_desde_fila(f: dict) -> LineaExtracto:
    marca = f.get("marca_manual") or {}
    if isinstance(marca, str):
        marca = json.loads(marca)
    return LineaExtracto(
        linea_id=f["linea_id"], fecha=_js(f["fecha"]),
        descripcion=f.get("descripcion") or "",
        monto=_num_s(f["monto"]), moneda=f.get("moneda") or "",
        referencia=f.get("referencia") or "",
        estado=f.get("estado") or "pendiente",
        contraparte_id=f.get("contraparte_id") or "",
        marca_manual=dict(marca))


def _hash_entradas(lineas: list[LineaExtracto], movs: list) -> str:
    """sha256 de las ENTRADAS del cotejo (no de los resultados).

    Excluye estado/contraparte_id calculados: un reintento idéntico da
    el mismo hash aunque los estados ya estén persistidos; una marca
    manual del operador (entrada real) sí cambia el hash.
    """
    h = hashlib.sha256()
    for lin in sorted(lineas, key=lambda l: l.linea_id):
        h.update("\x1f".join([
            lin.linea_id, lin.fecha or "", str(_dec(lin.monto)),
            lin.moneda or "", lin.referencia or "",
            json.dumps(lin.marca_manual or {}, sort_keys=True,
                       default=str)]).encode("utf-8"))
    for m in sorted(movs, key=lambda m: m.mov_id):
        h.update("\x1f".join([
            m.mov_id, (m.fecha or "")[:10], str(_dec(m.monto)),
            m.moneda or "", m.referencia or ""]).encode("utf-8"))
    return h.hexdigest()


# --- Transacciones -------------------------------------------------------------
class _TxContabPG:
    """Transacción propia del bloque (conexión dedicada + lock)."""

    def __init__(self, conn, company_id: str):
        self.conn = conn
        self.cur = conn.cursor()
        self.company_id = company_id
        self._eventos: list[dict] = []

    def auditar(self, tipo: str, actor: str, ref_id: str,
                detalle: dict | None = None) -> None:
        """Encola un evento; se inserta pre-commit (atomicidad)."""
        from os_persist.auditoria import TIPOS_EVENTO
        if tipo not in TIPOS_EVENTO:
            raise ErrorContab(f"tipo de evento inválido: {tipo!r}")
        self._eventos.append({
            "evento_id": uuid.uuid4().hex,
            "company_id": self.company_id,
            "tipo": tipo,
            "actor": actor or "",
            "ref_id": ref_id or "",
            "detalle": detalle or {},
            "resultado": "exito",
        })

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
def tx_contab(company_id: str):
    """Transacción de escritura con lock por empresa (patrón mutar_os).

    RE-ENTRANCIA: si el hilo ya corre dentro de un mutar_os para la
    misma empresa, PARTICIPA en esa transacción en vez de abrir una
    conexión anidada (la anidada se interbloquea con el lock de empresa:
    deadlock por transactionid indetectable por PG). En ese caso no
    hace commit/rollback propios: los hace mutar_os.

    Uso:
        with tx_contab(cid) as tx:
            alm = AlmacenContabPG(tx.cur, cid)
            alm.guardar_presupuesto(p)
            tx.auditar("presupuesto", actor, p.presupuesto_id, {...})
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
        tx = _TxContabPG(conn, company_id)
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
def lectura_contab(company_id: str):
    """Cursor de solo lectura (sin lock).

    Si hay una transacción ambiente de mutar_os para la empresa, usa su
    cursor (ve los cambios no confirmados de la operación en curso);
    si no, abre una conexión propia en autocommit.
    """
    if not company_id:
        raise ErrorContab("company_id es obligatorio")
    from os_persist.tx import tx_ambiente
    amb_cid, amb_cur = tx_ambiente()
    if amb_cid == company_id and amb_cur is not None:
        yield amb_cur
        return
    from os_persist.pg import connect
    conn = connect()
    try:
        conn.autocommit = True
        yield conn.cursor()
    finally:
        conn.close()


# =====================================================================
# Almacén: operaciones puras de almacenamiento sobre un cursor
# =====================================================================
class AlmacenContabPG:
    """Operaciones del bloque sobre PostgreSQL (migración 018).

    `cur`: cursor psycopg dentro de una transacción (tx_contab o la
    ambiente de mutar_os). No valida dominio ni audita: eso lo hacen los
    registros. Toda lectura filtra por company_id.
    """

    def __init__(self, cur, company_id: str):
        if not company_id:
            raise ErrorContab("company_id es obligatorio")
        self.cur = cur
        self.c = company_id

    # ---- presupuestos -------------------------------------------------
    def guardar_presupuesto(self, p: Presupuesto) -> tuple[dict, bool]:
        """INSERT idempotente por presupuesto_id.

        Devuelve (fila, inserto). Reintento con el mismo id y la misma
        clave natural -> (fila existente, False), sin duplicar. Mismo id
        con distinta clave natural, o clave natural de otro id ->
        ErrorPresupuesto (misma regla que el registro en memoria).
        """
        cur = self.cur
        try:
            cur.execute(
                f"""INSERT INTO os_presupuestos
                        (presupuesto_id, company_id, categoria, periodo,
                         limite, moneda)
                    VALUES (%s,%s,%s,%s,%s,%s)
                    ON CONFLICT (company_id, presupuesto_id) DO NOTHING
                    RETURNING {_COLS_PRES}""",
                (p.presupuesto_id, self.c, p.categoria, p.periodo,
                 str(_dec(p.limite)), p.moneda))
            fila = _una(cur)
        except Exception as e:
            # El DO NOTHING ya cubre el choque de presupuesto_id: un
            # UniqueViolation aquí es la clave natural (categoria,
            # periodo) de OTRO presupuesto_id.
            if "ux_os_presupuestos_clave_natural" in str(e):
                raise ErrorPresupuesto(
                    f"ya existe presupuesto para {p.categoria} en "
                    f"{p.periodo}")
            raise
        if fila is not None:
            return fila, True
        exist = self.obtener_presupuesto_por_id(p.presupuesto_id)
        if (exist["categoria"], exist["periodo"]) == (p.categoria,
                                                     p.periodo):
            return exist, False  # reintento idéntico: no duplica
        raise ErrorPresupuesto(
            f"presupuesto_id {p.presupuesto_id!r} ya existe con otra "
            f"categoría/período")

    def obtener_presupuesto(self, categoria: str,
                            periodo: str) -> dict | None:
        self.cur.execute(
            f"SELECT {_COLS_PRES} FROM os_presupuestos"
            " WHERE company_id=%s AND categoria=%s AND periodo=%s",
            (self.c, categoria, periodo))
        return _una(self.cur)

    def obtener_presupuesto_por_id(self, presupuesto_id: str) -> dict | None:
        self.cur.execute(
            f"SELECT {_COLS_PRES} FROM os_presupuestos"
            " WHERE company_id=%s AND presupuesto_id=%s",
            (self.c, presupuesto_id))
        return _una(self.cur)

    def listar_presupuestos(self) -> list[dict]:
        self.cur.execute(
            f"SELECT {_COLS_PRES} FROM os_presupuestos"
            " WHERE company_id=%s ORDER BY periodo, categoria",
            (self.c,))
        return _filas_dict(self.cur)

    # ---- extractos ------------------------------------------------------
    def reservar_linea_ids(self, n: int) -> list[str]:
        """Reserva n ids "L<k>" consecutivos para la empresa."""
        cur = self.cur
        cur.execute(
            "INSERT INTO os_contab_seqs (company_id) VALUES (%s)"
            " ON CONFLICT (company_id) DO NOTHING", (self.c,))
        cur.execute(
            "SELECT proxima_linea FROM os_contab_seqs WHERE company_id=%s",
            (self.c,))
        base = cur.fetchone()[0]
        cur.execute(
            "UPDATE os_contab_seqs SET proxima_linea = proxima_linea + %s"
            " WHERE company_id=%s", (n, self.c))
        return [f"L{base + i}" for i in range(n)]

    def asegurar_empresa(self) -> None:
        """Crea la fila en os_companies si falta (idempotente)."""
        self.cur.execute(
            "INSERT INTO os_companies (company_id) VALUES (%s)"
            " ON CONFLICT (company_id) DO NOTHING", (self.c,))

    def cargar_lineas(self, extracto_id: str,
                      filas: list[dict]) -> tuple[list[str], int]:
        """Inserta líneas; idempotente por (extracto_id, nro_linea).

        Devuelve (linea_ids en orden, cantidad_nueva). Un reintento con
        el mismo extracto_id devuelve los mismos ids sin insertar nada
        y sin consumir la secuencia (sin huecos en los L<n>).
        """
        cur = self.cur
        cur.execute(
            "SELECT nro_linea, linea_id FROM os_extractos"
            " WHERE company_id=%s AND extracto_id=%s",
            (self.c, extracto_id))
        existentes = {r[0]: r[1] for r in cur.fetchall()}
        faltan = [i for i in range(1, len(filas) + 1)
                  if i not in existentes]
        nuevos = self.reservar_linea_ids(len(faltan))
        mapa = dict(zip(faltan, nuevos))
        for i in faltan:
            f = filas[i - 1]
            cur.execute(
                """INSERT INTO os_extractos
                       (linea_id, company_id, extracto_id, nro_linea, fecha,
                        descripcion, monto, moneda, referencia)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (company_id, extracto_id, nro_linea)
                   DO NOTHING""",
                (mapa[i], self.c, extracto_id, i, f["fecha"],
                 f["descripcion"], f["monto"], f["moneda"],
                 f["referencia"]))
        out = [existentes[i] if i in existentes else mapa[i]
               for i in range(1, len(filas) + 1)]
        return out, len(faltan)

    def insertar_linea_legada(self, lin: LineaExtracto, extracto_id: str,
                              nro_linea: int) -> None:
        """Copia una línea del registro en memoria (migración legacy)."""
        self.cur.execute(
            """INSERT INTO os_extractos
                   (linea_id, company_id, extracto_id, nro_linea, fecha,
                    descripcion, monto, moneda, referencia, estado,
                    contraparte_id, marca_manual)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
               ON CONFLICT (company_id, extracto_id, nro_linea)
               DO NOTHING""",
            (lin.linea_id, self.c, extracto_id, nro_linea, lin.fecha,
             lin.descripcion, str(_dec(lin.monto)), lin.moneda,
             lin.referencia, lin.estado, lin.contraparte_id or "",
             json.dumps(lin.marca_manual or {}, default=str)))

    def fijar_proxima_linea(self, n: int) -> None:
        """Asegura proxima_linea >= n (tras migración legacy)."""
        self.cur.execute(
            """INSERT INTO os_contab_seqs (company_id, proxima_linea)
               VALUES (%s, %s)
               ON CONFLICT (company_id) DO UPDATE SET
                 proxima_linea = GREATEST(os_contab_seqs.proxima_linea,
                                         EXCLUDED.proxima_linea)""",
            (self.c, n))

    def listar_lineas(self) -> list[dict]:
        self.cur.execute(
            f"SELECT {_COLS_LIN} FROM os_extractos"
            " WHERE company_id=%s ORDER BY creado_en, linea_id",
            (self.c,))
        return _filas_dict(self.cur)

    def obtener_linea(self, linea_id: str) -> dict | None:
        self.cur.execute(
            f"SELECT {_COLS_LIN} FROM os_extractos"
            " WHERE company_id=%s AND linea_id=%s",
            (self.c, linea_id))
        return _una(self.cur)

    def actualizar_estado(self, linea_id: str, estado: str,
                          contraparte_id: str) -> None:
        self.cur.execute(
            "UPDATE os_extractos SET estado=%s, contraparte_id=%s,"
            " actualizado_en=now()"
            " WHERE company_id=%s AND linea_id=%s",
            (estado, contraparte_id or "", self.c, linea_id))

    def marcar_linea(self, linea_id: str, estado: str,
                     marca: dict) -> dict | None:
        self.cur.execute(
            f"""UPDATE os_extractos
                   SET estado=%s, marca_manual=%s::jsonb,
                       actualizado_en=now()
                 WHERE company_id=%s AND linea_id=%s
                 RETURNING {_COLS_LIN}""",
            (estado, json.dumps(marca, default=str), self.c, linea_id))
        return _una(self.cur)

    # ---- conciliaciones (corridas) ----------------------------------------
    def guardar_corrida(self, conciliacion_id: str, extracto_id: str,
                        entrada_hash: str, lineas_total: int, resumen: dict,
                        resultado: dict) -> dict:
        """Registra la corrida; idempotente por
        (extracto_id, entrada_hash): un reintento devuelve la corrida
        existente sin duplicar."""
        cur = self.cur
        cur.execute(
            f"""INSERT INTO os_conciliaciones
                    (conciliacion_id, company_id, extracto_id, entrada_hash,
                     lineas_total, resumen, resultado)
                VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb)
                ON CONFLICT (company_id, extracto_id, entrada_hash)
                DO NOTHING
                RETURNING {_COLS_CONC}""",
            (conciliacion_id, self.c, extracto_id, entrada_hash,
             lineas_total, json.dumps(resumen, default=str),
             json.dumps(resultado, default=str)))
        fila = _una(cur)
        if fila is not None:
            return fila
        cur.execute(
            f"SELECT {_COLS_CONC} FROM os_conciliaciones"
            " WHERE company_id=%s AND extracto_id=%s AND entrada_hash=%s",
            (self.c, extracto_id, entrada_hash))
        return _una(cur)

    def contar_corridas(self) -> int:
        self.cur.execute(
            "SELECT COUNT(*) FROM os_conciliaciones WHERE company_id=%s",
            (self.c,))
        return self.cur.fetchone()[0]


# =====================================================================
# Registros PG: misma API que los de memoria, almacenamiento en PG
# =====================================================================
class RegistroPresupuestosPG(RegistroPresupuestos):
    """Misma API que RegistroPresupuestos; almacenamiento en PostgreSQL.

    Hereda ejecucion()/proyeccion_cierre() (operan sobre obtener()).
    `mem`: registro en memoria con datos legacy; se copian a PG de forma
    idempotente en el primer uso (sin dañar la memoria).
    """

    def __init__(self, company_id: str,
                 mem: RegistroPresupuestos | None = None):
        super().__init__(company_id)
        self._mem = mem
        self._migrado = False
        self._mig_lock = threading.Lock()

    def _migrar_mem(self, alm: AlmacenContabPG) -> None:
        if self._migrado:
            return
        with self._mig_lock:
            if self._migrado:
                return
            self._migrado = True
            mem = self._mem
            if mem is None:
                return
            alm.asegurar_empresa()
            with mem._lock:
                filas = list(mem._pres.values())
            for p in filas:
                try:
                    alm.guardar_presupuesto(p)
                except ErrorPresupuesto:
                    pass  # ante choque de clave natural, PG manda

    # -- escritura ---------------------------------------------------------
    def crear(self, p: Presupuesto, actor: str = "",
              detalle: dict | None = None) -> Presupuesto:
        p.validate()
        if p.company_id != self.company_id:
            raise ErrorPresupuesto("presupuesto de otra empresa")
        with tx_contab(self.company_id) as tx:
            alm = AlmacenContabPG(tx.cur, self.company_id)
            self._migrar_mem(alm)
            fila, inserto = alm.guardar_presupuesto(p)
            if inserto:
                tx.auditar("presupuesto", actor, p.presupuesto_id,
                           detalle if detalle is not None else {
                               "categoria": p.categoria,
                               "periodo": p.periodo,
                               "limite": str(_dec(p.limite)),
                               "moneda": p.moneda})
        return _presupuesto_desde_fila(fila)

    # -- lectura -------------------------------------------------------------
    def obtener(self, categoria: str, periodo: str) -> Presupuesto:
        with lectura_contab(self.company_id) as cur:
            alm = AlmacenContabPG(cur, self.company_id)
            self._migrar_mem(alm)
            fila = alm.obtener_presupuesto(categoria, periodo)
        if fila is None:
            raise NoEncontrado(
                f"sin presupuesto para {categoria} en {periodo}")
        return _presupuesto_desde_fila(fila)

    def listar(self) -> list[Presupuesto]:
        with lectura_contab(self.company_id) as cur:
            alm = AlmacenContabPG(cur, self.company_id)
            self._migrar_mem(alm)
            filas = alm.listar_presupuestos()
        return [_presupuesto_desde_fila(f) for f in filas]


class RegistroConciliacionPG(RegistroConciliacion):
    """Misma API que RegistroConciliacion; almacenamiento en PostgreSQL.

    Reutiliza _parsear_csv_extracto() y _cotejar_linea() del módulo de
    memoria: las reglas de validación y cotejo son idénticas.
    """

    def __init__(self, company_id: str,
                 mem: RegistroConciliacion | None = None):
        super().__init__(company_id)
        self._mem = mem
        self._migrado = False
        self._mig_lock = threading.Lock()

    def _migrar_mem(self, alm: AlmacenContabPG) -> None:
        if self._migrado:
            return
        with self._mig_lock:
            if self._migrado:
                return
            self._migrado = True
            mem = self._mem
            if mem is None:
                return
            alm.asegurar_empresa()
            with mem._lock:
                lineas = sorted(mem._lineas.values(),
                                key=lambda l: l.linea_id)
            if not lineas:
                return
            maxn = 0
            for i, lin in enumerate(lineas, start=1):
                m = re.match(r"^L(\d+)$", lin.linea_id or "")
                nro = int(m.group(1)) if m else 0
                maxn = max(maxn, nro)
                alm.insertar_linea_legada(lin, "memoria",
                                          nro if nro else 10 ** 9 + i)
            alm.fijar_proxima_linea(maxn + 1)

    # -- carga ------------------------------------------------------------------
    def cargar_csv(self, texto_csv: str, actor: str = "",
                   extracto_id: str | None = None) -> dict:
        filas = _parsear_csv_extracto(texto_csv)
        eid = (extracto_id or "").strip() or uuid.uuid4().hex[:12]
        with tx_contab(self.company_id) as tx:
            alm = AlmacenContabPG(tx.cur, self.company_id)
            self._migrar_mem(alm)
            ids, nuevos = alm.cargar_lineas(eid, filas)
            if nuevos:
                tx.auditar("conciliacion", actor, "extracto",
                           {"accion": "cargar_csv_simulado",
                            "lineas": nuevos, "extracto_id": eid})
        return {"simulado": True, "aviso": AVISO_SIMULADO,
                "lineas_cargadas": len(ids), "linea_ids": ids,
                "extracto_id": eid}

    def lineas(self) -> list[LineaExtracto]:
        with lectura_contab(self.company_id) as cur:
            alm = AlmacenContabPG(cur, self.company_id)
            self._migrar_mem(alm)
            filas = alm.listar_lineas()
        return [_linea_desde_fila(f) for f in filas]

    # -- cotejo -------------------------------------------------------------------
    def conciliar(self, movimientos: list) -> dict:
        """Coteja y PERSISTE los estados en una sola transacción."""
        movs = list(movimientos)
        with tx_contab(self.company_id) as tx:
            alm = AlmacenContabPG(tx.cur, self.company_id)
            self._migrar_mem(alm)
            filas = alm.listar_lineas()
            lineas = [_linea_desde_fila(f) for f in filas]
            entrada_hash = _hash_entradas(lineas, movs)
            usadas: set[str] = set()
            detalle = []
            cambios = []
            for lin in lineas:
                if lin.marca_manual:
                    # La marca manual del operador prevalece.
                    detalle.append({**lin.to_dict(),
                                    "evidencia": lin.marca_manual})
                    if lin.contraparte_id:
                        usadas.add(lin.contraparte_id)
                    continue
                estado, contraparte, evidencia = _cotejar_linea(lin, movs)
                if estado == "conciliado":
                    if contraparte in usadas:
                        estado = "discrepancia"
                        evidencia = {
                            "regla": "contraparte ya conciliada por otra "
                                     "línea",
                            "contraparte_id": contraparte,
                            "nota": "revisar: ¿cargo duplicado en el "
                                     "extracto?"}
                        contraparte = ""
                    else:
                        usadas.add(contraparte)
                lin.estado, lin.contraparte_id = estado, contraparte or ""
                cambios.append((lin.linea_id, estado, contraparte or ""))
                detalle.append({**lin.to_dict(), "evidencia": evidencia})
            for lid, est, cp in cambios:
                alm.actualizar_estado(lid, est, cp)
            sin_extracto = [m.mov_id for m in movs if m.mov_id not in usadas]
            resumen: dict[str, int] = {}
            for d in detalle:
                resumen[d["estado"]] = resumen.get(d["estado"], 0) + 1
            alcance = ",".join(sorted(
                {f["extracto_id"] for f in filas if f.get("extracto_id")}))
            respuesta = {
                "simulado": True, "aviso": AVISO_SIMULADO,
                "lineas": detalle, "resumen": resumen,
                "movimientos_libro_sin_extracto": sin_extracto,
                "nota": ("los 'pendiente'/'discrepancia' son lista de "
                         "trabajo: revíselos contra su banco real")}
            alm.guardar_corrida("conc-" + uuid.uuid4().hex[:12], alcance,
                                entrada_hash, len(detalle), resumen,
                                respuesta)
        return respuesta

    # -- marca manual ---------------------------------------------------------------
    def marcar(self, linea_id: str, estado: str, actor: str = "",
               nota: str = "") -> dict:
        if estado not in ESTADOS:
            raise ErrorConciliacion(
                f"estado inválido: {estado} (válidos: {', '.join(ESTADOS)})")
        with tx_contab(self.company_id) as tx:
            alm = AlmacenContabPG(tx.cur, self.company_id)
            self._migrar_mem(alm)
            marca = {"estado_manual": estado,
                     "actor": actor or "(sin actor)",
                     "nota": nota or "",
                     "simulado": True}
            fila = alm.marcar_linea(linea_id, estado, marca)
            if fila is None:
                raise NoEncontrado(f"línea inexistente: {linea_id}")
            tx.auditar("conciliacion", actor, linea_id,
                       {"accion": "marca_manual", "estado": estado,
                        "nota": nota or ""})
        return {"simulado": True, "aviso": AVISO_SIMULADO,
                "linea": _linea_desde_fila(fila).to_dict()}
