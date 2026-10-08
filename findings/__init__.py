"""
ZAYVERO BUSINESS — FASE 2B.

Paquete `findings`: Anomaly Impact & Business Risk Engine.

FASE 2A responde "¿qué comportamiento es inusual?" (anomalía estadística).
FASE 2B responde "¿qué tan importante podría ser para el negocio?"
(hallazgo empresarial priorizado).

Una anomalía estadística NO es automáticamente un riesgo empresarial.

API pública:
    from findings import build_findings
    report = build_findings(anomalies_report_or_path, company_id="demo-retail")

El reporte (BusinessFindingReport) es 100% serializable a JSON y contiene:
    report_metadata, summary, top_findings, findings, methods, trace.

REGLA DE SEGURIDAD ANALÍTICA: nunca se afirma fraude/robo/pérdida/
error humano. La diferencia monetaria se llama "desviación respecto al
comportamiento esperado", nunca "pérdida" ni "ganancia".
"""

from __future__ import annotations

from findings.report import build_findings

__all__ = ["build_findings"]
