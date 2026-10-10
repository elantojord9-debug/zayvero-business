"""
ZAYVERO BUSINESS — pruebas de corrección controlada de calidad
(Diagnóstico Ejecutivo).

Datos de prueba 100% controlados y sintéticos; no tocan el dataset
demo ni los JSON derivados. Cubren los 6 hallazgos de la auditoría:

1. trend histórico vs. forecast_direction separados, sin "crecimiento"
   ante pronósticos de caída, con nota de discrepancia.
2. decline_risk proyectado: umbrales documentados, casos -42%, -43.9%,
   -37.8%, -69.3% y 0%; el 0% no recibe riesgo alto por el historial.
3. Clasificación centralizada y coherente (STABLE ante variaciones
   pequeñas y grandes).
4. Oportunidades con nombre de métrica, sin afirmar crecimiento si la
   métrica cae.
5. Formatos de periodo diario/semanal/mensual consistentes y legibles.
6. Totales vs. elementos mostrados en el diagnóstico.
Además: coherencia referencia/proyectado/porcentaje/intervalo/
dirección; conservación de evidencias y aislamiento entre empresas.
"""

from __future__ import annotations

import copy
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

from prediction import trends
from prediction.risk import decline_risk
from prediction_intelligence.attention import compute_attention
from prediction_intelligence.comparison import compute_comparison
from prediction_intelligence.report import build_insight
from prediction_intelligence.trend import (
    classify_forecast_direction,
    discrepancy_note,
    interpret_forecast_direction,
    interpret_trend,
)
from prediction_intelligence.periods import (
    format_period_display,
    format_period_label,
)
from business_context.opportunities import build_key_opportunities as build_opportunities

FORBIDDEN = ["garantiza", "asegura", "sin riesgo", "crecimiento asegurado"]


# ---------------------------------------------------------------- fixtures

def _make_pred(pct_target, trend="UPWARD", ptype="DEMAND_REVENUE",
               period="2012-01 a 2012-03", confidence=75.0,
               decline_risk_level=None):
    """Predicción 4A sintética: 8 periodos de 1000, horizonte 3.

    pct_target: cambio porcentual deseado del pronóstico vs. ventana
    reciente (últimos 3 observados = 3000).
    """
    obs = [1000.0] * 8
    predicted = 3000.0 * (1.0 + pct_target / 100.0)
    return {
        "prediction_id": "TEST-QA-1",
        "prediction_type": ptype,
        "entity": {"kind": "GLOBAL", "id": None, "label": "negocio completo"},
        "period": period,
        "forecast_horizon": 3,
        "prediction_status": "OK",
        "predicted_value": predicted,
        "lower_bound": predicted * 0.8,
        "upper_bound": predicted * 1.2,
        "confidence_score": confidence,
        "method": "naive",
        "trend": trend,
        "decline_risk": decline_risk_level or "LOW",
        "evidence_quality": "HIGH",
        "confidence_factors": {},
        "trace": {
            "n_periods": 8,
            "metrics": {"mape": 5.0, "mape_valid": True,
                        "mae": 10.0, "rmse": 12.0},
            "observed_data": {"train_last_6": obs[:6],
                              "validation_actual": obs[6:]},
        },
    }


def _insight_with_projected_risk(pct_target, **kwargs):
    """_make_pred + decline_risk calculado desde el cambio proyectado."""
    pred = _make_pred(pct_target, **kwargs)
    conf = kwargs.get("confidence", 75.0)
    pred["decline_risk"] = decline_risk(
        {"trend": pred["trend"]}, _obs_series([1000.0] * 12), conf,
        forecast_pct=pct_target)["decline_risk"]
    return pred


def _obs_series(values):
    return pd.Series(values, dtype=float)


# ------------------------------------------------------------- 1. tendencia

def test_historical_up_forecast_down_keeps_both_signals():
    pred = _insight_with_projected_risk(-42.0, trend="UPWARD")
    ins = build_insight(pred, 1)
    text = ins["business_interpretation"]
    # El pronóstico NUNCA se describe como crecimiento.
    assert "proyección estima una disminución" in text
    assert "proyecta una disminución esperada" in text
    assert "proyecta un incremento" not in text
    # La señal histórica se conserva (por separado).
    assert "historial muestra crecimiento" in text
    assert ins["forecast_direction"] == "DOWN"
    assert ins["trend_discrepancy_note"], "falta nota de discrepancia"
    assert "42.0%" in text
    # El riesgo ahora refleja la caída proyectada.
    assert ins["decline_risk"] == "HIGH"
    assert "riesgo elevado de disminución en el horizonte proyectado" in text


