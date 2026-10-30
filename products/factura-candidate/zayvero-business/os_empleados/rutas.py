"""BLOQUE EMPLEADOS (2026-10-13) — Registro de rutas `empleados/`.

CONTRATO (para el cableado en webapp, que lo hará otra fase):
- `rutas()` devuelve {subruta: spec} con prefijo `empleados/`.
- spec = {"metodo": "GET"|"POST", "permiso": "os.read"|"os.write",
          "descripcion": str, "handler": fn}.
- `fn(bundle, ctx, payload) -> dict` JSON-serializable. `payload` es
  la query parseada (dict de listas, como en os_routes) en GET y el
  body JSON (dict) en POST.
- Errores: `ErrorEmpleados`/`ValueError` -> 400; `PermissionError` ->
  403. El cableado los traduce; los handlers NO envían HTTP.

REGLA DE PERMISOS (respeta tenant/roles.py; precedente: reportes):
- Escrituras y datos sensibles de personal (analytics por empleado,
  detalle de empleado, consolidado/comparativa de sucursales):
  `os.write` (owner/admin).
- Operativa no sensible (tareas, SLA, promesas listado, sucursales
  visibles, indicadores de sucursales propias): `os.read`.
- `analytics/empleado`: con `os.read` un usuario solo ve SUS propias
  métricas (empleado vinculado a su ctx.user_id); con `os.write` ve
  cualquiera.
- `sucursales/indicadores`: además del permiso, se exige visibilidad
  sobre la sucursal (owner/admin ven todo).

company_id SIEMPRE del ctx (anti-IDOR). Mutaciones: transacción con
lock por empresa + auditoría en os_eventos en la misma transacción
(vía os_empleados/almacen.py). GET: solo lectura, sin lock.
"""
from __future__ import annotations

import os as _os
from contextlib import contextmanager

from .modelo import (
    ErrorEmpleados, PermissionDeniedEmpleados, validar_empleado,
    validar_tarea, validar_sla_config, validar_promesa, validar_sucursal,
    hoy_ymd,
)
from .almacen import (
    AlmacenPG, tx_empleados, lectura_pg, almacen_memoria,
)
from . import analytics as mod_analytics
from . import tareas as mod_tareas
from . import sla as mod_sla
from . import sucursales as mod_sucursales


# --- Backend -------------------------------------------------------------------
def _es_pg() -> bool:
    return _os.environ.get("ZAYVERO_OS_BACKEND") == "postgres"


@contextmanager
def _lectura(company_id: str):
    """Yields (conn|None, almacen). conn solo en PG (para eventos)."""
    if _es_pg():
        with lectura_pg(company_id) as (conn, alm):
            yield conn, alm
    else:
        yield None, almacen_memoria(company_id)


@contextmanager
def _escritura(company_id: str):
    """Yields almacen con auditoría transaccional (PG) o en memoria."""
    if _es_pg():
        with tx_empleados(company_id) as tx:
            yield AlmacenPG(tx.cur, company_id, tx.auditar)
    else:
        yield almacen_memoria(company_id)


def _lector(conn, almacen, company_id: str):
    if _es_pg():
        return mod_analytics.LectorEventosPG(conn, company_id)
    return mod_analytics.LectorEventosMem(almacen)


# --- Query -----------------------------------------------------------------------
def _q1(query: dict, name: str, default: str = "") -> str:
    v = query.get(name) if isinstance(query, dict) else None
    if not v:
        return default
    return v[0] if isinstance(v, (list, tuple)) else v


def _qbool(query: dict, name: str, default: bool = False) -> bool:
    v = _q1(query, name, "")
    if not v:
        return default
    return v.strip().lower() in ("1", "true", "si", "sí", "yes")


def _permiso(ctx, permiso: str, recurso: str) -> None:
    if not ctx.has(permiso):
        raise PermissionDeniedEmpleados(
            f"permiso insuficiente: {permiso} (recurso {recurso})")


# Alias público del 403 del bloque (el cableado lo traduce a HTTP 403).
PermissionDenied_ = PermissionDeniedEmpleados


def _cid(ctx) -> str:
    c = getattr(ctx, "company_id", "")
    if not c:
        raise ErrorEmpleados("company_id ausente en el contexto")
    return c


