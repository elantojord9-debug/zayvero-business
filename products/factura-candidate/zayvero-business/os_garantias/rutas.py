"""BLOQUE E — Garantías (2026-10-13) — Registro de rutas.

CONTRATO (patrón os_contab/rutas.py, para el cableado del coordinador):
- `rutas()` devuelve {subruta: spec} con claves RELATIVAS (el cableado
  antepone el prefijo `garantias/`).
- spec = {"metodo": "GET"|"POST", "permiso": "os.read"|"os.write",
          "descripcion": str, "handler": fn}.
- `fn(bundle, ctx, payload) -> dict` JSON-serializable. `payload` es la
  query parseada (dict) en GET y el body JSON (dict) en POST.
- `ctx` es TenantContext (objeto) o dict (tests): se lee con
  getattr + fallback a dict (patrón os_contab).
- Errores: ErrorGarantia/ValueError -> 400; NoEncontrado -> 404;
  PermisoDenegado -> 403. El cableado los traduce; los handlers NO
  envían HTTP.
- company_id SIEMPRE del ctx (anti-IDOR).
- Mutaciones: auditoría en os_eventos vía os_persist.auditoria.auditar
  (tipos 'garantia' y 'reclamo_garantia'; ver migración 017).

Estado del bloque: registro en memoria ligado al bundle
(`_os_garantias_bloque`), igual que os_crm/http.py. LIMITACIÓN: hasta
el wiring del panel no hay persistencia en snapshots/PG (documentado en
docs/bloque-garantias.md).
"""
from __future__ import annotations

import sys
import os
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .garantias import (  # noqa: E402
    ErrorGarantia, NoEncontrado, PermisoDenegado,
    TerminoGarantia, RegistroGarantias)
from os_inventory.lotes import RegistroLotes  # noqa: E402


# ---- estado del bloque por bundle ---------------------------------------
_BLOQUE_ATTR = "_os_garantias_bloque"
_bloque_lock = threading.RLock()


def _bloque(bundle) -> RegistroGarantias:
    with _bloque_lock:
        reg = getattr(bundle, _BLOQUE_ATTR, None)
        if reg is None:
            reg = RegistroGarantias(
                bundle.company_id,
                ventas=getattr(bundle, "ventas", None),
                gastos=getattr(bundle, "gastos", None),
                lotes=RegistroLotes.por_empresa(bundle.company_id))
            if getattr(bundle, "libro", None) is not None:
                reg.conectar_libro(bundle.libro)
            setattr(bundle, _BLOQUE_ATTR, reg)
        return reg


# ---- utilidades -----------------------------------------------------------
def _q1(payload, name, default=""):
    v = (payload or {}).get(name, default)
    if isinstance(v, (list, tuple)):
        return v[0] if v else default
    return v if v is not None else default


def _ctx_val(ctx, nombre: str) -> str:
    """Lee un valor del ctx sea objeto TenantContext o dict (tests)."""
    if ctx is None:
        return ""
    v = getattr(ctx, nombre, None)
    if v:
        return str(v)
    if isinstance(ctx, dict):
        return str(ctx.get(nombre, "") or "")
    return ""


def _ctx_company(bundle, ctx) -> str:
    cid = _ctx_val(ctx, "company_id")
    if not cid or cid != bundle.company_id:
        raise ErrorGarantia(
            "company_id del contexto no coincide con la empresa")
    return cid


def _actor(ctx) -> str:
    return str(_ctx_val(ctx, "user_id") or _ctx_val(ctx, "actor") or "")


def _permiso(ctx, permiso: str, recurso: str) -> None:
    tiene = False
    if hasattr(ctx, "has"):
        tiene = bool(ctx.has(permiso))
    elif isinstance(ctx, dict):
        tiene = permiso in (ctx.get("permisos") or [])
    else:
        # Objeto sin método has (p. ej. TenantContext simple): atributo
        # permisos como lista (getattr + fallback, sin romper dicts).
        tiene = permiso in (getattr(ctx, "permisos", None) or [])
    if not tiene:
        raise PermisoDenegado(
            f"permiso insuficiente: {permiso} (recurso {recurso})")


