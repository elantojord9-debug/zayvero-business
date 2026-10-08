"""FASE 5B — AI Business Advisor Engine (Foundation).

Motor interno determinista (sin LLM): recibe un BusinessIntelligenceContext
de FASE 5A y una pregunta en lenguaje natural, y produce una
BusinessAdvisorResponse estructurada basada solo en evidencia.

Arquitectura (motor -> respuesta estructurada -> futuro LLM):
    BusinessIntelligenceContext
            |
    AdvisorEngine (este paquete)
            |
    BusinessAdvisorResponse (JSON)
            |
    Future LLM = NO IMPLEMENTADO en esta fase
"""
from .engine import AdvisorEngine
from .models import BusinessQuestion, RetrievedEvidence, BusinessAdvisorResponse, ENGINE_VERSION

__all__ = [
    "AdvisorEngine",
    "BusinessQuestion",
    "RetrievedEvidence",
    "BusinessAdvisorResponse",
    "ENGINE_VERSION",
]
