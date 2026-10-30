"""BLOQUE PLATAFORMA SAAS (2026-10-13) — Registro SaaS, planes y límites,
métricas de uso, onboarding, centro de ayuda y gestión de usuarios.

Contrato: `rutas()` -> {"saas/...": fn(bundle, ctx, query|body)}.
Ver os_saas/rutas.py y docs/bloque-saas.md.

REGLA DURA: sin facturación real — preparación local, datos sintéticos.
"""
from .rutas import manejar, rutas

__all__ = ["rutas", "manejar"]
