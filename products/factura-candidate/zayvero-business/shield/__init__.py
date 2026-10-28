"""EXTRA 1 — SHIELD: alertas y detección de riesgos empresariales.

Motor de reglas que evalúa el estado de UNA empresa (bundle de motores
hidratado desde el snapshot — la misma fuente de verdad que usa el
panel) y emite alertas accionables con severidad, explicación bilingüe
(ES/EN), dato que la respalda y acción sugerida.

Uso:
    from shield import evaluar, ConfigShield
    alertas = evaluar(bundle, ConfigShield(), hoy="2026-10-12",
                      eventos=[...])   # lista de dicts tipo os_eventos
    for a in alertas:
        print(a.severidad, a.regla, a.explicacion_es)

Vía API: GET /api/v1/os/shield/alertas (ver shield/api.py y
webapp/os_routes.py). Permiso: os.read; company_id siempre del ctx.
"""
from .config import ConfigShield  # noqa: F401
from .reglas import (Alerta, REGLAS, evaluar)  # noqa: F401

__all__ = ["ConfigShield", "Alerta", "REGLAS", "evaluar"]
