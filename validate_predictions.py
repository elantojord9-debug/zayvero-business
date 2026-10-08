#!/usr/bin/env python3
"""
ZAYVERO BUSINESS — FASE 4C CLI: Prediction Validation & Learning.

Valida el desempeño REAL de las predicciones de FASE 4A contra los datos
reales del dataset normalizado, sin modificar 4A ni 4B.

Uso:
    .venv/bin/python validate_predictions.py \\
        --predictions data/predictions/demo-retail/online_retail_II_full_predictions.json \\
        --parquet data/processed/demo-retail/online_retail_II_full.parquet \\
        --company demo-retail \\
        [--insights data/prediction_intelligence/demo-retail/online_retail_II_prediction_intelligence.json] \\
        [--accuracy-threshold-pct 5.0] \\
        [--output data/prediction_validation/demo-retail/online_retail_II_prediction_validation.json]

Si el dataset no contiene los periodos pronosticados, las predicciones
quedan PENDING (honesto): el sistema está listo para cuando lleguen
nuevos datos.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from prediction_validation import build_validation_report, save_report


def main() -> int:
    ap = argparse.ArgumentParser(description="FASE 4C: valida predicciones de 4A.")
    ap.add_argument("--predictions", required=True, help="JSON de predicciones FASE 4A")
    ap.add_argument("--parquet", required=True, help="Parquet normalizado FASE 1C")
    ap.add_argument("--company", required=True, help="company_id")
    ap.add_argument("--insights", default=None,
                    help="JSON de Prediction Intelligence FASE 4B (opcional)")
    ap.add_argument("--accuracy-threshold-pct", type=float, default=5.0,
                    help="Umbral %% para bias ACCURATE (default 5.0)")
    ap.add_argument("--output", default=None, help="Ruta del JSON de salida")
    args = ap.parse_args()

    output = args.output or os.path.join(
        "data", "prediction_validation", args.company,
        "prediction_validation.json",
    )

    report = build_validation_report(
        predictions_path=args.predictions,
        parquet_path=args.parquet,
        company_id=args.company,
        insights_path=args.insights,
        accuracy_threshold_pct=args.accuracy_threshold_pct,
    )
    save_report(report, output)

    s = report["summary"]
    print("FASE 4C — Prediction Validation & Learning")
    print("  output: %s" % output)
    print("  total: %d | VALIDATED: %d | PENDING: %d | NOT_AVAILABLE: %d | INVALID: %d"
          % (s["total_predictions"], s["validated"], s["pending"],
             s["not_available"], s["invalid"]))
    print("  model_performance_status: %s" % s["model_performance_status"])
    print("  runtime: %.2fs" % report["report_metadata"]["runtime_seconds"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
