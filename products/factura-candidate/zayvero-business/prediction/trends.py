"""
ZAYVERO BUSINESS — FASE 4A: Prediction Engine MVP.

`trends`: detección de tendencia descriptiva y sustentada.

Procedimiento (sobre la serie de entrenamiento):
    1. Ventana reciente = últimos 6 periodos (o todos si hay menos).
    2. Pendiente por regresión lineal; pendiente relativa =
       pendiente / media de la ventana.
    3. CV reciente = std / media de la ventana.

Reglas (umbrales documentados):
    - n < 6                    → INSUFFICIENT_DATA
    - CV reciente > 0.6        → UNSTABLE (la volatilidad domina;
                                 afirmar dirección sería engañoso)
    - pendiente relativa >  0.05 → UPWARD
    - pendiente relativa < -0.05 → DOWNWARD
    - en otro caso              → STABLE

La tendencia es DESCRIPTIVA: describe lo observado, no afirma causas.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

UNSTABLE_CV_THRESHOLD = 0.6
SLOPE_THRESHOLD = 0.05  # ±5% de la media por periodo
RECENT_WINDOW = 6
MIN_TREND_OBS = 6


def detect_trend(series: pd.Series) -> dict:
    """Detecta la tendencia de la serie. Devuelve dict con trend, slope,
    relative_slope, cv_recent, n_recent y explicación."""
    values = series.to_numpy(dtype=float)
    n = len(values)
    if n < MIN_TREND_OBS:
        return {
            "trend": "INSUFFICIENT_DATA",
            "slope": None,
            "relative_slope": None,
            "cv_recent": None,
            "n_recent": n,
            "detail": f"solo {n} observaciones; se requieren {MIN_TREND_OBS}",
        }

    recent = values[-RECENT_WINDOW:]
    x = np.arange(len(recent))
    slope = float(np.polyfit(x, recent, 1)[0])
    mean_r = float(np.mean(np.abs(recent)))
    rel_slope = (slope / mean_r) if mean_r > 0 else 0.0
    cv_r = (float(np.std(recent, ddof=1)) / mean_r) if (mean_r > 0 and len(recent) > 1) else 0.0

    if cv_r > UNSTABLE_CV_THRESHOLD:
        trend = "UNSTABLE"
        detail = f"CV reciente {cv_r:.2f} > {UNSTABLE_CV_THRESHOLD}: volatilidad domina la dirección"
    elif rel_slope > SLOPE_THRESHOLD:
        trend = "UPWARD"
        detail = f"pendiente relativa +{rel_slope:.1%} por periodo"
    elif rel_slope < -SLOPE_THRESHOLD:
        trend = "DOWNWARD"
        detail = f"pendiente relativa {rel_slope:.1%} por periodo"
    else:
        trend = "STABLE"
        detail = f"pendiente relativa {rel_slope:+.1%} por periodo (dentro de ±{SLOPE_THRESHOLD:.0%})"

    return {
        "trend": trend,
        "slope": round(slope, 4),
        "relative_slope": round(rel_slope, 4),
        "cv_recent": round(cv_r, 4),
        "n_recent": len(recent),
        "detail": detail,
    }
