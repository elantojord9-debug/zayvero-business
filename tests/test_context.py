"""
ZAYVERO BUSINESS — FASE 2C: pruebas del Context Engine.

Cubre como mínimo:
1. Hallazgo con suficiente contexto.
2. Hallazgo sin contexto suficiente.
3. Evento aislado.
4. Evento recurrente.
5. Tendencia creciente.
6. Tendencia decreciente.
7. Posible explicación sin convertirla en hecho.
8. Recomendación de revisión.
9. Trace completo.
10. Ausencia de causalidad inventada.
11. Ausencia de valores inventados.
12. Compatibilidad con FASE 2B.
"""

from __future__ import annotations

import json
import os
import re
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from context import build_context_findings
from context.engine import ContextEngine
from context import comparisons as cmp

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARQUET = os.path.join(BASE, "data", "processed", "demo-retail",
                       "online_retail_II_full.parquet")
FINDINGS = os.path.join(BASE, "data", "findings", "demo-retail",
                        "online_retail_II_full_findings.json")

FORBIDDEN = ["fraude", "robo", "pérdida", "ganancia", "dinero perdido",
             "culpable", "culpabilidad", "error humano"]
# "causa" solo prohibida en construcciones causales afirmativas
CAUSAL_PATTERNS = [r"\bla causa (fue|es)\b", r"\bcausado por\b",
                   r"\bse debió a\b", r"\bhubo una promoción\b"]


def _load(n=30):
    with open(FINDINGS, encoding="utf-8") as fh:
        rep = json.load(fh)
    return rep["findings"][:n]


def _report(n=30):
    return build_context_findings(_load(n), PARQUET, "demo-retail")


def _dump(c):
    return json.dumps(c, ensure_ascii=False, default=str).lower()


def test_1_sufficient_context():
    r = _report(30)
    ok = [c for c in r["context_findings"]
          if c["context_status"] == "contextualized"]
    assert ok, "ningún hallazgo contextualizado"
    c = ok[0]
    assert c["facts"], "sin facts"
    assert c["observations"], "sin observations"
    assert c["recommendations"], "sin recommendations"
    print("t1 hallazgo con suficiente contexto ✓")


def test_2_insufficient_context():
    # Hallazgo sintético sin período ni entidad resoluble
    eng = ContextEngine(PARQUET, "demo-retail")
    from context.report import contextualize_finding
    fake = {"finding_id": "FND-T2", "type": "PRODUCT_ANOMALY",
            "title": "t2", "entity": {"kind": "product", "id": "__NOEXISTE__"},
            "period": {"start": None, "end": None},
            "observed_value": 10, "expected_value": 1,
            "statistical_explanation": "", "confidence_score": 50,
            "business_priority": "REVIEW", "impact_score": 10,
            "severity": "LOW", "evidence_quality": "LOW", "trace": {}}
    c = contextualize_finding(fake, eng)
    assert c["context_status"] == "insufficient_context", c["context_status"]
    assert c["evidence_quality"] == "LOW"
    # Debe decir explícitamente que no hay evidencia suficiente
    texts = " ".join(x["text"] for x in c["possible_explanations"]).lower()
    assert "no existe evidencia suficiente" in texts, texts[:200]
    print("t2 hallazgo sin contexto suficiente ✓")


def test_3_isolated_event():
    r = _report(60)
    iso = [c for c in r["context_findings"] if c["recurrence"] == "isolated"]
    assert iso, "ningún evento aislado detectado"
    c = iso[0]
    assert any("aislado" in x["text"].lower()
               for x in c["possible_explanations"]), "falta hipótesis de evento aislado"
    print(f"t3 evento aislado ✓ ({c['finding_id']})")


def test_4_recurrent_event():
    r = _report(60)
    rec = [c for c in r["context_findings"]
           if c["recurrence"] in ("recurrent", "rarely_recurrent")]
    assert rec, "ningún evento recurrente detectado"
    c = rec[0]
    assert any("repite" in x["text"].lower()
               for x in c["possible_explanations"]), "falta hipótesis de recurrencia"
    print(f"t4 evento recurrente ✓ ({c['finding_id']})")


def test_5_increasing_trend():
    r = _report(120)
    up = [c for c in r["context_findings"]
          if c["trend_context"].get("trend") == "sustained_high"]
    assert up, "ninguna tendencia creciente sostenida"
    print(f"t5 tendencia creciente ✓ ({up[0]['finding_id']})")


