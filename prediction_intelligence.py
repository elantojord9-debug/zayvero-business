#!/usr/bin/env python3
"""
ZAYVERO BUSINESS — FASE 4B: Prediction Intelligence (CLI).

Uso:
    .venv/bin/python prediction_intelligence.py \
        [--input data/predictions/demo-retail/online_retail_II_full_predictions.json] \
        [--output data/prediction_intelligence/demo-retail/online_retail_II_prediction_intelligence.json]

Lee los outputs REALES de FASE 4A (dinámicamente, sin hardcodear) y genera
PredictionInsights deterministas (sin LLM). No modifica FASE 4A.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prediction_intelligence import build_prediction_intelligence

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(
    BASE_DIR, "data", "predictions", "demo-retail",
    "online_retail_II_full_predictions.json")
DEFAULT_OUTPUT = os.path.join(
    BASE_DIR, "data", "prediction_intelligence", "demo-retail",
    "online_retail_II_prediction_intelligence.json")


def main() -> int:
    parser = argparse.ArgumentParser(description="FASE 4B — Prediction Intelligence")
    parser.add_argument("--input", default=DEFAULT_INPUT,
                        help="JSON de predicciones de FASE 4A")
    parser.add_argument("--output", default=DEFAULT_OUTPUT,
                        help="JSON de salida de FASE 4B")
    args = parser.parse_args()

    t0 = time.perf_counter()
    with open(args.input, "r", encoding="utf-8") as fh:
        predictions_data = json.load(fh)

    report = build_prediction_intelligence(predictions_data)
    runtime = time.perf_counter() - t0
    report["report_metadata"]["runtime_seconds"] = round(runtime, 2)

    os.makedirs(os.path.dirname(args.output), exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=1)

    s = report["summary"]
    print("FASE 4B — Prediction Intelligence")
    print("  insights generados : %d" % s["total_predictions"])
    print("  calidad            : HIGH=%d MODERATE=%d LOW=%d INSUFFICIENT=%d" % (
        s["high_quality"], s["moderate_quality"], s["low_quality"], s["insufficient"]))
    print("  atención           : URGENT=%d IMPORTANT=%d REVIEW=%d MONITOR=%d" % (
        s["urgent"], s["important"], s["review"], s["monitor"]))
    print("  incertidumbre      : HIGH=%d MEDIUM=%d LOW=%d" % (
        s["high_uncertainty"], s["medium_uncertainty"], s["low_uncertainty"]))
    print("  riesgo de caída    : HIGH=%d MEDIUM=%d LOW=%d" % (
        s["high_decline_risk"], s["medium_decline_risk"], s["low_decline_risk"]))
    print("  confianza media    : %s | attention medio: %s" % (
        s["average_confidence"], s["average_attention_score"]))
    print("  tiempo de ejecución: %.2fs" % runtime)
    print("  salida             : %s" % args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
