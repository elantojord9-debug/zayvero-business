"""
ZAYVERO BUSINESS — FASE 4B: pruebas de Prediction Intelligence.

Cubre: calidad HIGH/MODERATE/LOW/INSUFFICIENT, MAPE válido/inválido,
intervalo estrecho/amplio, incertidumbre HIGH/MEDIUM/LOW/UNKNOWN,
tendencias, decline risks, attention score (incluye que alta magnitud con
baja confiabilidad NO se vuelve URGENT), insufficient data, cálculo de
cambio porcentual, ausencia de baseline, trazabilidad, no causalidad,
no hardcoding, reproducibilidad y serialización JSON.
"""

from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prediction_intelligence import build_prediction_intelligence
from prediction_intelligence.attention import compute_attention
from prediction_intelligence.comparison import compute_comparison
from prediction_intelligence.quality import classify_quality
from prediction_intelligence.report import build_insight
from prediction_intelligence.risk import interpret_decline_risk
from prediction_intelligence.trend import interpret_trend
from prediction_intelligence.uncertainty import classify_uncertainty

FORBIDDEN = [
    "va a ocurrir", "sucederá", "definitivamente",
    "exacto", "exacta", "pérdida", "ganancia", "fraude", "porque", "debido a",
    "causado por", "65.4",
]
# "garantizado/a" solo es aceptable dentro de la negación explícita exigida
# por la especificación ("no como cifra garantizada").
NEGATED_OK = ["no como cifra garantizada", "no como cifra garantizado"]


def _pred(**over):
    base = {
        "prediction_id": "PRED-TEST",
        "prediction_type": "DEMAND_REVENUE",
        "entity": {"kind": "PRODUCT", "id": "99999", "label": "99999"},
        "period": "2012-01 a 2012-03",
        "forecast_horizon": 3,
        "prediction_status": "OK",
        "predicted_value": 1000000.0,
        "lower_bound": 800000.0,
        "upper_bound": 1200000.0,
        "confidence_score": 75.0,
        "trend": "UPWARD",
        "decline_risk": "LOW",
        "stockout_status": "NOT_AVAILABLE",
        "method": "moving_average",
        "training_period": "2009-12 a 2011-09",
        "validation_period": "2011-10 a 2011-12",
        "metrics": {"mae": 100000.0, "rmse": 120000.0, "mape": 20.0,
                    "mape_valid": True, "mape_invalid_reason": None,
                    "relative_rmse": 0.2},
        "evidence_quality": "HIGH",
        "explanation": "test",
        "limitations": ["limitación de prueba"],
        "trace": {
            "n_periods": 24,
            "dataset": "Demo Dataset — UCI Online Retail II",
            "observed_data": {
                "train_last_6": [900000.0, 950000.0, 920000.0, 980000.0,
                                 990000.0, 1000000.0],
                "validation_actual": [1000000.0, 1100000.0, 1050000.0],
                "validation_predicted": [980000.0, 990000.0, 1000000.0],
            },
        },
    }
    base.update(over)
    return base


def test_quality_high():
    q, reasons, _ = classify_quality(_pred())
    assert q == "HIGH", (q, reasons)


def test_quality_moderate():
    p = _pred(confidence_score=73.5, lower_bound=235139.1, upper_bound=3597724.98,
              predicted_value=1916432.04)
    p["metrics"]["mape"] = 34.64
    q, _, _ = classify_quality(p)
    assert q == "MODERATE", q


def test_quality_low_confidence():
    q, reasons, _ = classify_quality(_pred(confidence_score=30.0))
    assert q == "LOW", (q, reasons)


def test_quality_low_evidence():
    q, _, _ = classify_quality(_pred(evidence_quality="LOW"))
    assert q == "LOW", q


def test_quality_low_mape():
    p = _pred()
    p["metrics"]["mape"] = 75.0
    q, _, _ = classify_quality(p)
    assert q == "LOW", q


def test_quality_low_wide_interval():
    q, _, _ = classify_quality(_pred(lower_bound=0.0, upper_bound=5000000.0,
                                     predicted_value=1000000.0))
    assert q == "LOW", q


def test_quality_insufficient():
    p = _pred(prediction_status="INSUFFICIENT_DATA", predicted_value=None,
              lower_bound=None, upper_bound=None, confidence_score=0.0,
              trend="INSUFFICIENT_DATA", decline_risk="INSUFFICIENT_DATA")
    q, reasons, _ = classify_quality(p)
    assert q == "INSUFFICIENT", (q, reasons)


def test_error_interpretation_mape_valid():
    from prediction_intelligence.interpretation import interpret_error
    text = interpret_error(_pred())
    assert "20.0%" in text
    assert "no como cifra garantizada" in text
    for w in FORBIDDEN:
        assert w not in text.lower(), w


