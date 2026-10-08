"""FASE 5A CLI: construye el BusinessIntelligenceContext desde los outputs reales.

Uso:
    .venv/bin/python build_business_context.py [--company demo-retail]
    → data/business_context/<company>/online_retail_II_business_context.json
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from business_context import build_business_context, save_context, load_inputs


def main() -> int:
    ap = argparse.ArgumentParser(description="FASE 5A: AI Business Advisor Foundation")
    ap.add_argument("--company", default="demo-retail")
    args = ap.parse_args()

    t0 = time.perf_counter()
    inputs = load_inputs(args.company)
    context = build_business_context(inputs, company_id=args.company)
    out = os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "data", "business_context", args.company,
        "online_retail_II_business_context.json",
    )
    save_context(context, out)
    dt = time.perf_counter() - t0

    print(f"OK → {out}")
    print(f"findings: {len(context['critical_findings'])} | risks: {len(context['key_risks'])} | "
          f"opportunities: {len(context['key_opportunities'])} | trends: {len(context['key_trends'])} | "
          f"recommendations: {len(context['recommendations'])} | limitations: {len(context['limitations'])} | "
          f"evidence: {len(context['evidence_index'])}")
    print(f"confidence: {context['context_confidence']['context_confidence_score']} | "
          f"attention: {context['attention_summary']['attention_level']}")
    print(f"runtime: {dt:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
