"""BLOQUE 6 (sprint 3, 2026-10-12) — Aprobación humana de operaciones sensibles.

Devoluciones, aprobaciones financieras y cambios de precio fuera de
umbral NO se ejecutan solos: crean una solicitud en estado
`pendiente_aprobacion` y un humano con permiso `aprobaciones.decidir`
(owner/admin) la aprueba o rechaza vía HTTP. Recién aprobada se
ejecuta la operación subyacente (la capa HTTP la ejecuta y marca
`ejecutada`).

Sin PG las operaciones sensibles conservan su comportamiento anterior
(documentado en docs/aprobaciones.md): los umbrales solo se evalúan
con backend PostgreSQL.
"""
from .modelo import (TIPOS, ESTADOS, ConfigAprobaciones,
                     SolicitudAprobacion, ErrorAprobacion,
                     requiere_aprobacion, validar_tipo)
from .api import Aprobaciones, umbrales_empresa

__all__ = ["TIPOS", "ESTADOS", "ConfigAprobaciones", "SolicitudAprobacion",
           "ErrorAprobacion", "requiere_aprobacion", "validar_tipo",
           "Aprobaciones", "umbrales_empresa"]
