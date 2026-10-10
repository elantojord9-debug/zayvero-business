"""
ZAYVERO BUSINESS — FASE 4B: interpretación de decline_risk.

El riesgo de caída estima la probabilidad direccional de que el
indicador DISMINUYA en el horizonte PROYECTADO, a partir del cambio
proyectado (señal principal) y señales históricas (secundarias).
NO es una probabilidad calibrada ni una afirmación de que la caída
ocurrirá.
"""

from __future__ import annotations

RISK_TEXTS = {
    "LOW": (
        "El modelo identifica un riesgo bajo de disminución en el "
        "horizonte proyectado: no se proyecta una caída material. "
        "No se genera alarma."
    ),
    "MEDIUM": (
        "El modelo identifica un riesgo moderado de disminución en el "
        "horizonte proyectado. Se recomienda revisar la evolución del "
        "indicador en el próximo periodo."
    ),
    "HIGH": (
        "El modelo identifica un riesgo elevado de disminución en el "
        "horizonte proyectado. Merece atención prioritaria: conviene "
        "revisar las variables comerciales relacionadas antes de que "
        "avance el periodo."
    ),
    "INSUFFICIENT_DATA": (
        "No existe suficiente información para evaluar el riesgo de "
        "disminución en el horizonte proyectado."
    ),
}

VALID_RISKS = tuple(RISK_TEXTS.keys())


def interpret_decline_risk(risk: str) -> str:
    return RISK_TEXTS.get(
        risk,
        "El riesgo reportado (%s) no tiene una interpretación definida." % risk,
    )
