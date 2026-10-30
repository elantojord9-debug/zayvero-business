"""FASE 5B — AI Business Advisor Engine (Foundation, determinista, sin LLM).

Modelos de datos: BusinessQuestion, RetrievedEvidence, BusinessAdvisorResponse.
Todo es JSON-serializable.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


QUESTION_TYPES = [
    "URGENT_ISSUE", "FINANCIAL_PROBLEM", "OPPORTUNITY", "PRODUCT", "CUSTOMER",
    "SALES", "PRICE", "RISK", "PREDICTION", "TREND", "RECOMMENDATION",
    "EXPLANATION", "GENERAL_BUSINESS", "UNKNOWN",
]

ENGINE_VERSION = "advisor-5b-1.0.0"


@dataclass
class BusinessQuestion:
    question_id: str
    original_question: str
    normalized_question: str
    question_type: str
    entities: List[Dict[str, str]]
    requested_period: Optional[str]
    requested_metric: Optional[str]
    intent: str
    trace: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class RetrievedEvidence:
    evidence_id: str
    source: str
    source_type: str
    relevance_score: float
    claim: str
    value: Any
    period: Optional[str]
    trace: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BusinessAdvisorResponse:
    response_id: str
    question: BusinessQuestion
    answer: str
    executive_summary: str
    facts: List[str]
    key_findings: List[Dict[str, Any]]
    risks: List[Dict[str, Any]]
    opportunities: List[Dict[str, Any]]
    predictions: List[Dict[str, Any]]
    recommendations: List[Dict[str, Any]]
    uncertainty: Dict[str, Any]
    limitations: List[str]
    evidence_used: List[str]
    confidence: Dict[str, Any]
    trace: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["question"] = self.question.to_dict()
        return d