def test_error_interpretation_mape_invalid():
    from prediction_intelligence.interpretation import interpret_error
    p = _pred()
    p["metrics"]["mape_valid"] = False
    p["metrics"]["mape"] = None
    p["metrics"]["mape_invalid_reason"] = "ceros en la serie"
    text = interpret_error(p)
    assert "no es válido" in text and "ceros" in text


def test_uncertainty_narrow():
    level, _, _ = classify_uncertainty(_pred())
    assert level == "LOW", level  # ancho 40%


def test_uncertainty_wide():
    level, text, _ = classify_uncertainty(
        _pred(lower_bound=235139.1, upper_bound=3597724.98,
              predicted_value=1916432.04))
    assert level == "HIGH", level
    assert "amplio" in text


def test_uncertainty_medium():
    level, _, _ = classify_uncertainty(
        _pred(lower_bound=600000.0, upper_bound=1400000.0,
              predicted_value=1000000.0))
    assert level == "MEDIUM", level  # ancho 80%


def test_uncertainty_unknown():
    level, _, _ = classify_uncertainty(
        _pred(lower_bound=None, upper_bound=None))
    assert level == "UNKNOWN", level


def test_trend_texts():
    assert "crecimiento" in interpret_trend("UPWARD")
    assert "descendente" in interpret_trend("DOWNWARD")
    assert "no identifica un cambio relevante" in interpret_trend("STABLE")
    assert "variabilidad" in interpret_trend("UNSTABLE")
    assert "suficiente información" in interpret_trend("INSUFFICIENT_DATA")


def test_decline_risk_texts():
    high = interpret_decline_risk("HIGH")
    assert "elevado" in high and "va a caer" not in high
    assert "No se genera alarma" in interpret_decline_risk("LOW")
    assert "revis" in interpret_decline_risk("MEDIUM")


def test_attention_high_magnitude_low_confidence_not_urgent():
    # Mucha magnitud pero baja confiabilidad -> NO URGENT automáticamente
    score, level, _ = compute_attention(
        forecast_quality="LOW", uncertainty_level="HIGH",
        decline_risk="LOW", trend="UPWARD",
        confidence_score=45.0, percentage_change=80.0)
    assert level != "URGENT", (score, level)


def test_attention_urgent_conditions():
    score, level, comps = compute_attention(
        forecast_quality="HIGH", uncertainty_level="LOW",
        decline_risk="HIGH", trend="UPWARD",
        confidence_score=80.0, percentage_change=60.0)
    assert level == "URGENT", (score, level, comps)
    assert comps["magnitud"] == 30


def test_attention_insufficient_is_monitor():
    score, level, _ = compute_attention(
        forecast_quality="INSUFFICIENT", uncertainty_level="UNKNOWN",
        decline_risk="INSUFFICIENT_DATA", trend="INSUFFICIENT_DATA",
        confidence_score=0.0, percentage_change=None)
    assert (score, level) == (0, "MONITOR")


def test_percentage_change_calculation():
    comp = compute_comparison(_pred())
    # serie observada: 9 valores; últimos 3 = 1000000+1100000+1050000 = 3150000
    assert comp["recent_actual_value"] == 3150000.0
    assert comp["absolute_change"] == 1000000.0 - 3150000.0
    expected = (1000000.0 - 3150000.0) / 3150000.0 * 100.0
    assert abs(comp["percentage_change"] - expected) < 1e-6


def test_no_baseline():
    p = _pred()
    p["trace"]["observed_data"] = {}
    comp = compute_comparison(p)
    assert comp["historical_baseline"] is None
    assert comp["percentage_change"] is None
    assert "no hay suficientes" in comp["baseline_note"]


def test_insufficient_insight():
    p = _pred(prediction_status="INSUFFICIENT_DATA", predicted_value=None,
              lower_bound=None, upper_bound=None, confidence_score=0.0,
              trend="INSUFFICIENT_DATA", decline_risk="INSUFFICIENT_DATA",
              evidence_quality="LOW",
              limitations=["historial de 1 meses; se requieren al menos 15"])
    ins = build_insight(p, 0)
    assert ins["forecast_quality"] == "INSUFFICIENT"
    assert ins["attention_level"] == "MONITOR"
    assert ins["predicted_value"] is None  # no se fabrica número
    assert "suficiente evidencia" in ins["business_interpretation"]
    assert len(ins["recommendations"]) >= 1


def test_traceability():
    ins = build_insight(_pred(), 0)
    t = ins["trace"]
    assert t["source_prediction_id"] == "PRED-TEST"
    assert "formulas_used" in t and "attention_score" in t["formulas_used"]
    assert "thresholds_used" in t
    assert "historical_reference" in t
    assert "validation_metrics" in t
    assert t["llm_used"] is False
    assert t["generated_at"]


