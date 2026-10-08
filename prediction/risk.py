"""
ZAYVERO BUSINESS — FASE 4A: Prediction Engine MVP.

`risk`: indicadores de riesgo basados en señales observables.

decline_risk (LOW / MEDIUM / HIGH / INSUFFICIENT_DATA):
    Señales consideradas (ninguna afirma certeza):
    - tendencia DOWNWARD en la ventana reciente
    - aceleración negativa (la pendiente reciente es más negativa que
      la de la ventana anterior)
    - magnitud de la caída vs. media histórica
    - estabilidad del descenso (CV bajo = descenso sostenido)

    Reglas:
    - trend INSUFFICIENT_DATA            → INSUFFICIENT_DATA
    - DOWNWARD + descenso estable
      (CV reciente < 0.4) + (aceleración negativa o caída > 25%
      de la media)                      → HIGH
    - DOWNWARD (sin las condiciones de HIGH),
      o UNSTABLE con último valor < 80% de la media reciente → MEDIUM
    - resto                             → LOW

stockout_status:
    El dataset UCI Online Retail II NO contiene inventario confiable
    (no hay columna de stock). ZAYVERO NO inventa un stock actual a
    partir de las ventas. Por tanto, stockout_status = NOT_AVAILABLE
    con explicación explícita. Si un dataset futuro aportara inventario,
    este módulo es el punto de extensión (documentado, no implementado).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STABLE_DECLINE_CV = 0.4
SEVERE_DROP_FRACTION = 0.25


def decline_risk(trend_info: dict, series: pd.Series, confidence: float) -> dict:
    """Calcula el riesgo de caída con señales observables."""
    trend = trend_info.get("trend")
    if trend == "INSUFFICIENT_DATA":
        return {
            "decline_risk": "INSUFFICIENT_DATA",
            "signals": [],
            "detail": "sin tendencia sustentada; no se puede estimar riesgo de caída",
        }

    values = series.to_numpy(dtype=float)
    signals: list[str] = []
    recent = values[-6:] if len(values) >= 6 else values
    mean_r = float(np.mean(np.abs(recent))) if len(recent) else 0.0
    cv_r = (float(np.std(recent, ddof=1)) / mean_r) if (mean_r > 0 and len(recent) > 1) else 0.0

    # Aceleración: pendiente de la mitad reciente vs. mitad anterior.
    acceleration_negative = False
    if len(values) >= 12:
        x1 = np.arange(6)
        s_recent = float(np.polyfit(x1, values[-6:], 1)[0])
        s_prev = float(np.polyfit(x1, values[-12:-6], 1)[0])
        acceleration_negative = s_recent < s_prev < 0 or (s_prev >= 0 > s_recent)
        if acceleration_negative:
            signals.append("aceleración negativa: la caída se intensifica en la ventana reciente")

    drop_fraction = 0.0
    if trend == "DOWNWARD":
        signals.append(f"tendencia descendente ({trend_info.get('detail', '')})")
        if len(values) >= 12:
            older_mean = float(np.mean(np.abs(values[-12:-6])))
            if older_mean > 0:
                drop_fraction = (older_mean - mean_r) / older_mean
                if drop_fraction > 0:
                    signals.append(f"caída de {drop_fraction:.0%} vs. la ventana anterior")

    stable_decline = trend == "DOWNWARD" and cv_r < STABLE_DECLINE_CV
    if stable_decline:
        signals.append(f"descenso estable (CV {cv_r:.2f} < {STABLE_DECLINE_CV})")
    severe = drop_fraction > SEVERE_DROP_FRACTION

    if trend == "DOWNWARD" and stable_decline and (acceleration_negative or severe):
        level = "HIGH"
        detail = "descenso sostenido y estable, con aceleración o magnitud severa"
    elif trend == "DOWNWARD" or (
        trend == "UNSTABLE" and len(values) and values[-1] < 0.8 * mean_r and mean_r > 0
    ):
        level = "MEDIUM"
        if trend == "UNSTABLE":
            signals.append("volatilidad alta con último valor por debajo del 80% de la media reciente")
        detail = "señales de deterioro sin la estabilidad/magnitud de un riesgo alto"
    else:
        level = "LOW"
        detail = "sin señales observables de caída sostenida"

    return {"decline_risk": level, "signals": signals, "detail": detail}


def stockout_status() -> dict:
    """Estado de riesgo de agotamiento.

    El dataset no contiene inventario confiable: NO se fabrica un stock
    actual a partir de ventas. Siempre NOT_AVAILABLE en esta fase.
    """
    return {
        "stockout_status": "NOT_AVAILABLE",
        "detail": (
            "el dataset no contiene información de inventario confiable; "
            "ZAYVERO no estima un stock actual a partir de las ventas"
        ),
    }
