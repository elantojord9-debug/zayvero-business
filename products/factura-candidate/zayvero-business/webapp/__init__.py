"""FASE 6B — Web App ZAYVERO Business.

Paquete aislado para la experiencia web del cliente:

  LOGIN → MI EMPRESA → RESUMEN EJECUTIVO → HALLAZGOS → OPORTUNIDADES
  → PREDICCIONES → ADVISOR ("Pregúntale a ZAYVERO")

El frontend solo presenta información. Toda la lógica de negocio, permisos,
tenant isolation, cálculos y acceso a datos viven en el backend (fases 1-6A).

API pública:

  from webapp import TenantData, WebappConfig
  from webapp.server import run_server
"""

from __future__ import annotations

from .data import TenantData, WebappConfig

__all__ = ["TenantData", "WebappConfig"]
