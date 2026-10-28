"""
ZAYVERO BUSINESS — FASE 4B: forecast_quality.

Clasificación determinista de la calidad de una predicción de FASE 4A.

REGLAS (reproducibles, documentadas):
---------------------------------------------------------------
INSUFFICIENT
    prediction_status != 'OK'
    (no existe un número numérico que calificar)

LOW  (si se cumple ALGUNA)
    - confidence_score < 40
    - evidence_quality == 'LOW'
    - MAPE válido y > 60
    - amplitud del intervalo > 200% del valor central
      (el intervalo es tan amplio que el valor central apenas orienta)

HIGH (si se cumplen TODAS)
    - confidence_score >= 70
    - evidence_quality == 'HIGH'
    - MAPE válido y <= 35
    - amplitud del intervalo <= 60% del valor central
    - n_periods (historial) >= 18

MODERATE
    - cualquier otro caso con prediction_status == 'OK'

Nada se asigna arbitrariamente: cada etiqueta viene con sus razones.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

VALID_QUALITIES = ("HIGH", "MODERATE", "LOW", "INSUFFICIENT")


def _mape(pred: Dict[str, Any]) -> Tuple[float | None, bool]:
    metrics = pred.get("metrics") or {}
    value = metrics.get("mape")
    valid = bool(metrics.get("mape_valid")) and value is not None
    return (float(value) if valid else None, valid)


def _interval_width_pct(pred: Dict[str, Any]) -> float | None:
    try:
        lower = pred.get("lower_bound")
        upper = pred.get("upper_bound")
        center = pred.get("predicted_value")
        if lower is None or upper is None or not center:
            return None
        width = float(upper) - float(lower)
        if width < 0:
            return None
        return width / abs(float(center)) * 100.0
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def classify_quality(pred: Dict[str, Any]) -> Tuple[str, List[str], Dict[str, Any]]:
    """Devuelve (forecast_quality, razones, evidencia_usada)."""
    status = pred.get("prediction_status")
    if status != "OK":
        return (
            "INSUFFICIENT",
            ["la predicción de FASE 4A no generó un valor numérico (status=%s)" % status],
            {"prediction_status": status},
        )

    confidence = float(pred.get("confidence_score") or 0.0)
    evidence_quality = pred.get("evidence_quality")
    mape, mape_valid = _mape(pred)
    width_pct = _interval_width_pct(pred)
    trace = pred.get("trace") or {}
    n_periods = trace.get("n_periods")

    reasons: List[str] = []
    evidence = {
        "confidence_score": confidence,
        "evidence_quality": evidence_quality,
        "mape": mape,
        "mape_valid": mape_valid,
        "interval_width_pct": width_pct,
        "n_periods": n_periods,
    }

    low_flags: List[str] = []
    if confidence < 40:
        low_flags.append("confidence_score %.1f < 40" % confidence)
    if evidence_quality == "LOW":
        low_flags.append("evidence_quality == LOW")
    if mape_valid and mape is not None and mape > 60:
        low_flags.append("MAPE válido %.1f%% > 60" % mape)
    if width_pct is not None and width_pct > 200:
        low_flags.append("amplitud del intervalo %.1f%% > 200%%" % width_pct)
    if low_flags:
        reasons.append("calidad LOW por: " + "; ".join(low_flags))
        return "LOW", reasons, evidence

    high_checks: List[str] = []
    if confidence >= 70:
        high_checks.append("confidence_score %.1f >= 70" % confidence)
    if evidence_quality == "HIGH":
        high_checks.append("evidence_quality == HIGH")
    if mape_valid and mape is not None and mape <= 35:
        high_checks.append("MAPE válido %.1f%% <= 35" % mape)
    if width_pct is not None and width_pct <= 60:
        high_checks.append("amplitud del intervalo %.1f%% <= 60%%" % width_pct)
    if isinstance(n_periods, (int, float)) and n_periods >= 18:
        high_checks.append("historial de %d periodos >= 18" % int(n_periods))

    if len(high_checks) == 5:
        reasons.append("calidad HIGH: " + "; ".join(high_checks))
        return "HIGH", reasons, evidence

    reasons.append("calidad MODERATE: no cumple todas las condiciones de HIGH "
                   "ni ninguna de LOW")
    return "MODERATE", reasons, evidence