def test_historical_down_forecast_up_keeps_both_signals():
    pred = _insight_with_projected_risk(35.0, trend="DOWNWARD")
    ins = build_insight(pred, 1)
    text = ins["business_interpretation"]
    assert "proyección estima un incremento" in text.lower()
    assert "proyecta un incremento esperado" in text
    assert "tendencia descendente" in text
    assert ins["forecast_direction"] == "UP"
    assert ins["trend_discrepancy_note"], "falta nota de discrepancia"
    # El pronóstico sube, pero la señal histórica (DOWNWARD) eleva un nivel.
    assert ins["decline_risk"] == "MEDIUM"


def test_coherent_pair_has_no_discrepancy_note():
    pred = _make_pred(-45.0, trend="DOWNWARD")
    ins = build_insight(pred, 1)
    assert ins["forecast_direction"] == "DOWN"
    assert ins["trend_discrepancy_note"] is None


def test_no_forbidden_certainty_language():
    for pct, trend in [(-42.0, "UPWARD"), (35.0, "DOWNWARD"),
                       (-5.0, "STABLE"), (3.0, "UPWARD")]:
        ins = build_insight(_make_pred(pct, trend=trend), 1)
        for bad in FORBIDDEN:
            assert bad not in ins["business_interpretation"].lower(), bad


# ------------------------------------------------------------- 2. decline_risk

def test_decline_risk_projected_thresholds():
    cases = [
        (-42.0, "HIGH"),
        (-43.9, "HIGH"),
        (-37.8, "MEDIUM"),
        (-69.3, "HIGH"),
    ]
    for pct, expected in cases:
        r = decline_risk({"trend": "UPWARD"}, _obs_series([100.0] * 12),
                         75.0, forecast_pct=pct)
        assert r["decline_risk"] == expected, (pct, r["decline_risk"])


def test_decline_risk_zero_pct_not_high_from_history():
    # 0% proyectado: nunca HIGH solo por tendencia histórica.
    for trend in ("UPWARD", "DOWNWARD", "STABLE"):
        r = decline_risk({"trend": trend}, _obs_series([100.0] * 12),
                         75.0, forecast_pct=0.0)
        assert r["decline_risk"] != "HIGH", (trend, r["decline_risk"])


def test_decline_risk_low_confidence_caps_level():
    # Alta severidad proyectada + confianza baja -> MEDIUM como máximo.
    r = decline_risk({"trend": "UPWARD"}, _obs_series([100.0] * 12),
                     30.0, forecast_pct=-69.3)
    assert r["decline_risk"] == "MEDIUM"


def test_decline_risk_historical_mode_preserves_meaning():
    # Sin pronóstico: modo histórico, etiquetado como tal.
    r = decline_risk({"trend": "UPWARD"}, _obs_series([100.0] * 12), 75.0)
    assert r["decline_risk"] == "LOW"
    assert "modo histórico" in r["detail"]


def test_decline_risk_direction_agnostic_positive():
    # Crecimiento proyectado: riesgo de caída LOW.
    r = decline_risk({"trend": "UPWARD"}, _obs_series([100.0] * 12),
                     75.0, forecast_pct=42.0)
    assert r["decline_risk"] == "LOW"


# ------------------------------------------------------------- 3. STABLE

def test_classification_centralized_and_coherent():
    assert classify_forecast_direction(-69.3) == "DOWN"
    assert classify_forecast_direction(-5.0) == "FLAT"
    assert classify_forecast_direction(4.9) == "FLAT"
    assert classify_forecast_direction(25.0) == "UP"
    assert classify_forecast_direction(None) == "UNKNOWN"
    assert classify_forecast_direction(-20.0) == "DOWN"
    assert classify_forecast_direction(20.0) == "UP"


def test_stable_series_detected_by_trends():
    flat = _obs_series([100.0 + (i % 2) for i in range(20)])
    info = trends.detect_trend(flat)
    assert info["trend"] == "STABLE"


def test_interpret_forecast_direction_texts():
    assert "incremento" in interpret_forecast_direction(30.0)
    assert "disminución" in interpret_forecast_direction(-30.0)
    assert "no estima un cambio material" in interpret_forecast_direction(2.0)
    # Una caída proyectada nunca se describe como crecimiento.
    assert "crecimiento" not in interpret_forecast_direction(-30.0).lower()
    assert "aumento" not in interpret_forecast_direction(-30.0).lower()


def test_trend_text_is_historical():
    assert "histórico" in interpret_trend("UPWARD")
    assert "histórico" in interpret_trend("DOWNWARD")


