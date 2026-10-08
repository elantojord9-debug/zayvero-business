#!/usr/bin/env python3
"""
ZAYVERO BUSINESS — FASE 2A: CLI del motor de anomalías.

Uso:
    .venv/bin/python detect.py data/processed/demo-retail/online_retail_II_full.parquet \\
        --company demo-retail [--output-name online_retail_II_full] [--max 1000]

Genera data/anomalies/<company>/<output-name>_anomalies.json
"""

from __future__ import annotations

import argparse
import os
import sys

BASE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE)

from anomalies.report import detect_anomalies, save_report  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(
        description="ZAYVERO BUSINESS FASE 2A: motor de detección de anomalías"
    )
    ap.add_argument("parquet", help="Parquet normalizado de FASE 1A/1C")
    ap.add_argument("--company", required=True, help="company_id (multiempresa)")
    ap.add_argument(
        "--output-name",
        default=None,
        help="nombre base de salida (por defecto: nombre del parquet)",
    )
    ap.add_argument(
        "--max",
        type=int,
        default=1000,
        help="máximo de anomalías en el reporte (default 1000)",
    )
    args = ap.parse_args()

    base_name = args.output_name or os.path.splitext(
        os.path.basename(args.parquet)
    )[0]
    print(f"[detect] analizando {args.parquet} (company={args.company}) ...")
    report = detect_anomalies(args.parquet, args.company, max_anomalies=args.max)
    out = save_report(report, args.company, base_name)
    s = report["summary"]
    print(f"[detect] reporte guardado en: {out}")
    print(
        f"[detect] anomalías: {s['total_anomalies']} "
        f"(en reporte: {s['anomalies_in_report']})"
    )
    print(f"[detect] por severidad: {s['by_severity']}")
    print(f"[detect] por tipo: {s['by_type']}")
    print(
        f"[detect] tiempo: {report['report_metadata']['elapsed_seconds']}s, "
        f"confidence promedio: {s['avg_confidence']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
