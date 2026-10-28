"""FASE 8 — Productización comercial de ZAYVERO Business.

Capa de experiencia de producto. NO crea inteligencia nueva: consume
únicamente los resultados de las fases 1A–7C y los presenta como una
experiencia SaaS coherente (primera entrada, onboarding, estados,
diagnóstico, centro de inteligencia, advisor, demo, planes).

Todo el contenido es determinista y presentacional. La lógica de negocio,
los permisos y el aislamiento por tenant viven en el backend (6A) y en
los motores existentes; este paquete solo los consulta.
"""

from .experience import (
    build_onboarding,
    build_post_sequence,
    build_product_overview,
    build_product_state,
)
from .models import (
    BENEFITS,
    DATA_SOURCES,
    FIRST_ENTRY,
    FORBIDDEN_CLAIMS,
    ONBOARDING_STEPS,
    PLANS,
    POST_SEQUENCE,
    PRODUCT_STATES,
)

__all__ = [
    "BENEFITS",
    "DATA_SOURCES",
    "FIRST_ENTRY",
    "FORBIDDEN_CLAIMS",
    "ONBOARDING_STEPS",
    "PLANS",
    "POST_SEQUENCE",
    "PRODUCT_STATES",
    "build_onboarding",
    "build_post_sequence",
    "build_product_overview",
    "build_product_state",
]