# ------------------------------------------------------------- 4. oportunidades


def _opps(pi_report):
    from business_context.evidence import EvidenceIndex
    return build_opportunities(pi_report, {}, {},
                               {"company_id": "QA"}, EvidenceIndex())

def _insight(pct, ptype="DEMAND_REVENUE", trend="UPWARD", quality="MODERATE",
             attention="REVIEW", iid="INS-QA-1"):
    pred = _make_pred(pct, trend=trend, ptype=ptype)
    pred["decline_risk"] = "LOW"
    ins = build_insight(pred, 1)
    ins["insight_id"] = iid
    ins["forecast_quality"] = quality
    ins["attention_level"] = attention
    return ins


def test_opportunity_includes_metric_name():
    pi = {"prediction_insights": [
        _insight(30.0, ptype="DEMAND_REVENUE", iid="INS-QA-R"),
        _insight(30.0, ptype="DEMAND_QUANTITY", iid="INS-QA-Q"),
    ]}
    opps = _opps(pi)
    texts = [o["explanation"] for o in opps if o["type"] == "PROJECTED_GROWTH"]
    assert len(texts) == 2
    assert any("ingresos" in t for t in texts)
    assert any("unidades" in t for t in texts)
    # Evidencias conservadas: el source_record del insight queda trazado.
    src_records = {o["trace"]["source_record"] for o in opps
                   if o["type"] == "PROJECTED_GROWTH"}
    assert "INS-QA-R" in src_records and "INS-QA-Q" in src_records
    assert all(o["evidence_id"].startswith("EV-") for o in opps)


def test_no_growth_opportunity_when_metric_falls():
    # Historial UPWARD pero pronóstico DOWN: no es oportunidad de crecimiento.
    pi = {"prediction_insights": [
        _insight(-42.0, trend="UPWARD", iid="INS-QA-DOWN"),
    ]}
    opps = _opps(pi)
    assert not [o for o in opps if o["type"] == "PROJECTED_GROWTH"]


def test_opportunity_trace_keeps_forecast_direction():
    pi = {"prediction_insights": [_insight(30.0, iid="INS-QA-T")]}
    opps = _opps(pi)
    o = next(o for o in opps if o["type"] == "PROJECTED_GROWTH")
    assert o["evidence"]["forecast_direction"] == "UP"
    assert o["evidence"]["metric"] == "ingresos"
    assert o["trace"]["source_record"] == "INS-QA-T"


# ------------------------------------------------------------- 5. periodos

def test_period_monthly():
    assert format_period_display("2012-01 a 2012-03") == "ene 2012 a mar 2012"


def test_period_weekly():
    out = format_period_display(
        "2011-12-12/2011-12-18 a 2012-01-02/2012-01-08")
    assert out == "12–18 dic 2011 a 2–8 ene 2012"
    assert "2011-12" not in out  # no formato crudo ISO


def test_period_weekly_cross_month():
    out = format_period_label("2011-12-26/2012-01-01")
    assert out == "26 dic 2011–1 ene 2012"


def test_period_daily():
    assert format_period_display("2012-01-05 a 2012-01-07") == \
        "5 ene 2012 a 7 ene 2012"


def test_period_fallback_returns_original():
    weird = "Q1 2012 a Q2 2012"
    assert format_period_display(weird) == weird
    assert format_period_label("desconocido") == "desconocido"


def test_period_does_not_convert_granularity():
    # Semanal sigue siendo semanal: se conserva el rango de días.
    out = format_period_label("2011-12-12/2011-12-18")
    assert "12" in out and "18" in out and "dic" in out


def test_insight_keeps_raw_period_in_trace():
    pred = _make_pred(-10.0, period="2011-12-12/2011-12-18 a 2012-01-02/2012-01-08")
    ins = build_insight(pred, 1)
    assert ins["period_raw"] == pred["period"]
    assert ins["period"] == "12–18 dic 2011 a 2–8 ene 2012"


# ------------------------------------------------------------- 6. coherencia

def test_comparison_coherence():
    pred = _make_pred(-42.0)
    comp = compute_comparison(pred)
    assert comp["recent_actual_value"] == 3000.0
    assert abs(comp["percentage_change"] - (-42.0)) < 1e-6
    assert comp["absolute_change"] == pred["predicted_value"] - 3000.0
    assert classify_forecast_direction(
        comp["percentage_change"]) == "DOWN"


def test_interval_contains_predicted():
    pred = _make_pred(-42.0)
    assert pred["lower_bound"] <= pred["predicted_value"] <= pred["upper_bound"]


