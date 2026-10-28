"""BLOQUE B (2026-10-13) — Errores de dominio de os_notif."""
from __future__ import annotations


class ErrorNotif(Exception):
    """Error de dominio: se traduce a 400 en el cableado HTTP."""


class NoEncontrado(ErrorNotif):
    """Recurso inexistente: se traduce a 404."""


class PermisoDenegado(ErrorNotif):
    """Permiso insuficiente: se traduce a 403."""
