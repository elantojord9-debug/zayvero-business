"""
ZAYVERO BUSINESS — FASE 4B: interpretación de decline_risk.

El riesgo de caída es una señal probabilística derivada de patrones
observados, NO una afirmación de que la caída ocurrirá.
"""

from __future__ import annotations

RISK_TEXTS = {
    "LOW": (
        "El modelo identifica un riesgo bajo de disminución según el "
        "comportamiento histórico analizado. No se genera alarma."
    ),
    "MEDIUM": (
        "El modelo identifica un riesgo moderado de disminución según el "
        "comportamiento histórico analizado. Se recomienda revisar la "
        "evolución del indicador en el próximo periodo."
    ),
    "HIGH": (
        "El modelo identifica un riesgo elevado de disminución según el "
        "comportamiento histórico analizado. Merece atención prioritaria: "
        "conviene revisar las variables comerciales relacionadas antes de "
        "que avance el periodo."
    ),
    "INSUFFICIENT_DATA": (
        "No existe suficiente información para evaluar el riesgo de "
        "disminución."
    ),
}

VALID_RISKS = tuple(RISK_TEXTS.keys())


def interpret_decline_risk(risk: str) -> str:
    return RISK_TEXTS.get(
        risk,
        "El riesgo reportado (%s) no tiene una interpretación definida." % risk,
    )
