"""FASE 5C — Pipeline: Question → Advisor Engine → Structured Response →
Evidence Selection → LLM Provider → LLM Response → Response Validator →
Safe Natural Language Answer.

El LLM nunca se salta el Advisor Engine. Si el LLM no está disponible, se
devuelve la respuesta estructurada del Advisor Engine como fallback seguro.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional

from .models import (
    LLMAdvisorRequest,
    LLMAdvisorResponse,
    LLMResponseValidation,
    SYSTEM_PROMPT_VERSION,
)
from .provider import LLMProvider, LLMUnavailableError, provider_from_env
from .request_builder import build_request, render_messages
from .system_prompt import build_system_prompt
from .validator import validate


def _safe_answer_from_engine(advisor_response: Dict[str, Any], reason: str) -> str:
    """Respuesta segura cuando el LLM no está disponible o la validación falla."""
    base = advisor_response.get("answer", "") or "No tengo suficiente evidencia para determinarlo."
    return (
        f"{base}\n\n"
        f"[Respuesta directa del motor de ZAYVERO — {reason}. "
        "Cifras y conclusiones sin modificar.]"
    )


def _key_points(advisor_response: Dict[str, Any]) -> List[str]:
    pts = []
    if advisor_response.get("executive_summary"):
        pts.append(str(advisor_response["executive_summary"]))
    for f in (advisor_response.get("key_findings", []) or [])[:3]:
        if isinstance(f, dict):
            pts.append(str(f.get("claim") or f.get("title")))
    return pts[:5]


class LLMAdvisorPipeline:
    """Orquesta el flujo completo 5C."""

    def __init__(self, context_path: str, provider: Optional[LLMProvider] = None):
        # Import diferido: advisor es una fase anterior que no se modifica.
        from advisor.engine import AdvisorEngine

        self.engine = AdvisorEngine(context_path)
        self.provider = provider or provider_from_env()
        self.system_prompt = build_system_prompt()

    def ask(self, question: str) -> Dict[str, Any]:
        """Ejecuta el flujo completo y devuelve el dict serializable."""
        t0 = time.time()
        advisor_response = self.engine.ask(question).to_dict()
        request = build_request(advisor_response)

        fallback = False
        validation: Optional[LLMResponseValidation] = None
        token_usage = {"input_tokens": None, "output_tokens": None, "total_tokens": None}
        latency = None
        model = "none"
        provider_name = getattr(self.provider, "name", "unknown")

        try:
            result = self.provider.generate(request, self.system_prompt)
            model = result.model
            provider_name = result.provider_name
            latency = result.latency_seconds
            token_usage = {
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "total_tokens": result.total_tokens,
            }
            validation = validate(request, result.text, model, provider_name)
            if validation.status == "VALID":
                answer = result.text
            else:
                # VALIDATION_FAILED / INSUFFICIENT_EVIDENCE: respuesta segura.
                fallback = True
                answer = _safe_answer_from_engine(
                    advisor_response,
                    f"la respuesta del LLM no superó la validación ({validation.status})",
                )
        except LLMUnavailableError:
            fallback = True
            validation = LLMResponseValidation(
                request_id=request.request_id,
                status="LLM_UNAVAILABLE",
                validated_claims=[],
                unsupported_claims=[],
                numeric_checks=[],
                evidence_checks=[],
                causality_checks=[],
                prediction_checks=[],
                warnings=["Proveedor LLM no disponible; se devolvió la respuesta estructurada."],
                trace={"provider": provider_name},
            )
            answer = _safe_answer_from_engine(
                advisor_response, "el servicio de lenguaje no está disponible"
            )

        response_id = "LA-" + hashlib.sha256(
            (request.request_id + (model or "")).encode("utf-8")
        ).hexdigest()[:12]
        limitations = [str(x) for x in (advisor_response.get("limitations", []) or [])]
        if fallback and "Se utilizó la respuesta directa del motor determinista." not in limitations:
            limitations.append("Se utilizó la respuesta directa del motor determinista.")

        resp = LLMAdvisorResponse(
            response_id=response_id,
            request_id=request.request_id,
            answer=answer,
            key_points=_key_points(advisor_response),
            limitations=limitations,
            evidence_used=list(request.trace.get("evidence_ids", [])),
            confidence=advisor_response.get("confidence", {}),
            model=model,
            prompt_version=SYSTEM_PROMPT_VERSION,
            provider=provider_name,
            validation_status=validation.status if validation else "UNKNOWN",
            token_usage=token_usage,
            latency_seconds=latency,
            fallback=fallback,
            trace={
                "question_id": advisor_response.get("question", {}).get("question_id"),
                "context_id": advisor_response.get("trace", {}).get("context_id"),
                "request_id": request.request_id,
                "system_prompt_version": SYSTEM_PROMPT_VERSION,
                "validation": validation.to_dict() if validation else None,
                "runtime_seconds": round(time.time() - t0, 3),
            },
        )
        return resp.to_dict()
