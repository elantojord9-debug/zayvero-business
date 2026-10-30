"""
ZAYVERO BUSINESS — FASE 4C: métricas de validación (deterministas).

Fórmulas:
    absolute_error   = abs(predicted_value - actual_value)
    signed_error     = predicted_value - actual_value
    percentage_error = abs(predicted_value - actual_value)
                       / abs(actual_value) * 100
                     Si actual_value == 0 no se divide por cero:
                     percentage_error = "NOT_AVAILABLE" (documentado).

Bias (umbral configurable ACCURACY_THRESHOLD_PCT, documentado):
    |signed_error| <= umbral% de |actual| → ACCURATE
    signed_error > 0  → OVERPREDICTION
    signed_error < 0  → UNDERPREDICTION
    actual == 0: predicted == 0 → ACCURATE; si no, el signo decide.

Interval validation:
    lower_bound <= actual_value <= upper_bound → TRUE
    fuera del intervalo                        → FALSE
    sin límites                                → "NOT_AVAILABLE"

realized_forecast_quality (desempeño REAL, no confundir con forecast_quality
de 4B que es calidad esperada antes de conocer el resultado):
    EXCELLENT si percentage_error <= 10
    GOOD      si percentage_error <= 25
    FAIR      si percentage_error <= 50
    POOR      si percentage_error > 50
    Caso actual == 0: predicted == 0 → EXCELLENT; si no → POOR
    (una predicción distinta de cero frente a un cero real es un fallo
    claro; documentado).
    NOT_EVALUATED si no hay validación.
"""

from __future__ import annotations

from typing import Any, Dict, Union

# Umbral de "precisión aceptable": |error| <= 5% del valor real → ACCURATE.
# Documentado y configurable vía CLI (--accuracy-threshold-pct).
ACCURACY_THRESHOLD_PCT = 5.0

# Umbrales de calidad realizada sobre percentage_error (documentados).
REALIZED_QUALITY_THRESHOLDS = {
    "EXCELLENT": 10.0,
    "GOOD": 25.0,
    "FAIR": 50.0,
}

NOT_AVAILABLE = "NOT_AVAILABLE"


def compute_errors(
    predicted: float, actual: float, accuracy_threshold_pct: float = ACCURACY_THRESHOLD_PCT
) -> Dict[str, Any]:
    """Errores y sesgo entre predicción y valor real."""
    absolute_error = abs(predicted - actual)
    signed_error = predicted - actual

    if actual == 0:
        percentage_error: Union[float, str] = NOT_AVAILABLE
        pe_reason = (
            "actual_value es 0: no se puede dividir por cero; "
            "percentage_error = NOT_AVAILABLE."
        )
    else:
        percentage_error = absolute_error / abs(actual) * 100.0
        pe_reason = None

    if actual == 0:
        bias = "ACCURATE" if predicted == 0 else (
            "OVERPREDICTION" if signed_error > 0 else "UNDERPREDICTION"
        )
    else:
        threshold = abs(actual) * (accuracy_threshold_pct / 100.0)
        if abs(signed_error) <= threshold:
            bias = "ACCURATE"
        elif signed_error > 0:
            bias = "OVERPREDICTION"
        else:
            bias = "UNDERPREDICTION"

    return {
        "absolute_error": absolute_error,
        "signed_error": signed_error,
        "percentage_error": percentage_error,
        "percentage_error_reason": pe_reason,
        "bias_direction": bias,
        "accuracy_threshold_pct": accuracy_threshold_pct,
    }


def check_interval(
    actual: float, lower: Any, upper: Any
) -> Union[bool, str]:
    """¿El valor real cayó dentro del intervalo de 4A?"""
    try:
        lo = float(lower) if lower is not None else None
        hi = float(upper) if upper is not None else None
    except (TypeError, ValueError):
        return NOT_AVAILABLE
    if lo is None or hi is None:
        return NOT_AVAILABLE
    return bool(lo <= actual <= hi)


def realized_quality(
    percentage_error: Union[float, str], predicted: float, actual: float
) -> str:
    """Calidad realizada del pronóstico según el error real observado."""
    if isinstance(percentage_error, (int, float)):
        pe = float(percentage_error)
        if pe <= REALIZED_QUALITY_THRESHOLDS["EXCELLENT"]:
            return "EXCELLENT"
        if pe <= REALIZED_QUALITY_THRESHOLDS["GOOD"]:
            return "GOOD"
        if pe <= REALIZED_QUALITY_THRESHOLDS["FAIR"]:
            return "FAIR"
        return "POOR"
    # percentage_error == NOT_AVAILABLE  →  actual_value era 0.
    if actual == 0:
        return "EXCELLENT" if predicted == 0 else "POOR"
    return "NOT_EVALUATED"