# =====================================================================
# Handlers: empleados
# =====================================================================
def h_empleados_registrar(bundle, ctx, body: dict) -> dict:
    _permiso(ctx, "os.write", "personal/registrar")
    emp = validar_empleado(body or {}, _cid(ctx))
    with _escritura(_cid(ctx)) as alm:
        return alm.registrar_empleado(emp)


def h_empleados_listar(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.read", "personal/listar")
    with _lectura(_cid(ctx)) as (_, alm):
        emps = alm.listar_empleados()
    # os.read: directorio sin datos de contacto/vínculo (sensibles).
    return {"empleados": [
        {"empleado_id": e["empleado_id"], "nombre": e["nombre"],
         "cargo": e.get("cargo", ""), "sucursal_id": e.get("sucursal_id", ""),
         "activo": e.get("activo", True)} for e in emps]}


def h_empleados_detalle(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.write", "personal/detalle")
    eid = _q1(query, "empleado_id")
    if not eid:
        raise ErrorEmpleados("empleado_id es obligatorio")
    with _lectura(_cid(ctx)) as (_, alm):
        emp = alm.obtener_empleado(eid)
    if not emp:
        raise ErrorEmpleados(f"empleado inexistente: {eid}")
    return {"empleado": emp}


# =====================================================================
# Handlers: analytics
# =====================================================================
def _params_periodo(query: dict):
    desde = _q1(query, "desde") or None
    hasta = _q1(query, "hasta") or None
    hoy = _q1(query, "hoy") or None
    if desde:
        hoy_ymd(desde)
    if hasta:
        hoy_ymd(hasta)
    try:
        mm = int(_q1(query, "muestra_minima",
                     str(mod_analytics.MUESTRA_MINIMA_DEFAULT)))
    except ValueError:
        raise ErrorEmpleados("muestra_minima debe ser entero")
    if mm < 1:
        raise ErrorEmpleados("muestra_minima debe ser >= 1")
    return desde, hasta, hoy, mm


def h_analytics_resumen(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.write", "analytics/resumen")
    desde, hasta, hoy, mm = _params_periodo(query)
    with _lectura(_cid(ctx)) as (conn, alm):
        return mod_analytics.resumen_equipo(
            alm, _lector(conn, alm, _cid(ctx)), desde, hasta, hoy, mm)


def h_analytics_empleado(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.read", "analytics/empleado")
    eid = _q1(query, "empleado_id")
    if not eid:
        raise ErrorEmpleados("empleado_id es obligatorio")
    with _lectura(_cid(ctx)) as (conn, alm):
        if not ctx.has("os.write"):
            propio = alm.empleado_por_usuario(ctx.user_id)
            if not propio or propio["empleado_id"] != eid:
                raise PermissionDenied_(
                    "con os.read solo puede ver sus propias métricas")
        return mod_analytics.indicadores_empleado(
            alm, _lector(conn, alm, _cid(ctx)), eid,
            *_params_periodo(query))


# =====================================================================
# Handlers: tareas
# =====================================================================
def h_tareas_crear(bundle, ctx, body: dict) -> dict:
    _permiso(ctx, "os.write", "tareas/crear")
    body = dict(body or {})
    body.setdefault("creada_por", ctx.user_id)
    tarea = validar_tarea(body, _cid(ctx))
    with _escritura(_cid(ctx)) as alm:
        return {"tarea": alm.crear_tarea(tarea)}


def h_tareas_estado(bundle, ctx, body: dict) -> dict:
    _permiso(ctx, "os.write", "tareas/estado")
    body = body or {}
    tarea_id = str(body.get("tarea_id") or "").strip()
    nuevo = str(body.get("estado") or "").strip()
    if not tarea_id or not nuevo:
        raise ErrorEmpleados("tarea_id y estado son obligatorios")
    with _escritura(_cid(ctx)) as alm:
        return {"tarea": alm.cambiar_estado(
            tarea_id, nuevo, ctx.user_id, str(body.get("nota") or ""))}


def h_tareas_listar(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.read", "tareas/listar")
    with _lectura(_cid(ctx)) as (_, alm):
        items = alm.listar_tareas(
            estado=_q1(query, "estado") or None,
            responsable=_q1(query, "responsable") or None,
            sucursal_id=_q1(query, "sucursal_id") or None,
            venc_hasta=_q1(query, "vencimiento_hasta") or None,
            prioridad=_q1(query, "prioridad") or None)
    return {"tareas": items, "total": len(items)}


def h_tareas_alertas(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.read", "tareas/alertas")
    with _lectura(_cid(ctx)) as (_, alm):
        items = alm.listar_tareas()
    return mod_tareas.alertas(items, _q1(query, "hoy") or None)


def h_tareas_historial(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.read", "tareas/historial")
    tid = _q1(query, "tarea_id")
    if not tid:
        raise ErrorEmpleados("tarea_id es obligatorio")
    with _lectura(_cid(ctx)) as (_, alm):
        return {"tarea_id": tid, "historial": alm.historial_tarea(tid)}


# =====================================================================
# Handlers: promesas
# =====================================================================
def h_promesas_registrar(bundle, ctx, body: dict) -> dict:
    _permiso(ctx, "os.write", "promesas/registrar")
    prom = validar_promesa(body or {}, _cid(ctx))
    with _escritura(_cid(ctx)) as alm:
        return {"promesa": alm.registrar_promesa(prom, ctx.user_id)}


def h_promesas_evaluar(bundle, ctx, body: dict) -> dict:
    _permiso(ctx, "os.write", "promesas/evaluar")
    body = body or {}
    pid = str(body.get("promesa_id") or "").strip()
    if not pid:
        raise ErrorEmpleados("promesa_id es obligatorio")
    if "cumplida" not in body:
        raise ErrorEmpleados("cumplida (true/false) es obligatorio")
    with _escritura(_cid(ctx)) as alm:
        return {"promesa": alm.evaluar_promesa(
            pid, bool(body.get("cumplida")),
            str(body.get("cumplida_en") or "") or None, ctx.user_id)}


def h_promesas_listar(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.read", "promesas/listar")
    with _lectura(_cid(ctx)) as (_, alm):
        items = alm.listar_promesas(
            empleado_id=_q1(query, "empleado_id") or None,
            pendientes_solo=_qbool(query, "pendientes"))
    return {"promesas": items, "total": len(items)}


# =====================================================================
# Handlers: SLA
# =====================================================================
def h_sla_config(bundle, ctx, body: dict) -> dict:
    _permiso(ctx, "os.write", "sla/config")
    cfg = validar_sla_config(body or {}, _cid(ctx))
    with _escritura(_cid(ctx)) as alm:
        return {"sla": alm.fijar_sla(cfg, ctx.user_id)}


def h_sla_config_listar(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.read", "sla/config/listar")
    with _lectura(_cid(ctx)) as (_, alm):
        return {"config": alm.listar_sla()}


def h_sla_iniciar(bundle, ctx, body: dict) -> dict:
    _permiso(ctx, "os.write", "sla/mediciones/iniciar")
    body = body or {}
    tipo = str(body.get("tipo_operacion") or "").strip()
    if not tipo:
        raise ErrorEmpleados("tipo_operacion es obligatorio")
    with _escritura(_cid(ctx)) as alm:
        return {"medicion": alm.iniciar_medicion(
            tipo, str(body.get("ref_id") or ""),
            str(body.get("descripcion") or ""),
            str(body.get("responsable") or ""), ctx.user_id)}


def h_sla_cerrar(bundle, ctx, body: dict) -> dict:
    _permiso(ctx, "os.write", "sla/mediciones/cerrar")
    body = body or {}
    mid = str(body.get("medicion_id") or "").strip()
    if not mid:
        raise ErrorEmpleados("medicion_id es obligatorio")
    with _escritura(_cid(ctx)) as alm:
        return {"medicion": alm.cerrar_medicion(
            mid, str(body.get("fin") or "") or None, ctx.user_id)}


def h_sla_mediciones_listar(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.read", "sla/mediciones/listar")
    with _lectura(_cid(ctx)) as (_, alm):
        items = alm.listar_mediciones(
            tipo_operacion=_q1(query, "tipo_operacion") or None,
            abiertas_solo=_qbool(query, "abiertas"))
    return {"mediciones": items, "total": len(items)}


def h_sla_resumen(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.read", "sla/resumen")
    hoy = _q1(query, "hoy") or None
    tipo = _q1(query, "tipo_operacion") or None
    with _lectura(_cid(ctx)) as (_, alm):
        configs = {c["tipo_operacion"]: c for c in alm.listar_sla()}
        meds = alm.listar_mediciones(tipo_operacion=tipo)
    return mod_sla.resumen_por_tipo(meds, configs, hoy)


def h_sla_atrasadas(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.read", "sla/atrasadas")
    hoy = _q1(query, "hoy") or None
    with _lectura(_cid(ctx)) as (_, alm):
        configs = {c["tipo_operacion"]: c for c in alm.listar_sla()}
        meds = alm.listar_mediciones(abiertas_solo=True)
    return mod_sla.atrasadas(meds, configs, hoy)


# =====================================================================
# Handlers: sucursales
# =====================================================================
def h_sucursales_crear(bundle, ctx, body: dict) -> dict:
    _permiso(ctx, "os.write", "sucursales/crear")
    suc = validar_sucursal(body or {}, _cid(ctx))
    with _escritura(_cid(ctx)) as alm:
        return {"sucursal": alm.crear_sucursal(suc, ctx.user_id)}


def h_sucursales_listar(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.read", "sucursales/listar")
    with _lectura(_cid(ctx)) as (_, alm):
        return {"sucursales": alm.listar_sucursales()}


def h_sucursales_visibilidad(bundle, ctx, body: dict) -> dict:
    _permiso(ctx, "os.write", "sucursales/visibilidad")
    body = body or {}
    usuario_id = str(body.get("usuario_id") or "").strip()
    sucursal_id = str(body.get("sucursal_id") or "").strip()
    accion = str(body.get("accion") or "asignar").strip().lower()
    if not usuario_id or not sucursal_id:
        raise ErrorEmpleados("usuario_id y sucursal_id son obligatorios")
    if accion not in ("asignar", "quitar"):
        raise ErrorEmpleados("accion debe ser asignar o quitar")
    with _escritura(_cid(ctx)) as alm:
        if accion == "asignar":
            alm.asignar_visibilidad(usuario_id, sucursal_id)
        else:
            alm.quitar_visibilidad(usuario_id, sucursal_id)
        visibles = alm.sucursales_de_usuario(usuario_id)
    return {"usuario_id": usuario_id, "accion": accion,
            "sucursales_visibles": visibles}


def h_sucursales_visibles(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.read", "sucursales/visibles")
    with _lectura(_cid(ctx)) as (_, alm):
        return {"sucursales": mod_sucursales.sucursales_visibles(alm, ctx)}


def h_sucursales_indicadores(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.read", "sucursales/indicadores")
    sid = _q1(query, "sucursal_id")
    if not sid:
        raise ErrorEmpleados("sucursal_id es obligatorio")
    desde = _q1(query, "desde") or None
    hasta = _q1(query, "hasta") or None
    hoy = _q1(query, "hoy") or None
    with _lectura(_cid(ctx)) as (_, alm):
        mod_sucursales.exigir_visible(alm, ctx, sid)  # 403 si no la ve
        return mod_sucursales.indicadores_sucursal(
            bundle, alm, sid, desde, hasta, hoy)


def h_sucursales_comparar(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.write", "sucursales/comparar")
    sids = [s.strip() for s in _q1(query, "sucursal_ids").split(",")
            if s.strip()]
    if len(sids) < 2:
        raise ErrorEmpleados(
            "sucursal_ids requiere al menos 2 (separadas por coma)")
    with _lectura(_cid(ctx)) as (_, alm):
        return mod_sucursales.comparar_sucursales(
            bundle, alm, sids, _q1(query, "desde") or None,
            _q1(query, "hasta") or None, _q1(query, "hoy") or None)


def h_sucursales_consolidado(bundle, ctx, query: dict) -> dict:
    _permiso(ctx, "os.write", "sucursales/consolidado")
    with _lectura(_cid(ctx)) as (_, alm):
        return mod_sucursales.consolidado(
            bundle, alm, _q1(query, "desde") or None,
            _q1(query, "hasta") or None, _q1(query, "hoy") or None)


# =====================================================================
# Registro
# =====================================================================
def rutas() -> dict:
    """Registro de rutas del bloque. Prefijo: `empleados/`.

    El cableado (webapp) antepone el prefijo y traduce errores:
    ErrorEmpleados/ValueError -> 400, PermissionDenied_ -> 403.
    """
    def _r(metodo, permiso, descripcion, handler):
        return {"metodo": metodo, "permiso": permiso,
                "descripcion": descripcion, "handler": handler}

    return {
        # --- personal --------------------------------------------------
        "personal/registrar": _r(
            "POST", "os.write", "Registra un empleado.", h_empleados_registrar),
        "personal/listar": _r(
            "GET", "os.read",
            "Directorio de empleados (sin datos sensibles).",
            h_empleados_listar),
        "personal/detalle": _r(
            "GET", "os.write", "Ficha completa de un empleado.",
            h_empleados_detalle),
        # --- analytics ---------------------------------------------------
        "analytics/resumen": _r(
            "GET", "os.write",
            "Indicadores operativos de todo el equipo.",
            h_analytics_resumen),
        "analytics/empleado": _r(
            "GET", "os.read",
            "Indicadores de un empleado (propios con os.read).",
            h_analytics_empleado),
        # --- tareas ------------------------------------------------------
        "tareas/crear": _r(
            "POST", "os.write", "Crea una tarea.", h_tareas_crear),
        "tareas/estado": _r(
            "POST", "os.write", "Cambia el estado (con historial).",
            h_tareas_estado),
        "tareas/listar": _r(
            "GET", "os.read", "Lista tareas con filtros.",
            h_tareas_listar),
        "tareas/alertas": _r(
            "GET", "os.read", "Tareas vencidas y por vencer (48h).",
            h_tareas_alertas),
        "tareas/historial": _r(
            "GET", "os.read", "Historial de cambios de una tarea.",
            h_tareas_historial),
        # --- promesas ------------------------------------------------------
        "promesas/registrar": _r(
            "POST", "os.write",
            "Registra una promesa de pago con responsable.",
            h_promesas_registrar),
        "promesas/evaluar": _r(
            "POST", "os.write", "Marca una promesa cumplida/no cumplida.",
            h_promesas_evaluar),
        "promesas/listar": _r(
            "GET", "os.read", "Lista promesas con filtros.",
            h_promesas_listar),
        # --- SLA -------------------------------------------------------------
        "sla/config": _r(
            "POST", "os.write",
            "Fija el objetivo (horas) de un tipo de operación.",
            h_sla_config),
        "sla/config/listar": _r(
            "GET", "os.read", "Objetivos SLA configurados.",
            h_sla_config_listar),
        "sla/mediciones/iniciar": _r(
            "POST", "os.write", "Inicia una medición de SLA.",
            h_sla_iniciar),
        "sla/mediciones/cerrar": _r(
            "POST", "os.write",
            "Cierra una medición (calcula duración y cumplimiento).",
            h_sla_cerrar),
        "sla/mediciones/listar": _r(
            "GET", "os.read", "Lista mediciones con filtros.",
            h_sla_mediciones_listar),
        "sla/resumen": _r(
            "GET", "os.read",
            "Cumplimiento por tipo de operación.", h_sla_resumen),
        "sla/atrasadas": _r(
            "GET", "os.read",
            "Operaciones abiertas que superaron el objetivo.",
            h_sla_atrasadas),
        # --- sucursales ------------------------------------------------------
        "sucursales/crear": _r(
            "POST", "os.write", "Crea una sucursal.", h_sucursales_crear),
        "sucursales/listar": _r(
            "GET", "os.read", "Lista sucursales.", h_sucursales_listar),
        "sucursales/visibilidad": _r(
            "POST", "os.write",
            "Asigna/quita visibilidad de sucursales a un usuario.",
            h_sucursales_visibilidad),
        "sucursales/visibles": _r(
            "GET", "os.read",
            "Sucursales visibles para el usuario actual.",
            h_sucursales_visibles),
        "sucursales/indicadores": _r(
            "GET", "os.read",
            "Indicadores operativos de una sucursal (requiere "
            "visibilidad).", h_sucursales_indicadores),
        "sucursales/comparar": _r(
            "GET", "os.write",
            "Comparativa lado a lado entre sucursales.",
            h_sucursales_comparar),
        "sucursales/consolidado": _r(
            "GET", "os.write",
            "Vista consolidada del propietario (todas + caja empresa).",
            h_sucursales_consolidado),
    }
