"""
ZAYVERO BUSINESS — FASE 2B: pruebas del motor de hallazgos.

Cubre: alto impacto monetario, anomalía extrema con poca evidencia,
bajo impacto, deduplicación (múltiples detectores, mismo evento),
ausencia de baseline, datos insuficientes, diferencia monetaria,
lenguaje prohibido, y serialización JSON completa.
"""

from __future__ import annotations

import json
import math
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from findings import build_findings
from findings.dedup import dedup_anomalies
from findings.impact import impact_score, monetary_impact, compute_p95
from findings.priority import business_priority, evidence_quality

FORBIDDEN = ["fraude", "robo", "pérdida", "perdida", "error humano",
             "dinero perdido", "ganancia"]


def _mk_anomaly(**kw):
    """Anomalía 2A mínima sintética."""
    base = {
        "anomaly_id": "ANM-000001",
        "type": "product_sales_change",
        "severity": "HIGH",
        "entity": {"kind": "product", "id": "X1", "label": "X1 (Test)"},
        "period": {"start": "2011-01-01", "end": "2011-01-31",
                   "granularity": "30d_window"},
        "observed": 10000.0,
        "expected": 1000.0,
        "difference": 9000.0,
        "ratio": 10.0,
        "deviation": 8.0,
        "z_robust": 8.0,
        "confidence_score": 85,
        "confidence_factors": {
            "history_points": 120, "null_fraction": 0.0,
            "sustained_pattern": True,
        },
        "explanation": "explicación estadística",
        "evidence": {"history_days": 120, "recent_days_active": 26,
                     "sustained": True},
        "trace": {"dataset": "test", "method": "test"},
    }
    base.update(kw)
    return base


def _report(anomalies):
    return {
        "report_metadata": {"dataset_label": "test"},
        "anomalies": anomalies,
    }


def test_high_monetary_impact():
    """Anomalía de alto impacto monetario → impact alto y prioridad alta."""
    a = _mk_anomaly(observed=100000.0, expected=1000.0, difference=99000.0,
                    deviation=15.0, confidence_score=90)
    r = build_findings(_report([a]), "test-co")
    f = r["findings"][0]
    assert f["impact_score"] >= 75, f["impact_score"]
    assert f["business_priority"] in ("URGENT", "IMPORTANT"), f["business_priority"]
    assert f["difference"] == 99000.0
    print("ok: alto impacto monetario")


def test_extreme_low_evidence():
    """Anomalía extrema pero con poca evidencia → REVIEW, nunca URGENT."""
    a = _mk_anomaly(
        observed=50000.0, expected=100.0, difference=49900.0,
        deviation=20.0, confidence_score=95,
        confidence_factors={"history_points": 5, "null_fraction": 0.0,
                            "sustained_pattern": False},
    )
    r = build_findings(_report([a]), "test-co")
    f = r["findings"][0]
    assert f["evidence_quality"] == "LOW", f["evidence_quality"]
    assert f["business_priority"] == "REVIEW", f["business_priority"]
    assert f["business_priority"] != "URGENT"
    print("ok: extrema con poca evidencia → REVIEW")


def test_low_impact():
    """Anomalía de bajo impacto → MONITOR o REVIEW bajo."""
    a = _mk_anomaly(observed=110.0, expected=100.0, difference=10.0,
                    deviation=2.6, confidence_score=55,
                    confidence_factors={"history_points": 40,
                                        "null_fraction": 0.0,
                                        "sustained_pattern": False})
    r = build_findings(_report([a]), "test-co")
    f = r["findings"][0]
    assert f["business_priority"] in ("MONITOR", "REVIEW"), f["business_priority"]
    print("ok: bajo impacto")


def test_dedup_same_event():
    """Múltiples anomalías del mismo producto → un solo hallazgo."""
    a1 = _mk_anomaly(anomaly_id="ANM-000001", type="price_outlier",
                     observed=500.0, expected=50.0, difference=450.0,
                     deviation=9.0)
    a2 = _mk_anomaly(anomaly_id="ANM-000002", type="price_outlier",
                     observed=600.0, expected=50.0, difference=550.0,
                     deviation=10.0)
    a3 = _mk_anomaly(anomaly_id="ANM-000003", type="price_outlier",
                     observed=450.0, expected=50.0, difference=400.0,
                     deviation=8.0)
    groups = dedup_anomalies([a1, a2, a3])
    assert len(groups) == 1, len(groups)
    assert groups[0]["n_merged"] == 3
    assert groups[0]["total_difference"] == 1400.0
    r = build_findings(_report([a1, a2, a3]), "test-co")
    assert len(r["findings"]) == 1
    f = r["findings"][0]
    assert f["n_merged"] == 3
    assert len(f["contributing_anomaly_ids"]) == 3
    print("ok: deduplicación mismo evento")


def test_no_baseline():
    """Sin historial (baseline ausente) → evidence LOW, sin crash."""
    a = _mk_anomaly(confidence_factors={"history_points": 0,
                                        "null_fraction": 0.0,
                                        "sustained_pattern": False})
    r = build_findings(_report([a]), "test-co")
    f = r["findings"][0]
    assert f["evidence_quality"] == "LOW"
    assert f["business_priority"] in ("MONITOR", "REVIEW")
    print("ok: ausencia de baseline")


