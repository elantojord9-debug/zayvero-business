"""FASE 3 — DATA LAYER (adaptador de lectura).

Consume exclusivamente los outputs reales de FASE 2B y FASE 2C:

  data/findings/demo-retail/online_retail_II_full_findings.json  (Business Findings)
  data/context/demo-retail/online_retail_II_full_context.json     (Business Context Findings)

No modifica los motores 2A/2B/2C, no recalcula estadísticas y no inventa
valores: todo campo faltante se representa como None / "—" en la UI.
"""

from __future__ import annotations

import json
import os

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DEFAULT_FINDINGS_PATH = os.path.join(
    _PROJECT_ROOT, "data", "findings", "demo-retail",
    "online_retail_II_full_findings.json",
)
DEFAULT_CONTEXT_PATH = os.path.join(
    _PROJECT_ROOT, "data", "context", "demo-retail",
    "online_retail_II_full_context.json",
)

TYPE_LABELS = {
    "SALES_ANOMALY": "Ventas",
    "PRODUCT_ANOMALY": "Producto",
    "CUSTOMER_ANOMALY": "Cliente",
    "PRICE_ANOMALY": "Precio",
    "QUANTITY_ANOMALY": "Cantidad",
    "TEMPORAL_ANOMALY": "Temporal",
}

PRIORITY_ORDER = {"URGENT": 0, "IMPORTANT": 1, "REVIEW": 2, "MONITOR": 3}
PRIORITIES = ["URGENT", "IMPORTANT", "REVIEW", "MONITOR"]


def type_label(finding_type):
    """Etiqueta legible del tipo de hallazgo (español)."""
    return TYPE_LABELS.get(finding_type or "", finding_type or "—")


def load_raw(findings_path=DEFAULT_FINDINGS_PATH,
             context_path=DEFAULT_CONTEXT_PATH):
    """Lee los JSON de 2B y 2C y devuelve (findings, contexts, dataset_label)."""
    with open(findings_path, encoding="utf-8") as fh:
        data_2b = json.load(fh)
    with open(context_path, encoding="utf-8") as fh:
        data_2c = json.load(fh)
    findings = data_2b.get("findings") or []
    contexts = data_2c.get("context_findings") or []
    dataset_label = (
        (data_2b.get("report_metadata") or {}).get("dataset_label")
        or (data_2c.get("report_metadata") or {}).get("dataset")
        or "Dataset"
    )
    return findings, contexts, dataset_label


def build_view(finding, context):
    """Une un Business Finding (2B) con su Context Finding (2C).

    Tolerante a campos ausentes: cualquier valor que no exista en los
    outputs queda como None y la UI lo muestra como "—". Nunca inventa.
    """
    f = finding or {}
    c = context or {}
    entity = f.get("entity") or {}
    period = f.get("period") or {}
    recommendations = c.get("recommendations") or []
    return {
        # --- identidad (2B) ---
        "finding_id": f.get("finding_id"),
        "type": f.get("type"),
        "type_label": type_label(f.get("type")),
        "title": f.get("title") or "Sin título",
        "severity": f.get("severity"),
        "business_priority": f.get("business_priority"),
        "impact_score": f.get("impact_score"),
        "confidence_score": f.get("confidence_score"),
        "evidence_quality": f.get("evidence_quality"),
        "entity": {
            "kind": entity.get("kind"),
            "id": entity.get("id"),
            "label": entity.get("label") or "—",
        },
        "period": {
            "start": period.get("start"),
            "end": period.get("end"),
            "granularity": period.get("granularity"),
        },
        # --- evidencia monetaria (2B) ---
        "observed_value": f.get("observed_value"),
        "expected_value": f.get("expected_value"),
        "difference": f.get("difference"),
        "percentage_difference": f.get("percentage_difference"),
        # --- explicaciones (2B) ---
        "statistical_explanation": f.get("statistical_explanation"),
        "business_explanation": f.get("business_explanation"),
        "data_quality_warning": f.get("data_quality_warning"),
        "requires_review": bool(f.get("requires_review")),
        "requires_review_reason": f.get("requires_review_reason"),
        "impact_components": f.get("impact_components"),
        # --- contexto (2C) ---
        "context_status": c.get("context_status"),
        "facts": c.get("facts") or [],
        "observations": c.get("observations") or [],
        "possible_explanations": c.get("possible_explanations") or [],
        "recommendations": recommendations,
        "n_recommendations": len(recommendations),
        "related_entities": c.get("related_entities") or {},
        "historical_context": c.get("historical_context") or {},
        "trend_context": c.get("trend_context") or {},
        "recurrence": c.get("recurrence"),
        "recurrence_detail": c.get("recurrence_detail") or {},
        "concentration": c.get("concentration") or {},
        "evidence_quality_note": c.get("evidence_quality_note"),
        "context_trace": c.get("trace") or {},
    }


def load_dataset(findings_path=DEFAULT_FINDINGS_PATH,
                 context_path=DEFAULT_CONTEXT_PATH):
    """Carga y une 2B+2C en la vista que consume el dashboard.

    Devuelve {"dataset": str, "findings": [view...]}.
    """
    findings, contexts, dataset_label = load_raw(findings_path, context_path)
    ctx_by_id = {
        (c or {}).get("finding_id"): c for c in contexts
        if (c or {}).get("finding_id")
    }
    views = [
        build_view(f, ctx_by_id.get((f or {}).get("finding_id")))
        for f in findings
    ]
    return {"dataset": dataset_label, "findings": views}
