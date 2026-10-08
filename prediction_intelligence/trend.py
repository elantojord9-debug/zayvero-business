"""
ZAYVERO BUSINESS — FASE 4B: interpretación de tendencia (descriptiva).

Los textos son deterministas y describen la señal estadística detectada
por FASE 4A. No se inventan causas: una tendencia no es causalidad.
"""

from __future__ import annotations

TREND_TEXTS = {
    "UPWARD": (
        "El modelo proyecta una tendencia de crecimiento durante el horizonte "
        "analizado, según el comportamiento histórico observado."
    ),
    "DOWNWARD": (
        "El modelo proyecta una tendencia descendente durante el horizonte "
        "analizado, según el comportamiento histórico observado."
    ),
    "STABLE": (
        "El modelo no identifica un cambio relevante en la dirección esperada "
        "durante el horizonte analizado."
    ),
    "UNSTABLE": (
        "El comportamiento histórico presenta suficiente variabilidad como "
        "para limitar la confianza en una dirección estable durante el "
        "horizonte analizado."
    ),
    "INSUFFICIENT_DATA": (
        "No existe suficiente información para determinar una tendencia "
        "confiable."
    ),
}

VALID_TRENDS = tuple(TREND_TEXTS.keys())


def interpret_trend(trend: str) -> str:
    return TREND_TEXTS.get(
        trend,
        "La tendencia reportada (%s) no tiene una interpretación definida." % trend,
    )
