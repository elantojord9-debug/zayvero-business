"""
ZAYVERO BUSINESS — FASE 1B.

Paquete `profiling`: Business Data Profiling Engine.

Convierte el Parquet normalizado de FASE 1A en un `BusinessDatasetProfile`:
estructura de conocimiento empresarial serializable a JSON, diseñada para
que módulos futuros (anomalías, predicciones, advisor) la consuman.

NO incluye: dashboard, AI Business Advisor, predicciones, Autopilot.
NO inventa datos: toda métrica se calcula de los datos normalizados y
lleva su trazabilidad (fórmula, filtros, registros considerados, período).

Uso:
    from profiling import build_profile
    profile = build_profile("data/processed/demo-retail/online_retail_II.parquet",
                            company_id="demo-retail")
"""

from __future__ import annotations

from profiling.builder import build_profile

__all__ = ["build_profile"]
