"""
ZAYVERO BUSINESS — FASE 2A.

`stats`: primitivas estadísticas robustas (vectorizadas con numpy/pandas).

Métodos:
    - MAD (Median Absolute Deviation): medida de dispersión robusta,
      no se deja arrastrar por valores extremos como la desviación estándar.
    - robust_zscore: z = 0.6745 * (x - mediana) / MAD. El factor 0.6745
      hace que el z robusto sea comparable al z clásico en datos normales.
      Si MAD == 0 (serie constante), el z es 0 salvo que x != mediana,
      en cuyo caso se marca como desviación máxima.
    - iqr_bounds: límites Q1 - k*IQR / Q3 + k*IQR (k configurable;
      k=1.5 clásico, k=3.0 para "extremos lejanos", menos ruido).
    - rolling_robust: mediana y MAD móviles (ventana configurable),
      base para detectar picos/caídas temporales sin que el propio
      evento contamine la línea base (la mediana es robusta).

Todo vectorizado: sin loops Python por fila.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

# Factor para que el z robusto sea comparable al z clásico (distribución normal)
_MAD_SCALE = 0.6745


def mad(values: pd.Series | np.ndarray) -> float:
    """Median Absolute Deviation de una serie (robusto a extremos)."""
    v = pd.Series(values).dropna().to_numpy(dtype=float)
    if v.size == 0:
        return float("nan")
    med = np.median(v)
    return float(np.median(np.abs(v - med)))


def robust_zscore(
    values: pd.Series | np.ndarray,
    median: float | None = None,
    mad_value: float | None = None,
) -> np.ndarray:
    """
    z-score robusto vectorizado: 0.6745 * (x - mediana) / MAD.
    Si MAD == 0: devuelve 0 donde x == mediana, e inf con el signo
    de la diferencia donde x != mediana (desviación máxima).
    """
    v = pd.Series(values).to_numpy(dtype=float)
    med = float(np.median(v[~np.isnan(v)])) if median is None else float(median)
    m = mad(v) if mad_value is None else float(mad_value)
    out = np.zeros_like(v, dtype=float)
    if not np.isfinite(m) or m == 0:
        diff = v - med
        with np.errstate(invalid="ignore"):
            signed_inf = np.sign(diff) * np.inf
        out = np.where(np.isnan(v), np.nan, np.where(diff == 0, 0.0,
                       signed_inf))
        return out
    out = _MAD_SCALE * (v - med) / m
    out[np.isnan(v)] = np.nan
    return out


def iqr_bounds(
    values: pd.Series | np.ndarray, k: float = 3.0
) -> tuple[float, float, float, float]:
    """
    Límites por IQR: (Q1, Q3, lower, upper) con lower = Q1 - k*IQR,
    upper = Q3 + k*IQR. k=3.0 por defecto ("extremos lejanos") para
    reducir falsos positivos en datos de retail con colas pesadas.
    """
    v = pd.Series(values).dropna().to_numpy(dtype=float)
    if v.size < 4:
        return (float("nan"),) * 4
    q1, q3 = np.percentile(v, [25, 75])
    iqr = q3 - q1
    return (float(q1), float(q3), float(q1 - k * iqr), float(q3 + k * iqr))


def rolling_robust(
    series: pd.Series, window: int, min_periods: int | None = None
) -> pd.DataFrame:
    """
    Mediana y MAD móviles sobre una serie ordenada cronológicamente.
    Devuelve DataFrame con columnas [value, roll_median, roll_mad, z_robust].
    min_periods por defecto = max(4, window // 2).
    """
    if min_periods is None:
        min_periods = max(4, window // 2)
    roll_med = series.rolling(window=window, min_periods=min_periods).median()
    # MAD móvil: mediana de |x - mediana_móvil| en la ventana
    roll_mad = (
        (series - roll_med)
        .abs()
        .rolling(window=window, min_periods=min_periods)
        .median()
    )
    z = pd.Series(
        robust_zscore(series.to_numpy(), median=None, mad_value=None),
        index=series.index,
    )
    # z con baseline móvil: 0.6745 * (x - roll_med) / roll_mad
    with np.errstate(divide="ignore", invalid="ignore"):
        z_roll = _MAD_SCALE * (series - roll_med) / roll_mad.replace(0, np.nan)
    z_roll = z_roll.where(roll_mad > 0, np.sign(series - roll_med) * np.inf)
    z_roll = z_roll.where(series.notna(), np.nan)
    return pd.DataFrame(
        {
            "value": series,
            "roll_median": roll_med,
            "roll_mad": roll_mad,
            "z_robust": z_roll,
        }
    )
