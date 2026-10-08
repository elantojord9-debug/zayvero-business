#!/usr/bin/env python3
"""
ZAYVERO BUSINESS — FASE 2B (CLI).

Uso:
    .venv/bin/python find.py <anomalies.json|parquet> --company <id>
    .venv/bin/python find.py data/anomalies/demo-retail/online_retail_II_full_anomalies.json --company demo-retail
    .venv/bin/python find.py data/processed/demo-retail/online_retail_II_full.parquet --company demo-retail

Si se pasa un .parquet, ejecuta FASE 2A sin límite práctico y luego 2B
(todas las anomalías disponibles, no solo las 1,000 del reporte guardado).
Si se pasa un .json, consume el AnomalyReport tal cual.

Salida: data/findings/<company_id>/<base>_findings.json
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from findings.report import build_findings, build_from_parquet, save_report


def main() -> int:
    ap = argparse.ArgumentParser(description="FASE 2B: hallazgos empresariales")
    ap.add_argument("source", help="AnomalyReport JSON o Parquet normalizado")
    ap.add_argument("--company", required=True, help="company_id (obligatorio)")
    ap.add_argument("--top", type=int, default=100, help="límite de presentación")
    args = ap.parse_args()

    src = args.source
    if not os.path.isfile(src):
        print(f"ERROR: no existe: {src}", file=sys.stderr)
        return 1
    base = os.path.splitext(os.path.basename(src))[0].replace(
        "_anomalies", ""
    )

    if src.lower().endswith(".parquet"):
        print("Ejecutando FASE 2A (todas las anomalías) + FASE 2B...")
        report = build_from_parquet(src, args.company, top_limit=args.top)
    else:
        print("Consumiendo AnomalyReport de FASE 2A + FASE 2B...")
        report = build_findings(src, args.company, top_limit=args.top)

    out = save_report(report, args.company, base)
    s = report["summary"]
    print(f"Hallazgos: {s['total_findings']} "
          f"(dedup: {s['anomalies_deduped']} anomalías agrupadas)")
    print(f"Por prioridad: {s['by_priority']}")
    print(f"Impacto promedio: {s['avg_impact_score']}")
    print(f"Guardado en: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