def _auditar(bundle, ctx, tipo: str, ref_id: str, detalle: dict):
    from os_persist.auditoria import auditar
    auditar(_ctx_company(bundle, ctx), tipo, _actor(ctx), ref_id, detalle)


# =====================================================================
# Handlers: términos
# =====================================================================
def h_terminos(bundle, ctx, payload):
    query = payload
    _permiso(ctx, "os.read", "terminos")
    _ctx_company(bundle, ctx)
    return {"ok": True, "data": _bloque(bundle).terminos(
        producto_id=_q1(query, "producto_id") or None)}


def h_terminos_registrar(bundle, ctx, payload):
    body = payload or {}
    _permiso(ctx, "os.write", "terminos/registrar")
    cid = _ctx_company(bundle, ctx)
    try:
        t = _bloque(bundle).registrar_termino(TerminoGarantia(
            termino_id=str(body.get("termino_id") or "").strip(),
            company_id=cid,
            producto_id=str(body.get("producto_id") or "").strip(),
            duracion_meses=int(body.get("duracion_meses") or 0),
            cobertura=str(body.get("cobertura") or "fabricacion"),
            condiciones=str(body.get("condiciones") or ""),
            inicio=str(body.get("inicio") or "fecha_venta"),
            lote_id=str(body.get("lote_id") or "").strip(),
            costo_maximo_por_reclamo=str(
                body.get("costo_maximo_por_reclamo") or ""),
            proveedor_respalda=bool(body.get("proveedor_respalda"))))
    except (ErrorGarantia, ValueError) as e:
        raise ErrorGarantia(str(e))
    _auditar(bundle, ctx, "garantia", t.termino_id,
             {"accion": "registrar_termino",
              "producto_id": t.producto_id,
              "duracion_meses": t.duracion_meses,
              "cobertura": t.cobertura})
    return {"ok": True, "data": t.to_dict()}


# =====================================================================
# Handlers: tarjetas (activación)
# =====================================================================
def h_activar(bundle, ctx, payload):
    body = payload or {}
    _permiso(ctx, "os.write", "activar-desde-venta")
    _ctx_company(bundle, ctx)
    vid = str(body.get("venta_id") or "").strip()
    if not vid:
        raise ErrorGarantia("venta_id es obligatorio")
    try:
        creadas = _bloque(bundle).activar_desde_venta(
            vid, fecha_entrega=str(body.get("fecha_entrega") or ""))
    except NoEncontrado:
        raise
    except ErrorGarantia as e:
        raise ErrorGarantia(str(e))
    _auditar(bundle, ctx, "garantia", vid,
             {"accion": "activar_desde_venta",
              "tarjetas_creadas": len(creadas)})
    return {"ok": True, "data": {"tarjetas": creadas}}


def h_tarjetas(bundle, ctx, payload):
    query = payload
    _permiso(ctx, "os.read", "tarjetas")
    _ctx_company(bundle, ctx)
    return {"ok": True, "data": _bloque(bundle).tarjetas(
        estado=_q1(query, "estado") or None,
        producto_id=_q1(query, "producto_id") or None,
        cliente_id=_q1(query, "cliente_id") or None)}


# =====================================================================
# Handlers: reclamos
# =====================================================================
def h_reclamos(bundle, ctx, payload):
    query = payload
    _permiso(ctx, "os.read", "reclamos")
    _ctx_company(bundle, ctx)
    return {"ok": True, "data": _bloque(bundle).reclamos(
        estado=_q1(query, "estado") or None,
        producto_id=_q1(query, "producto_id") or None,
        cliente_id=_q1(query, "cliente_id") or None)}


