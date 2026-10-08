"""FASE 5A — critical_findings: hallazgos de 2B enriquecidos con 2C.

Se fusionan BusinessFindings (2B) con sus ContextFindings (2C) por
finding_id. NO se recalcula impact_score: se conserva el de 2B.
Ordenados por priority_rank (el orden real de 2B).
"""

from __future__ import annotations


def build_critical_findings(findings_report: dict, context_report: dict, evidence) -> list[dict]:
    findings = findings_report.get("findings", []) or []
    ctx_by_id = {
        c.get("finding_id"): c for c in (context_report.get("context_findings", []) or [])
    }

    out = []
    for f in sorted(findings, key=lambda x: x.get("priority_rank", 10**9)):
        fid = f.get("finding_id")
        ctx = ctx_by_id.get(fid, {})

        ev_id = evidence.add(
            source_module="FASE_2B",
            source_file="data/findings/demo-retail/online_retail_II_full_findings.json",
            source_record=fid,
            field="business_finding",
            value={"title": f.get("title"), "business_priority": f.get("business_priority"),
                   "impact_score": f.get("impact_score")},
            period=(f.get("period") or {}).get("start"),
            trace=f.get("trace"),
        )

        out.append(
            {
                "kind": "BUSINESS_FINDING",
                "finding_id": fid,
                "finding_type": f.get("type"),
                "business_priority": f.get("business_priority"),
                "impact_score": f.get("impact_score"),
                "confidence_score": f.get("confidence_score"),
                "evidence_quality": f.get("evidence_quality"),
                "severity": f.get("severity"),
                "title": f.get("title"),
                "entity": f.get("entity"),
                "period": f.get("period"),
                "observed_value": f.get("observed_value"),
                "expected_value": f.get("expected_value"),
                "difference": f.get("difference"),
                "percentage_difference": f.get("percentage_difference"),
                "statistical_explanation": f.get("statistical_explanation"),
                "business_explanation": f.get("business_explanation"),
                "facts": ctx.get("facts", []),
                "observations": ctx.get("observations", []),
                "possible_explanations": ctx.get("possible_explanations", []),
                "recommendations": ctx.get("recommendations", []),
                "recurrence": ctx.get("recurrence"),
                "concentration": ctx.get("concentration"),
                "requires_review": f.get("requires_review"),
                "data_quality_warning": f.get("data_quality_warning"),
                "evidence_id": ev_id,
                "trace": f.get("trace"),
            }
        )
    return out
