"""
ZAYVERO BUSINESS — FASE 4A: Prediction Engine MVP.

`confidence`: confidence_score 0-100, documentado y no arbitrario.

FÓRMULA (pesos fijos, cada componente en 0..1):

    confidence = 100 * (0.30*H + 0.25*S + 0.25*E + 0.10*T + 0.05*Sea + 0.05*Q)

    H   historial:      min(1, n_train / 24). Más historial = más evidencia.
    S   estabilidad:    1 / (1 + CV), CV = std/mean del train.
                        Series erráticas bajan la confianza.
    E   error:          max(0, 1 - RMSE_val / mean(|val|)).
                        Si el error de validación es del tamaño de la
                        propia señal, la confianza colapsa.
    T   consistencia:   fracción de cambios periodo-a-periodo recientes
                        con el mismo signo que la tendencia detectada
                        (1.0 si la tendencia es STABLE y CV reciente bajo).
    Sea estacionalidad: 1 si el método ganador fue seasonal_naive
                        (evidencia de patrón estacional), 0 en otro caso.
    Q   calidad/cont.:  fracción de periodos con actividad en el train.

Cada predicción conserva los componentes (factors) para auditoría.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

WEIGHTS = {"H": 0.30, "S": 0.25, "E": 0.25, "T": 0.10, "Sea": 0.05, "Q": 0.05}


def _safe_mean(values: np.ndarray) -> float:
    m = float(np.mean(np.abs(values))) if values.size else 0.0
    return m


def confidence_score(
    train: pd.Series,
    validation: pd.Series,
    rmse_val: float,
    trend: str,
    method: str,
) -> tuple[float, dict]:
    """Calcula el confidence_score 0-100 y sus factores."""
    t = train.to_numpy(dtype=float)
    v = validation.to_numpy(dtype=float)

    n_train = len(t)
    H = min(1.0, n_train / 24.0)

    mean_t = _safe_mean(t)
    cv = (float(np.std(t, ddof=1)) / mean_t) if (mean_t > 0 and n_train > 1) else 1.0
    S = 1.0 / (1.0 + cv)

    mean_v = _safe_mean(v)
    E = max(0.0, 1.0 - (rmse_val / mean_v)) if mean_v > 0 else 0.0

    # Consistencia de tendencia: últimos 6 cambios de train.
    diffs = np.diff(t[-7:]) if n_train >= 7 else np.diff(t)
    if trend == "UPWARD":
        T = float(np.mean(diffs > 0)) if diffs.size else 0.5
    elif trend == "DOWNWARD":
        T = float(np.mean(diffs < 0)) if diffs.size else 0.5
    elif trend == "STABLE":
        recent = t[-6:] if n_train >= 6 else t
        m_r = _safe_mean(recent)
        cv_r = (float(np.std(recent, ddof=1)) / m_r) if (m_r > 0 and len(recent) > 1) else 1.0
        T = max(0.0, 1.0 - cv_r)
    else:  # UNSTABLE / INSUFFICIENT_DATA
        T = 0.0

    Sea = 1.0 if method == "seasonal_naive" else 0.0

    active = int((t > 0).sum())
    Q = (active / n_train) if n_train else 0.0

    factors = {"H": round(H, 3), "S": round(S, 3), "E": round(E, 3),
               "T": round(T, 3), "Sea": Sea, "Q": round(Q, 3)}
    score = 100.0 * sum(WEIGHTS[k] * factors[k] for k in WEIGHTS)
    return round(float(score), 1), factors


def evidence_quality(confidence: float, n_train: int, continuity: float) -> str:
    """Calidad de evidencia derivada de la confianza y el historial."""
    if n_train < 12 or continuity < 0.5:
        return "LOW"
    if confidence >= 70:
        return "HIGH"
    if confidence >= 40:
        return "MEDIUM"
    return "LOW"
