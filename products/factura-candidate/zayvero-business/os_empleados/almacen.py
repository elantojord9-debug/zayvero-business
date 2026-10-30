"""BLOQUE EMPLEADOS (2026-10-13) — Almacén de persistencia del bloque.

- `AlmacenPG`: PostgreSQL (migración 011). Recibe un cursor y un
  `company_id`; si además recibe `auditar`, las escrituras generan
  eventos de auditoría (`os_eventos`, tipos 'tarea' y 'empleado').
  `tx_empleados()` abre la transacción con lock por empresa (mismo
  patrón que `os_persist/tx.py::mutar_os`) e inserta los eventos con
  el cursor de LA MISMA transacción: escritura y evento se confirman
  juntos o se revierten juntos. `lectura_pg()` abre una conexión de
  solo lectura (sin lock) para consultas.
- `AlmacenMem`: respaldo en memoria (misma API; útil para pruebas
  unitarias rápidas y modo sin PG).

Toda lectura filtra por company_id. Los dicts devueltos son
JSON-serializables (Decimal -> str, fechas -> ISO).
"""
from __future__ import annotations

import json
import os
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

from .modelo import (
    Empleado, Tarea, SLAConfig, PromesaSeguimiento, Sucursal,
    ErrorEmpleados, validar_transicion_estado,
)


# --- Serialización -----------------------------------------------------------
def _js(v):
    if isinstance(v, Decimal):
        return str(v)
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


# =====================================================================
# PostgreSQL
# =====================================================================
class _TxPG:
    """Transacción del bloque empleados sobre una conexión PG."""

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
            raise ErrorEmpleados(f"tipo de evento inválido: {tipo!r}")
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
            raise ErrorEmpleados(f"tipo de evento inválido: {tipo!r}")
        # Con la cola de mutar_os activa, auditar() encola el evento;
        # mutar_os lo inserta pre-commit en la misma transacción.
        auditar(self.company_id, tipo, actor, ref_id, detalle or {})


@contextmanager
def tx_empleados(company_id: str):
    """Transacción de escritura con lock por empresa (patrón mutar_os).

    RE-ENTRANCIA (2026-10-13, fix del coordinador): si el hilo ya corre
    dentro de un mutar_os para la misma empresa (p. ej. una ruta POST
    cableada en webapp/os_routes.py), PARTICIPA en esa transacción en vez
    de abrir una conexión anidada — la conexión anidada se interbloquea
    con el lock de empresa (deadlock por transactionid indetectable por
    PG). En ese caso no hace commit/rollback propios: los hace mutar_os.

    Uso:
        with tx_empleados(cid) as tx:
            almacen = AlmacenPG(tx.cur, cid, tx.auditar)
            almacen.crear_tarea(...)
        # commit automático al salir sin excepción; rollback si falla
    """
    if not company_id:
        raise ErrorEmpleados("company_id es obligatorio")
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
def lectura_pg(company_id: str):
    """Conexión de solo lectura (sin lock). Yields (conn, AlmacenPG)."""
    if not company_id:
        raise ErrorEmpleados("company_id es obligatorio")
    from os_persist.pg import connect
    conn = connect()
    try:
        conn.autocommit = True
        yield conn, AlmacenPG(conn.cursor(), company_id)
    finally:
        conn.close()


