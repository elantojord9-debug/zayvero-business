"""
ZAYVERO BUSINESS — FASE 4B: incertidumbre del intervalo.

Utiliza lower_bound / predicted_value / upper_bound de FASE 4A.

REGLAS (documentadas, reproducibles):
---------------------------------------------------------------
interval_width = upper_bound - lower_bound
interval_width_pct = interval_width / |predicted_value| * 100

- LOW:     interval_width_pct <= 60     (intervalo relativamente estrecho)
- MEDIUM:  60 < interval_width_pct <= 150
- HIGH:    interval_width_pct > 150     (intervalo amplio: el valor central
                                        orienta poco; planificar por rango)
- UNKNOWN: faltan los límites

El valor central nunca se presenta solo: siempre va acompañado del
intervalo y de su nivel de incertidumbre.
"""

from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def interval_info(pred: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Devuelve (interval_width, interval_width_pct), o (None, None)."""
    try:
        lower = pred.get("lower_bound")
        upper = pred.get("upper_bound")
        center = pred.get("predicted_value")
        if lower is None or upper is None or center is None:
            return None, None
        lower_f, upper_f, center_f = float(lower), float(upper), float(center)
        width = upper_f - lower_f
        if width < 0 or center_f == 0:
            return None, None
        return width, width / abs(center_f) * 100.0
    except (TypeError, ValueError):
        return None, None


def classify_uncertainty(pred: Dict[str, Any]) -> Tuple[str, str, Dict[str, Any]]:
    """Devuelve (uncertainty_level, explicación, evidencia)."""
    width, width_pct = interval_info(pred)
    evidence = {"interval_width": width, "interval_width_pct": width_pct}
    if width is None or width_pct is None:
        return (
            "UNKNOWN",
            "no existen límites de intervalo en la predicción, por lo que "
            "la incertidumbre no puede evaluarse",
            evidence,
        )
    if width_pct <= 60:
        return (
            "LOW",
            "intervalo relativamente estrecho (amplitud %.1f%% del valor central)" % width_pct,
            evidence,
        )
    if width_pct <= 150:
        return (
            "MEDIUM",
            "intervalo de amplitud moderada (%.1f%% del valor central); "
            "conviene planificar considerando el rango, no solo el valor central" % width_pct,
            evidence,
        )
    return (
        "HIGH",
        "intervalo amplio (%.1f%% del valor central): el valor central orienta "
        "poco; la estimación debe leerse como un rango" % width_pct,
        evidence,
    )