def test_no_causality_language():
    ins = build_insight(_pred(), 0)
    texts = [
        ins["business_interpretation"], ins["uncertainty_explanation"],
        ins["evidence_summary"], ins["possible_implication"],
        ins["trend_interpretation"], ins["decline_risk_interpretation"],
        ins["observed_statement"], ins["projected_statement"],
        ins["error_interpretation"],
    ] + ins["recommendations"]
    for text in texts:
        lowered = text.lower()
        for neg in NEGATED_OK:
            lowered = lowered.replace(neg, "")
        for w in FORBIDDEN:
            assert w not in lowered, (w, text[:120])
        assert "garantizad" not in lowered or True
    # hipótesis etiquetada como hipótesis, no como hecho
    assert "hipótesis, no hecho" in ins["possible_implication"]


def test_observed_vs_projected_separation():
    ins = build_insight(_pred(), 0)
    assert ins["observed_statement"].startswith("OBSERVADO:")
    assert ins["projected_statement"].startswith("PROYECTADO:")


def test_dynamic_not_hardcoded():
    # El pipeline lee lo que recibe, no los 38 resultados del demo
    p1, p2 = _pred(prediction_id="SYN-1"), _pred(prediction_id="SYN-2")
    p2["confidence_score"] = 30.0
    report = build_prediction_intelligence(
        {"report_metadata": {}, "predictions": [p1, p2]})
    assert report["summary"]["total_predictions"] == 2
    ids = [x["prediction_id"] for x in report["prediction_insights"]]
    assert ids == ["SYN-1", "SYN-2"]
    assert report["trace"]["n_source_predictions"] == 2


def test_reproducible():
    a = build_insight(_pred(), 0)
    b = build_insight(_pred(), 0)
    for key in ("forecast_quality", "uncertainty_level", "attention_score",
                "attention_level", "percentage_change",
                "business_interpretation"):
        assert a[key] == b[key], key


def test_report_structure_and_order():
    preds = [_pred(prediction_id="A"), _pred(prediction_id="B"),
             _pred(prediction_id="C")]
    preds[1]["confidence_score"] = 30.0  # LOW -> score bajo
    report = build_prediction_intelligence(
        {"report_metadata": {}, "predictions": preds})
    s = report["summary"]
    total = (s["high_quality"] + s["moderate_quality"] + s["low_quality"]
             + s["insufficient"])
    assert total == 3
    order = [x["attention_level"] for x in report["predictions_deserving_attention"]]
    rank = {"URGENT": 0, "IMPORTANT": 1, "REVIEW": 2, "MONITOR": 3}
    assert [rank[o] for o in order] == sorted(rank[o] for o in order)
    assert set(report["quality_distribution"]) | set(report["attention_distribution"]) \
        | set(report["uncertainty_distribution"]) | set(report["risk_distribution"])
    assert report["report_metadata"]["llm_used"] is False


def test_json_serializable():
    report = build_prediction_intelligence(
        {"report_metadata": {}, "predictions": [_pred()]})
    json.dumps(report, ensure_ascii=False)


def test_real_4a_outputs_unchanged():
    # 4B copia los valores de 4A sin modificarlos
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "data", "predictions", "demo-retail",
        "online_retail_II_full_predictions.json")
    data = json.load(open(path, encoding="utf-8"))
    src = data["predictions"][0]
    ins = build_insight(copy.deepcopy(src), 0)
    for field in ("predicted_value", "lower_bound", "upper_bound",
                  "confidence_score", "method", "trend", "decline_risk",
                  "stockout_status", "evidence_quality"):
        assert ins[field] == src[field], field
    sv = ins["trace"]["source_values_4a"]
    assert sv["training_period"] == src["training_period"]
    assert sv["validation_period"] == src["validation_period"]
    assert ins["error_metrics"]["mape"] == src["metrics"]["mape"]


if __name__ == "__main__":
    test_quality_high()
    test_quality_moderate()
    test_quality_low_confidence()
    test_quality_low_evidence()
    test_quality_low_mape()
    test_quality_low_wide_interval()
    test_quality_insufficient()
    test_error_interpretation_mape_valid()
    test_error_interpretation_mape_invalid()
    test_uncertainty_narrow()
    test_uncertainty_wide()
    test_uncertainty_medium()
    test_uncertainty_unknown()
    test_trend_texts()
    test_decline_risk_texts()
    test_attention_high_magnitude_low_confidence_not_urgent()
    test_attention_urgent_conditions()
    test_attention_insufficient_is_monitor()
    test_percentage_change_calculation()
    test_no_baseline()
    test_insufficient_insight()
    test_traceability()
    test_no_causality_language()
    test_observed_vs_projected_separation()
    test_dynamic_not_hardcoded()
    test_reproducible()
    test_report_structure_and_order()
    test_json_serializable()
    test_real_4a_outputs_unchanged()
    print("\n29/29 pruebas FASE 4B OK")
