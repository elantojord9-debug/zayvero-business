"""BLOQUE EMPLEADOS (2026-10-13) — Control de sucursales.

- Supervisión por ubicación: ventas, tareas, promesas, SLA y stock
  por sucursal/almacén, desde registros verificables.
- Roles y permisos por sucursal: `os_empleado_sucursal` mapea qué
  sucursales ve cada usuario. owner/admin ven todo (vista
  consolidada); el resto solo sus sucursales asignadas.
- Comparación de indicadores entre sucursales.

LIMITACIÓN DOCUMENTADA: la tesorería del OS no segrega la caja por
sucursal (diseño actual del motor). La caja se reporta a nivel
empresa en el consolidado; NO se inventa un reparto por sucursal.
"""
from __future__ import annotations

from decimal import Decimal

from .modelo import (ErrorEmpleados, PermissionDeniedEmpleados,
                     hoy_ymd)
from . import tareas as mon_tareas
from . import sla as mon_sla

_ROLES_TOTALES = ("owner", "admin")


def es_rol_total(ctx) -> bool:
    """owner/admin ven todas las sucursales (vista consolidada)."""
    return getattr(ctx, "role", "") in _ROLES_TOTALES


def sucursales_visibles(almacen, ctx) -> list[dict]:
    """Sucursales que el usuario del ctx puede ver. Solo lectura."""
    todas = almacen.listar_sucursales(activas_solo=False)
    if es_rol_total(ctx):
        return todas
    visibles = set(almacen.sucursales_de_usuario(ctx.user_id))
    return [s for s in todas if s["sucursal_id"] in visibles]


def exigir_visible(almacen, ctx, sucursal_id: str) -> dict:
    """Devuelve la sucursal si el usuario puede verla; si no, 403."""
    suc = almacen.obtener_sucursal(sucursal_id)
    if not suc:
        raise ErrorEmpleados(f"sucursal inexistente: {sucursal_id}")
    if es_rol_total(ctx):
        return suc
    if sucursal_id not in almacen.sucursales_de_usuario(ctx.user_id):
        raise PermissionDeniedEmpleados("sin acceso a esta sucursal")
    return suc


def _solicitud_sucursal(sol) -> str:
    if sol is None:
        return ""
    if isinstance(sol, dict):
        return str(sol.get("sucursal_id") or "")
    return str(getattr(sol, "sucursal_id", "") or "")


def _ventas_sucursal(bundle, sucursal_id: str,
                     desde: str | None, hasta: str | None) -> dict:
    """Ventas cuya solicitud indica esta sucursal (fuente: bundle)."""
    n = 0
    por_moneda: dict[str, Decimal] = {}
    for _vid, reg in (getattr(bundle.ventas, "_ventas", {}) or {}).items():
        sol = reg.get("solicitud") if isinstance(reg, dict) else None
        if _solicitud_sucursal(sol) != sucursal_id:
            continue
        fecha = ""
        if isinstance(reg, dict):
            fecha = str(reg.get("solicitud_fecha") or "")
            if not fecha and sol is not None:
                fecha = (sol.get("fecha", "") if isinstance(sol, dict)
                         else str(getattr(sol, "fecha", "")))
        f = fecha[:10]
        if desde and f < desde:
            continue
        if hasta and f > hasta:
            continue
        n += 1
        mon = "DOP"
        tot = Decimal("0")
        if isinstance(reg, dict):
            try:
                tot = Decimal(str(reg.get("total") or 0))
            except Exception:
                tot = Decimal("0")
            if sol is not None:
                mon = (sol.get("moneda") if isinstance(sol, dict)
                       else getattr(sol, "moneda", "DOP")) or "DOP"
        por_moneda[mon] = por_moneda.get(mon, Decimal("0")) + tot
    return {"n_ventas": n,
            "monto_por_moneda": {m: str(v)
                                 for m, v in sorted(por_moneda.items())},
            "fuente": "bundle.ventas (solicitud.sucursal_id)"}


def _stock_por_almacen(bundle, almacenes: list[str] | None) -> dict:
    """Stock por almacén desde el motor de inventario.

    Si la sucursal tiene almacenes asociados se filtra a esos; si no,
    se reportan todos con la advertencia correspondiente.
    """
    niveles = bundle.inventario.stock_empresa()  # {(prod, alm): Decimal}
    por_alm: dict[str, dict] = {}
    for (prod, alm), nivel in niveles.items():
        if almacenes and alm not in almacenes:
            continue
        por_alm.setdefault(alm, {})[prod] = str(nivel)
    return {
        "por_almacen": por_alm,
        "almacenes_filtrados": list(almacenes) if almacenes else None,
        "nota": (None if almacenes else
                 "la sucursal no tiene almacenes asociados: se muestra "
                 "el stock de todos los almacenes de la empresa"),
        "fuente": "bundle.inventario",
    }


