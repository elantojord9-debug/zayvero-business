"""BLOQUE A SEGURIDAD (2026-10-13, roadmap P1-12 / issue #2).

MFA/TOTP + códigos de recuperación + recuperación asistida +
exportación/borrado de datos + políticas de retención.

API pública:
    from os_seguridad import mfa, totp, envoltorio, crear_registro
    from os_seguridad.rutas import rutas
"""

from .almacen import RegistroSeguridad, crear_registro
from . import errores, totp, envoltorio, mfa

__all__ = ["RegistroSeguridad", "crear_registro", "errores", "totp",
           "envoltorio", "mfa"]
