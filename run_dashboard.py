#!/usr/bin/env python3
"""ZAYVERO Business — FASE 3 Dashboard MVP.

Uso:
    .venv/bin/python run_dashboard.py [--port 8501]

Abre http://127.0.0.1:8501 en el navegador.
Los datos provienen de los outputs reales de FASE 2B y 2C
(dataset demo: UCI Online Retail II).
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from dashboard.server import run


def main():
    parser = argparse.ArgumentParser(description="ZAYVERO Business — Dashboard MVP")
    parser.add_argument("--port", type=int, default=8501,
                        help="Puerto local (default: 8501)")
    args = parser.parse_args()
    run(port=args.port)


if __name__ == "__main__":
    main()
