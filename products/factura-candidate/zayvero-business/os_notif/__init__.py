"""BLOQUE B (2026-10-13) — NOTIFICACIONES Y REPORTES PROGRAMADOS.

Centro de notificaciones in-app por empresa + preferencias con
consentimiento explícito + reportes programados con bandeja de
salida SIMULADA.

REGLA DURA (no negociable): NUNCA se envían mensajes reales
(WhatsApp/SMS/correos). Todo "envío externo" queda en la bandeja de
salida con estado SIMULADO y requiere aprobación humana en el módulo
`aprobaciones/`; aun aprobada, el despacho real es una
implementación futura (ver docs/bloque-notificaciones.md).

Datos 100 % sintéticos, PostgreSQL local.
"""
from __future__ import annotations

from .errores import ErrorNotif, NoEncontrado, PermisoDenegado  # noqa: F401
from .modelo import (  # noqa: F401
    TIPOS_ALERTA, PRIORIDADES, CANALES, CANALES_EXTERNOS,
    FRECUENCIAS, FORMATOS_PROG, ESTADOS_SALIDA, ROLES,
    MODULOS_REPORTE,
)

__all__ = ["ErrorNotif", "NoEncontrado", "PermisoDenegado",
           "TIPOS_ALERTA", "PRIORIDADES", "CANALES", "CANALES_EXTERNOS",
           "FRECUENCIAS", "FORMATOS_PROG", "ESTADOS_SALIDA", "ROLES",
           "MODULOS_REPORTE"]
