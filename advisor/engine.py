"""FASE 5B — Advisor Engine: orquesta pregunta → retrieval → reasoning → respuesta."""
from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional

from .models import BusinessAdvisorResponse
from .question import build_question
from .retrieval import ContextStore
from .reasoning import split_reasoning
from .uncertainty import assess
from .recommendations import select, advisory
from .response import build_answer
from .trace import build_trace


class AdvisorEngine:
    """Motor interno del AI Business Advisor (determinista, sin LLM)."""

    def __init__(self, context_path: str):
        self.store = ContextStore(context_path)
        self.context = self.store.context
        self.context_id = self.store.context_id

    def ask(self, question_text: str, top_k: int = 12) -> BusinessAdvisorResponse:
        t0 = time.time()
        question = build_question(question_text, self.context)
        evidence = self.store.retrieve(question, top_k=top_k)
        reasoning = split_reasoning(evidence)
        uncertainty = assess(question, evidence, self.context)
        text = build_answer(question, evidence, reasoning, self.context, uncertainty)
        recs = select(evidence, self.context, n=5)
        recs += advisory(question, evidence)
        trace = build_trace(question, self.context_id, evidence)
        trace["runtime_seconds"] = round(time.time() - t0, 3)

        response_id = "R-" + hashlib.sha256(
            (question.question_id + self.context_id).encode("utf-8")
        ).hexdigest()[:12]

        # Datos de apoyo por sección
        key_findings = [
            {"finding_id": e.evidence_id, "claim": e.claim, "value": e.value,
             "period": e.period, "relevance_score": e.relevance_score}
            for e in evidence if e.source_type == "finding"
        ][:5]
        risks = [
            {"risk_id": e.evidence_id, "claim": e.claim, "relevance_score": e.relevance_score}
            for e in evidence if e.source_type == "risk"
        ][:5]
        opportunities = [
            {"opportunity_id": e.evidence_id, "claim": e.claim, "relevance_score": e.relevance_score}
            for e in evidence if e.source_type == "opportunity"
        ][:5]
        predictions = [
            {"claim": e.claim, "source_type": e.source_type, "relevance_score": e.relevance_score}
            for e in evidence if e.source_type in ("prediction", "prediction_validation", "trend")
        ][:5]
        limitations = [
            e.claim for e in evidence if e.source_type == "limitation"
        ][:5]

        return BusinessAdvisorResponse(
            response_id=response_id,
            question=question,
            answer=text["answer"],
            executive_summary=text["executive_summary"],
            facts=reasoning["facts"][:8],
            key_findings=key_findings,
            risks=risks,
            opportunities=opportunities,
            predictions=predictions,
            recommendations=recs,
            uncertainty=uncertainty,
            limitations=limitations,
            evidence_used=[e.evidence_id for e in evidence],
            confidence={
                "context_confidence_score": self._ctx_confidence(),
                "evidence_count": len(evidence),
                "note": "La confianza refleja la completitud del contexto y la evidencia recuperada.",
            },
            trace=trace,
        )

    def _ctx_confidence(self) -> Any:
        cc = self.context.get("context_confidence") or {}
        return cc.get("context_confidence_score")
