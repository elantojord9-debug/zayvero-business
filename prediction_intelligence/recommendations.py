"""
ZAYVERO BUSINESS — FASE 4B: recomendaciones de revisión.

Solo recomendaciones de REVISIÓN, derivadas de la evidencia.
Nunca ejecutar acciones: no modificar precios, no comprar, no contactar
clientes, no enviar mensajes, no activar Autopilot.
"""

from __future__ import annotations

from typing import Any, Dict, List

from .interpretation import entity_descriptor


def build_recommendations(
    pred: Dict[str, Any],
    forecast_quality: str,
    uncertainty_level: str,
    decline_risk: str,
    trend: str,
) -> List[str]:
    recs: List[str] = []
    entity = entity_descriptor(pred.get("entity") or {})
    period = pred.get("period") or "el periodo proyectado"
    horizon = pred.get("forecast_horizon") or 1
    validation = pred.get("validation_period") or "el periodo de validación"

    if pred.get("prediction_status") != "OK":
        return [
            "Reunir más historial para %s antes de generar una proyección: "
            "se requiere continuidad temporal suficiente para estimar con "
            "confianza." % entity,
            "Revisar la calidad de los datos de origen para %s." % entity,
        ]

    recs.append(
        "Revisar la evolución reciente en %s y compararla con la estimación "
        "para %s." % (entity, period)
    )
    recs.append(
        "Comparar la proyección con los objetivos internos para %s." % period
    )
    recs.append(
        "Validar la estimación contra datos nuevos conforme estén disponibles "
        "durante los próximos %d periodo(s)." % horizon
    )

    if decline_risk == "HIGH":
        recs.append(
            "Revisar las variables comerciales relacionadas con %s antes de "
            "que avance el periodo, dado el riesgo elevado de disminución." % entity
        )
    elif decline_risk == "MEDIUM":
        recs.append(
            "Monitorear el indicador en %s en el próximo periodo para "
            "confirmar o descartar la señal de disminución." % entity
        )

    if uncertainty_level == "HIGH":
        recs.append(
            "Planificar por rango (usar el intervalo completo) en lugar de la "
            "cifra central, dada la incertidumbre elevada."
        )

    if trend in ("UPWARD", "DOWNWARD") and (pred.get("entity") or {}).get("kind") == "PRODUCT":
        recs.append(
            "Revisar productos o mercados relacionados con %s, dado que la "
            "predicción es específica de un producto." % entity
        )

    if forecast_quality == "LOW":
        recs.append(
            "Tratar esta estimación como referencia de baja precisión: "
            "contrastarla con el desempeño real del periodo %s antes de usarla "
            "en decisiones." % validation
        )

    return recs
