"""
ZAYVERO BUSINESS — FASE 4C: desempeño agregado, calibración y estado.

Todo es descriptivo: 4C mide, no modifica. No hay reentrenamiento,
no hay cambio automático de modelos, no hay AutoML.

Agregados (solo sobre predicciones VALIDATED):
    count_validated, mean_absolute_error, median_absolute_error,
    mean_percentage_error, median_percentage_error (solo con pe válido),
    rmse = sqrt(mean(signed_error^2)), mean_signed_error,
    overprediction_rate, underprediction_rate, accuracy_rate,
    interval_hit_rate (solo con intervalo disponible).

Si no hay suficientes predicciones validadas, las métricas son
"INSUFFICIENT_DATA" — nunca cero como sustituto.

Confidence calibration: grupos por confidence_score de 4A con las mismas
reglas de 4B (HIGH ≥ 70, MEDIUM 40–69, LOW < 40). Solo mide si la alta
confianza tiende a ser más precisa; no modifica el confidence_score.

Performance trend (drift descriptivo): con ≥ 4 validadas con
percentage_error válido, ordenadas por inicio del periodo pronosticado,
se compara la media de la primera mitad contra la segunda mitad:
    segunda > primera * 1.25  → DEGRADING
        ("El error observado ha aumentado respecto a periodos anteriores.")
    segunda < primera * 0.80  → IMPROVING
    en otro caso              → STABLE
No se afirma la causa del cambio.

model_performance_status (determinista):
    INSUFFICIENT_DATA si validadas < 3 (nunca DEGRADING con una sola
        predicción mala).
    DEGRADING si trend == DEGRADING.
    WATCH si trend == STABLE y (mean_percentage_error > 50 o
        interval_hit_rate < 0.5).
    HEALTHY en otro caso.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from typing import Any, Dict, List, Union

from . import matching
from .metrics import NOT_AVAILABLE

INSUFFICIENT = "INSUFFICIENT_DATA"

# Reglas de grupos de confianza (las mismas usadas por 4A/4B).
CONFIDENCE_GROUPS = [("HIGH_CONFIDENCE", 70.0), ("MEDIUM_CONFIDENCE", 40.0)]

# Mínimo de validadas para declarar estado del modelo.
MIN_VALIDATED_FOR_STATUS = 3

# Mínimo de validadas con pe válido para evaluar tendencia.
MIN_VALIDATED_FOR_TREND = 4

# Umbrales de drift (documentados).
DRIFT_DEGRADE_FACTOR = 1.25
DRIFT_IMPROVE_FACTOR = 0.80

# Umbrales de WATCH (documentados).
WATCH_MEAN_PE = 50.0
WATCH_INTERVAL_HIT_RATE = 0.5


def _mean(values: List[float]) -> Union[float, str]:
    if not values:
        return INSUFFICIENT
    return float(sum(values) / len(values))


def _median(values: List[float]) -> Union[float, str]:
    if not values:
        return INSUFFICIENT
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return float((s[mid - 1] + s[mid]) / 2.0) if n % 2 == 0 else float(s[mid])


def aggregate(validated: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Métricas agregadas sobre una lista de PredictionValidation VALIDATED."""
    n = len(validated)
    if n == 0:
        return {
            "count_validated": 0,
            "mean_absolute_error": INSUFFICIENT,
            "median_absolute_error": INSUFFICIENT,
            "mean_percentage_error": INSUFFICIENT,
            "median_percentage_error": INSUFFICIENT,
            "rmse": INSUFFICIENT,
            "mean_signed_error": INSUFFICIENT,
            "overprediction_rate": INSUFFICIENT,
            "underprediction_rate": INSUFFICIENT,
            "accuracy_rate": INSUFFICIENT,
            "interval_hit_rate": INSUFFICIENT,
        }
    ae = [v["absolute_error"] for v in validated]
    se = [v["signed_error"] for v in validated]
    pe = [v["percentage_error"] for v in validated
          if isinstance(v["percentage_error"], (int, float))]
    bias = Counter(v["bias_direction"] for v in validated)
    hits = [v["interval_hit"] for v in validated
            if v["interval_hit"] in (True, False)]
    rmse = math.sqrt(sum(x * x for x in se) / n)
    return {
        "count_validated": n,
        "mean_absolute_error": _mean(ae),
        "median_absolute_error": _median(ae),
        "mean_percentage_error": _mean(pe),
        "median_percentage_error": _median(pe),
        "rmse": rmse,
        "mean_signed_error": _mean(se),
        "overprediction_rate": bias.get("OVERPREDICTION", 0) / n,
        "underprediction_rate": bias.get("UNDERPREDICTION", 0) / n,
        "accuracy_rate": bias.get("ACCURATE", 0) / n,
        "interval_hit_rate": (
            sum(1 for h in hits if h) / len(hits) if hits else INSUFFICIENT
        ),
    }


