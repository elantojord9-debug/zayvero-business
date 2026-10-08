#!/usr/bin/env python3
"""
ZAYVERO BUSINESS — FASE 2C (CLI): Contextual Business Intelligence.

Uso:
    .venv/bin/python context.py <findings.json> <parquet> --company <id> [--max N]

Consume los Business Findings de FASE 2B y genera BusinessContextFinding
por cada uno (contexto, posibles explicaciones, recomendaciones de revisión).

Salida: data/context/<company_id>/<base>_context.json
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from context.report import build_context_findings, save_report


def main() -> int:
    ap = argparse.ArgumentParser(description="FASE 2C: contexto empresarial")
    ap.add_argument("findings", help="BusinessFindingReport JSON de FASE 2B")
    ap.add_argument("parquet", help="Parquet normalizado FASE 1C")
    ap.add_argument("--company", required=True, help="company_id (obligatorio)")
    ap.add_argument("--max", type=int, default=None,
                    help="límite de hallazgos a contextualizar")
    args = ap.parse_args()

    for p in (args.findings, args.parquet):
        if not os.path.isfile(p):
            print(f"ERROR: no existe: {p}", file=sys.stderr)
            return 1
    base = os.path.splitext(os.path.basename(args.findings))[0].replace(
        "_findings", "")

    t0 = time.time()
    print("Construyendo contexto empresarial (FASE 2C)...")
    report = build_context_findings(args.findings, args.parquet,
                                    args.company, max_findings=args.max)
    out = save_report(report, args.company, base)
    dt = time.time() - t0
    s = report["summary"]
    print(f"Contextualizados: {s['total']} en {dt:.1f}s")
    print(f"Por estado: {s['by_context_status']}")
    print(f"Por recurrencia: {s['by_recurrence']}")
    print(f"Guardado en: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