def h_reclamo_registrar(bundle, ctx, payload):
    body = payload or {}
    _permiso(ctx, "os.write", "reclamos/registrar")
    _ctx_company(bundle, ctx)
    try:
        data = _bloque(bundle).registrar_reclamo(
            reclamo_id=str(body.get("reclamo_id") or "").strip(),
            tarjeta_id=str(body.get("tarjeta_id") or "").strip(),
            sintomas=str(body.get("sintomas") or ""),
            moneda=str(body.get("moneda") or "DOP"),
            serie_o_lote=str(body.get("serie_o_lote") or ""),
            fecha_reporte=str(body.get("fecha_reporte") or ""))
    except NoEncontrado:
        raise
    except (ErrorGarantia, ValueError) as e:
        raise ErrorGarantia(str(e))
    _auditar(bundle, ctx, "reclamo_garantia", data["reclamo_id"],
             {"accion": "intake", "tarjeta_id": data["tarjeta_id"],
              "producto_id": data["producto_id"]})
    return {"ok": True, "data": data}


def h_reclamo_diagnosticar(bundle, ctx, payload):
    body = payload or {}
    _permiso(ctx, "os.write", "reclamos/diagnosticar")
    _ctx_company(bundle, ctx)
    rid = str(body.get("reclamo_id") or "").strip()
    if not rid:
        raise ErrorGarantia("reclamo_id es obligatorio")
    try:
        data = _bloque(bundle).diagnosticar(
            rid, diagnostico=str(body.get("diagnostico") or ""),
            causa=str(body.get("causa") or ""),
            cubierto=bool(body.get("cubierto")))
    except NoEncontrado:
        raise
    except ErrorGarantia as e:
        raise ErrorGarantia(str(e))
    _auditar(bundle, ctx, "reclamo_garantia", rid,
             {"accion": "diagnosticar", "causa": data["causa"],
              "cubierto": data["cubierto"]})
    return {"ok": True, "data": data}


def h_reclamo_resolver(bundle, ctx, payload):
    body = payload or {}
    _permiso(ctx, "os.write", "reclamos/resolver")
    _ctx_company(bundle, ctx)
    rid = str(body.get("reclamo_id") or "").strip()
    if not rid:
        raise ErrorGarantia("reclamo_id es obligatorio")
    try:
        data = _bloque(bundle).resolver(
            rid, resolucion=str(body.get("resolucion") or ""),
            costo_mano_obra=str(body.get("costo_mano_obra") or "0"),
            costo_repuestos=str(body.get("costo_repuestos") or "0"),
            costo_envio=str(body.get("costo_envio") or "0"),
            fecha_cierre=str(body.get("fecha_cierre") or ""))
    except NoEncontrado:
        raise
    except ErrorGarantia as e:
        raise ErrorGarantia(str(e))
    _auditar(bundle, ctx, "reclamo_garantia", rid,
             {"accion": "resolver", "resolucion": data["resolucion"],
              "costo_total": data["costo_total"],
              "moneda": data["moneda"],
              "gasto_emitido": data["gasto_emitido"]})
    return {"ok": True, "data": data}


# =====================================================================
# Handlers: costos, margen, alertas, historial (solo lectura)
# =====================================================================
def h_costos(bundle, ctx, payload):
    query = payload
    _permiso(ctx, "os.read", "costos")
    _ctx_company(bundle, ctx)
    return {"ok": True, "data": {
        "costo_por_moneda": _bloque(bundle).costos_por_periodo(
            desde=_q1(query, "desde") or None,
            hasta=_q1(query, "hasta") or None,
            producto_id=_q1(query, "producto_id") or None),
        "nota": ("costo real de reclamos cerrados; con el registro de "
                 "gastos conectado estos montos ya están en "
                 "gastos_operativos (categoría 'garantia')")}}


def h_impacto_margen(bundle, ctx, payload):
    query = payload
    _permiso(ctx, "os.read", "impacto-margen")
    _ctx_company(bundle, ctx)
    pid = _q1(query, "producto_id")
    if not pid:
        raise ErrorGarantia("producto_id es obligatorio")
    return {"ok": True, "data": _bloque(bundle).impacto_margen(
        pid, desde=_q1(query, "desde") or None,
        hasta=_q1(query, "hasta") or None)}


