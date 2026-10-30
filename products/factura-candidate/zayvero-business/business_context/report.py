"""FASE 5A — report.py: ensambla el BusinessIntelligenceContext.

Estructura final (100% serializable a JSON):

    identity, business_snapshot, critical_findings, key_risks,
    key_opportunities, key_trends, prediction_intelligence,
    prediction_validation, recommendations, limitations,
    executive_questions, attention_summary, context_confidence,
    evidence_index, advisor_safe_data, trace

Determinista: mismos inputs → mismo output. Los IDs son secuenciales según
el orden ordenado de iteración. generated_at solo existe como metadata.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from datetime import datetime, timezone

from .evidence import EvidenceIndex
from .snapshot import build_snapshot
from .findings import build_critical_findings
from .risks import build_key_risks
from .opportunities import build_key_opportunities
from .trends import build_key_trends
from .predictions import build_prediction_summary, build_validation_summary
from .recommendations import build_recommendations
from .limitations import build_limitations
from .confidence import compute_context_confidence
from .attention import build_attention_summary

DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")

EXECUTIVE_QUESTIONS = [
    "¿Qué está pasando en el negocio según la evidencia disponible?",
    "¿Cuál es el problema más importante detectado?",
    "¿Dónde existe mayor riesgo?",
    "¿Qué productos presentan comportamiento inusual?",
    "¿Qué clientes requieren atención?",
    "¿Qué tendencias se observan en el historial?",
    "¿Qué tendencias se proyectan?",
    "¿Qué predicciones tienen mayor incertidumbre?",
    "¿Qué recomendaciones deberían revisarse primero?",
    "¿Qué información falta para decidir mejor?",
]

_SECRET_RE = re.compile(
    r"password|passwd|secret|token|api[_-]?key|private[_-]?key|credential|auth|session|cookie",
    re.IGNORECASE,
)


def sanitize_advisor_safe(obj):
    """Elimina recursivamente claves que parezcan secretos/credenciales."""
    if isinstance(obj, dict):
        return {
            k: sanitize_advisor_safe(v)
            for k, v in obj.items()
            if not _SECRET_RE.search(k)
        }
    if isinstance(obj, list):
        return [sanitize_advisor_safe(v) for v in obj]
    return obj


def _strip_heavy(item: dict, keep: list[str]) -> dict:
    return {k: item.get(k) for k in keep}


def load_inputs(company_id: str = "demo-retail") -> dict:
    base = os.path.join(DATA_DIR)
    def load(*parts):
        path = os.path.join(base, *parts)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    return {
        "profile": load("profiles", company_id, "online_retail_II_full_profile.json"),
        "findings": load("findings", company_id, "online_retail_II_full_findings.json"),
        "context": load("context", company_id, "online_retail_II_full_context.json"),
        "pi": load("prediction_intelligence", company_id, "online_retail_II_prediction_intelligence.json"),
        "validation": load("prediction_validation", company_id, "online_retail_II_prediction_validation.json"),
    }


def build_business_context(inputs: dict, company_id: str = "demo-retail") -> dict:
    evidence = EvidenceIndex()
    profile = inputs["profile"]

    snapshot = build_snapshot(profile, evidence)
    critical_findings = build_critical_findings(inputs["findings"], inputs["context"], evidence)
    key_risks = build_key_risks(inputs["findings"], inputs["pi"], profile, evidence)
    key_opportunities = build_key_opportunities(inputs["pi"], inputs["findings"], inputs["context"], profile, evidence)
    key_trends = build_key_trends(profile, inputs["pi"], evidence)
    prediction_intelligence = build_prediction_summary(inputs["pi"], evidence)
    prediction_validation = build_validation_summary(inputs["validation"], evidence)
    recommendations = build_recommendations(inputs["context"], inputs["pi"], evidence)
    limitations = build_limitations(profile, inputs["context"], inputs["pi"], inputs["validation"], evidence)
    attention_summary = build_attention_summary(critical_findings, key_risks, prediction_intelligence, profile)
    context_confidence = compute_context_confidence(profile, inputs["findings"], inputs["pi"], inputs["validation"])

    identity = {
        "context_id": None,  # determinista, se calcula abajo
        "company_id": company_id,
        "dataset_id": "online_retail_II",
        "dataset_label": "Demo Dataset — UCI Online Retail II",
        "generated_at": datetime.now(timezone.utc).isoformat(),  # metadata, no afecta contenido
    }

    advisor_safe_data = sanitize_advisor_safe({
        "facts": [f for cf in critical_findings for f in cf.get("facts", [])][:200],
        "findings": [
            _strip_heavy(cf, ["finding_id", "finding_type", "business_priority",
                              "impact_score", "confidence_score", "evidence_quality",
                              "title", "entity", "period", "observed_value",
                              "expected_value", "difference", "percentage_difference",
                              "business_explanation", "recurrence"])
            for cf in critical_findings
        ],
        "predictions": [
            _strip_heavy(pi, ["insight_id", "prediction_id", "prediction_type", "entity",
                              "period", "forecast_horizon", "prediction_status",
                              "predicted_value", "lower_bound", "upper_bound",
                              "confidence_score", "trend", "decline_risk",
                              "forecast_quality", "uncertainty_level", "attention_score",
                              "attention_level", "business_interpretation",
                              "uncertainty_explanation", "recommendations", "limitations"])
            for pi in inputs["pi"].get("prediction_insights", [])
        ],
        "risks": key_risks,
        "opportunities": key_opportunities,
        "trends": key_trends,
        "recommendations": recommendations,
        "limitations": limitations,
        "evidence": evidence.as_list(),
    })

    context = {
        "identity": identity,
        "business_snapshot": snapshot,
        "critical_findings": critical_findings,
        "key_risks": key_risks,
        "key_opportunities": key_opportunities,
        "key_trends": key_trends,
        "prediction_intelligence": prediction_intelligence,
        "prediction_validation": prediction_validation,
        "recommendations": recommendations,
        "limitations": limitations,
        "executive_questions": EXECUTIVE_QUESTIONS,
        "attention_summary": attention_summary,
        "context_confidence": context_confidence,
        "evidence_index": evidence.as_list(),
        "advisor_safe_data": advisor_safe_data,
        "trace": {
            "engine": "zayvero-business-context-5A",
            "method": "deterministic consolidation of real outputs (1B/1C, 2B, 2C, 4A, 4B, 4C); no LLM, no recalculation",
            "sources": [
                "data/profiles/demo-retail/online_retail_II_full_profile.json",
                "data/findings/demo-retail/online_retail_II_full_findings.json",
                "data/context/demo-retail/online_retail_II_full_context.json",
                "data/predictions/demo-retail/online_retail_II_full_predictions.json",
                "data/prediction_intelligence/demo-retail/online_retail_II_prediction_intelligence.json",
                "data/prediction_validation/demo-retail/online_retail_II_prediction_validation.json",
            ],
            "n_evidence": len(evidence),
        },
    }

    # context_id determinista: hash del contenido sin generated_at
    payload = copy.deepcopy(context)
    payload["identity"]["generated_at"] = ""
    digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")).hexdigest()[:16]
    context["identity"]["context_id"] = f"CTX-{digest}"

    return context


def save_context(context: dict, path: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(context, fh, ensure_ascii=False, indent=2, default=str)
    return path
