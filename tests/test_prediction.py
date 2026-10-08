"""
ZAYVERO BUSINESS — FASE 4A: Prediction Engine MVP.

18 pruebas mínimas:
 1. suficiente historial        10. confidence score
 2. historial insuficiente       11. decline risk
 3. tendencia creciente          12. ausencia de inventario
 4. tendencia decreciente        13. stockout_status NOT_AVAILABLE
 5. tendencia estable            14. trace completo
 6. datos inestables            15. predicción reproducible
 7. backtesting                 16. dataset vacío
 8. ausencia de leakage temporal 17. datos con ceros
 9. cálculo de métricas          18. compatibilidad con fases anteriores

Ejecutar: .venv/bin/python tests/test_prediction.py
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prediction import backtesting, baselines, confidence as conf_mod
from prediction import forecasting, metrics, risk, trends
from prediction.report import build_predictions, _build_prediction

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARQUET = os.path.join(BASE, "data", "processed", "demo-retail", "online_retail_II_full.parquet")
HAS_PARQUET = os.path.exists(PARQUET)


def _series(values, start="2020-01", freq="M"):
    return pd.Series(
        np.asarray(values, dtype=float),
        index=pd.period_range(start, periods=len(values), freq=freq),
    )


class TestSufficiency(unittest.TestCase):
    def test_01_sufficient_history(self):
        s = _series(np.linspace(100, 200, 20))
        ok, reason = forecasting.check_sufficiency(s, "M", 3)
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_02_insufficient_history(self):
        s = _series(np.linspace(100, 200, 8))
        ok, reason = forecasting.check_sufficiency(s, "M", 3)
        self.assertFalse(ok)
        self.assertIn("se requieren", reason)
        pred = _build_prediction("PRED-1", "DEMAND_REVENUE", "GLOBAL", None, s, "M", "t", "p")
        self.assertEqual(pred["prediction_status"], "INSUFFICIENT_DATA")
        self.assertIsNone(pred["predicted_value"])
        self.assertIn("no genera", pred["explanation"].lower() + pred["explanation"])

    def test_02b_discontinuous_series(self):
        # Producto vendido en 4 de 24 meses → serie discontinua.
        vals = [0.0] * 24
        for i in (3, 9, 15, 21):
            vals[i] = 500.0
        s = _series(vals)
        ok, reason = forecasting.check_sufficiency(s, "M", 3)
        self.assertFalse(ok)
        self.assertIn("discontinua", reason)


class TestTrends(unittest.TestCase):
    def test_03_upward_trend(self):
        s = _series(100 * (1.15 ** np.arange(18)))
        tr = trends.detect_trend(s)
        self.assertEqual(tr["trend"], "UPWARD")

    def test_04_downward_trend(self):
        s = _series(100 * (0.85 ** np.arange(18)))
        tr = trends.detect_trend(s)
        self.assertEqual(tr["trend"], "DOWNWARD")

    def test_05_stable_trend(self):
        rng = np.random.RandomState(3)
        s = _series(100 + rng.normal(0, 1.5, 18))
        tr = trends.detect_trend(s)
        self.assertEqual(tr["trend"], "STABLE")

    def test_06_unstable_data(self):
        vals = [100, 500, 50, 800, 30, 900, 40, 700, 60, 850, 45, 750]
        s = _series(vals)
        tr = trends.detect_trend(s)
        self.assertEqual(tr["trend"], "UNSTABLE")

    def test_06b_insufficient_for_trend(self):
        s = _series([1, 2, 3])
        tr = trends.detect_trend(s)
        self.assertEqual(tr["trend"], "INSUFFICIENT_DATA")


class TestBacktesting(unittest.TestCase):
    def test_07_backtesting_selects_best(self):
        # Serie perfectamente estacional: seasonal_naive debe ganar.
        base = np.tile([10, 20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120], 3).astype(float)
        s = _series(base)
        bt = backtesting.backtest(s, 3, "M")
        self.assertEqual(bt["best_method"], "seasonal_naive")
        self.assertAlmostEqual(bt["best_metrics"]["rmse"], 0.0, places=6)
        self.assertIn("seasonal_naive", bt["results"])

    def test_08_no_temporal_leakage(self):
        s = _series(np.arange(1, 25, dtype=float))
        train, validation = backtesting.temporal_split(s, 3)
        self.assertLess(train.index.max(), validation.index.min())
        self.assertEqual(len(validation), 3)
        self.assertEqual(len(train), 21)
        # El método solo ve train: verificar que el pronóstico naive
        # usa el último valor de train, no de validation.
        pred = baselines.naive(train.to_numpy(), 3)
        self.assertTrue(np.all(pred == train.to_numpy()[-1]))
        self.assertNotEqual(float(train.to_numpy()[-1]), float(s.to_numpy()[-1]))

    def test_09_metrics_known_values(self):
        a = np.array([100.0, 200.0, 300.0])
        p = np.array([110.0, 190.0, 310.0])
        self.assertAlmostEqual(metrics.mae(a, p), 10.0)
        self.assertAlmostEqual(metrics.rmse(a, p), 10.0)
        v, valid, _ = metrics.mape(a, p)
        self.assertTrue(valid)
        self.assertAlmostEqual(v, (10 / 100 + 10 / 200 + 10 / 300) / 3 * 100)

    def test_17_zeros_make_mape_invalid(self):
        a = np.array([0.0, 200.0, 300.0])
        p = np.array([5.0, 190.0, 310.0])
        v, valid, reason = metrics.mape(a, p)
        self.assertFalse(valid)
        self.assertIsNone(v)
        self.assertIn("cero", reason)


class TestConfidenceRisk(unittest.TestCase):
    def test_10_confidence_score(self):
        s = _series(100 + 2 * np.arange(24) + np.random.RandomState(1).normal(0, 2, 24))
        bt = backtesting.backtest(s, 3, "M")
        train, validation = bt["train"], bt["validation"]
        c, factors = conf_mod.confidence_score(
            train, validation, bt["best_metrics"]["rmse"], "UPWARD", bt["best_method"]
        )
        self.assertGreaterEqual(c, 0)
        self.assertLessEqual(c, 100)
        self.assertSetEqual(set(factors), {"H", "S", "E", "T", "Sea", "Q"})
        # Fórmula documentada: 100*(0.30H+0.25S+0.25E+0.10T+0.05Sea+0.05Q)
        w = {"H": 0.30, "S": 0.25, "E": 0.25, "T": 0.10, "Sea": 0.05, "Q": 0.05}
        expected = round(100 * sum(w[k] * factors[k] for k in w), 1)
        self.assertAlmostEqual(c, expected, places=1)

    def test_11_decline_risk_high(self):
        vals = 1000 - 40 * np.arange(18)  # caída lineal sostenida y estable
        s = _series(vals)
        tr = trends.detect_trend(s)
        self.assertEqual(tr["trend"], "DOWNWARD")
        dr = risk.decline_risk(tr, s, 80.0)
        self.assertEqual(dr["decline_risk"], "HIGH")
        self.assertTrue(dr["signals"])

    def test_11b_decline_risk_low_on_growth(self):
        s = _series(100 * (1.10 ** np.arange(18)))
        tr = trends.detect_trend(s)
        dr = risk.decline_risk(tr, s, 80.0)
        self.assertEqual(dr["decline_risk"], "LOW")

    def test_12_no_inventory(self):
        st = risk.stockout_status()
        self.assertEqual(st["stockout_status"], "NOT_AVAILABLE")

    def test_13_stockout_not_available_in_prediction(self):
        s = _series(np.linspace(100, 200, 24))
        pred = _build_prediction("PRED-1", "DEMAND_REVENUE", "GLOBAL", None, s, "M", "t", "p")
        self.assertEqual(pred["stockout_status"], "NOT_AVAILABLE")


class TestStructure(unittest.TestCase):
    def test_14_full_trace(self):
        s = _series(np.linspace(100, 200, 24))
        pred = _build_prediction("PRED-000001", "DEMAND_REVENUE", "GLOBAL", None, s, "M", "demo", "x.parquet")
        required = {"prediction_id", "prediction_type", "entity", "period", "forecast_horizon",
                    "prediction_status", "predicted_value", "lower_bound", "upper_bound",
                    "confidence_score", "trend", "decline_risk", "stockout_status", "method",
                    "training_period", "validation_period", "metrics", "evidence_quality",
                    "explanation", "limitations", "trace"}
        self.assertTrue(required.issubset(pred.keys()), required - pred.keys())
        trace_keys = {"dataset", "entity", "source_records", "training_period", "validation_period",
                      "forecast_horizon", "aggregation", "method", "baseline", "metrics",
                      "observed_data", "predicted_data", "confidence_formula"}
        self.assertTrue(trace_keys.issubset(pred["trace"].keys()), trace_keys - pred["trace"].keys())
        # 100% serializable a JSON
        json.dumps(pred, ensure_ascii=False)

    def test_14b_no_prohibited_language(self):
        s = _series(np.linspace(300, 50, 24))
        pred = _build_prediction("PRED-1", "DEMAND_REVENUE", "GLOBAL", None, s, "M", "t", "p")
        text = json.dumps(pred, ensure_ascii=False).lower()
        for word in ("va a ocurrir", "fraude", "robo", "pérdida", "culpabilidad"):
            self.assertNotIn(word, text)

    def test_15_reproducible(self):
        s = _series(100 + 3 * np.arange(24))
        p1 = _build_prediction("PRED-1", "DEMAND_REVENUE", "GLOBAL", None, s, "M", "t", "p")
        p2 = _build_prediction("PRED-1", "DEMAND_REVENUE", "GLOBAL", None, s, "M", "t", "p")
        self.assertEqual(json.dumps(p1, sort_keys=True), json.dumps(p2, sort_keys=True))

    def test_16_empty_dataset(self):
        s = pd.Series([], dtype=float, index=pd.PeriodIndex([], freq="M"))
        ok, reason = forecasting.check_sufficiency(s, "M", 3)
        self.assertFalse(ok)
        pred = _build_prediction("PRED-1", "DEMAND_REVENUE", "GLOBAL", None, s, "M", "t", "p")
        self.assertEqual(pred["prediction_status"], "INSUFFICIENT_DATA")
        json.dumps(pred)


@unittest.skipUnless(HAS_PARQUET, "requiere el Parquet real del dataset demo")
class TestRealData(unittest.TestCase):
    def test_18_compat_previous_phases(self):
        # El motor lee el Parquet de FASE 1C sin modificarlo ni tocar
        # los outputs de 2A/2B/2C/3: solo lectura.
        before = os.path.getmtime(PARQUET)
        with tempfile.TemporaryDirectory() as tmp:
            report = build_predictions(PARQUET, company_id="demo-retail", output_dir=tmp,
                                       top_products=3, top_countries=2)
            after = os.path.getmtime(PARQUET)
            self.assertEqual(before, after)
            self.assertIn("predictions", report)
            self.assertIn("summary", report)
            json.dumps(report, ensure_ascii=False)
            # El reporte se guardó aislado en data/predictions (vía tmp aquí).
            out = os.path.join(tmp, "demo-retail", "online_retail_II_full_predictions.json")
            self.assertTrue(os.path.exists(out))

    def test_real_global_monthly_ok(self):
        df = forecasting.load_completed(PARQUET)
        s = forecasting.series_global(df, "M", "Revenue")
        self.assertGreaterEqual(len(s), 20)
        pred = _build_prediction("PRED-1", "DEMAND_REVENUE", "GLOBAL", None, s, "M", "demo-retail", PARQUET)
        self.assertEqual(pred["prediction_status"], "OK")
        self.assertGreater(pred["predicted_value"], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
