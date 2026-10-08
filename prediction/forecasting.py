"""
ZAYVERO BUSINESS — FASE 4A: Prediction Engine MVP.

`forecasting`: construcción de series temporales y generación del
pronóstico final.

- Solo se usan filas `completed` (las canceladas son reversiones, no
  demanda). Queda registrado en el trace de cada predicción.
- Agregación vectorizada con pandas (groupby + PeriodIndex); sin loops
  por fila.
- El pronóstico final usa el método ganador del backtesting, ajustado
  sobre la serie COMPLETA (incluyendo el periodo de validación, que ya
  es pasado al momento de pronosticar el futuro).
- Intervalo de pronóstico: predicted ± 1.28 * std(residuos de validación)
  (~80% de intervalo empírico). El límite inferior se acota a 0 para
  revenue/cantidad (no existen ventas negativas en demanda proyectada)
  y se documenta en limitations.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from prediction import baselines

# Horizonte de pronóstico por granularidad (periodos hacia adelante).
FORECAST_HORIZON = {"M": 3, "W": 4, "D": 7}

# Mínimo de observaciones para intentar una predicción (antes del split).
MIN_OBSERVATIONS = {"M": 12, "W": 26, "D": 60}

# Continuidad mínima: fracción de periodos esperados con presencia de serie.
MIN_CONTINUITY = 0.5

FREQ_LABEL = {"M": "mes", "W": "semana", "D": "día"}
FREQ_LABEL_PLURAL = {"M": "meses", "W": "semanas", "D": "días"}


def load_completed(parquet_path: str) -> pd.DataFrame:
    """Carga el Parquet normalizado y conserva solo filas `completed`
    con Date válida. Devuelve columnas mínimas: Date, Revenue, Quantity,
    Product, Customer, Country, transaction_status."""
    df = pd.read_parquet(
        parquet_path,
        columns=["Date", "Revenue", "Quantity", "Product", "Customer", "Country", "transaction_status"],
    )
    df = df[df["transaction_status"] == "completed"]
    df = df[df["Date"].notna()].copy()
    df["Date"] = pd.to_datetime(df["Date"]).dt.floor("D")
    return df


def _to_full_series(grouped: pd.Series) -> pd.Series:
    """Completa el rango de periodos entre el primero y el último con 0.0.

    Un periodo sin ventas registradas = 0 (dato observado, no inventado).
    """
    if grouped.empty:
        return grouped
    full_idx = pd.period_range(grouped.index.min(), grouped.index.max(), freq=grouped.index.freq)
    return grouped.reindex(full_idx, fill_value=0.0).astype(float)


def series_global(df: pd.DataFrame, freq: str, value_col: str) -> pd.Series:
    """Serie agregada del negocio completo (entidad GLOBAL)."""
    periods = df["Date"].dt.to_period(freq.upper())
    grouped = df.groupby(periods)[value_col].sum(min_count=1).fillna(0.0)
    grouped.index = pd.PeriodIndex(grouped.index, freq=freq.upper())
    return _to_full_series(grouped)


def series_top_entities(
    df: pd.DataFrame, entity_col: str, top_n: int, freq: str, value_col: str
) -> list[tuple[str, pd.Series]]:
    """Series mensuales por entidad (producto/país), top_n por revenue total.

    Vectorizado: un solo groupby para todas las entidades.
    """
    periods = df["Date"].dt.to_period(freq.upper())
    totals = df.groupby(entity_col)[value_col].sum(min_count=1)
    top_ids = totals.nlargest(top_n).index.tolist()
    sub = df[df[entity_col].isin(top_ids)]
    grouped = sub.groupby([sub["Date"].dt.to_period(freq.upper()), entity_col])[value_col].sum(min_count=1)
    out: list[tuple[str, pd.Series]] = []
    for eid in top_ids:
        try:
            s = grouped.xs(str(eid), level=1)
        except KeyError:
            continue
        s.index = pd.PeriodIndex(s.index, freq=freq.upper())
        out.append((str(eid), _to_full_series(s)))
    return out


def continuity(series: pd.Series) -> float:
    """Fracción de periodos del rango con valor registrado.

    Como la serie ya viene completada con 0.0, la continuidad se mide
    sobre periodos con actividad (> 0) vs. el rango total. Una serie muy
    esporádica (ej: producto vendido en 3 de 24 meses) tiene baja
    continuidad y no debería pronosticarse.
    """
    if len(series) == 0:
        return 0.0
    active = int((series.to_numpy(dtype=float) > 0).sum())
    return active / len(series)


def check_sufficiency(series: pd.Series, freq: str, horizon: int) -> tuple[bool, str | None]:
    """Verifica si hay evidencia suficiente para pronosticar.

    Devuelve (suficiente, motivo_si_no). Nunca se genera un número sin
    evidencia: la honestidad analítica tiene prioridad.
    """
    n = len(series)
    min_obs = MIN_OBSERVATIONS.get(freq, 12)
    if n < min_obs + horizon:
        return False, (
            f"historial de {n} {FREQ_LABEL_PLURAL.get(freq, 'periodos')}; "
            f"se requieren al menos {min_obs + horizon} "
            f"({min_obs} para entrenar + {horizon} para validar)"
        )
    cont = continuity(series)
    if cont < MIN_CONTINUITY:
        return False, (
            f"serie discontinua: solo {cont:.0%} de los periodos tienen "
            f"actividad (mínimo {MIN_CONTINUITY:.0%})"
        )
    return True, None


def forecast_final(train_full: pd.Series, method: str, horizon: int, freq: str) -> np.ndarray:
    """Pronóstico final con el método ganador, ajustado sobre la serie completa."""
    spec = baselines.METHODS[method]
    kwargs = {"freq": freq} if method == "seasonal_naive" else {}
    return spec["fn"](train_full.to_numpy(dtype=float), horizon, **kwargs)


def prediction_interval(
    validation_actual: np.ndarray, validation_predicted: np.ndarray, forecast: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    """Intervalo empírico ±1.28·std(residuos de validación) (~80%).

    El límite inferior se acota a 0 (la demanda proyectada no es negativa).
    """
    resid = np.asarray(validation_actual, dtype=float) - np.asarray(validation_predicted, dtype=float)
    sigma = float(np.std(resid, ddof=1)) if len(resid) > 1 else 0.0
    margin = 1.28 * sigma
    lower = np.maximum(forecast - margin, 0.0)
    upper = forecast + margin
    return lower, upper


def forecast_period_labels(last_period: pd.Period, horizon: int) -> list[str]:
    """Etiquetas de los periodos pronosticados (los `horizon` siguientes)."""
    return [str(last_period + (i + 1)) for i in range(horizon)]