def test_attention_high_magnitude_low_conf_not_urgent():
    score, level, _ = compute_attention(
        forecast_quality="LOW", uncertainty_level="HIGH",
        decline_risk="LOW", trend="UPWARD", confidence_score=20.0,
        percentage_change=-69.3)
    assert level != "URGENT"


# ------------------------------------------------------------- 7. totales

def _build(ctx):
    from diagnostic.builder import build_diagnostic
    return build_diagnostic(ctx, {}, {},
                            company_id=ctx["company"]["company_id"],
                            company_name="QA Company",
                            is_demo=False,
                            dataset_state="READY")


def _mini_ctx():
    def f(fid, prio, impact):
        return {"finding_id": fid, "title": "T" + fid,
                "business_priority": prio, "impact_score": impact,
                "evidence_id": "EV-" + fid, "entity": None, "period": None}
    findings = ([f("F%02d" % i, "URGENT", 90 - i) for i in range(12)] +
                [f("I%02d" % i, "IMPORTANT", 50 - i) for i in range(8)] +
                [f("W%02d" % i, "WATCH", 20 - i) for i in range(5)])
    risks = [{"risk_id": "R%02d" % i, "title": "R", "severity": "HIGH",
              "evidence_id": "EVR", "entity": None, "period": None}
             for i in range(6)]
    opps = [{"opportunity_id": "O%02d" % i, "type": "PROJECTED_GROWTH",
             "importance": "HIGH", "explanation": "x", "evidence_id": "EVO",
             "trace": {}, "entity": None, "period": None}
            for i in range(10)]
    recs = [{"recommendation_id": "RC%02d" % i, "kind": "k", "text": "t"}
            for i in range(4)]
    trends = ([{"trend_id": "T%02d" % i, "trend_type": "OBSERVED_TREND",
                "direction": "UP", "period": "p", "interpretation": "i",
                "confidence": 70, "evidence_id": "EV", "source": "s"}
               for i in range(7)] +
              [{"trend_id": "P%02d" % i, "trend_type": "PROJECTED_TREND",
                "direction": "DOWN", "period": "p", "interpretation": "i",
                "confidence": 70, "evidence_id": "EV", "source": "s"}
               for i in range(5)])
    return {
        "company": {"company_id": "QA-COMPANY"},
        "identity": {"context_id": "CTX-QA"},
        "attention_summary": {"signals": {"urgent_findings": 12,
                                          "important_findings": 8}},
        "context_confidence": {},
        "business_snapshot": {"business_period": {}},
        "critical_findings": findings,
        "key_risks": risks,
        "key_opportunities": opps,
        "key_trends": trends,
        "recommendations": recs,
        "limitations": [],
    }


def test_section_counts_match_shown_and_totals():
    d = _build(_mini_ctx())
    sc = d["section_counts"]
    assert sc["priority_attention"]["shown"] == \
        len(d["priority_attention"])
    assert sc["priority_attention"]["total"] == 20  # 12 URGENT + 8 IMPORTANT
    assert sc["risks"]["shown"] == len(d["risks"])
    assert sc["risks"]["total"] == 6
    assert sc["opportunities"]["shown"] == len(d["opportunities"])
    assert sc["opportunities"]["total"] == 10
    assert sc["recommendations"]["shown"] == len(d["recommendations"])
    assert sc["recommendations"]["total"] == 4
    assert sc["trends_observed"]["total"] == 7
    assert sc["trends_projected"]["total"] == 5


def test_totals_never_mix_categories():
    d = _build(_mini_ctx())
    sc = d["section_counts"]
    assert sc["opportunities"]["total"] != sc["risks"]["total"]
    assert sc["priority_attention"]["total"] == 20
    # WATCH no entra en el total de atención prioritaria.
    assert len(d["priority_attention"]) <= sc["priority_attention"]["total"]


# ------------------------------------------------------------- 8. evidencias

def test_evidence_preserved_through_insight():
    pred = _make_pred(-42.0)
    ins = build_insight(pred, 1)
    # Cadena de evidencia: insight -> predicción 4A -> valores de origen.
    assert ins["prediction_id"] == "TEST-QA-1"
    assert ins["trace"]["source_values_4a"]["predicted_value"] == \
        pred["predicted_value"]
    assert ins["trace"]["source_values_4a"]["forecast_direction"] == "DOWN"


