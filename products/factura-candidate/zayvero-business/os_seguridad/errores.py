"""BLOQUE A SEGURIDAD — Errores del bloque.

Los NOMBRES importan: el despachador de webapp/os_routes.py traduce por
nombre de excepción (ver _despachar_modulo_os):
  - ValueError        -> 400
  - "NoEncontrado"    -> 404
  - "PermisoDenegado" -> 403
  - resto             -> 500

Por eso `rutas.py` deja propagar PermisoDenegado/NoEncontrado tal cual y
traduce ErrorSeguridad genérico a ValueError (400). No heredar de
ValueError: el dispatcher evalúa ValueError ANTES que el nombre y un
404 se convertiría en 400.
"""

from __future__ import annotations


class ErrorSeguridad(Exception):
    """Error del bloque de seguridad. Mensajes genéricos hacia afuera:
    nunca distinguen entre 'no existe', 'clave incorrecta' o 'código
    erróneo' (anti-enumeración)."""


class PermisoDenegado(ErrorSeguridad):
    """403 en el dispatcher (nombre exacto requerido)."""


class NoEncontrado(ErrorSeguridad):
    """404 en el dispatcher (nombre exacto requerido)."""
