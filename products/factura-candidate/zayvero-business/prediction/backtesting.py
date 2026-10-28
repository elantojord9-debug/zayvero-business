"""
ZAYVERO BUSINESS — FASE 4A: Prediction Engine MVP.

`backtesting`: evaluación temporal honesta de los métodos.

REGLA DE ORO: nunca se mezcla información futura con la de entrenamiento.
El procedimiento es:

    1. Dividir la serie en train (todo menos los últimos `horizon`
       periodos) y validation (los últimos `horizon` periodos).
    2. Ajustar cada método candidato SOLO con train.
    3. Pronosticar `horizon` periodos y comparar contra validation.
    4. Elegir el método con menor RMSE en validation
       (desempate: orden de simplicidad del registro de baselines).

Así, el método elegido demostró su rendimiento sobre datos que no vio
durante su "entrenamiento". Lo que no se puede verificar, no se afirma.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from prediction import baselines
from prediction import metrics


def temporal_split(series: pd.Series, horizon: int) -> tuple[pd.Series, pd.Series]:
    """Divide la serie en train / validation sin mezclar futuro y pasado.

    train = todo excepto los últimos `horizon` periodos.
    validation = los últimos `horizon` periodos.
    Garantía: train.index.max() < validation.index.min().
    """
    if len(series) <= horizon:
        raise ValueError(
            f"serie de {len(series)} periodos insuficiente para "
            f"horizonte de {horizon} (se necesita al menos {horizon + 1})."
        )
    train = series.iloc[:-horizon]
    validation = series.iloc[-horizon:]
    assert train.index.max() < validation.index.min(), "fuga temporal: train y validation se solapan"
    return train, validation


def evaluate_method(
    train: pd.Series, validation: pd.Series, method: str, freq: str
) -> dict:
    """Ajusta un método con train y lo evalúa contra validation.

    Devuelve pronóstico, métricas y validez de MAPE.
    """
    spec = baselines.METHODS[method]
    train_vals = train.to_numpy(dtype=float)
    val_vals = validation.to_numpy(dtype=float)
    horizon = len(validation)

    kwargs = {}
    if method == "seasonal_naive":
        kwargs["freq"] = freq
    predicted = spec["fn"](train_vals, horizon, **kwargs)

    mape_value, mape_valid, mape_reason = metrics.mape(val_vals, predicted)
    return {
        "method": method,
        "predicted": predicted,
        "mae": metrics.mae(val_vals, predicted),
        "rmse": metrics.rmse(val_vals, predicted),
        "mape": mape_value,
        "mape_valid": mape_valid,
        "mape_invalid_reason": mape_reason,
        "relative_rmse": metrics.relative_rmse_vs_mean(val_vals, predicted),
    }


def backtest(series: pd.Series, horizon: int, freq: str) -> dict:
    """Ejecuta backtesting temporal sobre todos los métodos elegibles.

    Devuelve:
        train, validation (series),
        results: {método: evaluación},
        best_method: método con menor RMSE (desempate por simplicidad).
    """
    train, validation = temporal_split(series, horizon)
    candidates = baselines.eligible_methods(len(train), freq)
    results: dict[str, dict] = {}
    for method in candidates:
        results[method] = evaluate_method(train, validation, method, freq)
    best_method = min(
        results,
        key=lambda m: (results[m]["rmse"], list(baselines.METHODS).index(m)),
    )
    return {
        "train": train,
        "validation": validation,
        "results": results,
        "best_method": best_method,
        "best_metrics": {
            "mae": results[best_method]["mae"],
            "rmse": results[best_method]["rmse"],
            "mape": results[best_method]["mape"],
            "mape_valid": results[best_method]["mape_valid"],
            "mape_invalid_reason": results[best_method]["mape_invalid_reason"],
            "relative_rmse": results[best_method]["relative_rmse"],
        },
    }
