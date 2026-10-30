"""BLOQUE Documentos/Integraciones — Contrato de rutas `integraciones/`.

Contrato del bloque:
    def rutas(): return {...}
con claves `integraciones/...` y funciones (bundle, ctx, query/body)
-> dict JSON-serializable.

- GET  -> fn(bundle, ctx, query)
- POST -> fn(bundle, ctx, body)

`manejar()` es el adaptador estilo reportes/rutas.py::manejar para el
cableado futuro en webapp/os_routes.py (este bloque no toca
os_routes.py por orden).

Rutas:
    integraciones/adaptadores                 GET   lista (modo declarado)
    integraciones/<fuente>/productos          GET
    integraciones/<fuente>/pedidos            GET
    integraciones/<fuente>/clientes           GET
    integraciones/<fuente>/stock              GET   ?producto_id=
    integraciones/<fuente>/mapeo              GET   mapeo de campos
    integraciones/<fuente>/sincronizar        POST  sync simulada -> monitor
    integraciones/<fuente>/que-falta          GET   requisitos conexión real
    integraciones/monitor                     GET   estado por fuente
    integraciones/monitor/resumen             GET
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from os_integraciones.shopify import AdaptadorShopify  # noqa: E402
from os_integraciones.odoo import AdaptadorOdoo  # noqa: E402
from os_integraciones.sap import AdaptadorSAP  # noqa: E402
from os_integraciones.quickbooks import AdaptadorQuickBooks  # noqa: E402
from os_integraciones.pos import AdaptadorPOS  # noqa: E402
from os_integraciones.monitor import (  # noqa: E402
    obtener_monitor, sincronizar_fuente)

_ADAPTADORES = {
    "shopify": AdaptadorShopify(),
    "odoo": AdaptadorOdoo(),
    "sap": AdaptadorSAP(),
    "quickbooks": AdaptadorQuickBooks(),
    "pos": AdaptadorPOS(),
}


def _fuente_de(sub: str) -> str:
    partes = sub.split("/")
    if len(partes) < 2 or partes[1] not in _ADAPTADORES:
        raise ValueError(
            f"fuente desconocida; use: {', '.join(sorted(_ADAPTADORES))}")
    return partes[1]


def _company(ctx) -> str:
    cid = getattr(ctx, "company_id", "") or ""
    if not cid:
        raise ValueError("ctx sin company_id")
    return cid


# ------------------------------------------------------------------ handlers

def _adaptadores(bundle, ctx, query):
    return {"ok": True, "modo_global": "simulado",
            "adaptadores": [a.info() for a in _ADAPTADORES.values()],
            "advertencia": ("todos los adaptadores son SIMULADOS: datos "
                            "sintéticos locales, sin llamadas de red")}


def _entidad(bundle, ctx, query, sub, metodo):
    fuente = _fuente_de(sub)
    a = _ADAPTADORES[fuente]
    try:
        if metodo == "leer_stock":
            pid = (query.get("producto_id") or [""])[0] if query else ""
            items = a.leer_stock(pid or None)
        else:
            items = getattr(a, metodo)()
    except ValueError:
        raise
    except Exception as e:
        return {"ok": False, "fuente": fuente, "error": str(e)}
    return {"ok": True, "fuente": fuente, "modo": a.MODO,
            "total": len(items), "items": items}


def _mapeo(bundle, ctx, query):
    fuente = _fuente_de(query.get("_sub", [""])[0] if query else "")
    a = _ADAPTADORES[fuente]
    return {"ok": True, "fuente": fuente, "modo": a.MODO,
            "mapeo_campos": a.mapeo_campos()}


def _que_falta(bundle, ctx, query):
    fuente = _fuente_de(query.get("_sub", [""])[0] if query else "")
    a = _ADAPTADORES[fuente]
    return {"ok": True, "fuente": fuente, "modo": a.MODO,
            "advertencia": ("esto NO es una conexión real; para conectarla "
                            "falta:"),
            "que_falta_para_conexion_real": a.que_falta_para_real()}


def _sincronizar(bundle, ctx, body):
    fuente = _fuente_de((body or {}).get("_sub", ""))
    a = _ADAPTADORES[fuente]
    company_id = _company(ctx)
    estado = sincronizar_fuente(company_id, a)
    return {"ok": estado["estado"] != "error", "sincronizacion": estado}


def _monitor(bundle, ctx, query):
    m = obtener_monitor(_company(ctx))
    fuente = (query.get("fuente") or [""])[0] if query else ""
    if fuente:
        if fuente not in _ADAPTADORES:
            return {"ok": False,
                    "error": f"fuente desconocida: {fuente}"}
        return {"ok": True, "estado": m.estado(fuente)}
    return {"ok": True, "estados": m.estado()}


def _monitor_resumen(bundle, ctx, query):
    return {"ok": True, "resumen": obtener_monitor(_company(ctx)).resumen()}


# ------------------------------------------------------------------ contrato

def _r(sub_base: str):
    """Construye el handler de entidad/stock para una ruta concreta."""
    metodo = "leer_stock" if sub_base.endswith("/stock") else {
        "productos": "listar_productos", "pedidos": "listar_pedidos",
        "clientes": "listar_clientes"}[sub_base.rsplit("/", 1)[1]]

    def _h(bundle, ctx, query, _m=metodo, _s=sub_base):
        return _entidad(bundle, ctx, query, _s, _m)
    return _h


def rutas():
    """Contrato del bloque: prefijo integraciones/ -> fn(bundle, ctx, ...)."""
    tabla = {
        "integraciones/adaptadores": _adaptadores,
        "integraciones/monitor": _monitor,
        "integraciones/monitor/resumen": _monitor_resumen,
    }
    for fuente in _ADAPTADORES:
        tabla[f"integraciones/{fuente}/productos"] = _r(
            f"integraciones/{fuente}/productos")
        tabla[f"integraciones/{fuente}/pedidos"] = _r(
            f"integraciones/{fuente}/pedidos")
        tabla[f"integraciones/{fuente}/clientes"] = _r(
            f"integraciones/{fuente}/clientes")
        tabla[f"integraciones/{fuente}/stock"] = _r(
            f"integraciones/{fuente}/stock")

        def _mk_mapeo(f=fuente):
            def _h(bundle, ctx, query):
                return _mapeo(bundle, ctx,
                              {"_sub": [f"integraciones/{f}/mapeo"]})
            return _h

        def _mk_falta(f=fuente):
            def _h(bundle, ctx, query):
                return _que_falta(bundle, ctx,
                                  {"_sub": [f"integraciones/{f}/que-falta"]})
            return _h

        def _mk_sync(f=fuente):
            def _h(bundle, ctx, body):
                return _sincronizar(bundle, ctx,
                                    {"_sub": f"integraciones/{f}/sincronizar"})
            return _h

        tabla[f"integraciones/{fuente}/mapeo"] = _mk_mapeo()
        tabla[f"integraciones/{fuente}/que-falta"] = _mk_falta()
        tabla[f"integraciones/{fuente}/sincronizar"] = _mk_sync()
    return tabla


def manejar(bundle, ctx, sub, query=None, body=None, method="GET"):
    """Adaptador estilo reportes/rutas.py::manejar.

    Devuelve (manejada: bool, respuesta: dict)."""
    tabla = rutas()
    if sub not in tabla:
        return False, {}
    fn = tabla[sub]
    try:
        if method.upper() == "GET":
            resp = fn(bundle, ctx, query or {})
        else:
            resp = fn(bundle, ctx, body or {})
    except ValueError as e:
        resp = {"ok": False, "error": str(e)}
    except Exception as e:  # fail-closed
        resp = {"ok": False, "error": f"error interno: {e}"}
    json.dumps(resp, default=str)
    return True, resp
