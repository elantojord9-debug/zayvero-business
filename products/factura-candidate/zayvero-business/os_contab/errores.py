"""Errores tipados del bloque Contabilidad.

Mapeo que aplica el cableado (webapp):
- ErrorContab / ValueError -> 400
- NoEncontrado            -> 404
- PermisoDenegado         -> 403
Los handlers NO conocen HTTP: solo lanzan estos errores.
"""
from __future__ import annotations


class ErrorContab(Exception):
    """Error de contrato/validación del bloque (el cableado lo traduce a 400)."""


class NoEncontrado(ErrorContab):
    """Recurso inexistente (el cableado lo traduce a 404).

    Subclase de ErrorContab: el cableado debe verificar NoEncontrado
    ANTES que ErrorContab.
    """


class PermisoDenegado(Exception):
    """Permiso insuficiente (el cableado lo traduce a 403).

    Jerarquía separada de ErrorContab a propósito: un 403 nunca debe
    degradarse a 400 por el orden de los isinstance.
    """
