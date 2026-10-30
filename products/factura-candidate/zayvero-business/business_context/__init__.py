"""FASE 5A — AI Business Advisor Foundation.

Business Intelligence Context: capa estructurada, 100% determinista (sin LLM),
que consolida los outputs reales de las fases anteriores (1B/1C, 2B, 2C, 4A,
4B, 4C) en un contexto empresarial unificado para el futuro AI Business
Advisor.

API pública:
    build_business_context(inputs) -> dict  (BusinessIntelligenceContext)
    save_context(context, path) -> path
    load_inputs(base_dir, company_id) -> dict
"""

from .report import build_business_context, save_context, load_inputs

__all__ = ["build_business_context", "save_context", "load_inputs"]
