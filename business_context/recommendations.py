"""FASE 5A — recommendations: consolidación de recomendaciones de 2C y 4B.

- NO se crean recomendaciones nuevas por inferencia.
- Se deduplican por texto normalizado (minúsculas, sin espacios extra).
- Prioridad: para 2C se usa el business_priority del hallazgo; para 4B el
  attention_level del insight.
- Son sugerencias de revisión, NO acciones ejecutadas.
"""

from __future__ import annotations

import re


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def build_recommendations(context_report, pi_report, evidence) -> list[dict]:
    seen: dict[str, dict] = {}

    # 2C: recomendaciones de los context findings
    for c in (context_report.get("context_findings", []) or []):
        for r in (c.get("recommendations", []) or []):
            text = r.get("text") if isinstance(r, dict) else r
            if not text:
                continue
            key = _norm(text)
            if key in seen:
                continue
            seen[key] = {
                "kind": "RECOMMENDATION",
                "recommendation_id": None,  # se asigna al final
                "source": "FASE_2C",
                "priority": c.get("business_priority"),
                "text": text,
                "related_finding": c.get("finding_id"),
                "related_prediction": None,
                "evidence": {"finding_title": c.get("title"),
                             "impact_score": c.get("impact_score")},
                "trace": {"rule": "consolidación 2C (FASE 5A recommendations.py)"},
            }

    # 4B: recomendaciones de los prediction insights
    for ins in (pi_report.get("prediction_insights", []) or []):
        for text in (ins.get("recommendations", []) or []):
            if not text:
                continue
            key = _norm(text)
            if key in seen:
                continue
            seen[key] = {
                "kind": "RECOMMENDATION",
                "recommendation_id": None,
                "source": "FASE_4B",
                "priority": ins.get("attention_level"),
                "text": text,
                "related_finding": None,
                "related_prediction": ins.get("prediction_id"),
                "evidence": {"entity": ins.get("entity"),
                             "attention_score": ins.get("attention_score")},
                "trace": {"rule": "consolidación 4B (FASE 5A recommendations.py)"},
            }

    recs = list(seen.values())
    for i, rec in enumerate(recs, 1):
        rec["recommendation_id"] = f"REC-{i:04d}"
        rec["evidence_id"] = evidence.add(
            source_module=rec["source"],
            source_file="data/context + data/prediction_intelligence (demo-retail)",
            source_record=rec["related_finding"] or rec["related_prediction"],
            field="recommendation",
            value=rec["text"],
            period=None,
            trace=None,
        )
    return recs
