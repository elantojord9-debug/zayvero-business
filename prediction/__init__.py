"""
ZAYVERO BUSINESS — FASE 4A: Prediction Engine MVP.

Paquete `prediction`: primer motor de predicción basada en historial.

Hasta ahora ZAYVERO hacía:
    DATOS → PERFIL → ANOMALÍAS → IMPACTO → CONTEXTO → DASHBOARD
Ahora incorpora:
    → PREDICCIÓN BASADA EN HISTORIAL

PRINCIPIO: una predicción NO es un hecho. Toda predicción indica valor
estimado, horizonte, confianza, evidencia, método, calidad de evidencia
y limitaciones. Lenguaje: "estimación", "proyección", "tendencia
esperada", "riesgo estimado". Nunca "va a ocurrir".

REGLA PRINCIPAL: se prefiere "No hay suficiente evidencia para predecir"
antes que una predicción numérica poco confiable.

API pública:
    from prediction import build_predictions
    report = build_predictions("data/processed/demo-retail/online_retail_II_full.parquet",
                               company_id="demo-retail",
                               output_dir="data/predictions")

El PredictionReport es 100% serializable a JSON y contiene:
    report_metadata, summary, predictions, methods, trace.
"""

from __future__ import annotations

from prediction.report import build_predictions, save_report

__all__ = ["build_predictions", "save_report"]
