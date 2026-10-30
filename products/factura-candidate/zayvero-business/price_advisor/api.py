"""EXTRA 3 (sprint 12-oct-2026) — Adaptador de ruta del Price Advisor.

Llamado desde `webapp.os_routes::_handle_get` (GET, solo lectura):

    GET /api/v1/os/price-advisor/recomendaciones
        ?moneda=DOP&margen_objetivo=0.40&margen_minimo=0.0
        &tolerancia=0.03&almacen=a1&producto_ids=p1,p2
        &ventana_demanda_dias=30&hoy=2026-10-12&lang=es

Seguridad (heredada de `handle_os`, igual que el resto de GETs OS):
- Módulo OS habilitado (503 si no).
- Rate limiting de lectura ANTES de cualquier trabajo (429).
- Auth (401) y aislamiento multiempresa: `company_id` siempre del ctx
  (anti-IDOR); se verifica además que el bundle sea de esa empresa.
- Permiso `os.read` (403 si falta). GET no muta → no requiere CSRF,
  igual que todos los demás GET de `/api/v1/os/*`.
- Solo lectura: nunca pasa por `mutar_os`, no audita.

Parámetros inválidos → ValueError → 400 legible.
"""
from __future__ import annotations

import os as _os
import sys

sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from .engine import AsesorPrecios, ErrorAdvisor  # noqa: E402


def _q1(query, name, default=""):
    v = query.get(name)
    if not v:
        return default
    return v[0] if isinstance(v, (list, tuple)) else v


def recomendaciones_para_ruta(bundle, company_id: str, query: dict) -> dict:
    """Calcula las recomendaciones sobre el bundle (SOLO lectura).

    Lanza ValueError ante parámetros inválidos (la ruta los traduce
    a 400).
    """
    if getattr(bundle, "company_id", None) != company_id:
        raise ErrorAdvisor("bundle no pertenece a la empresa del contexto")
    lang = _q1(query, "lang", "es").lower()
    if lang not in ("es", "en"):
        raise ErrorAdvisor("lang inválido: use es o en")
    try:
        ventana = int(_q1(query, "ventana_demanda_dias", "30"))
    except ValueError:
        raise ErrorAdvisor("ventana_demanda_dias debe ser entero")
    pids_raw = _q1(query, "producto_ids", "").strip()
    producto_ids = ([p.strip() for p in pids_raw.split(",") if p.strip()]
                    if pids_raw else None)

    # BLOQUE 6 (sprint 3): umbrales configurables por empresa. Si el
    # parámetro NO viene en la query, se usa la config de la empresa
    # (defaults de DEMO si nunca se personalizó; ver
    # catalogo/config.py). La respuesta indica el origen.
    cfg = _config_empresa(company_id)
    params = {}
    for nombre, clave_q, clave_cfg in (
            ("margen_objetivo", "margen_objetivo", "margen_objetivo"),
            ("margen_minimo", "margen_minimo", "margen_minimo"),
            ("tolerancia", "tolerancia", None)):
        dado = _q1(query, clave_q, "")
        if dado:
            params[nombre] = (dado, "peticion")
        elif clave_cfg and cfg:
            params[nombre] = (cfg[clave_cfg], "config_empresa")
    asesor = AsesorPrecios(bundle)
    data = asesor.recomendar(
        producto_ids=producto_ids,
        almacen_id=_q1(query, "almacen", "") or None,
        moneda=_q1(query, "moneda", "DOP"),
        margen_objetivo=params.get("margen_objetivo",
                                   ("0.40", "peticion"))[0],
        margen_minimo=params.get("margen_minimo",
                                 ("0.0", "peticion"))[0],
        tolerancia=params.get("tolerancia", ("0.03", "peticion"))[0],
        ventana_demanda_dias=ventana,
        hoy=_q1(query, "hoy", "") or None,
        lang=lang)
    data["insumos"]["parametros_fuente"] = {
        "margen_objetivo": params.get("margen_objetivo",
                                      ("0.40", "peticion"))[1],
        "margen_minimo": params.get("margen_minimo",
                                    ("0.0", "peticion"))[1],
        "tolerancia": params.get("tolerancia", ("0.03", "peticion"))[1],
    }
    data["insumos"]["config_origen"] = (
        cfg.get("origen") if cfg else "no_disponible_sin_pg")
    data["insumos"]["nota_demo"] = (
        "los umbrales no dados en la petición vienen de la config de "
        "la empresa; si es 'demo_por_defecto' son valores sintéticos "
        "no calibrados (personalícelos en POST catalogo/config)")
    return data


def _config_empresa(company_id: str) -> dict | None:
    """Config de umbrales de la empresa, o None sin PG."""
    try:
        from catalogo.config import leer_config
        return leer_config(company_id)
    except Exception:
        return None
