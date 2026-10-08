"""
ZAYVERO BUSINESS — FASE 4C: tests de Prediction Validation & Learning.

35 tests: matching, estados, métricas, bias, intervalos, realized quality,
agregados, calibración, tendencia, data leakage, trazabilidad y
compatibilidad (no hardcoding, JSON serializable).
"""

import json
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prediction_validation import matching, metrics, performance
from prediction_validation.matching import compute_actual, forecast_periods
from prediction_validation.metrics import (
    check_interval,
    compute_errors,
    realized_quality,
)
from prediction_validation.report import build_validation


def make_df(rows):
    df = pd.DataFrame(
        rows,
        columns=["Date", "transaction_status", "Product", "Country",
                 "Revenue", "Quantity"],
    )
    df["Date"] = pd.to_datetime(df["Date"])
    return df


def make_pred(pid="PRED-T1", ptype="DEMAND_REVENUE", kind="GLOBAL", eid=None,
              period="2026-01 a 2026-03", status="OK", predicted=1000.0,
              lo=800.0, hi=1200.0, conf=70.0, method="naive"):
    return {
        "prediction_id": pid,
        "prediction_type": ptype,
        "entity": {"kind": kind, "id": eid, "label": eid or "negocio completo"},
        "period": period,
        "forecast_horizon": 3,
        "prediction_status": status,
        "predicted_value": predicted,
        "lower_bound": lo,
        "upper_bound": hi,
        "confidence_score": conf,
        "trend": "STABLE",
        "decline_risk": "LOW",
        "stockout_status": "NOT_AVAILABLE",
        "method": method,
        "training_period": "2025-01 a 2025-12",
        "validation_period": "2025-10 a 2025-12",
        "metrics": {"mae": 50.0, "rmse": 60.0, "mape": 10.0},
        "evidence_quality": "HIGH",
    }


# Dataset sintético: enero–marzo 2026 con valores conocidos.
BASE_ROWS = [
    ("2026-01-05", "completed", "P1", "ES", 400.0, 10),
    ("2026-01-20", "completed", "P1", "ES", 600.0, 20),
    ("2026-02-10", "completed", "P1", "ES", 500.0, 15),
    ("2026-03-15", "completed", "P1", "ES", 300.0, 12),
    ("2026-02-12", "completed", "P2", "ES", 900.0, 30),   # otro producto
    ("2026-01-08", "cancelled", "P1", "ES", 999.0, 99),   # cancelada: se ignora
    ("2026-04-01", "completed", "P1", "ES", 200.0, 5),    # extiende el rango
]


