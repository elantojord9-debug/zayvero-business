"""BLOQUE SECTOR — Registro de rutas `sector/`.

CONTRATO (para el cableado en webapp, que lo hará otra fase):
- `rutas()` devuelve {subruta: spec} con claves RELATIVAS (el cableado
  antepone el prefijo `sector/`).
- spec = {"metodo": "GET"|"POST", "permiso": "os.read"|"os.write",
          "descripcion": str, "handler": fn}.
- `fn(bundle, ctx, payload) -> dict` JSON-serializable. `payload` es la
  query parseada (dict de listas, como en os_routes) en GET y el body
  JSON (dict) en POST.
- Errores: `ErrorSector`/`ValueError` -> 400; `NoEncontrado` -> 404;
  `PermisoDenegado` -> 403. El cableado los traduce; los handlers NO
  envían HTTP.

company_id SIEMPRE del ctx (anti-IDOR). ctx es TenantContext (getattr)
o dict en tests (fallback), igual que os_contab/rutas.py.

Rutas:
- plantillas          GET  os.read   lista de las 4 plantillas (resumen)
- plantilla/detalle   GET  os.read   plantilla completa (?sector=)
- estado              GET  os.read   sector asignado a la empresa
- asignar             POST os.write  asigna sector (SOLO owner/admin;
                                    además del permiso, el handler exige
                                    el rol; el cambio se audita)
- panel               GET  os.read   KPIs/umbrales/checklist del sector
                                    asignado, cada KPI con "datos":
                                    disponibles|sin_datos según lo
                                    registrado en el bundle. Sin sector
                                    asignado -> 404 con guía.

El panel CONFIGURA, no diagnostica: no calcula KPIs ni emite alertas;
eso lo hacen los módulos fuente (shield, inspector, finanzas, contab).
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .errores import (  # noqa: E402
    ErrorSector, NoEncontrado, PermisoDenegado)
from .plantillas import (  # noqa: E402
    listar_plantillas, obtener_plantilla, SECTORES)
from .estado import estado as _estado, asignar as _asignar  # noqa: E402


# ---- utilidades (mismo patrón que os_contab/rutas.py) --------------------
def _q1(payload, name, default=""):
    v = (payload or {}).get(name, default)
    if isinstance(v, (list, tuple)):
        return v[0] if v else default
    return v if v is not None else default


def _ctx_val(ctx, nombre: str) -> str:
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
        raise ErrorSector("company_id del contexto no coincide con la empresa")
    return cid


def _permiso(ctx, permiso: str, recurso: str) -> None:
    tiene = False
    if hasattr(ctx, "has"):
        tiene = bool(ctx.has(permiso))
    elif isinstance(ctx, dict):
        tiene = permiso in (ctx.get("permisos") or [])
    if not tiene:
        raise PermisoDenegado(
            f"permiso insuficiente: {permiso} (recurso {recurso})")


# ---- disponibilidad de datos (solo lectura, sin inventar) -----------------
def _hay(registros) -> bool:
    try:
        return len(registros) > 0
    except Exception:
        return False


def _disponibilidad(bundle) -> dict:
    """Qué tipos de datos tiene registrados la empresa (booleano c/u).

    Solo presencia/ausencia: nunca cantidades ni valores calculados.
    """
    disp = {"ventas": False, "compras": False, "inventario": False,
            "gastos": False, "cxc": False, "cxp": False,
            "tesoreria": False, "presupuestos": False}
    try:
        ventas = getattr(getattr(bundle, "ventas", None), "_ventas", {})
        disp["ventas"] = any(
            (r.get("estado") if isinstance(r, dict) else
             getattr(r, "estado", "")) == "confirmada"
            for r in ventas.values())
    except Exception:
        pass
    try:
        disp["compras"] = _hay(
            getattr(getattr(bundle, "compras", None), "_ordenes", {}))
    except Exception:
        pass
    try:
        disp["inventario"] = _hay(bundle.inventario.movimientos())
    except Exception:
        pass
    try:
        disp["gastos"] = _hay(bundle.gastos.todos())
    except Exception:
        pass
    try:
        teso = getattr(bundle, "tesoreria", None)
        disp["cxc"] = _hay(getattr(teso, "_cxc", {}))
        disp["cxp"] = _hay(getattr(teso, "_cxp", {}))
        disp["tesoreria"] = True  # el módulo existe siempre en el bundle
    except Exception:
        pass
    return disp


# =====================================================================
# Handlers
# =====================================================================
def h_plantillas(bundle, ctx, payload):
    _permiso(ctx, "os.read", "sector/plantillas")
    _ctx_company(bundle, ctx)
    return {"ok": True, "data": listar_plantillas(),
            "nota": ("Las plantillas configuran qué mirar; no "
                     "diagnostican ni inventan datos.")}


def h_plantilla_detalle(bundle, ctx, payload):
    _permiso(ctx, "os.read", "sector/plantilla/detalle")
    _ctx_company(bundle, ctx)
    sid = str(_q1(payload, "sector") or "").strip().lower()
    if not sid:
        raise ErrorSector("parámetro 'sector' obligatorio "
                          f"({', '.join(SECTORES)})")
    p = obtener_plantilla(sid)
    if p is None:
        raise NoEncontrado(f"sector inexistente: {sid!r}")
    return {"ok": True, "data": p}


def h_estado(bundle, ctx, payload):
    _permiso(ctx, "os.read", "sector/estado")
    _ctx_company(bundle, ctx)
    e = _estado(bundle)
    p = obtener_plantilla(e["sector"]) if e["sector"] else None
    return {"ok": True, "data": {
        "sector": e["sector"],
        "plantilla": p["nombre"] if p else "",
        "fijado_en": e["fijado_en"], "fijado_por": e["fijado_por"],
        "historial": e["historial"]}}


def h_asignar(bundle, ctx, payload):
    body = payload  # alias: POST recibe el body JSON
    _permiso(ctx, "os.write", "sector/asignar")
    body = body or {}
    sector_id = str(body.get("sector") or "").strip().lower()
    res = _asignar(bundle, ctx, sector_id)
    p = obtener_plantilla(res["sector"])
    return {"ok": True, "data": {
        "sector": res["sector"],
        "plantilla": p["nombre"] if p else "",
        "fijado_en": res["fijado_en"], "fijado_por": res["fijado_por"],
        "sin_cambios": res["sin_cambios"],
        "historial": res["historial"]}}


def h_panel(bundle, ctx, payload):
    _permiso(ctx, "os.read", "sector/panel")
    _ctx_company(bundle, ctx)
    e = _estado(bundle)
    if not e["sector"]:
        raise NoEncontrado(
            "la empresa no tiene sector asignado: use POST sector/asignar "
            f"(sectores: {', '.join(SECTORES)})")
    p = obtener_plantilla(e["sector"])
    disp = _disponibilidad(bundle)
    kpis = []
    for k in p["kpis"]:
        faltan = [d for d in k["requiere"] if not disp.get(d)]
        kpis.append({**k,
                     "datos": "sin_datos" if faltan else "disponibles",
                     "datos_faltantes": faltan})
    return {"ok": True, "data": {
        "sector": p["id"], "plantilla": p["nombre"],
        "kpis": kpis, "umbrales": p["umbrales"],
        "modulos_recomendados": list(p["modulos_recomendados"]),
        "checklist": p["checklist"],
        "nota": ("Las plantillas configuran, no diagnostican: cada KPI "
                 "se calcula en su módulo fuente solo si hay datos "
                 "registrados ('sin_datos' = la empresa aún no registra "
                 "ese tipo de dato).")}}


# =====================================================================
# Registro
# =====================================================================
def rutas() -> dict:
    """Registro de rutas del bloque. Prefijo: `sector/` (lo antepone el
    cableado).

    El cableado traduce errores: ErrorSector/ValueError -> 400,
    NoEncontrado -> 404, PermisoDenegado -> 403.
    """
    def _r(metodo, permiso, descripcion, handler):
        return {"metodo": metodo, "permiso": permiso,
                "descripcion": descripcion, "handler": handler}

    return {
        "plantillas": _r(
            "GET", "os.read",
            "Lista las 4 plantillas sectoriales (resumen: KPIs, "
            "umbrales, módulos, checklist).",
            h_plantillas),
        "plantilla/detalle": _r(
            "GET", "os.read",
            "Plantilla completa de un sector (?sector=tienda|"
            "restaurante|clinica|servicios).",
            h_plantilla_detalle),
        "estado": _r(
            "GET", "os.read",
            "Sector asignado a la empresa y su historial de cambios.",
            h_estado),
        "asignar": _r(
            "POST", "os.write",
            "Asigna o cambia el sector de la empresa. SOLO rol "
            "owner/admin (además de os.write); el cambio queda auditado "
            "en os_eventos (tipo 'sector').",
            h_asignar),
        "panel": _r(
            "GET", "os.read",
            "KPIs, umbrales sugeridos, módulos y checklist del sector "
            "asignado; cada KPI indica si hay datos registrados "
            "(disponibles|sin_datos). Configura, no diagnostica.",
            h_panel),
    }
