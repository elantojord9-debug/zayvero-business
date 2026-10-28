"""BLOQUE 6 — Aprobaciones: persistencia PostgreSQL.

P1d (issue #6, 2026-10-13) — PERSISTENCIA REAL + EJECUCIÓN ÚNICA:
- Los estados (pendiente/aprobada/rechazada/ejecutada/cancelada)
  viven en `os_aprobaciones` (migración 008 + 021): sobreviven
  reinicios sin pérdida.
- Transiciones de estado ATÓMICAS: cada decidir/marcar/cancelar corre
  en UNA transacción que incluye el evento de auditoría en
  `os_eventos` (mismo cursor, pre-commit). Estado y evento se
  confirman juntos o se revierten juntos.
- EJECUCIÓN ÚNICA: `decidir(aprobado)` sobre una solicitud ya
  'aprobada' devuelve 409, SALVO que la ejecución anterior haya
  fallado y quedado registrada (`marcar_ejecucion_fallida`). Dos
  decisiones concurrentes se serializan con SELECT ... FOR UPDATE
  sobre la fila: la segunda ve 'aprobada' sin fallo registrado y
  recibe 409 -> la operación subyacente se ejecuta UNA sola vez.
- IDEMPOTENCIA por clave de operación (migración 021): `solicitar`
  con `clave_operacion` repetida devuelve la solicitud existente
  (sin duplicar); misma clave + operación distinta -> 409.

Diseño anti-deadlock (de la 008, se conserva):
- `solicitar()`: INSERT simple (sin locks de fila).
- `decidir()` y demás: SELECT ... FOR UPDATE sobre la FILA de la
  solicitud + UPDATE en una transacción corta. NUNCA toman
  os_company_lock, por lo que es seguro llamarlos DENTRO de mutar_os
  (que ya tiene os_company_lock) y desde el handler del catálogo.
- RE-ENTRANCIA (P1d): si el hilo ya corre dentro de un `mutar_os`
  para la misma empresa, se PARTICIPA en esa transacción
  (`tx_ambiente()`, mismo patrón que os_empleados.tx_empleados) en
  vez de abrir una conexión anidada — la conexión anidada se
  interbloquea con el lock de empresa. En modo ambiente no hay
  commit/rollback propios: los hace el mutar_os exterior, y la
  auditoría va por su cola (atomicidad total).

Sin DSN: solicitar/decidir lanzan ErrorAprobacion (las rutas HTTP
responden 503; los umbrales no se evalúan sin PG).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid
from contextlib import contextmanager
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .modelo import (  # noqa: E402
    TIPOS, ESTADOS, ConfigAprobaciones, ErrorAprobacion,
    SolicitudAprobacion, validar_tipo,
)

DSN_ENV = "ZAYVERO_OS_DATABASE_URL"
_MIGRACION_021 = "021_os_aprobaciones_pg.sql"


def umbrales_empresa(company_id: str) -> ConfigAprobaciones:
    """Umbrales de aprobación de la empresa (os_catalogo_config).

    Sin fila: defaults de DEMO (origen demo_por_defecto). Sin DSN:
    lanza ErrorAprobacion (el llamador decide: legado sin PG).
    """
    try:
        from catalogo.config import leer_config
        fila = leer_config(company_id)
    except Exception as e:
        raise ErrorAprobacion(f"no se pudo leer la configuración: {e}")
    return ConfigAprobaciones.desde_fila({
        "umbral_cambio_precio": fila["umbral_cambio_precio"],
        "umbral_devolucion_monto": fila["umbral_devolucion_monto"],
        "moneda_defecto": fila["moneda_defecto"],
        "personalizada": fila["origen"] == "personalizado",
    })


def _dsn() -> str:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        raise ErrorAprobacion(
            f"las aprobaciones requieren {DSN_ENV} (PostgreSQL local)")
    return dsn


def _nueva_id() -> str:
    return f"apr-{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------
# Migración 021: aplicación idempotente sin pasar por el manifiesto.
#
# _orden.txt está congelado por orden de la coordinación (no editar);
# la 021 se aplica desde aquí: DDL 100 % IF NOT EXISTS, seguro de
# correr N veces y desde N procesos a la vez. Una vez por proceso
# (bandera de módulo); las pruebas pueden forzarla con
# aplicar_migracion_021().
# ---------------------------------------------------------------------
_021_aplicada = False


def _sql_021() -> str:
    ruta = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "os_persist", "schema", _MIGRACION_021)
    with open(ruta, encoding="utf-8") as fh:
        return fh.read()


def aplicar_migracion_021(conn=None) -> None:
    """Aplica la 021 de forma idempotente (IF NOT EXISTS).

    Con `conn`: usa esa conexión (la prueba hace commit). Sin `conn`:
    abre una conexión propia en autocommit y la cierra.
    """
    sql = _sql_021()
    if conn is None:
        from os_persist.pg import connect
        propia = connect(_dsn())
        try:
            propia.autocommit = True
            with propia.cursor() as cur:
                cur.execute(sql)
        finally:
            propia.close()
    else:
        with conn.cursor() as cur:
            cur.execute(sql)


def _asegurar_schema_021() -> None:
    global _021_aplicada
    if _021_aplicada:
        return
    aplicar_migracion_021()
    _021_aplicada = True


# ---------------------------------------------------------------------
# Transacciones: propia (standalone) o ambiente (dentro de mutar_os).
# ---------------------------------------------------------------------
class _TxPropia:
    """Transacción propia: abre conexión, commit/rollback al salir.

    La auditoría se encola y se inserta pre-commit con el MISMO
    cursor (patrón de os_empleados/almacen.py::_TxPG).
    """

    def __init__(self, conn, company_id: str):
        self.conn = conn
        self.cur = conn.cursor()
        self.company_id = company_id
        self._eventos: list[dict] = []

    def auditar(self, tipo: str, actor: str, ref_id: str,
                detalle: dict | None = None) -> None:
        from os_persist.auditoria import TIPOS_EVENTO
        if tipo not in TIPOS_EVENTO:
            raise ErrorAprobacion(f"tipo de evento inválido: {tipo!r}")
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
    """Participa en el mutar_os ambiente: sin conexión propia, sin
    commit/rollback (los hace el mutar_os exterior). La auditoría usa
    la cola del mutar_os (se inserta pre-commit en la misma
    transacción)."""

    def __init__(self, cur, company_id: str):
        self.cur = cur
        self.company_id = company_id

    def auditar(self, tipo: str, actor: str, ref_id: str,
                detalle: dict | None = None) -> None:
        from os_persist.auditoria import auditar, TIPOS_EVENTO
        if tipo not in TIPOS_EVENTO:
            raise ErrorAprobacion(f"tipo de evento inválido: {tipo!r}")
        auditar(self.company_id, tipo, actor, ref_id, detalle or {})

    def commit(self) -> None:
        pass  # lo hace el mutar_os exterior

    def rollback(self) -> None:
        pass  # lo hace el mutar_os exterior


@contextmanager
def _tx(company_id: str):
    """Transacción de escritura, re-entrante con mutar_os.

    Yields un objeto con `.cur`, `.company_id`, `.auditar(...)`,
    `.commit()`, `.rollback()`. En modo ambiente el commit/rollback
    son no-op (los hace el mutar_os exterior).
    """
    from os_persist.tx import tx_ambiente
    amb_cid, amb_cur = tx_ambiente()
    if amb_cid == company_id and amb_cur is not None:
        yield _TxAmbiente(amb_cur, company_id)
        return
    from os_persist.pg import connect
    conn = connect(_dsn())
    tx = None
    try:
        conn.autocommit = False
        tx = _TxPropia(conn, company_id)
        tx.cur.execute(
            "INSERT INTO os_companies (company_id) VALUES (%s)"
            " ON CONFLICT (company_id) DO NOTHING", (company_id,))
        yield tx
        tx.commit()
    except Exception:
        if tx is not None:
            tx.rollback()
        raise
    finally:
        conn.close()


# ---------------------------------------------------------------------
# Lectura de filas
# ---------------------------------------------------------------------
_COLS = ("company_id, aprobacion_id, tipo, estado, solicitante, payload,"
         " monto, moneda, motivo_solicitud, clave_operacion, evidencia,"
         " created_at, decidida_por, decidida_at, motivo_decision,"
         " ejecutada_at, intentos_ejecucion, fallo_ejecucion_at,"
         " ultimo_error_ejecucion")


def _iso(v):
    if v is None:
        return None
    return v.isoformat() if isinstance(v, datetime) else str(v)


def _fila(r) -> dict:
    (cid, aid, tipo, estado, sol, pay, monto, mon, mot, clave, evid,
     creada, dpor, dat, dmot, ejec, intentos, fallo_at,
     ultimo_error) = r
    return {"company_id": cid, "aprobacion_id": aid, "tipo": tipo,
            "estado": estado, "solicitante": sol, "payload": pay or {},
            "monto": str(monto) if monto is not None else None,
            "moneda": mon, "motivo_solicitud": mot,
            "clave_operacion": clave, "evidencia": evid or {},
            "created_at": _iso(creada), "decidida_por": dpor,
            "decidida_at": _iso(dat), "motivo_decision": dmot,
            "ejecutada_at": _iso(ejec),
            "intentos_ejecucion": intentos or 0,
            "fallo_ejecucion_at": _iso(fallo_at),
            "ultimo_error_ejecucion": ultimo_error or ""}


def _hash_operacion(tipo: str, clave_operacion: str,
                    payload: dict | None = None) -> str:
    """sha256 canónico de (tipo, clave, payload).

    Misma clave + mismo cuerpo -> mismo hash (replay). Cualquier
    diferencia en el cuerpo -> hash distinto -> 409 (la clave
    identifica UNA operación; fail-closed).
    """
    canon = json.dumps({"tipo": tipo, "clave": clave_operacion,
                        "payload": payload or {}},
                       sort_keys=True, separators=(",", ":"),
                       ensure_ascii=False, default=str)
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


class Aprobaciones:
    """Solicitudes de aprobación de UNA empresa (PostgreSQL)."""

    def __init__(self, company_id: str):
        cid = (company_id or "").strip()
        if not cid:
            raise ErrorAprobacion("company_id no puede estar vacío")
        self.company_id = cid
        _dsn()  # falla temprano y claro sin DSN

    def _conn_lectura(self):
        from os_persist.pg import connect
        return connect(_dsn())

    def solicitar(self, tipo: str, solicitante: str, payload: dict,
                  monto=None, moneda: str | None = None,
                  motivo: str = "", clave_operacion: str | None = None,
                  evidencia: dict | None = None) -> dict:
        """Crea la solicitud en `pendiente_aprobacion`.

        `clave_operacion` (opcional): idempotencia por clave. Si ya
        existe una solicitud con la misma clave para esta empresa:
        - misma operación (mismo hash) -> se DEVUELVE la existente
          (`repetida: True`), sin duplicar;
        - operación distinta -> ErrorAprobacion (409 en HTTP).
        Sin clave: cada llamada crea una solicitud nueva (legado).

        La creación y su evento de auditoría ('solicitada') se
        confirman en la MISMA transacción. Si corre dentro de un
        mutar_os, participa en él (re-entrancia).
        """
        tipo = validar_tipo(tipo)
        clave = (clave_operacion or "").strip() or None
        aid = _nueva_id()
        _asegurar_schema_021()
        with _tx(self.company_id) as tx:
            cur = tx.cur
            if clave is not None:
                chash = _hash_operacion(tipo, clave, payload)
                cur.execute(
                    """INSERT INTO os_aprobaciones
                       (company_id, aprobacion_id, tipo, estado,
                        solicitante, payload, monto, moneda,
                        motivo_solicitud, clave_operacion, clave_hash,
                        evidencia)
                       VALUES (%s,%s,%s,'pendiente_aprobacion',
                               %s,%s::jsonb,%s,%s,%s,%s,%s,%s::jsonb)
                       ON CONFLICT (company_id, clave_operacion)
                       WHERE clave_operacion IS NOT NULL
                       DO NOTHING""",
                    (self.company_id, aid, tipo, solicitante or "",
                     json.dumps(payload or {}),
                     str(monto) if monto is not None else None,
                     (moneda or None), motivo or "", clave, chash,
                     json.dumps(evidencia or {})))
                if cur.rowcount == 0:
                    # La clave ya existía: devolver la solicitud
                    # original (idempotencia) o fallar si es otra
                    # operación con la misma clave.
                    cur.execute(
                        f"SELECT {_COLS} FROM os_aprobaciones"
                        " WHERE company_id=%s AND clave_operacion=%s",
                        (self.company_id, clave))
                    r = cur.fetchone()
                    if r is None:  # no debería pasar; reintentar limpio
                        raise ErrorAprobacion(
                            "conflicto de idempotencia: reintente")
                    reg = _fila(r)
                    # El hash de la fila existente se compara contra
                    # el de esta operación.
                    cur.execute(
                        "SELECT clave_hash FROM os_aprobaciones"
                        " WHERE company_id=%s AND clave_operacion=%s",
                        (self.company_id, clave))
                    hexist = cur.fetchone()[0]
                    if hexist != chash:
                        raise ErrorAprobacion(
                            f"clave de operación {clave!r} ya usada por "
                            f"otra solicitud ({reg['aprobacion_id']}); "
                            "use una clave distinta para una operación "
                            "distinta")
                    reg["repetida"] = True
                    reg["idempotente"] = True
                    return reg
                tx.auditar("aprobacion", solicitante or "", aid,
                           {"accion": "solicitada", "tipo": tipo,
                            "clave_operacion": clave})
                return {"ok": True, "aprobacion_id": aid, "tipo": tipo,
                        "estado": "pendiente_aprobacion",
                        "repetida": False, "idempotente": True}
            # Sin clave: INSERT simple (legado).
            cur.execute(
                """INSERT INTO os_aprobaciones
                   (company_id, aprobacion_id, tipo, estado,
                    solicitante, payload, monto, moneda,
                    motivo_solicitud, evidencia)
                   VALUES (%s,%s,%s,'pendiente_aprobacion',
                           %s,%s::jsonb,%s,%s,%s,%s::jsonb)""",
                (self.company_id, aid, tipo, solicitante or "",
                 json.dumps(payload or {}),
                 str(monto) if monto is not None else None,
                 (moneda or None), motivo or "",
                 json.dumps(evidencia or {})))
            tx.auditar("aprobacion", solicitante or "", aid,
                       {"accion": "solicitada", "tipo": tipo})
            return {"ok": True, "aprobacion_id": aid, "tipo": tipo,
                    "estado": "pendiente_aprobacion",
                    "repetida": False, "idempotente": False}

    def obtener(self, aprobacion_id: str) -> dict | None:
        conn = self._conn_lectura()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"SELECT {_COLS} FROM os_aprobaciones"
                    " WHERE company_id=%s AND aprobacion_id=%s",
                    (self.company_id, aprobacion_id))
                r = cur.fetchone()
                return _fila(r) if r else None
        finally:
            conn.close()

    def listar(self, estado: str | None = None,
               limite: int = 100) -> list[dict]:
        if estado is not None and estado not in ESTADOS:
            raise ErrorAprobacion(
                f"estado inválido: {estado!r} (use {', '.join(ESTADOS)})")
        conn = self._conn_lectura()
        try:
            with conn.cursor() as cur:
                q = (f"SELECT {_COLS} FROM os_aprobaciones"
                     " WHERE company_id=%s")
                args: list = [self.company_id]
                if estado:
                    q += " AND estado=%s"
                    args.append(estado)
                q += " ORDER BY created_at DESC LIMIT %s"
                args.append(max(1, min(int(limite), 500)))
                cur.execute(q, args)
                return [_fila(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def decidir(self, aprobacion_id: str, aprobar: bool,
               decidida_por: str, motivo: str = "") -> dict:
        """Aprueba o rechaza. Transacción corta con FOR UPDATE sobre la
        fila + evento de auditoría en la MISMA transacción.

        EJECUCIÓN ÚNICA: dos decisiones concurrentes se serializan; la
        segunda ve el estado ya decidido:
        - 'pendiente_aprobacion' -> decide (aprobada/rechazada).
        - 'aprobada' + aprobar=True -> 409, SALVO que haya un fallo de
          ejecución registrado (reintento tras fallo permitido).
        - 'aprobada' + aprobar=False -> 409 (no se rechaza lo aprobado).
        - 'rechazada'/'ejecutada'/'cancelada' -> 409.

        Devuelve el registro decidido. La ejecución de la operación
        aprobada la hace la capa HTTP y luego llama a
        marcar_ejecutada() (o a marcar_ejecucion_fallida() si falla).
        """
        aid = (aprobacion_id or "").strip()
        if not aid:
            raise ErrorAprobacion("aprobacion_id no puede estar vacío")
        _asegurar_schema_021()
        with _tx(self.company_id) as tx:
            cur = tx.cur
            cur.execute(
                f"SELECT {_COLS} FROM os_aprobaciones"
                " WHERE company_id=%s AND aprobacion_id=%s"
                " FOR UPDATE",
                (self.company_id, aid))
            r = cur.fetchone()
            if r is None:
                raise ErrorAprobacion(
                    f"solicitud inexistente: {aid!r}")
            reg = _fila(r)
            if reg["estado"] == "ejecutada":
                raise ErrorAprobacion(
                    f"{aid!r} ya fue ejecutada; no se puede decidir "
                    "de nuevo")
            if reg["estado"] == "cancelada":
                raise ErrorAprobacion(
                    f"{aid!r} ya está decidida (cancelada)")
            if reg["estado"] == "rechazada":
                raise ErrorAprobacion(
                    f"{aid!r} ya está decidida (rechazada)")
            reintento_tras_fallo = False
            if reg["estado"] == "aprobada":
                if not aprobar:
                    raise ErrorAprobacion(
                        f"{aid!r} ya está aprobada; no se puede "
                        "rechazar")
                # Aprobar una aprobada = reintento de ejecución, SOLO
                # si la ejecución anterior falló y quedó registrada.
                # Sin fallo registrado, la ejecución está pendiente o
                # en curso -> 409 (la operación se ejecuta UNA vez).
                if not reg["fallo_ejecucion_at"]:
                    raise ErrorAprobacion(
                        f"{aid!r} ya está aprobada (ejecución pendiente "
                        "o en curso); no se puede decidir de nuevo")
                reintento_tras_fallo = True
            nuevo = "aprobada" if aprobar else "rechazada"
            cur.execute(
                """UPDATE os_aprobaciones
                   SET estado=%s, decidida_por=%s, decidida_at=now(),
                       motivo_decision=%s,
                       fallo_ejecucion_at=NULL,
                       ultimo_error_ejecucion=''
                   WHERE company_id=%s AND aprobacion_id=%s""",
                (nuevo, decidida_por or "", motivo or "",
                 self.company_id, aid))
            tx.auditar("aprobacion", decidida_por or "", aid,
                       {"accion": "decidida",
                        "decision": nuevo,
                        "tipo": reg["tipo"],
                        "motivo": motivo or "",
                        "reintento_tras_fallo": reintento_tras_fallo})
            cur.execute(
                f"SELECT {_COLS} FROM os_aprobaciones"
                " WHERE company_id=%s AND aprobacion_id=%s",
                (self.company_id, aid))
            return _fila(cur.fetchone())

    def marcar_ejecutada(self, aprobacion_id: str,
                         detalle: dict | None = None) -> dict:
        """La llama la capa HTTP DESPUÉS de ejecutar la operación
        aprobada. Solo desde 'aprobada'. Transacción + auditoría en la
        misma TX. Concurrente: solo una gana (la otra recibe 409)."""
        aid = (aprobacion_id or "").strip()
        if not aid:
            raise ErrorAprobacion("aprobacion_id no puede estar vacío")
        _asegurar_schema_021()
        with _tx(self.company_id) as tx:
            cur = tx.cur
            cur.execute(
                f"SELECT {_COLS} FROM os_aprobaciones"
                " WHERE company_id=%s AND aprobacion_id=%s"
                " FOR UPDATE",
                (self.company_id, aid))
            r = cur.fetchone()
            if r is None:
                raise ErrorAprobacion(
                    f"solicitud inexistente: {aid!r}")
            reg = _fila(r)
            if reg["estado"] == "ejecutada":
                raise ErrorAprobacion(
                    f"{aid!r} ya fue ejecutada")
            if reg["estado"] != "aprobada":
                raise ErrorAprobacion(
                    f"{aid!r} no está aprobada pendiente de ejecución "
                    f"(estado: {reg['estado']})")
            cur.execute(
                """UPDATE os_aprobaciones
                   SET estado='ejecutada', ejecutada_at=now(),
                       intentos_ejecucion=intentos_ejecucion+1,
                       fallo_ejecucion_at=NULL,
                       ultimo_error_ejecucion=''
                   WHERE company_id=%s AND aprobacion_id=%s""",
                (self.company_id, aid))
            tx.auditar("aprobacion", reg["decidida_por"] or "", aid,
                       {"accion": "ejecutada", "tipo": reg["tipo"],
                        **(detalle or {})})
            return {"ok": True, "aprobacion_id": aid,
                    "estado": "ejecutada"}

    def marcar_ejecucion_fallida(self, aprobacion_id: str,
                                 error: str) -> dict:
        """Registra que la ejecución de una solicitud aprobada FALLÓ.

        Solo desde 'aprobada'. Habilita el reintento: un nuevo
        `decidir(aprobado)` limpia el marcador y autoriza otro
        intento. Transacción + auditoría en la misma TX.
        """
        aid = (aprobacion_id or "").strip()
        if not aid:
            raise ErrorAprobacion("aprobacion_id no puede estar vacío")
        _asegurar_schema_021()
        with _tx(self.company_id) as tx:
            cur = tx.cur
            cur.execute(
                f"SELECT {_COLS} FROM os_aprobaciones"
                " WHERE company_id=%s AND aprobacion_id=%s"
                " FOR UPDATE",
                (self.company_id, aid))
            r = cur.fetchone()
            if r is None:
                raise ErrorAprobacion(
                    f"solicitud inexistente: {aid!r}")
            reg = _fila(r)
            if reg["estado"] != "aprobada":
                raise ErrorAprobacion(
                    f"{aid!r} no está aprobada pendiente de ejecución "
                    f"(estado: {reg['estado']})")
            err = (error or "")[:500]
            cur.execute(
                """UPDATE os_aprobaciones
                   SET intentos_ejecucion=intentos_ejecucion+1,
                       fallo_ejecucion_at=now(),
                       ultimo_error_ejecucion=%s
                   WHERE company_id=%s AND aprobacion_id=%s""",
                (err, self.company_id, aid))
            tx.auditar("aprobacion", "", aid,
                       {"accion": "ejecucion_fallida",
                        "tipo": reg["tipo"], "error": err})
            return {"ok": True, "aprobacion_id": aid,
                    "estado": "aprobada",
                    "intentos_ejecucion": reg["intentos_ejecucion"] + 1,
                    "ultimo_error_ejecucion": err}

    def cancelar(self, aprobacion_id: str, solicitante: str) -> dict:
        """El solicitante cancela su propia solicitud pendiente.
        Transacción + auditoría en la misma TX."""
        aid = (aprobacion_id or "").strip()
        if not aid:
            raise ErrorAprobacion("aprobacion_id no puede estar vacío")
        _asegurar_schema_021()
        with _tx(self.company_id) as tx:
            cur = tx.cur
            cur.execute(
                f"SELECT {_COLS} FROM os_aprobaciones"
                " WHERE company_id=%s AND aprobacion_id=%s"
                " FOR UPDATE",
                (self.company_id, aid))
            r = cur.fetchone()
            if r is None:
                raise ErrorAprobacion(
                    f"solicitud inexistente: {aid!r}")
            reg = _fila(r)
            if (reg["estado"] != "pendiente_aprobacion"
                    or reg["solicitante"] != (solicitante or "")):
                raise ErrorAprobacion(
                    f"{aid!r} no es cancelable por "
                    "este solicitante")
            cur.execute(
                """UPDATE os_aprobaciones SET estado='cancelada'
                   WHERE company_id=%s AND aprobacion_id=%s""",
                (self.company_id, aid))
            tx.auditar("aprobacion", solicitante or "", aid,
                       {"accion": "cancelada", "tipo": reg["tipo"]})
            return {"ok": True, "aprobacion_id": aid,
                    "estado": "cancelada"}
