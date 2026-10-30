"""FASE 5B — Razonamiento empresarial determinista.

Transforma evidencia recuperada en FACTS / OBSERVATIONS / RISKS /
OPPORTUNITIES / PREDICTIONS / RECOMMENDATIONS. Nunca convierte
correlación en causalidad.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .models import BusinessQuestion, RetrievedEvidence


def split_reasoning(evidence: List[RetrievedEvidence]) -> Dict[str, List[str]]:
    """Clasifica la evidencia recuperada en bloques de razonamiento."""
    facts: List[str] = []
    observations: List[str] = []
    risks: List[str] = []
    opportunities: List[str] = []
    predictions: List[str] = []
    for e in evidence:
        item = {
            "evidence_id": e.evidence_id,
            "claim": e.claim,
            "source_type": e.source_type,
            "value": e.value,
            "period": e.period,
        }
        if e.source_type == "finding":
            facts.append(_fact_from_finding(e))
        elif e.source_type == "risk":
            risks.append(e.claim)
        elif e.source_type == "opportunity":
            opportunities.append(e.claim)
        elif e.source_type in ("trend", "prediction", "prediction_validation"):
            predictions.append(e.claim)
        elif e.source_type == "snapshot":
            facts.append("Dato observado: " + e.claim)
        elif e.source_type == "limitation":
            observations.append("Limitación registrada: " + e.claim)
        else:
            observations.append(e.claim)
    return {
        "facts": facts, "observations": observations, "risks": risks,
        "opportunities": opportunities, "predictions": predictions,
    }


def _fact_from_finding(e: RetrievedEvidence) -> str:
    return (
        "Hecho observado: " + e.claim +
        (" (periodo: " + str(e.period) + ")" if e.period else "")
    )


def no_causality_guard(text: str) -> str:
    """Verifica que el texto no afirme causalidad prohibida."""
    banned = ["porque", "causado por", "se debe a", "el motivo fue", "debido a que"]
    lowered = text.lower()
    for b in banned:
        if b in lowered and "no permite determinar" not in lowered:
            return (
                "Los datos disponibles no permiten determinar por sí solos la causa. "
                "Se requiere revisión adicional para establecerla."
            )
    return text