def h_alertas_vencimiento(bundle, ctx, payload):
    query = payload
    _permiso(ctx, "os.read", "alertas/vencimiento")
    _ctx_company(bundle, ctx)
    try:
        dias = int(_q1(query, "dias_alerta", "30"))
    except (TypeError, ValueError):
        raise ErrorGarantia("dias_alerta debe ser entero")
    return {"ok": True, "data": {
        "alertas": _bloque(bundle).alertas_vencimiento(
            dias_alerta=dias, hoy=_q1(query, "hoy") or None),
        "contrato_notif": ("cada alerta es una fuente para os_notif: "
                            "{fuente, tipo, severidad, mensaje, "
                            "tarjeta_id, cliente_id, producto_id, "
                            "venta_id, fecha_vencimiento, "
                            "dias_para_vencer}")}}


def h_historial(bundle, ctx, payload):
    query = payload
    _permiso(ctx, "os.read", "historial")
    _ctx_company(bundle, ctx)
    cid = _q1(query, "cliente_id")
    pid = _q1(query, "producto_id")
    if cid:
        return {"ok": True, "data": _bloque(bundle).historial_cliente(
            cid)}
    if pid:
        return {"ok": True, "data": _bloque(bundle).historial_producto(
            pid)}
    raise ErrorGarantia("cliente_id o producto_id es obligatorio")


# =====================================================================
# Registro
# =====================================================================
def rutas() -> dict:
    """Registro de rutas del bloque. Claves RELATIVAS: el cableado
    antepone el prefijo `garantias/`.

    El cableado traduce errores: ErrorGarantia/ValueError -> 400,
    NoEncontrado -> 404, PermisoDenegado -> 403.
    """
    def _r(metodo, permiso, descripcion, handler):
        return {"metodo": metodo, "permiso": permiso,
                "descripcion": descripcion, "handler": handler}

    return {
        # --- términos --------------------------------------------------
        "terminos": _r(
            "GET", "os.read",
            "Términos de garantía; filtro producto_id=.",
            h_terminos),
        "terminos/registrar": _r(
            "POST", "os.write",
            "Registra un término (duración, cobertura, condiciones; "
            "opcional por lote).", h_terminos_registrar),
        # --- activación ------------------------------------------------
        "activar-desde-venta": _r(
            "POST", "os.write",
            "Activa tarjetas de garantía desde una venta confirmada "
            "(idempotente por venta).", h_activar),
        "tarjetas": _r(
            "GET", "os.read",
            "Tarjetas activadas; filtros estado=, producto_id=, "
            "cliente_id=.", h_tarjetas),
        # --- reclamos --------------------------------------------------
        "reclamos": _r(
            "GET", "os.read",
            "Reclamos; filtros estado=, producto_id=, cliente_id=.",
            h_reclamos),
        "reclamos/registrar": _r(
            "POST", "os.write",
            "Intake de reclamo: tarjeta vigente + síntomas.",
            h_reclamo_registrar),
        "reclamos/diagnosticar": _r(
            "POST", "os.write",
            "Diagnóstico: causa + cubierto (requiere estado abierto).",
            h_reclamo_diagnosticar),
        "reclamos/resolver": _r(
            "POST", "os.write",
            "Resolución con costo real: reparacion|reemplazo|devolucion|"
            "rechazado. Emite el gasto operativo si hay costo.",
            h_reclamo_resolver),
        # --- costos / margen / alertas / historial ----------------------
        "costos": _r(
            "GET", "os.read",
            "Costo real de reclamos cerrados por moneda "
            "(desde=, hasta=, producto_id=).", h_costos),
        "impacto-margen": _r(
            "GET", "os.read",
            "Margen de contribución del producto con y sin garantías "
            "(producto_id= obligatorio).", h_impacto_margen),
        "alertas/vencimiento": _r(
            "GET", "os.read",
            "Garantías vencidas o próximas a vencer; formato fuente "
            "para os_notif (dias_alerta=, hoy=).", h_alertas_vencimiento),
        "historial": _r(
            "GET", "os.read",
            "Historial por cliente (cliente_id=) o por producto "
            "(producto_id=).", h_historial),
    }