class TestMatching(unittest.TestCase):
    def test_matching_correcto(self):
        df = make_df(BASE_ROWS)
        info = compute_actual(df, make_pred(period="2026-01 a 2026-03"))
        self.assertEqual(info["status"], "READY")
        # P1: 400+600+500+300 = 1800; P2 (feb): 900 → total 2700.
        # La cancelada de 999 no cuenta.
        self.assertAlmostEqual(info["actual_value"], 2700.0)

    def test_matching_incorrecto_tipo(self):
        df = make_df(BASE_ROWS)
        info = compute_actual(df, make_pred(ptype="DEMAND_DESCONOCIDA"))
        self.assertEqual(info["status"], "INVALID")

    def test_periodo_diferente_no_se_mezcla(self):
        df = make_df(BASE_ROWS)
        # Predicción solo para febrero: debe usar solo febrero
        # (P1 500 + P2 900 = 1400), aunque enero y marzo tengan otros valores.
        info = compute_actual(df, make_pred(period="2026-02 a 2026-02"))
        self.assertEqual(info["status"], "READY")
        self.assertAlmostEqual(info["actual_value"], 1400.0)

    def test_entidad_producto(self):
        df = make_df(BASE_ROWS)
        info = compute_actual(
            df, make_pred(kind="PRODUCT", eid="P2", period="2026-02 a 2026-02"))
        self.assertEqual(info["status"], "READY")
        self.assertAlmostEqual(info["actual_value"], 900.0)

    def test_entidad_desconocida_invalida(self):
        df = make_df(BASE_ROWS)
        info = compute_actual(df, make_pred(kind="ALIEN"))
        self.assertEqual(info["status"], "INVALID")

    def test_periodo_no_parseable_invalido(self):
        df = make_df(BASE_ROWS)
        info = compute_actual(df, make_pred(period="ayer"))
        self.assertEqual(info["status"], "INVALID")

    def test_pending_periodo_futuro(self):
        df = make_df(BASE_ROWS)  # termina 2026-03-15
        v = build_validation(make_pred(period="2026-04 a 2026-06"), 0, df,
                             {}, 5.0, "2026-01-01T00:00:00+00:00", "2026-03-15")
        self.assertEqual(v["validation_status"], "PENDING")
        self.assertIsNone(v["actual_value"])

    def test_pending_periodo_parcial(self):
        # La semana contiene el último dato: no es observable completa.
        df = make_df([("2026-01-05", "completed", "P1", "ES", 100.0, 1)])
        v = build_validation(
            make_pred(period="2026-01-05/2026-01-11 a 2026-01-05/2026-01-11"),
            0, df, {}, 5.0, "2026-01-01T00:00:00+00:00", "2026-01-05")
        self.assertEqual(v["validation_status"], "PENDING")

    def test_periodo_sin_actividad_es_cero(self):
        # Febrero observable (el dataset llega a marzo) pero sin filas de P1
        # → 0.0 observado, igual que en FASE 4A (no inventado).
        rows = [
            ("2026-01-05", "completed", "P1", "ES", 400.0, 10),
            ("2026-03-15", "completed", "P1", "ES", 300.0, 12),
        ]
        df2 = make_df(rows)
        info = compute_actual(
            df2, make_pred(kind="PRODUCT", eid="P1",
                           period="2026-02 a 2026-02"))
        self.assertEqual(info["status"], "READY")
        self.assertAlmostEqual(info["actual_value"], 0.0)

    def test_parse_semanal(self):
        periods = forecast_periods("2026-01-05/2026-01-11 a 2026-01-12/2026-01-18")
        self.assertIsNotNone(periods)
        self.assertEqual(len(periods), 2)
        self.assertEqual(periods[0].freqstr, "W-SUN")

    def test_parse_mensual(self):
        periods = forecast_periods("2026-01 a 2026-03")
        self.assertEqual(len(periods), 3)
        self.assertEqual(periods[0].freqstr, "M")


class TestEstados(unittest.TestCase):
    def test_validated(self):
        df = make_df(BASE_ROWS)
        v = build_validation(make_pred(), 0, df, {}, 5.0,
                             "2026-01-01T00:00:00+00:00", "2026-03-15")
        self.assertEqual(v["validation_status"], "VALIDATED")
        self.assertAlmostEqual(v["actual_value"], 2700.0)
        self.assertAlmostEqual(v["predicted_value"], 1000.0)

    def test_not_available_insufficient_data(self):
        df = make_df(BASE_ROWS)
        v = build_validation(
            make_pred(status="INSUFFICIENT_DATA", predicted=None), 0, df, {},
            5.0, "2026-01-01T00:00:00+00:00", "2026-03-15")
        self.assertEqual(v["validation_status"], "NOT_AVAILABLE")
        self.assertIsNone(v["actual_value"])
        self.assertEqual(v["realized_forecast_quality"], "NOT_EVALUATED")

    def test_invalid(self):
        df = make_df(BASE_ROWS)
        v = build_validation(make_pred(ptype="XXX"), 0, df, {}, 5.0,
                             "2026-01-01T00:00:00+00:00", "2026-03-15")
        self.assertEqual(v["validation_status"], "INVALID")