def test_6_decreasing_or_normal_trend():
    r = _report(200)
    other = [c for c in r["context_findings"]
             if c["trend_context"].get("trend") in ("returned_to_normal",
                                                   "dropped", "stable")]
    assert other, "ninguna tendencia no-creciente"
    print(f"t6 tendencia decreciente/normal ✓ ({other[0]['finding_id']})")


def test_7_explanation_is_hypothesis():
    r = _report(30)
    for c in r["context_findings"]:
        for h in c["possible_explanations"]:
            assert h["kind"] == "POSSIBLE_EXPLANATION", h
            assert h.get("basis"), f"hipótesis sin basis en {c['finding_id']}"
            t = h["text"].lower()
            # Lenguaje hipotético obligatorio
            assert any(w in t for w in
                       ["podría", "puede", "posible", "sugiere",
                        "no existe evidencia"]), \
                f"hipótesis presentada como hecho: {h['text'][:100]}"
    print("t7 explicaciones como hipótesis ✓")


def test_8_recommendation_is_review():
    r = _report(30)
    for c in r["context_findings"]:
        assert c["recommendations"], f"sin recomendaciones en {c['finding_id']}"
        for x in c["recommendations"]:
            assert x["kind"] == "RECOMMENDATION", x
            t = x["text"].lower()
            assert any(w in t for w in
                       ["revisar", "verificar", "verificación", "confirmar",
                        "comparar", "identificar"]), \
                f"recomendación no es de revisión: {x['text'][:100]}"
    print("t8 recomendaciones de revisión ✓")


def test_9_trace_complete():
    r = _report(30)
    required = ["dataset", "period", "filters", "records", "entity",
                "baseline", "observed_value", "comparison_value",
                "method", "evidence_used"]
    for c in r["context_findings"]:
        tr = c["trace"]
        for k in required:
            assert k in tr, f"trace sin {k} en {c['finding_id']}"
    print("t9 trace completo ✓")


def test_10_no_invented_causality():
    r = _report(100)
    for c in r["context_findings"]:
        blob = _dump(c)
        for w in FORBIDDEN:
            assert w not in blob, \
                f"lenguaje prohibido '{w}' en {c['finding_id']}"
        for pat in CAUSAL_PATTERNS:
            assert not re.search(pat, blob), \
                f"causalidad afirmada '{pat}' en {c['finding_id']}"
    print("t10 sin causalidad inventada ✓")


def test_11_no_invented_values():
    # Los facts numéricos del contexto deben derivarse del dataset:
    # verifica que el revenue 'during' de un producto coincide con el parquet
    eng = ContextEngine(PARQUET, "demo-retail")
    r = _report(10)
    c = next(x for x in r["context_findings"]
             if x["type"] == "PRODUCT_ANOMALY")
    during = c["historical_context"]["during"]
    p = c["trace"]["filters"]
    pid = p.split("entity_id=")[1]
    rows = eng.product_rows(pid)
    s, e = c["trace"]["period"]["start"], c["trace"]["period"]["end"]
    s = pd.to_datetime(s).normalize(); e = pd.to_datetime(e).normalize()
    mask = (rows["day"] >= s) & (rows["day"] <= e)
    expected_rev = float(rows.loc[mask, "Revenue"].sum())
    assert abs(during["revenue"] - expected_rev) < 0.01, \
        f"valor inventado: {during['revenue']} vs {expected_rev}"
    print("t11 sin valores inventados ✓")


def test_12_compat_2b():
    # Todos los campos de 2B deben preservarse en el contexto
    r = _report(20)
    src = {f["finding_id"]: f for f in _load(20)}
    for c in r["context_findings"]:
        f = src[c["finding_id"]]
        for k in ("finding_id", "type", "title", "severity",
                  "business_priority", "impact_score", "confidence_score"):
            assert c[k] == f[k], f"campo 2B alterado: {k}"
    print("t12 compatibilidad con FASE 2B ✓")


if __name__ == "__main__":
    test_1_sufficient_context()
    test_2_insufficient_context()
    test_3_isolated_event()
    test_4_recurrent_event()
    test_5_increasing_trend()
    test_6_decreasing_or_normal_trend()
    test_7_explanation_is_hypothesis()
    test_8_recommendation_is_review()
    test_9_trace_complete()
    test_10_no_invented_causality()
    test_11_no_invented_values()
    test_12_compat_2b()
    print("\n12/12 pruebas FASE 2C ✓")
