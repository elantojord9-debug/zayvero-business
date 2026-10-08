"""
ZAYVERO BUSINESS — FASE 4B: ensamblaje del reporte PredictionIntelligence.

Cada PredictionInsight es 100% serializable a JSON.
Los valores de FASE 4A se copian sin modificar: este módulo solo
interpreta y contextualiza.
"""

from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Dict, List

from .attention import compute_attention
from .comparison import compute_comparison
from .interpretation import (
    business_interpretation,
    entity_descriptor,
    evidence_summary,
    interpret_error,
    observed_statement,
    possible_implication,
    projected_statement,
    uncertainty_explanation,
)
from .quality import classify_quality
from .recommendations import build_recommendations
from .risk import interpret_decline_risk
from .trend import interpret_trend
from .uncertainty import classify_uncertainty


def build_insight(pred: Dict[str, Any], index: int) -> Dict[str, Any]:
    """Construye un PredictionInsight a partir de una predicción de 4A."""
    prediction_id = pred.get("prediction_id") or ("PRED-UNK-%d" % index)
    insight_id = "INS-%s" % prediction_id.replace("PRED-", "")
    ptype = pred.get("prediction_type", "UNKNOWN")
    status_ok = pred.get("prediction_status") == "OK"

    quality, quality_reasons, quality_evidence = classify_quality(pred)
    uncertainty_level, uncertainty_text, uncertainty_evidence = classify_uncertainty(pred)
    comparison = compute_comparison(pred)
    trend = pred.get("trend") or "INSUFFICIENT_DATA"
    decline_risk = pred.get("decline_risk") or "INSUFFICIENT_DATA"
    trend_text = interpret_trend(trend)
    risk_text = interpret_decline_risk(decline_risk)

    if status_ok:
        score, level, components = compute_attention(
            forecast_quality=quality,
            uncertainty_level=uncertainty_level,
            decline_risk=decline_risk,
            trend=trend,
            confidence_score=float(pred.get("confidence_score") or 0.0),
            percentage_change=comparison.get("percentage_change"),
        )
        business_text = business_interpretation(
            pred, comparison, quality, uncertainty_level, trend_text, risk_text)
        implication = possible_implication(pred, trend, decline_risk)
        uncertainty_full = uncertainty_explanation(pred, uncertainty_level, uncertainty_evidence)
    else:
        score, level, components = 0, "MONITOR", {
            "magnitud": 0, "confianza": 0, "calidad": 0,
            "incertidumbre": 0, "riesgo_caida": 0, "direccion": 0,
        }
        limitations = pred.get("limitations") or []
        limitations_text = " ".join(
            l if l.endswith(".") else l + "." for l in limitations)
        business_text = (
            "No existe suficiente evidencia para generar una estimación "
            "numérica confiable para %s. %s" % (
                entity_descriptor(pred.get("entity") or {}),
                limitations_text)
        )
        implication = (
            "Posible implicación (hipótesis, no hecho): sin historial "
            "suficiente no es posible anticipar el comportamiento del "
            "indicador; cualquier decisión debería basarse en datos "
            "observados, no en una proyección."
        )
        uncertainty_full = (
            "Sin una estimación numérica no es posible evaluar la "
            "incertidumbre del intervalo."
        )

    metrics = pred.get("metrics") or {}
    insight: Dict[str, Any] = {
        "insight_id": insight_id,
        "prediction_id": prediction_id,
        "prediction_type": ptype,
        "entity": copy.deepcopy(pred.get("entity")),
        "period": pred.get("period"),
        "forecast_horizon": pred.get("forecast_horizon"),
        "prediction_status": pred.get("prediction_status"),
        # Valores de 4A, copiados sin modificar:
        "predicted_value": pred.get("predicted_value"),
        "lower_bound": pred.get("lower_bound"),
        "upper_bound": pred.get("upper_bound"),
        "confidence_score": pred.get("confidence_score"),
        "method": pred.get("method"),
        "trend": trend,
        "decline_risk": decline_risk,
        "stockout_status": pred.get("stockout_status"),
        # Magnitud (trazable):
        "recent_actual_value": comparison["recent_actual_value"],
        "historical_baseline": comparison["historical_baseline"],
        "absolute_change": comparison["absolute_change"],
        "percentage_change": comparison["percentage_change"],
        "comparison_note": comparison["baseline_note"],
        # Calidad e incertidumbre:
        "forecast_quality": quality,
        "quality_reasons": quality_reasons,
        "uncertainty_level": uncertainty_level,
        "uncertainty_text": uncertainty_text,
        "interval_width": uncertainty_evidence["interval_width"],
        "interval_width_pct": uncertainty_evidence["interval_width_pct"],
        "evidence_quality": pred.get("evidence_quality"),
        # Atención:
        "attention_score": score,
        "attention_level": level,
        "attention_components": components,
        # Interpretación (determinista, sin LLM):
        "observed_statement": observed_statement(pred, comparison),
        "projected_statement": projected_statement(pred) if status_ok else
            "PROYECTADO: no se generó proyección por falta de evidencia.",
        "error_interpretation": interpret_error(pred) if status_ok else
            "Sin estimación numérica no hay error de validación que interpretar.",
        "trend_interpretation": trend_text,
        "decline_risk_interpretation": risk_text,
        "business_interpretation": business_text,
        "uncertainty_explanation": uncertainty_full,
        "evidence_summary": evidence_summary(pred, quality, quality_reasons),
        "possible_implication": implication,
        "recommendations": build_recommendations(
            pred, quality, uncertainty_level, decline_risk, trend),
        "limitations": list(pred.get("limitations") or []) + [
            "Prediction Intelligence es un sistema de apoyo a decisiones. "
            "Las predicciones no son garantías de resultados futuros."
        ],
        "error_metrics": {
            "mae": metrics.get("mae"),
            "rmse": metrics.get("rmse"),
            "mape": metrics.get("mape"),
            "mape_valid": metrics.get("mape_valid"),
            "relative_rmse": metrics.get("relative_rmse"),
        },
        "trace": {
            "source_prediction_id": prediction_id,
            "source_fields": list(pred.keys()),
            "source_values_4a": {
                "predicted_value": pred.get("predicted_value"),
                "lower_bound": pred.get("lower_bound"),
                "upper_bound": pred.get("upper_bound"),
                "confidence_score": pred.get("confidence_score"),
                "method": pred.get("method"),
                "trend": trend,
                "decline_risk": decline_risk,
                "stockout_status": pred.get("stockout_status"),
                "training_period": pred.get("training_period"),
                "validation_period": pred.get("validation_period"),
            },
            "formulas_used": {
                "interval_width": "upper_bound - lower_bound",
                "interval_width_pct": "interval_width / |predicted_value| * 100",
                "recent_actual_value": "suma de los últimos forecast_horizon valores observados",
                "historical_baseline": "promedio de los valores observados disponibles en 4A",
                "absolute_change": "predicted_value - recent_actual_value",
                "percentage_change": "absolute_change / |recent_actual_value| * 100",
                "attention_score": "magnitud(0-30)+confianza(0-20)+calidad(0-15)"
                                   "+incertidumbre(0-10)+riesgo_caida(0-15)+direccion(0-5)",
            },
            "thresholds_used": {
                "quality": "HIGH: conf>=70, ev=HIGH, MAPE<=35, ancho<=60%, n>=18; "
                           "LOW: conf<40 o ev=LOW o MAPE>60 o ancho>200%",
                "uncertainty": "LOW<=60%, MEDIUM<=150%, HIGH>150%",
                "attention": "URGENT: score>=75 y calidad HIGH/MODERATE y "
                             "incertidumbre LOW/MEDIUM; IMPORTANT>=60; REVIEW>=35",
            },
            "historical_reference": comparison["baseline_note"],
            "validation_metrics": metrics,
            "dataset": (pred.get("trace") or {}).get("dataset"),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "llm_used": False,
        },
    }
    return insight


