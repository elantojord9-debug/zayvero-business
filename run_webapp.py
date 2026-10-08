"""FASE 6B — Punto de entrada de la web app ZAYVERO Business.

Uso:
    .venv/bin/python run_webapp.py [--port 8701]

Después: abrir http://127.0.0.1:8701
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from webapp.server import run_server

if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8701
    if len(sys.argv) > 2 and sys.argv[1] == "--port":
        port = int(sys.argv[2])
    run_server(port)
