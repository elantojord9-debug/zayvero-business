"""
ZAYVERO BUSINESS — FASE 4A: Prediction Engine MVP.

`metrics`: métricas de error para backtesting.

MAE  (Mean Absolute Error):      mean(|actual - predicted|)
RMSE (Root Mean Squared Error):  sqrt(mean((actual - predicted)^2))
MAPE (Mean Absolute Pct Error):  100 * mean(|actual - predicted| / |actual|)

MAPE solo es válido cuando todos los valores reales son distintos de
cero. Con ceros en los reales, la división no está definida: en ese
caso se devuelve (None, False, motivo) en lugar de inventar un número.
"""

from __future__ import annotations

import numpy as np


def mae(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Error absoluto medio."""
    return float(np.mean(np.abs(np.asarray(actual, dtype=float) - np.asarray(predicted, dtype=float))))


def rmse(actual: np.ndarray, predicted: np.ndarray) -> float:
    """Raíz del error cuadrático medio."""
    diff = np.asarray(actual, dtype=float) - np.asarray(predicted, dtype=float)
    return float(np.sqrt(np.mean(diff ** 2)))


def mape(actual: np.ndarray, predicted: np.ndarray) -> tuple[float | None, bool, str | None]:
    """Error porcentual absoluto medio.

    Devuelve (valor, válido, motivo). Si algún valor real es cero,
    MAPE no es válido: se devuelve (None, False, motivo) y NUNCA se
    sustituye por un número inventado.
    """
    a = np.asarray(actual, dtype=float)
    p = np.asarray(predicted, dtype=float)
    if a.size == 0:
        return None, False, "sin valores reales para evaluar"
    if np.any(a == 0):
        n_zero = int(np.sum(a == 0))
        return None, False, f"{n_zero} de {a.size} valores reales son cero; MAPE no definido"
    return float(np.mean(np.abs((a - p) / a)) * 100.0), True, None


def relative_rmse_vs_mean(actual: np.ndarray, predicted: np.ndarray) -> float | None:
    """RMSE dividido por la media de los reales (error relativo).

    Útil cuando MAPE no es válido. Devuelve None si la media es cero.
    """
    a = np.asarray(actual, dtype=float)
    mean = float(np.mean(np.abs(a)))
    if mean == 0:
        return None
    return rmse(a, predicted) / mean
