"""
ZAYVERO BUSINESS — FASE 4B: prediction_attention_score (0-100).

Responde: "¿cuánto debería llamar la atención esta predicción?"
NO representa la probabilidad de que la predicción ocurra.

FÓRMULA DOCUMENTADA (determinista, reproducible):
---------------------------------------------------------------
score = magnitud(0-30) + confianza(0-20) + calidad(0-15)
      + incertidumbre(0-10) + riesgo_caida(0-15) + direccion(0-5)
Máximo posible: 95.

magnitud (|percentage_change|):
    >= 50% -> 30 | >= 20% -> 20 | >= 10% -> 10 | None -> 0 | otro -> 5
confianza (confidence_score):
    >= 70 -> 20 | >= 50 -> 12 | >= 40 -> 6 | otro -> 0
calidad (forecast_quality):
    HIGH -> 15 | MODERATE -> 8 | LOW -> 3 | INSUFFICIENT -> 0
incertidumbre (uncertainty_level, inverso):
    LOW -> 10 | MEDIUM -> 5 | HIGH -> 0 | UNKNOWN -> 2
riesgo_caida (decline_risk):
    HIGH -> 15 | MEDIUM -> 7 | LOW -> 0 | INSUFFICIENT_DATA -> 0
direccion (trend UPWARD/DOWNWARD con |pct| >= 10): +5, otro 0

Una predicción con mucha magnitud pero baja confiabilidad NO se vuelve
URGENT automáticamente: la incertidumbre y la calidad reducen el score
y la clasificación final exige condiciones adicionales.

CLASIFICACIÓN (reproducible):
- URGENT:    score >= 75  Y  calidad en (HIGH, MODERATE)
             Y  incertidumbre en (LOW, MEDIUM)
- IMPORTANT: score >= 60
- REVIEW:    score >= 35
- MONITOR:   resto (incluye forecast_quality INSUFFICIENT)
"""

from __future__ import annotations

from typing import Any, Dict, Tuple

VALID_LEVELS = ("URGENT", "IMPORTANT", "REVIEW", "MONITOR")


def _magnitude_points(pct: float | None) -> int:
    if pct is None:
        return 0
    a = abs(pct)
    if a >= 50:
        return 30
    if a >= 20:
        return 20
    if a >= 10:
        return 10
    return 5


def _confidence_points(confidence: float) -> int:
    if confidence >= 70:
        return 20
    if confidence >= 50:
        return 12
    if confidence >= 40:
        return 6
    return 0


_QUALITY_POINTS = {"HIGH": 15, "MODERATE": 8, "LOW": 3, "INSUFFICIENT": 0}
_UNCERTAINTY_POINTS = {"LOW": 10, "MEDIUM": 5, "HIGH": 0, "UNKNOWN": 2}
_RISK_POINTS = {"HIGH": 15, "MEDIUM": 7, "LOW": 0, "INSUFFICIENT_DATA": 0}


def compute_attention(
    forecast_quality: str,
    uncertainty_level: str,
    decline_risk: str,
    trend: str,
    confidence_score: float,
    percentage_change: float | None,
) -> Tuple[int, str, Dict[str, Any]]:
    """Devuelve (attention_score, attention_level, componentes)."""
    components: Dict[str, Any] = {
        "magnitud": _magnitude_points(percentage_change),
        "confianza": _confidence_points(confidence_score),
        "calidad": _QUALITY_POINTS.get(forecast_quality, 0),
        "incertidumbre": _UNCERTAINTY_POINTS.get(uncertainty_level, 0),
        "riesgo_caida": _RISK_POINTS.get(decline_risk, 0),
        "direccion": 0,
    }
    if trend in ("UPWARD", "DOWNWARD") and percentage_change is not None \
            and abs(percentage_change) >= 10:
        components["direccion"] = 5

    if forecast_quality == "INSUFFICIENT":
        return 0, "MONITOR", components

    score = sum(components.values())

    if score >= 75 and forecast_quality in ("HIGH", "MODERATE") \
            and uncertainty_level in ("LOW", "MEDIUM"):
        level = "URGENT"
    elif score >= 60:
        level = "IMPORTANT"
    elif score >= 35:
        level = "REVIEW"
    else:
        level = "MONITOR"
    return score, level, components
