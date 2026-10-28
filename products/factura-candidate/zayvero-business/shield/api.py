"""EXTRA 1 — SHIELD: adaptador para la ruta GET /api/v1/os/shield/alertas.

- Lee los eventos de auditoría de la empresa según el backend activo
  (PG: os_eventos vía leer_eventos; memoria: bundle.eventos_memoria).
- Filtros de query: hoy=YYYY-MM-DD, severidad, regla.
- La paginación la aplica la ruta con _paginar (convención del bloque 4).

El company_id SIEMPRE viene del ctx (anti-IDOR); esta función nunca lo
toma de la query ni del cuerpo.
"""
from __future__ import annotations

from .config import ConfigShield
from .reglas import SEVERIDADES, REGLAS, evaluar

_LIMITE_EVENTOS = 2000


def _eventos_empresa(company_id: str, bundle) -> list[dict]:
    """Eventos de auditoría de UNA empresa (aislamiento por company_id).

    PG: lee os_eventos (append-only, orden cronológico). Memoria: la
    lista acotada del bundle (sin fecha: se tratan como recientes).
    """
    # Import perezoso: webapp.os_api no importa shield (sin ciclos).
    from webapp.os_api import backend_activo, _pg
    if backend_activo() == "postgres":
        from os_persist.auditoria import leer_eventos
        return leer_eventos(_pg(), company_id, limite=_LIMITE_EVENTOS)
    return list(getattr(bundle, "eventos_memoria", []) or [])


def _q1(query, name, default=""):
    v = query.get(name)
    return v[0] if v else default


def alertas_para_ruta(bundle, company_id: str, query) -> list[dict]:
    """Evalúa SHIELD y devuelve la lista de dicts lista para JSON.

    Lanza ValueError con mensaje legible si un filtro es inválido
    (la ruta lo convierte en 400).
    """
    hoy = _q1(query, "hoy") or None
    if hoy:
        from datetime import datetime
        try:
            datetime.strptime(hoy, "%Y-%m-%d")
        except ValueError:
            raise ValueError("hoy debe ser YYYY-MM-DD")
    sev = _q1(query, "severidad")
    if sev and sev not in SEVERIDADES:
        raise ValueError("severidad inválida: use " +
                         ", ".join(SEVERIDADES))
    regla = _q1(query, "regla")
    if regla and regla not in REGLAS:
        raise ValueError("regla inválida: use " + ", ".join(REGLAS))

    eventos = _eventos_empresa(company_id, bundle)
    alertas = evaluar(bundle, ConfigShield(), hoy=hoy, eventos=eventos)
    if sev:
        alertas = [a for a in alertas if a.severidad == sev]
    if regla:
        alertas = [a for a in alertas if a.regla == regla]
    out = []
    for a in alertas:
        d = a.to_dict()
        # BLOQUE 6 (sprint 3): el OS opera sobre el dataset de
        # demostración (100 % sintético); las alertas lo indican en su
        # evidencia para que nadie las confunda con datos reales.
        d["dato"]["origen_datos"] = "sintetico_demo"
        out.append(d)
    return out
