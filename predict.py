#!/usr/bin/env python3
"""
ZAYVERO BUSINESS — FASE 4A: Prediction Engine MVP (CLI).

Uso:
    .venv/bin/python predict.py data/processed/demo-retail/online_retail_II_full.parquet \
        --company demo-retail [--output-dir data/predictions] [--top-products 25] [--top-countries 10]

Genera data/predictions/<company_id>/<base>_predictions.json
"""

from __future__ import annotations

import argparse
import json
import sys

from prediction import build_predictions


def main() -> int:
    ap = argparse.ArgumentParser(description="ZAYVERO Business FASE 4A — Prediction Engine MVP")
    ap.add_argument("parquet", help="Parquet normalizado FASE 1A/1C")
    ap.add_argument("--company", required=True, help="company_id (obligatorio)")
    ap.add_argument("--output-dir", default="data/predictions", help="directorio de salida")
    ap.add_argument("--top-products", type=int, default=25)
    ap.add_argument("--top-countries", type=int, default=10)
    args = ap.parse_args()

    report = build_predictions(
        args.parquet,
        company_id=args.company,
        output_dir=args.output_dir,
        top_products=args.top_products,
        top_countries=args.top_countries,
    )
    s = report["summary"]
    md = report["report_metadata"]
    print(f"predicciones: {s['n_predictions']} (OK={s['n_ok']}, INSUFFICIENT_DATA={s['n_insufficient_data']})")
    print(f"métodos: {s['methods_used']}")
    print(f"confianza media: {s['confidence']['mean']}")
    print(f"runtime: {md['runtime_seconds']}s")
    print(f"salida: {md.get('output_path')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
