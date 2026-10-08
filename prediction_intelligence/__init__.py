"""
ZAYVERO BUSINESS — FASE 4B: Prediction Intelligence.

Convierte las predicciones estadísticas de FASE 4A en inteligencia
empresarial comprensible mediante reglas deterministas (sin LLM).

La predicción original de FASE 4A es la fuente matemática: este paquete
NO modifica predicted_value, lower_bound, upper_bound, confidence_score,
método, métricas, periodos, trend, decline_risk ni stockout_status.
Solo interpreta y contextualiza.
"""

from __future__ import annotations

from .report import build_prediction_intelligence, build_insight

__all__ = ["build_prediction_intelligence", "build_insight"]
