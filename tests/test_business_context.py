"""FASE 5A — tests del Business Intelligence Context.

30 pruebas deterministas con inputs sintéticos (sin depender del dataset
real), más una prueba de integración con los outputs reales existentes.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from business_context import build_business_context, load_inputs
from business_context.evidence import EvidenceIndex
from business_context.confidence import compute_context_confidence
from business_context.attention import build_attention_summary
from business_context.report import sanitize_advisor_safe, EXECUTIVE_QUESTIONS


def _profile():
    monthly = [
        {"period": f"2024-{m:02d}", "revenue": 1000.0 + m * 100, "units": 100.0,
         "transactions": 10, "customers": 5, "avg_ticket": 100.0, "products_sold": 20}
        for m in range(1, 10)
    ]
    return {
        "dataset_metadata": {"dataset_label": "Demo Dataset — Test", "company_id": "test-co"},
        "date_range": {"min_date": "2024-01-01", "max_date": "2024-09-30", "days_with_activity": 200},
        "transactions": {"total_rows": 1000, "unique_transactions": 500,
                         "completed_rows": 990, "cancelled_rows": 10},
        "entities": {"customers": 50, "products": 20, "countries": 3},
        "customers": {"total_customers": 50, "active_customers": 40, "trace": {}},
        "products": {"total_products": 20, "trace": {}},
        "sales": {"gross_revenue": 50000.0, "net_revenue": 49000.0,
                  "cancelled_revenue": 1000.0, "units_sold_net": 5000.0, "trace": {}},
        "cancellations": {"count": 10, "trace": {}},
        "data_quality": {"score": 90.0, "deductions": [
            {"component": "duplicates", "points": 6.0, "detail": "test detail"}]},
        "countries": {"growth_6m_vs_prev_6m": [
            {"country": "Testland", "revenue_prev_6m": 100.0,
             "revenue_last_6m": 200.0, "pct_change": 100.0, "trend": "crecimiento"}]},
        "temporal_metrics": {"monthly": monthly},
    }


def _finding(fid="FND-000001", priority="URGENT", impact=90.0, ftype="PRODUCT_ANOMALY",
             evidence_q="HIGH", pct=50.0, rank=1):
    return {
        "finding_id": fid, "type": ftype, "title": f"Título {fid}",
        "severity": "HIGH", "business_priority": priority, "impact_score": impact,
        "confidence_score": 95.0, "evidence_quality": evidence_q,
        "entity": {"kind": "product", "id": "P1", "label": "P1 (Test)"},
        "period": {"start": "2024-08-01", "end": "2024-08-31", "granularity": "30d_window"},
        "observed_value": 1500.0, "expected_value": 1000.0, "difference": 500.0,
        "percentage_difference": pct, "statistical_explanation": "stat",
        "business_explanation": "biz", "recommended_review": "Revisar X.",
        "data_quality_warning": None, "requires_review": False,
        "contributing_detectors": ["x"], "contributing_anomaly_ids": ["A1"],
        "n_merged": 1, "trace": {}, "priority_rank": rank,
    }


def _ctx_finding(fid="FND-000001", priority="URGENT", impact=90.0, recurrence="recurrent"):
    return {
        "finding_id": fid, "context_status": "contextualized",
        "facts": [{"kind": "FACT", "text": "hecho"}], "observations": [],
        "possible_explanations": [{"kind": "POSSIBLE_EXPLANATION", "text": "posible"}],
        "recommendations": [{"kind": "RECOMMENDATION", "text": "Revisar X."}],
        "related_entities": [], "historical_context": {}, "trend_context": {},
        "recurrence": recurrence, "recurrence_detail": {}, "concentration": {},
        "evidence_quality": "HIGH", "evidence_quality_note": "",
        "confidence_score": 95.0, "business_priority": priority,
        "impact_score": impact, "severity": "HIGH", "type": "PRODUCT_ANOMALY",
        "title": f"Título {fid}", "trace": {},
    }


def _insight(iid="INS-000001", trend="UPWARD", quality="MODERATE", att="REVIEW",
             decline="LOW", unc="LOW", status="OK"):
    return {
        "insight_id": iid, "prediction_id": "PRED-1", "prediction_type": "REVENUE",
        "entity": {"kind": "GLOBAL", "id": None, "label": "negocio completo"},
        "period": "2024-10 a 2024-12", "forecast_horizon": 3,
        "prediction_status": status, "predicted_value": 60000.0,
        "lower_bound": 50000.0, "upper_bound": 70000.0, "confidence_score": 70.0,
        "method": "naive", "trend": trend, "decline_risk": decline,
        "stockout_status": "NOT_AVAILABLE", "recent_actual_value": 55000.0,
        "historical_baseline": 54000.0, "absolute_change": 5000.0,
        "percentage_change": 9.0, "comparison_note": "", "forecast_quality": quality,
        "quality_reasons": [], "uncertainty_level": unc, "uncertainty_text": "",
        "interval_width": 20000.0, "interval_width_pct": 33.0,
        "evidence_quality": "HIGH", "attention_score": 55, "attention_level": att,
        "attention_components": {}, "observed_statement": "obs",
        "projected_statement": "proj", "error_interpretation": "",
        "trend_interpretation": "", "decline_risk_interpretation": "",
        "business_interpretation": "interp", "uncertainty_explanation": "",
        "evidence_summary": "", "possible_implication": "",
        "recommendations": ["Monitorear el indicador."],
        "limitations": ["limit"], "error_metrics": {}, "trace": {},
    }


def _inputs(**over):
    pi_summary = {"total_predictions": 2, "high_quality": 0, "moderate_quality": 1,
                  "low_quality": 1, "insufficient": 0, "high_uncertainty": 0,
                  "medium_uncertainty": 1, "low_uncertainty": 1, "urgent": 0,
                  "important": 0, "review": 1, "monitor": 1,
                  "high_decline_risk": 0, "medium_decline_risk": 0,
                  "low_decline_risk": 2, "average_confidence": 70.0,
                  "average_attention_score": 50.0,
                  "predictions_with_high_uncertainty": 0,
                  "predictions_requiring_review": 1}
    v_summary = {"total_predictions": 2, "validated": 0, "pending": 2,
                 "not_available": 0, "invalid": 0,
                 "accuracy_rate": "INSUFFICIENT_DATA",
                 "interval_hit_rate": "INSUFFICIENT_DATA",
                 "mean_absolute_error": "INSUFFICIENT_DATA",
                 "median_absolute_error": "INSUFFICIENT_DATA",
                 "mean_percentage_error": "INSUFFICIENT_DATA",
                 "mean_signed_error": "INSUFFICIENT_DATA",
                 "overprediction_rate": "INSUFFICIENT_DATA",
                 "underprediction_rate": "INSUFFICIENT_DATA",
                 "model_performance_status": "INSUFFICIENT_DATA"}
    base = {
        "profile": _profile(),
        "findings": {"findings": [_finding()]},
        "context": {"context_findings": [_ctx_finding()]},
        "pi": {"summary": pi_summary, "prediction_insights": [_insight()]},
        "validation": {"summary": v_summary, "model_performance_status": "INSUFFICIENT_DATA"},
    }
    base.update(over)
    return base


# 1. BusinessIntelligenceContext: todas las secciones
def test_context_structure():
    ctx = build_business_context(_inputs())
    for key in ("identity", "business_snapshot", "critical_findings", "key_risks",
                "key_opportunities", "key_trends", "prediction_intelligence",
                "prediction_validation", "recommendations", "limitations",
                "executive_questions", "attention_summary", "context_confidence",
                "evidence_index", "advisor_safe_data", "trace"):
        assert key in ctx, f"falta {key}"


# 2. snapshot: métricas con los 5 campos
def test_snapshot_metrics_fields():
    ctx = build_business_context(_inputs())
    for m in ctx["business_snapshot"]["metrics"]:
        for k in ("metric_name", "value", "period", "source", "trace"):
            assert k in m
    names = [m["metric_name"] for m in ctx["business_snapshot"]["metrics"]]
    assert "gross_revenue" in names and "net_revenue" in names
    assert "data_quality_score" in names


# 3. impact_score NO se recalcula
def test_impact_not_recalculated():
    ctx = build_business_context(_inputs())
    assert ctx["critical_findings"][0]["impact_score"] == 90.0


# 4. prioridad y orden preservados
def test_priority_order_preserved():
    inp = _inputs()
    inp["findings"] = {"findings": [
        _finding("FND-1", priority="REVIEW", impact=60.0, rank=2),
        _finding("FND-2", priority="URGENT", impact=95.0, rank=1)]}
    inp["context"] = {"context_findings": [
        _ctx_finding("FND-1", priority="REVIEW", impact=60.0),
        _ctx_finding("FND-2", priority="URGENT", impact=95.0)]}
    ctx = build_business_context(inp)
    assert [f["finding_id"] for f in ctx["critical_findings"]] == ["FND-2", "FND-1"]


# 5. risk extraction: URGENT genera riesgo
def test_risk_from_urgent():
    ctx = build_business_context(_inputs())
    assert any(r["trace"]["source_record"] == "FND-000001" for r in ctx["key_risks"])


# 6. IMPORTANT con impact < 75 NO genera riesgo
def test_important_low_impact_no_risk():
    inp = _inputs()
    inp["findings"] = {"findings": [_finding(priority="IMPORTANT", impact=50.0, rank=1)]}
    inp["context"] = {"context_findings": [_ctx_finding(priority="IMPORTANT", impact=50.0)]}
    ctx = build_business_context(inp)
    assert not any(r["trace"]["source_record"] == "FND-000001" and r["risk_type"] == "PRODUCT_RISK"
                   for r in ctx["key_risks"])


# 7. mapeo de tipo de riesgo
def test_risk_type_mapping():
    inp = _inputs()
    inp["findings"] = {"findings": [
        _finding("FND-P", ftype="PRICE_ANOMALY", rank=1),
        _finding("FND-C", ftype="CUSTOMER_ANOMALY", rank=2)]}
    inp["context"] = {"context_findings": [
        _ctx_finding("FND-P"), _ctx_finding("FND-C")]}
    ctx = build_business_context(inp)
    by_id = {r["trace"]["source_record"]: r["risk_type"] for r in ctx["key_risks"]}
    assert by_id["FND-P"] == "PRICE_RISK"
    assert by_id["FND-C"] == "CUSTOMER_RISK"


# 8. decline_risk HIGH → PREDICTION_RISK HIGH
def test_decline_risk_to_prediction_risk():
    inp = _inputs()
    inp["pi"]["prediction_insights"] = [_insight(decline="HIGH")]
    ctx = build_business_context(inp)
    prs = [r for r in ctx["key_risks"] if r["risk_type"] == "PREDICTION_RISK"]
    assert any(r["severity"] == "HIGH" for r in prs)


# 9. oportunidad solo con evidencia (UPWARD + calidad + atención)
def test_opportunity_with_evidence():
    ctx = build_business_context(_inputs())
    assert any(o["type"] == "PROJECTED_GROWTH" for o in ctx["key_opportunities"])


# 10. UPWARD sin calidad suficiente NO genera oportunidad
def test_opportunity_not_without_quality():
    inp = _inputs()
    inp["pi"]["prediction_insights"] = [_insight(quality="LOW")]
    ctx = build_business_context(inp)
    assert not any(o["type"] == "PROJECTED_GROWTH" for o in ctx["key_opportunities"])


# 11. separación de tendencias
def test_trend_separation():
    ctx = build_business_context(_inputs())
    types = {t["trend_type"] for t in ctx["key_trends"]}
    assert "OBSERVED_TREND" in types and "PROJECTED_TREND" in types


# 12. lenguaje observado vs proyectado
def test_observed_vs_projected_language():
    ctx = build_business_context(_inputs())
    obs = [t for t in ctx["key_trends"] if t["trend_type"] == "OBSERVED_TREND"][0]
    proj = [t for t in ctx["key_trends"] if t["trend_type"] == "PROJECTED_TREND"][0]
    assert obs["interpretation"].startswith("OBSERVADO:")
    assert proj["interpretation"].startswith("PROYECTADO:")


# 13. prediction summary consumido sin recalcular
def test_prediction_summary_consumed():
    ctx = build_business_context(_inputs())
    assert ctx["prediction_intelligence"]["summary"]["total_predictions"] == 2
    assert ctx["prediction_intelligence"]["summary"]["moderate_quality"] == 1


# 14. validation summary con validated=0 → frase exacta
def test_validation_zero_text():
    ctx = build_business_context(_inputs())
    assert ctx["prediction_validation"]["validation_note"] == (
        "No existen suficientes resultados reales posteriores para evaluar todavía "
        "el desempeño de las predicciones.")
    assert "correctas" not in ctx["prediction_validation"]["validation_note"]


# 15. deduplicación de recomendaciones
def test_recommendation_dedup():
    inp = _inputs()
    # misma recomendación en 2C y 4B
    inp["context"]["context_findings"][0]["recommendations"] = [
        {"kind": "RECOMMENDATION", "text": "Monitorear el indicador."}]
    ctx = build_business_context(inp)
    texts = [r["text"] for r in ctx["recommendations"]]
    assert texts.count("Monitorear el indicador.") == 1


# 16. limitaciones consolidadas
def test_limitations_present():
    ctx = build_business_context(_inputs())
    types = {l["limitation_type"] for l in ctx["limitations"]}
    assert "DATA_QUALITY" in types
    assert "NO_VALIDATION_YET" in types
    assert "NO_STOCKOUT" in types


# 17. evidence index: campos e IDs secuenciales
def test_evidence_index():
    ctx = build_business_context(_inputs())
    ev = ctx["evidence_index"]
    assert len(ev) > 0
    ids = [e["evidence_id"] for e in ev]
    assert ids == [f"EV-{i:04d}" for i in range(1, len(ev) + 1)]
    for e in ev:
        for k in ("evidence_id", "source_module", "source_file", "field", "value"):
            assert k in e


# 18. context confidence: fórmula exacta
def test_confidence_formula():
    inp = _inputs()
    cc = compute_context_confidence(inp["profile"], inp["findings"], inp["pi"], inp["validation"])
    # dq=90*0.4=36; evidence coverage=100%*0.25=25; pred quality: 1 numeric,
    # MODERATE→100%*0.2=20; validation 0 → 36+25+20=81
    assert cc["context_confidence_score"] == 81.0, cc
    assert cc["components"]["validation_available"]["value"] == 0.0


# 19. attention summary: CRITICAL con 20 urgentes
def test_attention_critical():
    inp = _inputs()
    inp["findings"] = {"findings": [_finding(f"FND-{i:04d}", rank=i + 1) for i in range(20)]}
    inp["context"] = {"context_findings": [_ctx_finding(f"FND-{i:04d}") for i in range(20)]}
    ctx = build_business_context(inp)
    assert ctx["attention_summary"]["attention_level"] == "CRITICAL"
    assert ctx["attention_summary"]["signals"]["urgent_findings"] == 20


# 20. executive questions estructurales, no respondidas
def test_executive_questions():
    assert len(EXECUTIVE_QUESTIONS) == 10
    assert any("riesgo" in q for q in EXECUTIVE_QUESTIONS)
    ctx = build_business_context(_inputs())
    assert ctx["executive_questions"] == EXECUTIVE_QUESTIONS


# 21. advisor-safe: sin secretos
def test_no_credentials():
    dirty = {"api_key": "xxx", "token": "yyy", "facts": [{"text": "ok"}],
             "nested": {"password": "zzz", "value": 1}}
    clean = sanitize_advisor_safe(dirty)
    assert "api_key" not in clean and "token" not in clean
    assert "password" not in clean["nested"]
    assert clean["facts"][0]["text"] == "ok"


# 22. insights vacíos no rompen
def test_missing_prediction_data():
    inp = _inputs()
    inp["pi"] = {"summary": {}, "prediction_insights": []}
    ctx = build_business_context(inp)
    assert ctx["key_trends"]  # al menos la observada
    assert ctx["context_confidence"]["components"]["prediction_quality"]["value"] == 0.0


# 23. validación vacía no rompe
def test_missing_validation():
    inp = _inputs()
    inp["validation"] = {"summary": {"total_predictions": 0, "validated": 0}, "model_performance_status": None}
    ctx = build_business_context(inp)
    assert ctx["prediction_validation"]["validation_note"] is not None


# 24. insight INSUFFICIENT_DATA se maneja
def test_insufficient_insight():
    inp = _inputs()
    inp["pi"]["prediction_insights"] = [_insight(status="INSUFFICIENT_DATA")]
    ctx = build_business_context(inp)
    assert not any(t["trend_type"] == "PROJECTED_TREND" for t in ctx["key_trends"])


# 25. no hardcoding: inputs sintéticos → outputs sintéticos
def test_no_hardcoding():
    ctx = build_business_context(_inputs())
    blob = json.dumps(ctx, ensure_ascii=False)
    assert "23084" not in blob and "RABBIT" not in blob


# 26. determinismo: mismo context_id
def test_deterministic():
    a = build_business_context(_inputs())
    b = build_business_context(_inputs())
    assert a["identity"]["context_id"] == b["identity"]["context_id"]


# 27. confidence separation: contexto ≠ predicción
def test_confidence_separation():
    ctx = build_business_context(_inputs())
    assert ctx["context_confidence"]["context_confidence_score"] != 70.0  # ≠ confidence del insight


# 28. sin afirmaciones causales
def test_no_causal_claims():
    ctx = build_business_context(_inputs())
    import re
    bad = re.compile(r"\b(fraude|robo|estafa|culpable|la causa es|porque los clientes están)\b", re.I)
    texts = ([r["explanation"] for r in ctx["key_risks"]]
             + [o["explanation"] for o in ctx["key_opportunities"]]
             + [t["interpretation"] for t in ctx["key_trends"]])
    assert not any(bad.search(t) for t in texts)


# 29. serializable a JSON
def test_json_serializable():
    ctx = build_business_context(_inputs())
    json.dumps(ctx, ensure_ascii=False)


# 30. integración con outputs reales
def test_real_outputs_integration():
    inputs = load_inputs("demo-retail")
    ctx = build_business_context(inputs, company_id="demo-retail")
    assert len(ctx["critical_findings"]) == 708
    assert ctx["attention_summary"]["attention_level"] == "CRITICAL"
    assert ctx["prediction_validation"]["summary"]["validated"] == 0
    assert len(ctx["evidence_index"]) > 1000


if __name__ == "__main__":
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL {t.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    raise SystemExit(1 if failed else 0)