class TestMetricas(unittest.TestCase):
    def test_absolute_error(self):
        e = compute_errors(1000.0, 1800.0)
        self.assertAlmostEqual(e["absolute_error"], 800.0)

    def test_signed_error(self):
        e = compute_errors(1000.0, 1800.0)
        self.assertAlmostEqual(e["signed_error"], -800.0)

    def test_percentage_error(self):
        e = compute_errors(1000.0, 1800.0)
        self.assertAlmostEqual(e["percentage_error"], 800.0 / 1800.0 * 100)

    def test_actual_cero_percentage_not_available(self):
        e = compute_errors(100.0, 0.0)
        self.assertEqual(e["percentage_error"], "NOT_AVAILABLE")
        self.assertIsNotNone(e["percentage_error_reason"])

    def test_overprediction(self):
        e = compute_errors(2000.0, 1000.0)
        self.assertEqual(e["bias_direction"], "OVERPREDICTION")

    def test_underprediction(self):
        e = compute_errors(500.0, 1000.0)
        self.assertEqual(e["bias_direction"], "UNDERPREDICTION")

    def test_accurate_dentro_umbral(self):
        # 1030 vs 1000 → 3% <= 5% → ACCURATE
        e = compute_errors(1030.0, 1000.0, accuracy_threshold_pct=5.0)
        self.assertEqual(e["bias_direction"], "ACCURATE")

    def test_interval_hit_true(self):
        self.assertTrue(check_interval(1000.0, 800.0, 1200.0))

    def test_interval_hit_false(self):
        self.assertFalse(check_interval(1500.0, 800.0, 1200.0))

    def test_interval_hit_borde(self):
        self.assertTrue(check_interval(800.0, 800.0, 1200.0))

    def test_interval_not_available(self):
        self.assertEqual(check_interval(1000.0, None, 1200.0), "NOT_AVAILABLE")
        self.assertEqual(check_interval(1000.0, None, None), "NOT_AVAILABLE")

    def test_realized_quality(self):
        self.assertEqual(realized_quality(8.0, 1000.0, 900.0), "EXCELLENT")
        self.assertEqual(realized_quality(20.0, 1000.0, 900.0), "GOOD")
        self.assertEqual(realized_quality(40.0, 1000.0, 900.0), "FAIR")
        self.assertEqual(realized_quality(80.0, 1000.0, 900.0), "POOR")

    def test_realized_quality_actual_cero(self):
        self.assertEqual(
            realized_quality("NOT_AVAILABLE", 0.0, 0.0), "EXCELLENT")
        self.assertEqual(
            realized_quality("NOT_AVAILABLE", 100.0, 0.0), "POOR")


class TestPerformance(unittest.TestCase):
    def _vals(self, n=4):
        df = make_df(BASE_ROWS)
        out = []
        for i in range(n):
            v = build_validation(
                make_pred(pid="PRED-T%d" % i, predicted=1000.0 + i * 100,
                          method="naive" if i % 2 == 0 else "moving_average"),
                i, df, {}, 5.0, "2026-01-01T00:00:00+00:00", "2026-03-15")
            out.append(v)
        return out

    def test_performance_aggregate(self):
        agg = performance.aggregate(self._vals())
        self.assertEqual(agg["count_validated"], 4)
        self.assertIsInstance(agg["mean_absolute_error"], float)
        self.assertIsInstance(agg["rmse"], float)
        self.assertAlmostEqual(
            agg["overprediction_rate"] + agg["underprediction_rate"]
            + agg["accuracy_rate"], 1.0)

    def test_insufficient_validation_data(self):
        agg = performance.aggregate([])
        self.assertEqual(agg["count_validated"], 0)
        self.assertEqual(agg["mean_absolute_error"], "INSUFFICIENT_DATA")
        # Nunca cero como sustituto.
        self.assertNotEqual(agg["mean_absolute_error"], 0)

    def test_performance_by_method(self):
        bym = performance.performance_by_method(self._vals())
        self.assertIn("naive", bym)
        self.assertIn("moving_average", bym)
        self.assertEqual(bym["naive"]["count_validated"], 2)

    def test_performance_by_type(self):
        df = make_df(BASE_ROWS)
        vs = [
            build_validation(make_pred(pid="PRED-R1", ptype="DEMAND_REVENUE"),
                             0, df, {}, 5.0, "t", "2026-03-15"),
            build_validation(make_pred(pid="PRED-Q1", ptype="DEMAND_QUANTITY",
                                       predicted=50.0),
                             1, df, {}, 5.0, "t", "2026-03-15"),
        ]
        byt = performance.performance_by_type(vs)
        self.assertIn("DEMAND_REVENUE", byt)
        self.assertIn("DEMAND_QUANTITY", byt)

    def test_confidence_calibration(self):
        df = make_df(BASE_ROWS)
        vs = [
            build_validation(make_pred(pid="PRED-H", conf=80.0), 0, df, {},
                             5.0, "t", "2026-03-15"),
            build_validation(make_pred(pid="PRED-L", conf=30.0), 1, df, {},
                             5.0, "t", "2026-03-15"),
        ]
        cal = performance.confidence_calibration(vs)
        self.assertEqual(cal["HIGH_CONFIDENCE"]["count_validated"], 1)
        self.assertEqual(cal["LOW_CONFIDENCE"]["count_validated"], 1)
        self.assertEqual(cal["MEDIUM_CONFIDENCE"]["count_validated"], 0)

    def test_performance_trend_degrading(self):
        # 4 validadas con error creciente en el tiempo.
        vals = []
        for i, pe_target in enumerate([5.0, 8.0, 30.0, 40.0]):
            actual = 1000.0
            predicted = actual * (1 + pe_target / 100.0)
            v = {
                "validation_status": "VALIDATED",
                "percentage_error": pe_target,
                "forecast_period": "2026-%02d a 2026-%02d" % (i + 1, i + 1),
                "bias_direction": "OVERPREDICTION",
                "absolute_error": predicted - actual,
                "signed_error": predicted - actual,
                "interval_hit": True,
            }
            vals.append(v)
        trend = performance.performance_trend(vals)
        self.assertEqual(trend["trend"], "PREDICTION_PERFORMANCE_DEGRADING")

    def test_model_status_insufficient(self):
        st = performance.model_performance_status(
            [], {"mean_percentage_error": "INSUFFICIENT_DATA",
                 "interval_hit_rate": "INSUFFICIENT_DATA"},
            {"trend": "INSUFFICIENT_DATA"})
        self.assertEqual(st, "INSUFFICIENT_DATA")

    def test_model_status_degrading(self):
        vals = [{"validation_status": "VALIDATED"}] * 4
        st = performance.model_performance_status(
            vals, {"mean_percentage_error": 60.0, "interval_hit_rate": 1.0},
            {"trend": "PREDICTION_PERFORMANCE_DEGRADING"})
        self.assertEqual(st, "DEGRADING")

    def test_model_status_healthy(self):
        vals = [{"validation_status": "VALIDATED"}] * 4
        st = performance.model_performance_status(
            vals, {"mean_percentage_error": 10.0, "interval_hit_rate": 1.0},
            {"trend": "STABLE"})
        self.assertEqual(st, "HEALTHY")


