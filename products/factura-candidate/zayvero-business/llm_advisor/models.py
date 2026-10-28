"""FASE 5C — LLM Business Advisor (integración controlada).

Modelos de datos: LLMAdvisorRequest, LLMAdvisorResponse, LLMResponseValidation,
ProviderResult. Todo es JSON-serializable.

El LLM NUNCA es fuente de datos: solo recibe la pregunta, la respuesta
estructurada del Advisor Engine (FASE 5B) y la evidencia mínima necesaria.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional


VALIDATION_STATUSES = ["VALID", "VALIDATION_FAILED", "LLM_UNAVAILABLE", "INSUFFICIENT_EVIDENCE"]

SYSTEM_PROMPT_VERSION = "zayvero-llm-sys-v1"
ENGINE_COMPONENT = "llm-advisor-5c-1.0.0"


@dataclass
class LLMAdvisorRequest:
    """Input validado para el LLM: pregunta + respuesta estructurada + evidencia mínima."""

    request_id: str
    question: str
    normalized_question: str
    advisor_response: Dict[str, Any]
    evidence: Dict[str, Any]
    system_rules: List[str]
    context_confidence: Optional[float]
    uncertainty: Dict[str, Any]
    system_prompt_version: str
    trace: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderResult:
    """Resultado crudo del proveedor LLM."""

    text: str
    model: str
    input_tokens: Optional[int]
    output_tokens: Optional[int]
    total_tokens: Optional[int]
    latency_seconds: Optional[float]
    provider_name: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LLMAdvisorResponse:
    """Respuesta natural del LLM, validada."""

    response_id: str
    request_id: str
    answer: str
    key_points: List[str]
    limitations: List[str]
    evidence_used: List[str]
    confidence: Dict[str, Any]
    model: str
    prompt_version: str
    provider: str
    validation_status: str
    token_usage: Dict[str, Any]
    latency_seconds: Optional[float]
    fallback: bool
    trace: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LLMResponseValidation:
    """Auditoría posterior de la respuesta del LLM."""

    request_id: str
    status: str
    validated_claims: List[Dict[str, Any]]
    unsupported_claims: List[Dict[str, Any]]
    numeric_checks: List[Dict[str, Any]]
    evidence_checks: List[Dict[str, Any]]
    causality_checks: List[Dict[str, Any]]
    prediction_checks: List[Dict[str, Any]]
    warnings: List[str]
    trace: Dict[str, Any]

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)
