"""
ZAYVERO BUSINESS — FASE 4B: interpretación de tendencia (descriptiva).

Separación estricta de dos señales que antes se mezclaban:

- `historical_trend` (UPWARD / DOWNWARD / STABLE / UNSTABLE /
  INSUFFICIENT_DATA): lo que calcula FASE 4A (`prediction/trends.py::
  detect_trend`) sobre la serie HISTÓRICA. Es DESCRIPTIVA del pasado;
  nunca afirma lo que ocurrirá.

- `forecast_direction` (UP / DOWN / FLAT / UNKNOWN): la dirección del
  cambio PROYECTADO, derivada del `percentage_change` de 4B
  (pronóstico vs. nivel reciente observado). Describe el futuro
  estimado, no el pasado.

Umbrales de materialidad (±20 %): consistentes con el módulo de
atención (`prediction_intelligence/attention.py`), donde |pct| >= 20 %
aporta 20/30 puntos de magnitud ("material"). Un cambio proyectado
menor al 20 % se considera no material (FLAT) a efectos de dirección.

Cuando ambas señales discrepan (p. ej. historial UPWARD con pronóstico
DOWN), se presentan POR SEPARADO con una nota de discrepancia: nunca
se describe una caída proyectada como "crecimiento".
"""

from __future__ import annotations

# --- Tendencia histórica: describe el pasado observado -----------------
TREND_TEXTS = {
    "UPWARD": (
        "El comportamiento histórico observado muestra una tendencia de "
        "crecimiento en los periodos recientes analizados. Describe el "
        "pasado, no afirma lo que ocurrirá."
    ),
    "DOWNWARD": (
        "El comportamiento histórico observado muestra una tendencia "
        "descendente en los periodos recientes analizados. Describe el "
        "pasado, no afirma lo que ocurrirá."
    ),
    "STABLE": (
        "El comportamiento histórico observado no muestra un cambio "
        "relevante de dirección en los periodos recientes analizados. "
        "Describe el pasado, no afirma lo que ocurrirá."
    ),
    "UNSTABLE": (
        "El comportamiento histórico presenta suficiente variabilidad como "
        "para limitar la confianza en una dirección estable. Describe el "
        "pasado, no afirma lo que ocurrirá."
    ),
    "INSUFFICIENT_DATA": (
        "No existe suficiente información histórica para determinar una "
        "tendencia confiable."
    ),
}

VALID_TRENDS = tuple(TREND_TEXTS.keys())

# --- Dirección del pronóstico: describe el futuro estimado -------------
# Umbrales documentados (±20 %): banda de materialidad consistente con
# el módulo de atención (attention.py: |pct| >= 20 % = cambio material).
FORECAST_UP_THRESHOLD = 20.0
FORECAST_DOWN_THRESHOLD = -20.0

FORECAST_DIRECTION_TEXTS = {
    # Se completan con el % real en forecast_direction_text().
    "UP": "La proyección estima un incremento material respecto a los "
          "niveles recientes observados.",
    "DOWN": "La proyección estima una disminución material respecto a "
            "los niveles recientes observados.",
    "FLAT": "La proyección no estima un cambio material respecto a los "
            "niveles recientes observados.",
    "UNKNOWN": "No se pudo determinar la dirección del cambio proyectado "
               "por falta de una referencia histórica válida.",
}

VALID_FORECAST_DIRECTIONS = tuple(FORECAST_DIRECTION_TEXTS.keys())


def interpret_trend(trend: str) -> str:
    """Texto de la tendencia HISTÓRICA (pasado observado)."""
    return TREND_TEXTS.get(
        trend,
        "La tendencia histórica reportada (%s) no tiene una interpretación definida." % trend,
    )


def classify_forecast_direction(percentage_change: float | None) -> str:
    """Clasifica la dirección del cambio proyectado.

    UP:   pct >= +20 %  (incremento material)
    DOWN: pct <= -20 %  (disminución material)
    FLAT: cambio no material
    UNKNOWN: sin referencia válida (pct None)
    """
    if percentage_change is None:
        return "UNKNOWN"
    try:
        pct = float(percentage_change)
    except (TypeError, ValueError):
        return "UNKNOWN"
    if pct >= FORECAST_UP_THRESHOLD:
        return "UP"
    if pct <= FORECAST_DOWN_THRESHOLD:
        return "DOWN"
    return "FLAT"


def interpret_forecast_direction(percentage_change: float | None) -> str:
    """Texto de la dirección del pronóstico (futuro estimado)."""
    direction = classify_forecast_direction(percentage_change)
    return FORECAST_DIRECTION_TEXTS.get(
        direction,
        "La dirección reportada (%s) no tiene una interpretación definida." % direction,
    )


_TREND_WORD = {
    "UPWARD": "crecimiento",
    "DOWNWARD": "descenso",
    "STABLE": "estabilidad",
    "UNSTABLE": "comportamiento inestable",
    "INSUFFICIENT_DATA": "información insuficiente",
}
_DIRECTION_WORD = {
    "UP": "un incremento",
    "DOWN": "una disminución",
    "FLAT": "ningún cambio material",
    "UNKNOWN": "una dirección no determinable",
}


def discrepancy_note(trend: str, forecast_direction: str) -> str | None:
    """Nota cuando historial y pronóstico discrepan.

    Solo para las combinaciones engañosas: historial UPWARD con
    pronóstico DOWN (y viceversa). Devuelve None si no hay
    discrepancia relevante.
    """
    pairs = {("UPWARD", "DOWN"), ("DOWNWARD", "UP")}
    if (trend, forecast_direction) not in pairs:
        return None
    return (
        "Nota: el historial muestra %s, pero la proyección estima %s. "
        "Se presentan por separado: miden cosas distintas — el "
        "pasado observado frente al futuro estimado." % (
            _TREND_WORD.get(trend, trend),
            _DIRECTION_WORD.get(forecast_direction, forecast_direction),
        )
    )