class TestIntegridad(unittest.TestCase):
    def test_no_data_leakage(self):
        # Filas fuera del horizonte no deben afectar el valor real.
        df_base = make_df(BASE_ROWS)
        noisy = [("2026-05-01", "completed", "P1", "ES", 77777.0, 1)]
        df_noisy = make_df(BASE_ROWS + noisy)
        a = compute_actual(df_base, make_pred(period="2026-01 a 2026-01"))
        b = compute_actual(df_noisy, make_pred(period="2026-01 a 2026-01"))
        self.assertAlmostEqual(a["actual_value"], b["actual_value"])
        self.assertAlmostEqual(a["actual_value"], 1000.0)

    def test_no_hardcoding_datos_sinteticos(self):
        # El motor funciona con datos totalmente sintéticos (no depende de
        # constantes del dataset UCI).
        df = make_df([("2030-06-10", "completed", "ZX", "XX", 42.0, 2),
                      ("2030-07-05", "completed", "ZX", "XX", 1.0, 1)])
        info = compute_actual(
            df, make_pred(kind="PRODUCT", eid="ZX",
                          period="2030-06 a 2030-06"))
        self.assertEqual(info["status"], "READY")
        self.assertAlmostEqual(info["actual_value"], 42.0)

    def test_traceability(self):
        df = make_df(BASE_ROWS)
        v = build_validation(make_pred(), 0, df, {}, 5.0,
                             "2026-01-01T00:00:00+00:00", "2026-03-15")
        trace = v["trace"]
        for key in ("prediction_id", "source_prediction", "actual_data_source",
                    "matched_period", "matching_rule", "source_fields",
                    "formulas", "thresholds", "validation_timestamp"):
            self.assertIn(key, trace, "falta %s en trace" % key)

    def test_json_serializable(self):
        df = make_df(BASE_ROWS)
        v = build_validation(make_pred(), 0, df, {}, 5.0,
                             "2026-01-01T00:00:00+00:00", "2026-03-15")
        json.dumps(v, ensure_ascii=False)  # no debe lanzar

    def test_original_4a_no_modificado(self):
        df = make_df(BASE_ROWS)
        pred = make_pred()
        snapshot = json.dumps(pred, sort_keys=True)
        build_validation(pred, 0, df, {}, 5.0,
                         "2026-01-01T00:00:00+00:00", "2026-03-15")
        self.assertEqual(json.dumps(pred, sort_keys=True), snapshot)


if __name__ == "__main__":
    unittest.main()
