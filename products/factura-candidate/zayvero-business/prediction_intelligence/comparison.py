"""
ZAYVERO BUSINESS — FASE 4B: comparación de magnitud.

Compara el valor predicho por FASE 4A contra una referencia histórica
OBSERVADA (no proyectada) de forma trazable.

Definiciones:
- observed_series: train_last_6 + validation_actual del trace de 4A
  (periodos observados disponibles en el output de 4A).
- recent_actual_value: suma de los últimos `forecast_horizon` valores
  observados (misma escala que predicted_value, que es suma del horizonte).
- historical_baseline: promedio de los valores observados disponibles.
- absolute_change: predicted_value - recent_actual_value
- percentage_change: absolute_change / |recent_actual_value| * 100
  (None si la referencia es cero o falta).

No se confunde cambio histórico observado con cambio esperado futuro.
"""

from __future__ import annotations

from typing import Any, Dict


def _safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        v = float(value)
        return v
    except (TypeError, ValueError):
        return None


def observed_series(pred: Dict[str, Any]) -> list:
    trace = pred.get("trace") or {}
    observed = trace.get("observed_data") or {}
    series = []
    for key in ("train_last_6", "validation_actual"):
        for v in observed.get(key) or []:
            fv = _safe_float(v)
            if fv is not None:
                series.append(fv)
    return series


def compute_comparison(pred: Dict[str, Any]) -> Dict[str, Any]:
    series = observed_series(pred)
    predicted = _safe_float(pred.get("predicted_value"))
    horizon = pred.get("forecast_horizon") or 1
    try:
        horizon = int(horizon)
    except (TypeError, ValueError):
        horizon = 1
    if horizon < 1:
        horizon = 1

    result: Dict[str, Any] = {
        "n_observed_periods": len(series),
        "recent_window_periods": horizon,
        "recent_actual_value": None,
        "historical_baseline": None,
        "absolute_change": None,
        "percentage_change": None,
        "baseline_note": None,
    }
    if not series or predicted is None:
        result["baseline_note"] = (
            "no hay suficientes valores observados disponibles en el "
            "output de FASE 4A para construir una referencia histórica"
        )
        return result

    window = series[-horizon:]
    recent = sum(window)
    baseline = sum(series) / len(series)
    result["recent_actual_value"] = recent
    result["historical_baseline"] = baseline
    result["baseline_note"] = (
        "referencia calculada sobre %d periodos observados disponibles "
        "en el output de FASE 4A (train + validación)" % len(series)
    )
    absolute = predicted - recent
    result["absolute_change"] = absolute
    if recent != 0:
        result["percentage_change"] = absolute / abs(recent) * 100.0
    else:
        result["percentage_change"] = None
    return result