def _dist(values: List[str]) -> Dict[str, int]:
    d: Dict[str, int] = {}
    for v in values:
        d[v] = d.get(v, 0) + 1
    return d


def build_prediction_intelligence(
    predictions_data: Dict[str, Any],
) -> Dict[str, Any]:
    """Ensambla el reporte completo de FASE 4B desde el JSON real de 4A."""
    predictions = predictions_data.get("predictions") or []
    insights = [build_insight(p, i) for i, p in enumerate(predictions)]

    attention_order = {"URGENT": 0, "IMPORTANT": 1, "REVIEW": 2, "MONITOR": 3}
    deserve_attention = sorted(
        insights,
        key=lambda x: (attention_order.get(x["attention_level"], 9),
                       -x["attention_score"], -float(x.get("confidence_score") or 0)),
    )

    confidences = [float(x.get("confidence_score") or 0)
                   for x in insights if x["prediction_status"] == "OK"]
    scores = [x["attention_score"] for x in insights]

    summary = {
        "total_predictions": len(insights),
        "high_quality": sum(1 for x in insights if x["forecast_quality"] == "HIGH"),
        "moderate_quality": sum(1 for x in insights if x["forecast_quality"] == "MODERATE"),
        "low_quality": sum(1 for x in insights if x["forecast_quality"] == "LOW"),
        "insufficient": sum(1 for x in insights if x["forecast_quality"] == "INSUFFICIENT"),
        "high_uncertainty": sum(1 for x in insights if x["uncertainty_level"] == "HIGH"),
        "medium_uncertainty": sum(1 for x in insights if x["uncertainty_level"] == "MEDIUM"),
        "low_uncertainty": sum(1 for x in insights if x["uncertainty_level"] == "LOW"),
        "urgent": sum(1 for x in insights if x["attention_level"] == "URGENT"),
        "important": sum(1 for x in insights if x["attention_level"] == "IMPORTANT"),
        "review": sum(1 for x in insights if x["attention_level"] == "REVIEW"),
        "monitor": sum(1 for x in insights if x["attention_level"] == "MONITOR"),
        "high_decline_risk": sum(1 for x in insights if x["decline_risk"] == "HIGH"),
        "medium_decline_risk": sum(1 for x in insights if x["decline_risk"] == "MEDIUM"),
        "low_decline_risk": sum(1 for x in insights if x["decline_risk"] == "LOW"),
        "average_confidence": round(sum(confidences) / len(confidences), 1) if confidences else 0.0,
        "average_attention_score": round(sum(scores) / len(scores), 1) if scores else 0.0,
        "predictions_with_high_uncertainty": [
            x["insight_id"] for x in insights if x["uncertainty_level"] == "HIGH"],
        "predictions_requiring_review": [
            x["insight_id"] for x in insights
            if x["attention_level"] in ("URGENT", "IMPORTANT", "REVIEW")],
    }

    report = {
        "report_metadata": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "company_id": (predictions_data.get("report_metadata") or {}).get("company_id"),
            "dataset": (predictions_data.get("report_metadata") or {}).get("dataset"),
            "phase": "4B — Prediction Intelligence",
            "source_predictions_file": "data/predictions/demo-retail/online_retail_II_full_predictions.json",
            "llm_used": False,
        },
        "summary": summary,
        "prediction_insights": insights,
        "predictions_deserving_attention": [
            {
                "insight_id": x["insight_id"],
                "prediction_id": x["prediction_id"],
                "prediction_type": x["prediction_type"],
                "entity": x["entity"],
                "period": x["period"],
                "predicted_value": x["predicted_value"],
                "absolute_change": x["absolute_change"],
                "percentage_change": x["percentage_change"],
                "confidence_score": x["confidence_score"],
                "forecast_quality": x["forecast_quality"],
                "uncertainty_level": x["uncertainty_level"],
                "decline_risk": x["decline_risk"],
                "trend": x["trend"],
                "attention_score": x["attention_score"],
                "attention_level": x["attention_level"],
                "brief_explanation": x["business_interpretation"][:400],
                "main_recommendation": (x["recommendations"] or [None])[0],
            }
            for x in deserve_attention
        ],
        "quality_distribution": _dist([x["forecast_quality"] for x in insights]),
        "attention_distribution": _dist([x["attention_level"] for x in insights]),
        "uncertainty_distribution": _dist([x["uncertainty_level"] for x in insights]),
        "risk_distribution": _dist([x["decline_risk"] for x in insights]),
        "trace": {
            "source": "outputs reales de FASE 4A (Prediction Engine MVP)",
            "n_source_predictions": len(predictions),
            "phase_4a_modified": False,
            "deterministic_rules_only": True,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        },
    }
    return report