def indicadores_sucursal(bundle, almacen, sucursal_id: str,
                         desde: str | None = None,
                         hasta: str | None = None,
                         hoy: str | None = None) -> dict:
    """Indicadores operativos de UNA sucursal. Solo lectura."""
    hoy = hoy_ymd(hoy)
    suc = almacen.obtener_sucursal(sucursal_id)
    if not suc:
        raise ErrorEmpleados(f"sucursal inexistente: {sucursal_id}")

    tareas = almacen.listar_tareas(sucursal_id=sucursal_id)
    res_tareas = mon_tareas.resumen(tareas, hoy)

    empleados = [e for e in almacen.listar_empleados()
                 if e.get("sucursal_id") == sucursal_id]
    ids_emp = {e["empleado_id"] for e in empleados}
    promesas = [p for p in almacen.listar_promesas()
                if p["empleado_id"] in ids_emp]

    configs = {c["tipo_operacion"]: c for c in almacen.listar_sla()}
    mediciones = [m for m in almacen.listar_mediciones()
                  if m.get("responsable") in ids_emp]
    res_sla = mon_sla.resumen_por_tipo(mediciones, configs, hoy)

    return {
        "sucursal": {
            "sucursal_id": suc["sucursal_id"],
            "nombre": suc["nombre"],
            "ubicacion": suc.get("ubicacion", ""),
            "activa": suc.get("activa", True),
        },
        "periodo": {"desde": desde, "hasta": hasta, "hoy": hoy},
        "ventas": _ventas_sucursal(bundle, sucursal_id, desde, hasta),
        "tareas": res_tareas,
        "empleados_activos": len(empleados),
        "promesas": {
            "n_promesas": len(promesas),
            "pendientes": sum(1 for p in promesas
                              if p.get("cumplida") is None),
            "fuente": "os_promesas_seguimiento (por empleado de la sucursal)",
        },
        "sla": res_sla,
        "stock": _stock_por_almacen(bundle, suc.get("almacenes") or None),
        "caja": {
            "nota": "la tesorería no segrega caja por sucursal "
                    "(diseño actual del motor): ver caja_empresa en el "
                    "consolidado. No se inventa un reparto.",
            "caja_empresa": None,
        },
        "origen_datos": "sintetico_demo",
    }


def comparar_sucursales(bundle, almacen, sucursal_ids: list[str],
                        desde: str | None = None,
                        hasta: str | None = None,
                        hoy: str | None = None) -> dict:
    """Tabla comparativa lado a lado. Solo lectura."""
    hoy = hoy_ymd(hoy)
    filas = []
    for sid in sucursal_ids:
        ind = indicadores_sucursal(bundle, almacen, sid, desde, hasta, hoy)
        ventas = ind["ventas"]
        filas.append({
            "sucursal_id": sid,
            "nombre": ind["sucursal"]["nombre"],
            "n_ventas": ventas["n_ventas"],
            "monto_por_moneda": ventas["monto_por_moneda"],
            "tareas_total": ind["tareas"]["total"],
            "tareas_vencidas": ind["tareas"]["n_vencidas"],
            "empleados_activos": ind["empleados_activos"],
            "promesas_pendientes": ind["promesas"]["pendientes"],
            "sla_tipos": {
                t: {"tasa_cumplimiento": v["tasa_cumplimiento"],
                    "abiertas_atrasadas": v["abiertas_atrasadas"]}
                for t, v in ind["sla"]["por_tipo"].items()},
        })
    return {"hoy": hoy, "periodo": {"desde": desde, "hasta": hasta},
            "comparativa": filas,
            "nota": "comparación descriptiva; no es un ranking de "
                    "desempeño individual (ver nota ética del bloque)",
            "origen_datos": "sintetico_demo"}


def consolidado(bundle, almacen, desde: str | None = None,
                hasta: str | None = None,
                hoy: str | None = None) -> dict:
    """Vista del propietario: todas las sucursales + totales empresa.

    Incluye la caja a nivel empresa (la única granularidad que la
    tesorería soporta hoy).
    """
    hoy = hoy_ymd(hoy)
    sucs = almacen.listar_sucursales(activas_solo=False)
    por_sucursal = [indicadores_sucursal(bundle, almacen,
                                        s["sucursal_id"], desde, hasta, hoy)
                    for s in sucs]
    total_ventas = sum(p["ventas"]["n_ventas"] for p in por_sucursal)
    total_tareas_venc = sum(p["tareas"]["n_vencidas"] for p in por_sucursal)
    caja = {m: str(v) for m, v in
            (getattr(bundle.tesoreria, "_caja", {}) or {}).items()}
    for p in por_sucursal:
        p["caja"]["caja_empresa"] = caja
    return {
        "hoy": hoy,
        "periodo": {"desde": desde, "hasta": hasta},
        "n_sucursales": len(sucs),
        "totales": {
            "n_ventas": total_ventas,
            "tareas_vencidas": total_tareas_venc,
            "caja_empresa": caja,
            "fuente_caja": "bundle.tesoreria (nivel empresa; no segregada "
                           "por sucursal)",
        },
        "por_sucursal": por_sucursal,
        "origen_datos": "sintetico_demo",
    }
