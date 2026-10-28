"""
ZAYVERO BUSINESS — FASE 2C.

Paquete `context`: Contextual Business Intelligence.

Toma los Business Findings de FASE 2B y les agrega CONTEXTO EMPRESARIAL
y RECOMENDACIONES DE REVISIÓN.

Evolución:
    DATOS → ANÁLISIS → HALLAZGO → CONTEXTO → POSIBLES INTERPRETACIONES
    → RECOMENDACIÓN DE REVISIÓN

REGLA DE ORO: diferenciar siempre FACT / OBSERVATION /
POSSIBLE_EXPLANATION / RECOMMENDATION. Nunca se presenta una hipótesis
como hecho. Nunca se afirma causalidad sin evidencia.

API pública:
    from context import build_context_findings
    report = build_context_findings(findings_report_or_path, parquet_path,
                                    company_id="demo-retail")

El reporte (BusinessContextFindingReport) es 100% serializable a JSON.

REGLA DE SEGURIDAD ANALÍTICA: nunca se afirma fraude, error, pérdida,
ganancia, causa ni culpabilidad. Lenguaje permitido: "comportamiento
inusual", "desviación respecto al comportamiento esperado",
"requiere revisión", "posible explicación",
"no existe evidencia suficiente para determinar la causa".
"""

from __future__ import annotations

from context.report import build_context_findings

__all__ = ["build_context_findings"]
