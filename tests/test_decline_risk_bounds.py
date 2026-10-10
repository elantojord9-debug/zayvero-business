"""
ZAYVERO BUSINESS — pruebas de límites exactos de decline_risk.

Cubre los bordes documentados en docs/decline-risk-bounds.md:
- umbrales exactos -20% y -40% y valores justo a cada lado;
- 0% y positivos;
- tope por confianza baja (< 40) y borde exacto en 40.0;
- NaN / no numérico / None (modo histórico);
- elevación histórica de un solo escalón, sin doble elevación;
- determinismo: misma entrada, misma salida.

Datos 100% sintéticos y controlados; no tocan el dataset demo.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
import pandas as pd

from prediction import trends
from prediction.risk import decline_risk


def _series(values):
    return pd.Series(
        np.asarray(values, dtype=float),
        index=pd.period_range("2020-01", periods=len(values), freq="M"),
    )


def _neutral_trend():
    """Serie de crecimiento estable: sin señales históricas de deterioro."""
    s = _series(100 * (1.05 ** np.arange(18)))
    tr = trends.detect_trend(s)
    assert tr["trend"] in ("UPWARD", "STABLE"), tr["trend"]
    return tr, s


def _declining_trend():
    """Serie de caída sostenida: con señales históricas de deterioro."""
    s = _series(1000 - 40 * np.arange(18))
    tr = trends.detect_trend(s)
    assert tr["trend"] == "DOWNWARD", tr["trend"]
    return tr, s


# ------------------------------------------------- umbrales exactos ---

def test_exact_minus_40_is_high():
    tr, s = _neutral_trend()
    assert decline_risk(tr, s, 80.0, forecast_pct=-40.0)["decline_risk"] == "HIGH"


def test_just_below_minus_40_is_high():
    tr, s = _neutral_trend()
    assert decline_risk(tr, s, 80.0, forecast_pct=-40.1)["decline_risk"] == "HIGH"


def test_just_above_minus_40_is_medium():
    tr, s = _neutral_trend()
    assert decline_risk(tr, s, 80.0, forecast_pct=-39.9)["decline_risk"] == "MEDIUM"


def test_exact_minus_20_is_medium():
    tr, s = _neutral_trend()
    assert decline_risk(tr, s, 80.0, forecast_pct=-20.0)["decline_risk"] == "MEDIUM"


def test_just_below_minus_20_is_medium():
    tr, s = _neutral_trend()
    assert decline_risk(tr, s, 80.0, forecast_pct=-20.1)["decline_risk"] == "MEDIUM"


def test_just_above_minus_20_is_low():
    tr, s = _neutral_trend()
    assert decline_risk(tr, s, 80.0, forecast_pct=-19.9)["decline_risk"] == "LOW"


def test_zero_pct_is_low():
    tr, s = _neutral_trend()
    assert decline_risk(tr, s, 80.0, forecast_pct=0.0)["decline_risk"] == "LOW"


def test_positive_pct_is_low():
    tr, s = _neutral_trend()
    assert decline_risk(tr, s, 80.0, forecast_pct=15.0)["decline_risk"] == "LOW"


# --------------------------------------- tope por confianza baja ---

def test_low_confidence_caps_high_to_medium():
    tr, s = _neutral_trend()
    dr = decline_risk(tr, s, 30.0, forecast_pct=-45.0)
    assert dr["decline_risk"] == "MEDIUM"
    assert any("confianza" in sig for sig in dr["signals"])


def test_low_confidence_never_exceeds_medium_even_with_history():
    # MEDIUM por pct + elevación histórica -> HIGH -> tope -> MEDIUM.
    tr, s = _declining_trend()
    dr = decline_risk(tr, s, 30.0, forecast_pct=-25.0)
    assert dr["decline_risk"] == "MEDIUM"


def test_confidence_exactly_40_does_not_cap():
    tr, s = _neutral_trend()
    assert decline_risk(tr, s, 40.0, forecast_pct=-45.0)["decline_risk"] == "HIGH"


def test_high_confidence_keeps_high():
    tr, s = _neutral_trend()
    assert decline_risk(tr, s, 80.0, forecast_pct=-45.0)["decline_risk"] == "HIGH"


# ------------------------------------------- valores no válidos ---

def test_nan_pct_is_insufficient():
    tr, s = _neutral_trend()
    assert decline_risk(tr, s, 80.0, forecast_pct=float("nan"))["decline_risk"] == "INSUFFICIENT_DATA"


def test_non_numeric_pct_is_insufficient():
    tr, s = _neutral_trend()
    assert decline_risk(tr, s, 80.0, forecast_pct="no-numérico")["decline_risk"] == "INSUFFICIENT_DATA"


def test_none_pct_uses_historical_mode():
    tr, s = _neutral_trend()
    dr = decline_risk(tr, s, 80.0, forecast_pct=None)
    assert dr["decline_risk"] == "LOW"
    assert "histórico" in dr["detail"]


def test_historical_mode_downward_is_medium_or_high():
    tr, s = _declining_trend()
    dr = decline_risk(tr, s, 80.0, forecast_pct=None)
    assert dr["decline_risk"] in ("MEDIUM", "HIGH")
    assert "histórico" in dr["detail"]


def test_insufficient_trend_is_insufficient():
    tr = {"trend": "INSUFFICIENT_DATA", "detail": ""}
    s = _series(np.linspace(100, 200, 18))
    assert decline_risk(tr, s, 80.0, forecast_pct=-50.0)["decline_risk"] == "INSUFFICIENT_DATA"


# --------------------------------------- elevación histórica ---

def test_historical_signals_elevate_one_step_only():
    # pct=-10 -> LOW base; con historial de deterioro -> MEDIUM (un escalón).
    tr, s = _declining_trend()
    dr = decline_risk(tr, s, 80.0, forecast_pct=-10.0)
    assert dr["decline_risk"] == "MEDIUM"
    assert any("eleva" in sig for sig in dr["signals"])


def test_historical_signals_do_not_double_elevate():
    # pct=-25 -> MEDIUM base; con historial -> HIGH (un escalón, no dos).
    tr, s = _declining_trend()
    dr = decline_risk(tr, s, 80.0, forecast_pct=-25.0)
    assert dr["decline_risk"] == "HIGH"


def test_no_historical_signals_no_elevation():
    tr, s = _neutral_trend()
    dr = decline_risk(tr, s, 80.0, forecast_pct=-25.0)
    assert dr["decline_risk"] == "MEDIUM"
    assert not any("eleva" in sig for sig in dr["signals"])


# ------------------------------------------------- determinismo ---

def test_boundaries_are_deterministic():
    tr, s = _neutral_trend()
    for pct in (-40.0, -20.0, -40.1, -39.9, -20.1, -19.9, 0.0):
        first = decline_risk(tr, s, 80.0, forecast_pct=pct)["decline_risk"]
        second = decline_risk(tr, s, 80.0, forecast_pct=pct)["decline_risk"]
        assert first == second, pct


def test_case_values_from_audit():
    # Casos reales de la auditoría del diagnóstico demo.
    tr, s = _neutral_trend()
    assert decline_risk(tr, s, 75.0, forecast_pct=-42.0)["decline_risk"] == "HIGH"
    assert decline_risk(tr, s, 75.0, forecast_pct=-43.9)["decline_risk"] == "HIGH"
    assert decline_risk(tr, s, 75.0, forecast_pct=-37.8)["decline_risk"] == "MEDIUM"
    assert decline_risk(tr, s, 75.0, forecast_pct=-69.3)["decline_risk"] == "HIGH"
    assert decline_risk(tr, s, 75.0, forecast_pct=0.0)["decline_risk"] == "LOW"


_TESTS = [
    test_exact_minus_40_is_high,
    test_just_below_minus_40_is_high,
    test_just_above_minus_40_is_medium,
    test_exact_minus_20_is_medium,
    test_just_below_minus_20_is_medium,
    test_just_above_minus_20_is_low,
    test_zero_pct_is_low,
    test_positive_pct_is_low,
    test_low_confidence_caps_high_to_medium,
    test_low_confidence_never_exceeds_medium_even_with_history,
    test_confidence_exactly_40_does_not_cap,
    test_high_confidence_keeps_high,
    test_nan_pct_is_insufficient,
    test_non_numeric_pct_is_insufficient,
    test_none_pct_uses_historical_mode,
    test_historical_mode_downward_is_medium_or_high,
    test_insufficient_trend_is_insufficient,
    test_historical_signals_elevate_one_step_only,
    test_historical_signals_do_not_double_elevate,
    test_no_historical_signals_no_elevation,
    test_boundaries_are_deterministic,
    test_case_values_from_audit,
]


def main():
    for t in _TESTS:
        t()
    print("\n%d/%d pruebas de límites decline_risk OK" % (len(_TESTS), len(_TESTS)))


if __name__ == "__main__":
    main()
