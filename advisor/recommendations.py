"""FASE 5B — Selección de recomendaciones.

Reutiliza las existentes del contexto (EXISTING_RECOMMENDATION).
Si se deriva una de la evidencia, se marca como ADVISORY_RECOMMENDATION.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .models import BusinessQuestion, RetrievedEvidence

_PRIORITY_ORDER = {"URGENT": 0, "IMPORTANT": 1, "REVIEW": 2, "MONITOR": 3}


def select(evidence: List[RetrievedEvidence], context: Dict[str, Any],
           n: int = 5) -> List[Dict[str, Any]]:
    """Selecciona recomendaciones existentes relevantes (determinista)."""
    rec_ev = [e for e in evidence if e.source_type == "recommendation"]
    recs = []
    for e in rec_ev:
        item = e.trace  # no usado
        recs.append((e.relevance_score, e))
    recs.sort(key=lambda r: r[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for _, e in recs[:n]:
        out.append({
            "kind": "EXISTING_RECOMMENDATION",
            "recommendation_id": e.evidence_id,
            "text": e.claim,
            "evidence_id": e.evidence_id,
            "note": "Recomendación generada por FASE 2C/4B; es una sugerencia de revisión, no una acción ejecutada.",
        })
    return out


def advisory(question: BusinessQuestion, evidence: List[RetrievedEvidence]) -> List[Dict[str, Any]]:
    """Recomendación derivada de la evidencia, marcada explícitamente como ADVISORY."""
    if not evidence:
        return []
    top = evidence[0]
    text = (
        "Revisar con el equipo responsable la evidencia citada ("
        + top.evidence_id + ") para validar el hallazgo antes de tomar una decisión."
    )
    return [{
        "kind": "ADVISORY_RECOMMENDATION",
        "recommendation_id": "ADVISORY-001",
        "text": text,
        "evidence_id": top.evidence_id,
        "note": "Derivada de la evidencia disponible; no sustituye el juicio del responsable.",
    }]
