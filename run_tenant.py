"""FASE 6A — lanza la API mínima de autenticación (puerto 8601)."""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tenant.server import run_server

if __name__ == "__main__":
    run_server(int(sys.argv[1]) if len(sys.argv) > 1 else 8601)