def _group_key(validated: List[Dict[str, Any]], key_fn) -> Dict[str, Dict[str, Any]]:
    groups: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for v in validated:
        groups[key_fn(v)].append(v)
    return {k: aggregate(vs) for k, vs in sorted(groups.items())}


def performance_by_method(validated: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Desempeño por método de 4A. Solo mide; no cambia el método."""
    return _group_key(validated, lambda v: v.get("method") or "UNKNOWN")


def performance_by_type(validated: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """¿ZAYVERO predice mejor revenue o quantity?"""
    return _group_key(validated, lambda v: v.get("prediction_type") or "UNKNOWN")


def performance_by_entity(validated: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    """Desempeño por tipo de entidad (solo las existentes en las predicciones)."""
    return _group_key(
        validated, lambda v: ((v.get("entity") or {}).get("kind") or "UNKNOWN")
    )


def confidence_group(score: Any) -> str:
    try:
        s = float(score)
    except (TypeError, ValueError):
        return "UNKNOWN_CONFIDENCE"
    if s >= CONFIDENCE_GROUPS[0][1]:
        return "HIGH_CONFIDENCE"
    if s >= CONFIDENCE_GROUPS[1][1]:
        return "MEDIUM_CONFIDENCE"
    return "LOW_CONFIDENCE"


def confidence_calibration(
    validations: List[Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """¿La alta confianza de 4A tiende a ser más precisa? Solo mide."""
    validated = [v for v in validations if v["validation_status"] == "VALIDATED"]
    out: Dict[str, Dict[str, Any]] = {}
    for group in ("HIGH_CONFIDENCE", "MEDIUM_CONFIDENCE", "LOW_CONFIDENCE"):
        vs = [v for v in validated if confidence_group(v.get("confidence_score")) == group]
        pe = [v["percentage_error"] for v in vs
              if isinstance(v["percentage_error"], (int, float))]
        acc = sum(1 for v in vs if v["bias_direction"] == "ACCURATE")
        out[group] = {
            "count_validated": len(vs),
            "mean_percentage_error": _mean(pe),
            "accuracy_rate": (acc / len(vs)) if vs else INSUFFICIENT,
        }
    return out


def _forecast_start(v: Dict[str, Any]):
    periods = matching.forecast_periods(v.get("forecast_period"))
    if not periods:
        return None
    return periods[0].start_time


def performance_trend(validated: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Drift descriptivo del error a lo largo del tiempo. Sin causalidad."""
    vs = [v for v in validated
          if isinstance(v.get("percentage_error"), (int, float))
          and _forecast_start(v) is not None]
    vs.sort(key=_forecast_start)
    if len(vs) < MIN_VALIDATED_FOR_TREND:
        return {
            "trend": INSUFFICIENT,
            "n_evaluated": len(vs),
            "first_half_mean_pe": INSUFFICIENT,
            "second_half_mean_pe": INSUFFICIENT,
            "note": "Se requieren al menos %d validadas con error porcentual "
                    "válido para evaluar tendencia." % MIN_VALIDATED_FOR_TREND,
        }
    half = len(vs) // 2
    first = [v["percentage_error"] for v in vs[:half]]
    second = [v["percentage_error"] for v in vs[half:]]
    m1 = sum(first) / len(first)
    m2 = sum(second) / len(second)
    if m2 > m1 * DRIFT_DEGRADE_FACTOR:
        trend = "PREDICTION_PERFORMANCE_DEGRADING"
        note = ("El error observado ha aumentado respecto a periodos "
                "anteriores (%.1f%% → %.1f%%). No se afirma la causa." % (m1, m2))
    elif m2 < m1 * DRIFT_IMPROVE_FACTOR:
        trend = "PREDICTION_PERFORMANCE_IMPROVING"
        note = "El error observado ha disminuido respecto a periodos anteriores."
    else:
        trend = "STABLE"
        note = "El error observado se mantiene estable entre periodos."
    return {
        "trend": trend,
        "n_evaluated": len(vs),
        "first_half_mean_pe": m1,
        "second_half_mean_pe": m2,
        "note": note,
    }


def model_performance_status(
    validated: List[Dict[str, Any]],
    overview: Dict[str, Any],
    trend: Dict[str, Any],
) -> str:
    """Estado del modelo: HEALTHY / WATCH / DEGRADING / INSUFFICIENT_DATA."""
    if len(validated) < MIN_VALIDATED_FOR_STATUS:
        return INSUFFICIENT
    if trend.get("trend") == "PREDICTION_PERFORMANCE_DEGRADING":
        return "DEGRADING"
    mean_pe = overview.get("mean_percentage_error")
    ihr = overview.get("interval_hit_rate")
    if trend.get("trend") == "STABLE" and (
        (isinstance(mean_pe, (int, float)) and mean_pe > WATCH_MEAN_PE)
        or (isinstance(ihr, (int, float)) and ihr < WATCH_INTERVAL_HIT_RATE)
    ):
        return "WATCH"
    return "HEALTHY"