def test_tenant_isolation_in_section_counts():
    from diagnostic.builder import build_diagnostic
    ctx_a = _mini_ctx()
    ctx_b = _mini_ctx()
    ctx_b["company"]["company_id"] = "QA-COMPANY-B"
    d_a = _build(ctx_a)
    d_b = _build(ctx_b)
    assert d_a["company_id"] == "QA-COMPANY"
    assert d_b["company_id"] == "QA-COMPANY-B"
    assert d_a["section_counts"] == d_b["section_counts"]
    # Sin contaminación cruzada de identificadores.
    assert "QA-COMPANY-B" not in json.dumps(d_a)


def test_diagnostic_json_serializable():
    d = _build(_mini_ctx())
    json.dumps(d, default=str)


# ------------------------------------------------------------------ runner

# --------------------------------- fixes visuales (pre-producción)

def test_predictions_view_includes_forecast_fields():
    # webapp/data.py::predictions() debe exponer los campos que el JS
    # necesita para el badge "Dirección proyectada" en #/predicciones.
    import re
    src = open(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                            "webapp", "data.py"), encoding="utf-8").read()
    for field in ("forecast_direction", "forecast_direction_text",
                  "trend_discrepancy_note"):
        assert '"%s": ins.get("%s")' % (field, field) in src, field


def test_recommendations_use_formatted_period():
    # Las recomendaciones no deben incrustar el periodo crudo
    # "2011-12-12/2011-12-18 a ...".
    from prediction_intelligence.recommendations import build_recommendations
    pred = {"entity": {"kind": "BUSINESS", "label": "negocio completo"},
            "period": "2011-12-12/2011-12-18 a 2012-01-02/2012-01-08",
            "forecast_horizon": 3, "prediction_status": "OK"}
    recs = build_recommendations(pred, "HIGH", "LOW", "LOW", "STABLE")
    assert recs, "sin recomendaciones"
    for r in recs:
        assert "2011-12-12/2011-12-18" not in r, r
    assert any("12–18 dic 2011" in r for r in recs), recs[0]


def test_prediction_labels_have_i18n_keys():
    # Las etiquetas "Tendencia histórica" / "Dirección proyectada" deben
    # resolverse por T(), no estar hardcodeadas en español.
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                        "webapp", "static")
    app = open(os.path.join(base, "app.js"), encoding="utf-8").read()
    assert "T(\"prediction.historical_trend\")" in app
    assert "T(\"prediction.forecast_direction\")" in app
    assert ">Tendencia histórica: " not in app
    assert ">Dirección proyectada: " not in app
    i18n = open(os.path.join(base, "i18n.js"), encoding="utf-8").read()
    for key, es, en in [
            ("prediction.historical_trend", "Tendencia histórica", "Historical trend"),
            ("prediction.forecast_direction", "Dirección proyectada", "Forecast direction")]:
        assert '"%s": "%s"' % (key, es) in i18n, (key, "es")
        assert '"%s": "%s"' % (key, en) in i18n, (key, "en")


_TESTS = [
    test_historical_up_forecast_down_keeps_both_signals,
    test_historical_down_forecast_up_keeps_both_signals,
    test_coherent_pair_has_no_discrepancy_note,
    test_no_forbidden_certainty_language,
    test_decline_risk_projected_thresholds,
    test_decline_risk_zero_pct_not_high_from_history,
    test_decline_risk_low_confidence_caps_level,
    test_decline_risk_historical_mode_preserves_meaning,
    test_decline_risk_direction_agnostic_positive,
    test_classification_centralized_and_coherent,
    test_stable_series_detected_by_trends,
    test_interpret_forecast_direction_texts,
    test_trend_text_is_historical,
    test_opportunity_includes_metric_name,
    test_no_growth_opportunity_when_metric_falls,
    test_opportunity_trace_keeps_forecast_direction,
    test_period_monthly,
    test_period_weekly,
    test_period_weekly_cross_month,
    test_period_daily,
    test_period_fallback_returns_original,
    test_period_does_not_convert_granularity,
    test_insight_keeps_raw_period_in_trace,
    test_comparison_coherence,
    test_interval_contains_predicted,
    test_attention_high_magnitude_low_conf_not_urgent,
    test_section_counts_match_shown_and_totals,
    test_totals_never_mix_categories,
    test_evidence_preserved_through_insight,
    test_tenant_isolation_in_section_counts,
    test_diagnostic_json_serializable,
    test_predictions_view_includes_forecast_fields,
    test_recommendations_use_formatted_period,
    test_prediction_labels_have_i18n_keys,
]


def main():
    for t in _TESTS:
        t()
    print("\n%d/%d pruebas corrección de calidad OK" % (len(_TESTS), len(_TESTS)))


if __name__ == "__main__":
    main()