class AlmacenPG:
    """Operaciones del bloque sobre PostgreSQL.

    `cur`: cursor psycopg. `auditar`: callable(tipo, actor, ref_id,
    detalle) o None (en lectura no se audita).
    """

    def __init__(self, cur, company_id: str, auditar=None):
        if not company_id:
            raise ErrorEmpleados("company_id es obligatorio")
        self.cur = cur
        self.c = company_id
        self._auditar = auditar

    def _audit(self, tipo: str, actor: str, ref_id: str,
               detalle: dict | None = None) -> None:
        if self._auditar is not None:
            self._auditar(tipo, actor, ref_id, detalle or {})

    # ---- empleados ------------------------------------------------------
    def registrar_empleado(self, emp: Empleado) -> dict:
        cur = self.cur
        cur.execute(
            """INSERT INTO os_empleados
                   (empleado_id, company_id, nombre, cargo, sucursal_id,
                    usuario_id, email, activo)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (company_id, empleado_id) DO NOTHING""",
            (emp.empleado_id, self.c, emp.nombre, emp.cargo,
             emp.sucursal_id, emp.usuario_id, emp.email, emp.activo))
        self._audit("empleado", emp.usuario_id, emp.empleado_id,
                    {"accion": "registrar", "nombre": emp.nombre,
                     "cargo": emp.cargo, "sucursal_id": emp.sucursal_id})
        return self.obtener_empleado(emp.empleado_id)

    def obtener_empleado(self, empleado_id: str) -> dict | None:
        self.cur.execute(
            "SELECT empleado_id, company_id, nombre, cargo, sucursal_id,"
            " usuario_id, email, activo, creado_en FROM os_empleados"
            " WHERE company_id=%s AND empleado_id=%s",
            (self.c, empleado_id))
        return _una(self.cur)

    def listar_empleados(self, activos_solo: bool = True) -> list[dict]:
        sql = ("SELECT empleado_id, company_id, nombre, cargo, sucursal_id,"
               " usuario_id, email, activo, creado_en FROM os_empleados"
               " WHERE company_id=%s")
        if activos_solo:
            sql += " AND activo"
        sql += " ORDER BY nombre"
        self.cur.execute(sql, (self.c,))
        return _fila_dict(self.cur)

    def empleado_por_usuario(self, usuario_id: str) -> dict | None:
        self.cur.execute(
            "SELECT empleado_id, company_id, nombre, cargo, sucursal_id,"
            " usuario_id, email, activo, creado_en FROM os_empleados"
            " WHERE company_id=%s AND usuario_id=%s AND activo"
            " ORDER BY creado_en LIMIT 1",
            (self.c, usuario_id))
        return _una(self.cur)

    # ---- tareas ----------------------------------------------------------
    def crear_tarea(self, tarea: Tarea) -> dict:
        if not self.obtener_empleado(tarea.responsable):
            raise ErrorEmpleados(
                f"responsable inexistente: {tarea.responsable}")
        cur = self.cur
        cur.execute(
            """INSERT INTO os_tareas
                   (tarea_id, company_id, titulo, descripcion, responsable,
                    sucursal_id, vencimiento, estado, prioridad, creada_por)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (company_id, tarea_id) DO NOTHING""",
            (tarea.tarea_id, self.c, tarea.titulo, tarea.descripcion,
             tarea.responsable, tarea.sucursal_id, tarea.vencimiento,
             tarea.estado, tarea.prioridad, tarea.creada_por))
        self._audit("tarea", tarea.creada_por, tarea.tarea_id,
                    {"accion": "crear", "titulo": tarea.titulo,
                     "responsable": tarea.responsable,
                     "vencimiento": tarea.vencimiento,
                     "prioridad": tarea.prioridad})
        return self.obtener_tarea(tarea.tarea_id)

    def obtener_tarea(self, tarea_id: str) -> dict | None:
        self.cur.execute(
            "SELECT tarea_id, company_id, titulo, descripcion, responsable,"
            " sucursal_id, vencimiento, estado, prioridad, creada_por,"
            " creada_en, completada_en FROM os_tareas"
            " WHERE company_id=%s AND tarea_id=%s",
            (self.c, tarea_id))
        return _una(self.cur)

    def cambiar_estado(self, tarea_id: str, nuevo: str, actor: str,
                       nota: str = "") -> dict:
        tarea = self.obtener_tarea(tarea_id)
        if not tarea:
            raise ErrorEmpleados(f"tarea inexistente: {tarea_id}")
        anterior = tarea["estado"]
        validar_transicion_estado(anterior, nuevo)
        cur = self.cur
        if nuevo == "completada":
            cur.execute(
                "UPDATE os_tareas SET estado=%s, completada_en=now()"
                " WHERE company_id=%s AND tarea_id=%s",
                (nuevo, self.c, tarea_id))
        else:
            cur.execute(
                "UPDATE os_tareas SET estado=%s, completada_en=NULL"
                " WHERE company_id=%s AND tarea_id=%s",
                (nuevo, self.c, tarea_id))
        cur.execute(
            """INSERT INTO os_tareas_historial
                   (hist_id, company_id, tarea_id, actor,
                    estado_anterior, estado_nuevo, nota)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (uuid.uuid4().hex, self.c, tarea_id, actor or "",
             anterior, nuevo, (nota or "")[:500]))
        self._audit("tarea", actor, tarea_id,
                    {"accion": "cambio_estado",
                     "estado_anterior": anterior,
                     "estado_nuevo": nuevo, "nota": (nota or "")[:200]})
        return self.obtener_tarea(tarea_id)

    def listar_tareas(self, estado: str | None = None,
                      responsable: str | None = None,
                      sucursal_id: str | None = None,
                      venc_hasta: str | None = None,
                      prioridad: str | None = None) -> list[dict]:
        sql = ("SELECT tarea_id, company_id, titulo, descripcion,"
               " responsable, sucursal_id, vencimiento, estado, prioridad,"
               " creada_por, creada_en, completada_en FROM os_tareas"
               " WHERE company_id=%s")
        params: list = [self.c]
        if estado:
            sql += " AND estado=%s"
            params.append(estado)
        if responsable:
            sql += " AND responsable=%s"
            params.append(responsable)
        if sucursal_id:
            sql += " AND sucursal_id=%s"
            params.append(sucursal_id)
        if venc_hasta:
            sql += " AND vencimiento<=%s"
            params.append(venc_hasta)
        if prioridad:
            sql += " AND prioridad=%s"
            params.append(prioridad)
        sql += " ORDER BY vencimiento, prioridad"
        self.cur.execute(sql, params)
        return _fila_dict(self.cur)

    def historial_tarea(self, tarea_id: str) -> list[dict]:
        self.cur.execute(
            "SELECT hist_id, tarea_id, fecha, actor, estado_anterior,"
            " estado_nuevo, nota FROM os_tareas_historial"
            " WHERE company_id=%s AND tarea_id=%s"
            " ORDER BY fecha ASC, hist_id ASC",
            (self.c, tarea_id))
        return _fila_dict(self.cur)

    # ---- SLA --------------------------------------------------------------
    def fijar_sla(self, cfg: SLAConfig, actor: str) -> dict:
        cur = self.cur
        cur.execute(
            """INSERT INTO os_sla_config
                   (company_id, tipo_operacion, objetivo_horas, activo,
                    actualizado_por)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (company_id, tipo_operacion) DO UPDATE SET
                   objetivo_horas=EXCLUDED.objetivo_horas,
                   activo=EXCLUDED.activo,
                   actualizado_por=EXCLUDED.actualizado_por,
                   actualizado_en=now()""",
            (self.c, cfg.tipo_operacion, cfg.objetivo_horas, cfg.activo,
             actor or ""))
        self._audit("empleado", actor, cfg.tipo_operacion,
                    {"accion": "sla_config",
                     "objetivo_horas": cfg.objetivo_horas,
                     "activo": cfg.activo})
        return self.obtener_sla(cfg.tipo_operacion)

    def obtener_sla(self, tipo_operacion: str) -> dict | None:
        self.cur.execute(
            "SELECT tipo_operacion, objetivo_horas, activo,"
            " actualizado_por, actualizado_en FROM os_sla_config"
            " WHERE company_id=%s AND tipo_operacion=%s",
            (self.c, tipo_operacion))
        return _una(self.cur)

    def listar_sla(self) -> list[dict]:
        self.cur.execute(
            "SELECT tipo_operacion, objetivo_horas, activo,"
            " actualizado_por, actualizado_en FROM os_sla_config"
            " WHERE company_id=%s ORDER BY tipo_operacion", (self.c,))
        return _fila_dict(self.cur)

    def iniciar_medicion(self, tipo_operacion: str, ref_id: str,
                         descripcion: str, responsable: str,
                         creada_por: str) -> dict:
        cfg = self.obtener_sla(tipo_operacion)
        if not cfg or not cfg["activo"]:
            raise ErrorEmpleados(
                f"tipo de operación sin SLA activo: {tipo_operacion}")
        medicion_id = uuid.uuid4().hex[:16]
        self.cur.execute(
            """INSERT INTO os_sla_mediciones
                   (medicion_id, company_id, tipo_operacion, ref_id,
                    descripcion, responsable, creada_por)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (medicion_id, self.c, tipo_operacion, ref_id or "",
             (descripcion or "")[:500], responsable or "", creada_por or ""))
        return self.obtener_medicion(medicion_id)

    def obtener_medicion(self, medicion_id: str) -> dict | None:
        self.cur.execute(
            "SELECT medicion_id, company_id, tipo_operacion, ref_id,"
            " descripcion, responsable, inicio, fin, duracion_horas,"
            " cumplio, creada_por FROM os_sla_mediciones"
            " WHERE company_id=%s AND medicion_id=%s",
            (self.c, medicion_id))
        return _una(self.cur)

    def cerrar_medicion(self, medicion_id: str, fin: str | None = None,
                        actor: str = "") -> dict:
        from .sla import evaluar_medicion
        med = self.obtener_medicion(medicion_id)
        if not med:
            raise ErrorEmpleados(f"medición inexistente: {medicion_id}")
        if med["fin"]:
            raise ErrorEmpleados("la medición ya está cerrada")
        cfg = self.obtener_sla(med["tipo_operacion"])
        objetivo = cfg["objetivo_horas"] if cfg else None
        fin_iso = fin or datetime.now(timezone.utc).isoformat()
        duracion, cumplio = evaluar_medicion(
            med["inicio"], fin_iso, objetivo)
        self.cur.execute(
            "UPDATE os_sla_mediciones SET fin=%s, duracion_horas=%s,"
            " cumplio=%s WHERE company_id=%s AND medicion_id=%s",
            (fin_iso, str(duracion), cumplio, self.c, medicion_id))
        self._audit("empleado", actor, medicion_id,
                    {"accion": "sla_cerrar",
                     "tipo_operacion": med["tipo_operacion"],
                     "duracion_horas": str(duracion),
                     "cumplio": cumplio})
        return self.obtener_medicion(medicion_id)

    def listar_mediciones(self, tipo_operacion: str | None = None,
                          abiertas_solo: bool = False) -> list[dict]:
        sql = ("SELECT medicion_id, company_id, tipo_operacion, ref_id,"
               " descripcion, responsable, inicio, fin, duracion_horas,"
               " cumplio, creada_por FROM os_sla_mediciones"
               " WHERE company_id=%s")
        params: list = [self.c]
        if tipo_operacion:
            sql += " AND tipo_operacion=%s"
            params.append(tipo_operacion)
        if abiertas_solo:
            sql += " AND fin IS NULL"
        sql += " ORDER BY inicio DESC"
        self.cur.execute(sql, params)
        return _fila_dict(self.cur)

    # ---- sucursales ---------------------------------------------------------
    def crear_sucursal(self, suc: Sucursal, actor: str = "") -> dict:
        self.cur.execute(
            """INSERT INTO os_sucursales
                   (sucursal_id, company_id, nombre, ubicacion, almacenes,
                    activa)
               VALUES (%s,%s,%s,%s,%s::jsonb,%s)
               ON CONFLICT (company_id, sucursal_id) DO NOTHING""",
            (suc.sucursal_id, self.c, suc.nombre, suc.ubicacion,
             json.dumps(suc.almacenes), suc.activa))
        self._audit("empleado", actor, suc.sucursal_id,
                    {"accion": "sucursal_crear", "nombre": suc.nombre})
        return self.obtener_sucursal(suc.sucursal_id)

    def obtener_sucursal(self, sucursal_id: str) -> dict | None:
        self.cur.execute(
            "SELECT sucursal_id, company_id, nombre, ubicacion, almacenes,"
            " activa, creada_en FROM os_sucursales"
            " WHERE company_id=%s AND sucursal_id=%s",
            (self.c, sucursal_id))
        return _una(self.cur)

    def listar_sucursales(self, activas_solo: bool = False) -> list[dict]:
        sql = ("SELECT sucursal_id, company_id, nombre, ubicacion,"
               " almacenes, activa, creada_en FROM os_sucursales"
               " WHERE company_id=%s")
        if activas_solo:
            sql += " AND activa"
        sql += " ORDER BY nombre"
        self.cur.execute(sql, (self.c,))
        return _fila_dict(self.cur)

    def asignar_visibilidad(self, usuario_id: str, sucursal_id: str) -> None:
        if not self.obtener_sucursal(sucursal_id):
            raise ErrorEmpleados(f"sucursal inexistente: {sucursal_id}")
        self.cur.execute(
            """INSERT INTO os_empleado_sucursal
                   (company_id, usuario_id, sucursal_id)
               VALUES (%s,%s,%s)
               ON CONFLICT (company_id, usuario_id, sucursal_id)
               DO NOTHING""",
            (self.c, usuario_id, sucursal_id))

    def quitar_visibilidad(self, usuario_id: str, sucursal_id: str) -> None:
        self.cur.execute(
            "DELETE FROM os_empleado_sucursal"
            " WHERE company_id=%s AND usuario_id=%s AND sucursal_id=%s",
            (self.c, usuario_id, sucursal_id))

    def sucursales_de_usuario(self, usuario_id: str) -> list[str]:
        self.cur.execute(
            "SELECT sucursal_id FROM os_empleado_sucursal"
            " WHERE company_id=%s AND usuario_id=%s ORDER BY sucursal_id",
            (self.c, usuario_id))
        return [r[0] for r in self.cur.fetchall()]

    # ---- promesas --------------------------------------------------------------
    def registrar_promesa(self, prom: PromesaSeguimiento,
                          actor: str = "") -> dict:
        if not self.obtener_empleado(prom.empleado_id):
            raise ErrorEmpleados(
                f"empleado inexistente: {prom.empleado_id}")
        promesa_id = uuid.uuid4().hex[:16]
        self.cur.execute(
            """INSERT INTO os_promesas_seguimiento
                   (promesa_id, company_id, cuenta_id, empleado_id,
                    fecha_prometida, monto, moneda)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (promesa_id, self.c, prom.cuenta_id, prom.empleado_id,
             prom.fecha_prometida, prom.monto, prom.moneda))
        self._audit("empleado", actor or prom.empleado_id, promesa_id,
                    {"accion": "promesa_registrar",
                     "cuenta_id": prom.cuenta_id,
                     "fecha_prometida": prom.fecha_prometida,
                     "monto": prom.monto, "moneda": prom.moneda})
        return self.obtener_promesa(promesa_id)

    def obtener_promesa(self, promesa_id: str) -> dict | None:
        self.cur.execute(
            "SELECT promesa_id, company_id, cuenta_id, empleado_id,"
            " fecha_prometida, monto, moneda, registrada_en, cumplida,"
            " cumplida_en FROM os_promesas_seguimiento"
            " WHERE company_id=%s AND promesa_id=%s",
            (self.c, promesa_id))
        return _una(self.cur)

    def listar_promesas(self, empleado_id: str | None = None,
                        pendientes_solo: bool = False) -> list[dict]:
        sql = ("SELECT promesa_id, company_id, cuenta_id, empleado_id,"
               " fecha_prometida, monto, moneda, registrada_en, cumplida,"
               " cumplida_en FROM os_promesas_seguimiento"
               " WHERE company_id=%s")
        params: list = [self.c]
        if empleado_id:
            sql += " AND empleado_id=%s"
            params.append(empleado_id)
        if pendientes_solo:
            sql += " AND cumplida IS NULL"
        sql += " ORDER BY fecha_prometida"
        self.cur.execute(sql, params)
        return _fila_dict(self.cur)

    def evaluar_promesa(self, promesa_id: str, cumplida: bool,
                        cumplida_en: str | None = None,
                        actor: str = "") -> dict:
        prom = self.obtener_promesa(promesa_id)
        if not prom:
            raise ErrorEmpleados(f"promesa inexistente: {promesa_id}")
        self.cur.execute(
            "UPDATE os_promesas_seguimiento SET cumplida=%s, cumplida_en=%s"
            " WHERE company_id=%s AND promesa_id=%s",
            (cumplida,
             cumplida_en or (datetime.now(timezone.utc).isoformat()
                             if cumplida else None),
             self.c, promesa_id))
        self._audit("empleado", actor, promesa_id,
                    {"accion": "promesa_evaluar", "cumplida": cumplida})
        return self.obtener_promesa(promesa_id)


# =====================================================================
# Memoria (misma API, sin PG)
# =====================================================================
class AlmacenMem:
    """Respaldo en memoria. Los eventos van a una lista propia con el
    mismo shape que os_eventos (útil para pruebas y modo sin PG)."""

    def __init__(self, company_id: str):
        if not company_id:
            raise ErrorEmpleados("company_id es obligatorio")
        self.company_id = company_id
        self._lock = threading.RLock()
        self.empleados: dict[str, dict] = {}
        self.tareas: dict[str, dict] = {}
        self.historial: list[dict] = []
        self.sla: dict[str, dict] = {}
        self.mediciones: dict[str, dict] = {}
        self.sucursales: dict[str, dict] = {}
        self.visibilidad: dict[str, set[str]] = {}
        self.promesas: dict[str, dict] = {}
        self.eventos: list[dict] = []  # shape os_eventos

    # -- helpers ---------------------------------------------------------
    def _auditar(self, tipo: str, actor: str, ref_id: str,
                 detalle: dict | None = None) -> None:
        ev = _evento(self.company_id, tipo, actor, ref_id, detalle or {})
        ev["fecha"] = datetime.now(timezone.utc).isoformat()
        with self._lock:
            self.eventos.append(ev)

    def _audit(self, tipo: str, actor: str, ref_id: str,
               detalle: dict | None = None) -> None:
        self._auditar(tipo, actor, ref_id, detalle)

    def _ahora(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    # -- empleados ---------------------------------------------------------
    def registrar_empleado(self, emp: Empleado) -> dict:
        with self._lock:
            self.empleados.setdefault(emp.empleado_id, {
                "empleado_id": emp.empleado_id, "company_id": self.company_id,
                "nombre": emp.nombre, "cargo": emp.cargo,
                "sucursal_id": emp.sucursal_id, "usuario_id": emp.usuario_id,
                "email": emp.email, "activo": emp.activo,
                "creado_en": self._ahora()})
            self._audit("empleado", emp.usuario_id, emp.empleado_id,
                        {"accion": "registrar", "nombre": emp.nombre})
            return dict(self.empleados[emp.empleado_id])

    def obtener_empleado(self, empleado_id: str) -> dict | None:
        with self._lock:
            e = self.empleados.get(empleado_id)
            return dict(e) if e else None

    def listar_empleados(self, activos_solo: bool = True) -> list[dict]:
        with self._lock:
            out = [dict(e) for e in self.empleados.values()
                   if not activos_solo or e["activo"]]
            return sorted(out, key=lambda e: e["nombre"])

    def empleado_por_usuario(self, usuario_id: str) -> dict | None:
        with self._lock:
            for e in self.empleados.values():
                if e["usuario_id"] == usuario_id and e["activo"]:
                    return dict(e)
            return None

    # -- tareas --------------------------------------------------------------
    def crear_tarea(self, tarea: Tarea) -> dict:
        with self._lock:
            if tarea.responsable not in self.empleados:
                raise ErrorEmpleados(
                    f"responsable inexistente: {tarea.responsable}")
            self.tareas.setdefault(tarea.tarea_id, {
                "tarea_id": tarea.tarea_id, "company_id": self.company_id,
                "titulo": tarea.titulo, "descripcion": tarea.descripcion,
                "responsable": tarea.responsable,
                "sucursal_id": tarea.sucursal_id,
                "vencimiento": tarea.vencimiento, "estado": tarea.estado,
                "prioridad": tarea.prioridad, "creada_por": tarea.creada_por,
                "creada_en": self._ahora(), "completada_en": None})
            self._audit("tarea", tarea.creada_por, tarea.tarea_id,
                        {"accion": "crear", "titulo": tarea.titulo})
            return dict(self.tareas[tarea.tarea_id])

    def obtener_tarea(self, tarea_id: str) -> dict | None:
        with self._lock:
            t = self.tareas.get(tarea_id)
            return dict(t) if t else None

    def cambiar_estado(self, tarea_id: str, nuevo: str, actor: str,
                       nota: str = "") -> dict:
        with self._lock:
            t = self.tareas.get(tarea_id)
            if not t:
                raise ErrorEmpleados(f"tarea inexistente: {tarea_id}")
            anterior = t["estado"]
            validar_transicion_estado(anterior, nuevo)
            t["estado"] = nuevo
            t["completada_en"] = (self._ahora()
                                  if nuevo == "completada" else None)
            self.historial.append({
                "hist_id": uuid.uuid4().hex, "tarea_id": tarea_id,
                "fecha": self._ahora(), "actor": actor or "",
                "estado_anterior": anterior, "estado_nuevo": nuevo,
                "nota": (nota or "")[:500]})
            self._audit("tarea", actor, tarea_id,
                        {"accion": "cambio_estado",
                         "estado_anterior": anterior,
                         "estado_nuevo": nuevo})
            return dict(t)

    def listar_tareas(self, estado=None, responsable=None, sucursal_id=None,
                      venc_hasta=None, prioridad=None) -> list[dict]:
        with self._lock:
            out = []
            for t in self.tareas.values():
                if estado and t["estado"] != estado:
                    continue
                if responsable and t["responsable"] != responsable:
                    continue
                if sucursal_id and t["sucursal_id"] != sucursal_id:
                    continue
                if venc_hasta and t["vencimiento"] > venc_hasta:
                    continue
                if prioridad and t["prioridad"] != prioridad:
                    continue
                out.append(dict(t))
            return sorted(out, key=lambda t: (t["vencimiento"],
                                             t["prioridad"]))

    def historial_tarea(self, tarea_id: str) -> list[dict]:
        with self._lock:
            return [dict(h) for h in self.historial
                    if h["tarea_id"] == tarea_id]

    # -- SLA -------------------------------------------------------------------
    def fijar_sla(self, cfg: SLAConfig, actor: str) -> dict:
        with self._lock:
            self.sla[cfg.tipo_operacion] = {
                "tipo_operacion": cfg.tipo_operacion,
                "objetivo_horas": str(Decimal(cfg.objetivo_horas)),
                "activo": cfg.activo, "actualizado_por": actor or "",
                "actualizado_en": self._ahora()}
            self._audit("empleado", actor, cfg.tipo_operacion,
                        {"accion": "sla_config"})
            return dict(self.sla[cfg.tipo_operacion])

    def obtener_sla(self, tipo_operacion: str) -> dict | None:
        with self._lock:
            s = self.sla.get(tipo_operacion)
            return dict(s) if s else None

    def listar_sla(self) -> list[dict]:
        with self._lock:
            return [dict(s) for _, s in sorted(self.sla.items())]

    def iniciar_medicion(self, tipo_operacion, ref_id, descripcion,
                         responsable, creada_por) -> dict:
        from .sla import validar_inicio_medicion
        with self._lock:
            cfg = self.sla.get(tipo_operacion)
            validar_inicio_medicion(cfg, tipo_operacion)
            mid = uuid.uuid4().hex[:16]
            self.mediciones[mid] = {
                "medicion_id": mid, "company_id": self.company_id,
                "tipo_operacion": tipo_operacion, "ref_id": ref_id or "",
                "descripcion": (descripcion or "")[:500],
                "responsable": responsable or "",
                "inicio": self._ahora(), "fin": None,
                "duracion_horas": None, "cumplio": None,
                "creada_por": creada_por or ""}
            return dict(self.mediciones[mid])

    def obtener_medicion(self, medicion_id: str) -> dict | None:
        with self._lock:
            m = self.mediciones.get(medicion_id)
            return dict(m) if m else None

    def cerrar_medicion(self, medicion_id: str, fin=None,
                        actor: str = "") -> dict:
        from .sla import evaluar_medicion
        with self._lock:
            m = self.mediciones.get(medicion_id)
            if not m:
                raise ErrorEmpleados(f"medición inexistente: {medicion_id}")
            if m["fin"]:
                raise ErrorEmpleados("la medición ya está cerrada")
            cfg = self.sla.get(m["tipo_operacion"])
            objetivo = cfg["objetivo_horas"] if cfg else None
            fin_iso = fin or self._ahora()
            duracion, cumplio = evaluar_medicion(m["inicio"], fin_iso,
                                                objetivo)
            m["fin"] = fin_iso
            m["duracion_horas"] = str(duracion)
            m["cumplio"] = cumplio
            self._audit("empleado", actor, medicion_id,
                        {"accion": "sla_cerrar",
                         "duracion_horas": str(duracion),
                         "cumplio": cumplio})
            return dict(m)

    def listar_mediciones(self, tipo_operacion=None,
                          abiertas_solo: bool = False) -> list[dict]:
        with self._lock:
            out = []
            for m in self.mediciones.values():
                if tipo_operacion and m["tipo_operacion"] != tipo_operacion:
                    continue
                if abiertas_solo and m["fin"]:
                    continue
                out.append(dict(m))
            return sorted(out, key=lambda m: m["inicio"], reverse=True)

    # -- sucursales --------------------------------------------------------------
    def crear_sucursal(self, suc: Sucursal, actor: str = "") -> dict:
        with self._lock:
            self.sucursales.setdefault(suc.sucursal_id, {
                "sucursal_id": suc.sucursal_id,
                "company_id": self.company_id, "nombre": suc.nombre,
                "ubicacion": suc.ubicacion, "almacenes": list(suc.almacenes),
                "activa": suc.activa, "creada_en": self._ahora()})
            self._audit("empleado", actor, suc.sucursal_id,
                        {"accion": "sucursal_crear"})
            return dict(self.sucursales[suc.sucursal_id])

    def obtener_sucursal(self, sucursal_id: str) -> dict | None:
        with self._lock:
            s = self.sucursales.get(sucursal_id)
            return dict(s) if s else None

    def listar_sucursales(self, activas_solo: bool = False) -> list[dict]:
        with self._lock:
            out = [dict(s) for s in self.sucursales.values()
                   if not activas_solo or s["activa"]]
            return sorted(out, key=lambda s: s["nombre"])

    def asignar_visibilidad(self, usuario_id: str, sucursal_id: str) -> None:
        with self._lock:
            if sucursal_id not in self.sucursales:
                raise ErrorEmpleados(f"sucursal inexistente: {sucursal_id}")
            self.visibilidad.setdefault(usuario_id, set()).add(sucursal_id)

    def quitar_visibilidad(self, usuario_id: str, sucursal_id: str) -> None:
        with self._lock:
            self.visibilidad.get(usuario_id, set()).discard(sucursal_id)

    def sucursales_de_usuario(self, usuario_id: str) -> list[str]:
        with self._lock:
            return sorted(self.visibilidad.get(usuario_id, set()))

    # -- promesas ------------------------------------------------------------------
    def registrar_promesa(self, prom: PromesaSeguimiento,
                          actor: str = "") -> dict:
        with self._lock:
            if prom.empleado_id not in self.empleados:
                raise ErrorEmpleados(
                    f"empleado inexistente: {prom.empleado_id}")
            pid = uuid.uuid4().hex[:16]
            self.promesas[pid] = {
                "promesa_id": pid, "company_id": self.company_id,
                "cuenta_id": prom.cuenta_id, "empleado_id": prom.empleado_id,
                "fecha_prometida": prom.fecha_prometida,
                "monto": str(Decimal(prom.monto)), "moneda": prom.moneda,
                "registrada_en": self._ahora(), "cumplida": None,
                "cumplida_en": None}
            self._audit("empleado", actor or prom.empleado_id, pid,
                        {"accion": "promesa_registrar",
                         "cuenta_id": prom.cuenta_id})
            return dict(self.promesas[pid])

    def obtener_promesa(self, promesa_id: str) -> dict | None:
        with self._lock:
            p = self.promesas.get(promesa_id)
            return dict(p) if p else None

    def listar_promesas(self, empleado_id=None,
                        pendientes_solo: bool = False) -> list[dict]:
        with self._lock:
            out = []
            for p in self.promesas.values():
                if empleado_id and p["empleado_id"] != empleado_id:
                    continue
                if pendientes_solo and p["cumplida"] is not None:
                    continue
                out.append(dict(p))
            return sorted(out, key=lambda p: p["fecha_prometida"])

    def evaluar_promesa(self, promesa_id: str, cumplida: bool,
                        cumplida_en=None, actor: str = "") -> dict:
        with self._lock:
            p = self.promesas.get(promesa_id)
            if not p:
                raise ErrorEmpleados(f"promesa inexistente: {promesa_id}")
            p["cumplida"] = bool(cumplida)
            p["cumplida_en"] = (cumplida_en or self._ahora()
                                if cumplida else None)
            self._audit("empleado", actor, promesa_id,
                        {"accion": "promesa_evaluar",
                         "cumplida": bool(cumplida)})
            return dict(p)


# --- Fábrica ----------------------------------------------------------------------
_mem_stores: dict[str, AlmacenMem] = {}
_mem_lock = threading.RLock()


def almacen_memoria(company_id: str) -> AlmacenMem:
    """Almacén en memoria de la empresa (cacheado por company_id)."""
    with _mem_lock:
        if company_id not in _mem_stores:
            _mem_stores[company_id] = AlmacenMem(company_id)
        return _mem_stores[company_id]
