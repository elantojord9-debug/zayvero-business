"""
ZAYVERO BUSINESS — FASE 2A.

Paquete `anomalies`: primer motor de detección de anomalías.

PRINCIPIO: una anomalía = "comportamiento que se desvía significativamente
de un patrón esperado". NO significa fraude, robo, pérdida ni error humano.

API pública:
    from anomalies import detect_anomalies
    report = detect_anomalies(parquet_path, company_id="demo-retail")

El reporte (AnomalyReport) es 100% serializable a JSON y contiene:
    report_metadata, summary, anomalies, statistics, methods, trace.

REGLA DE SEGURIDAD ANALÍTICA: el motor nunca afirma fraude/robo/pérdida/
error humano. El lenguaje permitido: "comportamiento inusual", "desviación",
"requiere revisión", "posible anomalía".
"""

from __future__ import annotations

from anomalies.report import detect_anomalies

__all__ = ["detect_anomalies"]
