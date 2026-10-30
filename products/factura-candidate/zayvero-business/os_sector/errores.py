"""BLOQUE SECTOR — Errores tipados.

Mapeo que aplica el cableado (webapp), igual que os_contab:
- ErrorSector / ValueError -> 400
- NoEncontrado            -> 404
- PermisoDenegado         -> 403
Los handlers NO conocen HTTP: solo lanzan estos errores.
"""
from __future__ import annotations


class ErrorSector(Exception):
    """Error de contrato/validación del bloque (el cableado lo traduce a 400)."""


class NoEncontrado(ErrorSector):
    """Recurso inexistente (el cableado lo traduce a 404).

    Subclase de ErrorSector: el cableado debe verificar NoEncontrado
    ANTES que ErrorSector.
    """


class PermisoDenegado(Exception):
    """Permiso insuficiente (el cableado lo traduce a 403).

    Jerarquía separada de ErrorSector a propósito: un 403 nunca debe
    degradarse a 400 por el orden de los isinstance.
    """
