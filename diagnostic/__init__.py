"""FASE 7B — Executive Business Diagnostic.

API pública: build_diagnostic() ensambla el Diagnóstico Ejecutivo ZAYVERO
a partir de los outputs reales existentes (5A, 4B, 4C), de forma
determinista y sin crear inteligencia nueva.
"""

from __future__ import annotations

from .models import (
    DIAGNOSTIC_VERSION,
    SECTIONS,
    REQUIRED_TOP_LEVEL,
    AVAILABLE,
    LIMITED,
    INSUFFICIENT,
    STATUS_NOTES,
    SUBTITLE,
    SUGGESTED_ADVISOR_QUESTIONS,
    validate,
)
from .builder import build_diagnostic

__all__ = [
    "DIAGNOSTIC_VERSION",
    "SECTIONS",
    "REQUIRED_TOP_LEVEL",
    "AVAILABLE",
    "LIMITED",
    "INSUFFICIENT",
    "STATUS_NOTES",
    "SUBTITLE",
    "SUGGESTED_ADVISOR_QUESTIONS",
    "validate",
    "build_diagnostic",
]
