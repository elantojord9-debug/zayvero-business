"""
ZAYVERO BUSINESS — FASE 4A: Prediction Engine MVP.

`baselines`: métodos de pronóstico estadísticos, simples y explicables.

PRINCIPIO: una predicción NO es un hecho. Estos métodos son baselines
transparentes (sin cajas negras): cada uno tiene una fórmula documentada
y un requisito mínimo de historial. El motor (backtesting) elige entre
ellos según rendimiento real sobre datos pasados, nunca por preferencia.

Métodos disponibles:
    naive          : repite el último valor observado.
    moving_average : promedio de los últimos k valores.
    rolling_median : mediana de los últimos k valores (robusta a picos).
    seasonal_naive : repite el valor del mismo periodo del ciclo anterior
                     (requiere al menos 2 ciclos completos de historial).

Todos reciben un vector 1-D `train` (numpy) y un `horizon` entero, y
devuelven un vector 1-D de longitud `horizon` con el pronóstico.
"""

from __future__ import annotations

import numpy as np

# Ventana por defecto de los métodos de promedio móvil.
_DEFAULT_WINDOW = 3

# Ciclos estacionales por granularidad (periodos por ciclo).
SEASONAL_CYCLE = {"M": 12, "W": 52, "D": 7}


def naive(train: np.ndarray, horizon: int, **kwargs) -> np.ndarray:
    """Repite el último valor observado.

    Fórmula: y_hat(t+i) = y(t) para i = 1..horizon.
    """
    last = float(train[-1])
    return np.full(horizon, last, dtype=float)


def moving_average(train: np.ndarray, horizon: int, window: int = _DEFAULT_WINDOW, **kwargs) -> np.ndarray:
    """Promedio aritmético de los últimos `window` valores.

    Fórmula: y_hat(t+i) = mean(y(t-window+1) .. y(t)) para i = 1..horizon.
    """
    value = float(np.mean(train[-window:]))
    return np.full(horizon, value, dtype=float)


def rolling_median(train: np.ndarray, horizon: int, window: int = _DEFAULT_WINDOW, **kwargs) -> np.ndarray:
    """Mediana de los últimos `window` valores (robusta ante picos).

    Fórmula: y_hat(t+i) = median(y(t-window+1) .. y(t)) para i = 1..horizon.
    """
    value = float(np.median(train[-window:]))
    return np.full(horizon, value, dtype=float)


def seasonal_naive(
    train: np.ndarray, horizon: int, cycle: int | None = None, freq: str = "M", **kwargs
) -> np.ndarray:
    """Repite el valor del mismo periodo del ciclo anterior.

    Fórmula (frecuencia mensual, ciclo=12):
        y_hat(t+i) = y(t - 12 + ((i-1) mod 12) + 1)
    Requiere al menos 2 ciclos completos de historial (ver `eligible`).
    """
    if cycle is None:
        cycle = SEASONAL_CYCLE.get(freq, 12)
    if len(train) < 2 * cycle:
        raise ValueError(
            f"seasonal_naive requiere al menos {2 * cycle} observaciones "
            f"(2 ciclos); recibido {len(train)}."
        )
    base = train[-cycle:]
    idx = np.arange(horizon) % cycle
    return base[idx].astype(float)


# Registro de métodos: orden = desempate por simplicidad cuando el
# backtesting produce empates de RMSE.
METHODS = {
    "naive": {
        "fn": naive,
        "min_train": 1,
        "description": "Repite el último valor observado.",
    },
    "moving_average": {
        "fn": moving_average,
        "min_train": 3,
        "description": "Promedio de los últimos 3 periodos.",
    },
    "rolling_median": {
        "fn": rolling_median,
        "min_train": 3,
        "description": "Mediana de los últimos 3 periodos (robusta a picos).",
    },
    "seasonal_naive": {
        "fn": seasonal_naive,
        "min_train": None,  # depende de la frecuencia: 2 * ciclo
        "description": "Repite el mismo periodo del ciclo anterior.",
    },
}


def eligible_methods(n_train: int, freq: str) -> list[str]:
    """Métodos elegibles dado el historial disponible y la frecuencia."""
    cycle = SEASONAL_CYCLE.get(freq, 12)
    out = []
    for name, spec in METHODS.items():
        if name == "seasonal_naive":
            if n_train >= 2 * cycle:
                out.append(name)
        elif n_train >= spec["min_train"]:
            out.append(name)
    return out
