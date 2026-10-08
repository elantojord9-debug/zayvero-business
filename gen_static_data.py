"""Genera data.js con los datos reales de FASE 2B+2C para el dashboard ESTÁTICO.

Solo usa los outputs reales del proyecto. No inventa ningún valor:
los campos son exactamente los que la API del dashboard (server.py)
serializa, menos trace/impact_components que la UI nunca muestra.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from dashboard.adapter import TYPE_LABELS, load_dataset
from dashboard.service import (
    PERIOD_BUCKETS,
    filter_findings,
    panorama,
    priority_counts,
    reference_date,
    sort_findings,
    type_counts,
)

DEMO_DISCLAIMER = (
    "Datos de demostración — Demo Dataset: UCI Online Retail II. "
    "No representan un cliente real."
)

OUT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dashboard-static")


def card(view):
    period = view.get("period") or {}
    entity = view.get("entity") or {}
    return {
        "finding_id": view.get("finding_id"),
        "title": view.get("title"),
        "type": view.get("type"),
        "type_label": view.get("type_label"),
        "business_priority": view.get("business_priority"),
        "impact_score": view.get("impact_score"),
        "confidence_score": view.get("confidence_score"),
        "evidence_quality": view.get("evidence_quality"),
        "period_start": period.get("start"),
        "period_end": period.get("end"),
        "entity_id": entity.get("id"),
        "entity_label": entity.get("label"),
        "observed_value": view.get("observed_value"),
        "expected_value": view.get("expected_value"),
        "difference": view.get("difference"),
        "percentage_difference": view.get("percentage_difference"),
        "business_explanation": view.get("business_explanation"),
        "n_recommendations": view.get("n_recommendations"),
        "requires_review": view.get("requires_review"),
    }


def trim_block(items, keys):
    out = []
    for it in items or []:
        it = it or {}
        out.append({k: it.get(k) for k in keys})
    return out


def detail(view):
    """Detalle completo con los campos que renderiza la UI (nada inventado)."""
    entity = view.get("entity") or {}
    period = view.get("period") or {}
    hist = view.get("historical_context") or {}

    def hist_side(s):
        s = s or {}
        return {
            "revenue": s.get("revenue"),
            "n_trx": s.get("n_trx"),
            "avg_daily": s.get("avg_daily"),
        }

    trend = view.get("trend_context") or {}
    rec_det = view.get("recurrence_detail") or {}
    conc = view.get("concentration") or {}
    return {
        "finding_id": view.get("finding_id"),
        "title": view.get("title"),
        "type": view.get("type"),
        "type_label": view.get("type_label"),
        "business_priority": view.get("business_priority"),
        "impact_score": view.get("impact_score"),
        "confidence_score": view.get("confidence_score"),
        "evidence_quality": view.get("evidence_quality"),
        "entity": {"label": entity.get("label")},
        "entity_label": entity.get("label"),
        "period": {"start": period.get("start"), "end": period.get("end")},
        "period_start": period.get("start"),
        "period_end": period.get("end"),
        "observed_value": view.get("observed_value"),
        "expected_value": view.get("expected_value"),
        "difference": view.get("difference"),
        "percentage_difference": view.get("percentage_difference"),
        "business_explanation": view.get("business_explanation"),
        "statistical_explanation": view.get("statistical_explanation"),
        "data_quality_warning": view.get("data_quality_warning"),
        "requires_review": view.get("requires_review"),
        "requires_review_reason": view.get("requires_review_reason"),
        "context_status": view.get("context_status"),
        "facts": trim_block(view.get("facts"), ["kind", "text", "source"]),
        "observations": trim_block(view.get("observations"), ["kind", "text", "basis"]),
        "possible_explanations": trim_block(
            view.get("possible_explanations"), ["text", "basis"]),
        "recommendations": trim_block(view.get("recommendations"), ["text"]),
        "historical_context": {
            "before": hist_side(hist.get("before")),
            "during": hist_side(hist.get("during")),
            "after": hist_side(hist.get("after")),
        },
        "trend_context": {"trend": trend.get("trend"), "note": trend.get("note")},
        "recurrence": view.get("recurrence"),
        "recurrence_detail": {"note": rec_det.get("note")},
        "concentration": {
            "n_trx": conc.get("n_trx"),
            "concentrated": conc.get("concentrated"),
        },
        "evidence_quality_note": view.get("evidence_quality_note"),
    }


def main():
    ds = load_dataset()
    findings = sort_findings(ds["findings"])
    counts = priority_counts(findings)
    tc = type_counts(findings)
    ref = reference_date(findings)
    pan = panorama(findings)

    periods = []
    for bid, label, _d in PERIOD_BUCKETS:
        periods.append({
            "id": bid,
            "label": label,
            "count": len(filter_findings(findings, period=bid, ref=ref)),
        })

    payload = {
        "meta": {
            "dataset": ds["dataset"],
            "demo_disclaimer": DEMO_DISCLAIMER,
            "total_findings": len(findings),
            "by_priority": counts,
            "types": [
                {"id": t, "label": TYPE_LABELS.get(t, t), "count": tc.get(t, 0)}
                for t in sorted(tc)
            ],
            "periods": periods,
            "reference_date": ref.isoformat() if ref else None,
        },
        "panorama": pan,
        "items": [card(v) for v in findings],
        "details": {v.get("finding_id"): detail(v) for v in findings},
    }

    os.makedirs(OUT_DIR, exist_ok=True)
    out_path = os.path.join(OUT_DIR, "data.js")
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write("/* Datos reales FASE 2B+2C — generados, no editar a mano. */\n")
        fh.write("window.ZAYVERO_DATA = ")
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
        fh.write(";\n")

    size = os.path.getsize(out_path)
    print(f"findings: {len(findings)}")
    print(f"by_priority: {counts}")
    print(f"panorama: {json.dumps(pan, ensure_ascii=False)}")
    print(f"reference_date: {ref}")
    print(f"data.js: {size:,} bytes -> {out_path}")


if __name__ == "__main__":
    main()
