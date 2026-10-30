"""ZAYVERO Business OS — persistencia PostgreSQL (LOTE 9A, local).

Activación: ZAYVERO_OS_BACKEND=postgres + ZAYVERO_OS_DATABASE_URL.
Por defecto: memoria (los despliegues públicos nunca la activan solos).
"""
from .pg import OSPgStore, connect, migrate, tx  # noqa: F401

__all__ = ["OSPgStore", "connect", "migrate", "tx"]
