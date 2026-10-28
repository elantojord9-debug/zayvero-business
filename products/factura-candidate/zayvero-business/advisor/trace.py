"""FASE 5B — Trazabilidad de respuestas."""
from __future__ import annotations

import datetime
from typing import Any, Dict, List

from .models import BusinessQuestion, RetrievedEvidence, ENGINE_VERSION


def build_trace(question: BusinessQuestion, context_id: str,
                evidence: List[RetrievedEvidence]) -> Dict[str, Any]:
    return {
        "question_id": question.question_id,
        "context_id": context_id,
        "evidence_ids": [e.evidence_id for e in evidence],
        "retrieval_method": "indice de contexto + puntuacion de relevancia determinista (coincidencia de palabras, entidad, prioridad, impacto, confianza)",
        "reasoning_rules": "mapeo determinista de evidencia a FACTS/OBSERVATIONS/RISKS/OPPORTUNITIES/PREDICTIONS; sin conversion de correlacion a causalidad",
        "uncertainty_rules": "niveles LOW/MEDIUM/HIGH/UNKNOWN segun puntuacion de relevancia y calidad de evidencia",
        "timestamp": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "engine_version": ENGINE_VERSION,
    }