def test_insufficient_data():
    """Dataset con 1 sola anomalía débil no falla silenciosamente."""
    a = _mk_anomaly(observed=None, expected=None, difference=None,
                    deviation=0.5, confidence_score=10)
    r = build_findings(_report([a]), "test-co")
    assert len(r["findings"]) == 1
    f = r["findings"][0]
    assert f["impact_score"] >= 0
    print("ok: datos insuficientes")


def test_monetary_difference():
    """Diferencia monetaria bien calculada y con lenguaje correcto."""
    m = monetary_impact(44051.6, 791.14)
    assert m["absolute_difference"] == 44051.6 - 791.14
    assert m["percentage_difference"] is not None
    assert m["label"] == "desviación respecto al comportamiento esperado"
    # expected = 0 → percentage None, sin crash
    m2 = monetary_impact(100.0, 0.0)
    assert m2["percentage_difference"] is None
    assert m2["absolute_difference"] == 100.0
    print("ok: diferencia monetaria")


def test_forbidden_language():
    """Ningún hallazgo AFIRMA fraude/robo/pérdida (la documentación de
    métodos puede mencionar la regla en negativo)."""
    anomalies = [
        _mk_anomaly(anomaly_id=f"ANM-{i:06d}", observed=10000.0 + i * 100,
                    difference=9000.0 + i * 100)
        for i in range(1, 6)
    ]
    r = build_findings(_report(anomalies), "test-co")
    # Revisar solo el contenido de los hallazgos, no la doc de métodos.
    for f in r["findings"]:
        blob = json.dumps(
            {k: f[k] for k in ("title", "statistical_explanation",
                               "business_explanation", "recommended_review")
             if k in f},
            ensure_ascii=False).lower()
        for w in FORBIDDEN:
            assert w not in blob, f"lenguaje prohibido en hallazgo: {w}"
    # La etiqueta monetaria nunca es "pérdida"/"ganancia"
    for f in r["findings"]:
        assert f["trace"]["fase_2b"]["monetary_label"] == \
            "desviación respecto al comportamiento esperado"
    print("ok: sin lenguaje prohibido")


def test_json_serializable():
    """Todo el reporte es 100% serializable a JSON."""
    a = _mk_anomaly()
    r = build_findings(_report([a]), "test-co")
    s = json.dumps(r, ensure_ascii=False)
    r2 = json.loads(s)
    assert r2["findings"][0]["finding_id"].startswith("FND-")
    print("ok: JSON serializable")


def test_finding_structure():
    """BusinessFinding tiene todos los campos requeridos."""
    required = {"finding_id", "type", "title", "severity",
                "business_priority", "impact_score", "confidence_score",
                "evidence_quality", "entity", "period", "observed_value",
                "expected_value", "difference", "percentage_difference",
                "statistical_explanation", "business_explanation",
                "recommended_review", "trace"}
    a = _mk_anomaly()
    r = build_findings(_report([a]), "test-co")
    f = r["findings"][0]
    missing = required - set(f.keys())
    assert not missing, f"faltan: {missing}"
    assert f["type"] == "PRODUCT_ANOMALY"
    assert f["statistical_explanation"] == "explicación estadística"
    assert len(f["business_explanation"]) > 20
    assert len(f["recommended_review"]) > 20
    print("ok: estructura BusinessFinding")


def test_urgent_cap():
    """Máximo 25 URGENT por ranking."""
    anomalies = [
        _mk_anomaly(anomaly_id=f"ANM-{i:06d}",
                    entity={"kind": "product", "id": f"P{i}",
                            "label": f"P{i}"},
                    observed=200000.0, expected=1000.0,
                    difference=199000.0, deviation=15.0,
                    confidence_score=95,
                    confidence_factors={"history_points": 200,
                                        "null_fraction": 0.0,
                                        "sustained_pattern": True})
        for i in range(60)
    ]
    r = build_findings(_report(anomalies), "test-co")
    n_urgent = sum(1 for f in r["findings"]
                   if f["business_priority"] == "URGENT")
    assert n_urgent <= 25, n_urgent
    assert n_urgent == 25, n_urgent  # todos califican, el tope aplica
    print("ok: tope URGENT por ranking")


def test_empty_anomalies():
    """Reporte vacío no crashea."""
    r = build_findings(_report([]), "test-co")
    assert r["summary"]["total_findings"] == 0
    assert r["findings"] == []
    print("ok: sin anomalías")


if __name__ == "__main__":
    test_high_monetary_impact()
    test_extreme_low_evidence()
    test_low_impact()
    test_dedup_same_event()
    test_no_baseline()
    test_insufficient_data()
    test_monetary_difference()
    test_forbidden_language()
    test_json_serializable()
    test_finding_structure()
    test_urgent_cap()
    test_empty_anomalies()
    print("\n12/12 pruebas FASE 2B OK")
