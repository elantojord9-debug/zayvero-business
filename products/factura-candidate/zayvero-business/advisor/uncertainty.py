"""FASE 5B — Evaluación determinista de incertidumbre.

Categorías: LOW / MEDIUM / HIGH / UNKNOWN.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .models import BusinessQuestion, RetrievedEvidence


def assess(question: BusinessQuestion, evidence: List[RetrievedEvidence],
           context: Dict[str, Any]) -> Dict[str, Any]:
    """Evalúa la incertidumbre de la respuesta según la evidencia disponible."""
    missing: List[str] = []
    if not evidence:
        return {
            "uncertainty_level": "UNKNOWN",
            "evidence_quality": "INSUFFICIENT",
            "context_confidence": _context_confidence(context),
            "missing_information": ["No se encontró evidencia relevante para esta pregunta en el contexto disponible."],
            "notes": ["No se puede producir una conclusión: evidencia insuficiente."],
        }
    qualities = [str((e.trace.get("uid") or "")) for e in evidence]
    low_evidence = sum(1 for e in evidence if e.source_type == "limitation")
    # Si la mejor evidencia es débil, la incertidumbre es alta
    best = evidence[0].relevance_score
    if best < 8.0:
        level = "HIGH"
    elif best < 25.0 or low_evidence > 0:
        level = "MEDIUM"
    else:
        level = "LOW"
    if question.question_type == "UNKNOWN":
        missing.append("La pregunta no pudo clasificarse en una categoría conocida; la respuesta es orientativa.")
    return {
        "uncertainty_level": level,
        "evidence_quality": "LOW" if level == "HIGH" else ("MEDIUM" if level == "MEDIUM" else "HIGH"),
        "context_confidence": _context_confidence(context),
        "missing_information": missing,
        "notes": [
            "La incertidumbre refleja la calidad y cantidad de la evidencia recuperada, "
            "no una probabilidad de acierto.",
        ],
    }


def _context_confidence(context: Dict[str, Any]) -> Any:
    cc = context.get("context_confidence") or {}
    return cc.get("context_confidence_score", "no disponible")
