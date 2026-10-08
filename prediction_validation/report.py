"""
ZAYVERO BUSINESS — FASE 4C: ensamblaje del PredictionValidationReport.

Cada PredictionValidation es 100% serializable a JSON y responde
"¿de dónde salió este resultado?" con trace completo.

Estructura PredictionValidation:
    validation_id, prediction_id, prediction_type, entity, forecast_period,
    predicted_value, actual_value,
    absolute_error, percentage_error, signed_error, bias_direction,
    lower_bound, upper_bound, interval_hit,
    confidence_score, forecast_quality_original, realized_forecast_quality,
    method, validation_status, evidence_quality, trace.

Estados:
    VALIDATED     → existe resultado real válido del mismo periodo.
    PENDING       → el periodo real aún no ocurrió en los datos.
    NOT_AVAILABLE → no hay suficiente información para evaluar
                    (p. ej. la predicción de 4A fue INSUFFICIENT_DATA).
    INVALID       → información incompatible o inconsistente.

Las predicciones de 4A se leen como copias de solo lectura: ningún valor
original se modifica (no data leakage, no reescritura retrospectiva).
"""

from __future__ import annotations

import copy
import json
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from . import matching, performance
from .matching import compute_actual
from .metrics import NOT_AVAILABLE, check_interval, compute_errors, realized_quality

DATASET_LABEL = "Demo Dataset — UCI Online Retail II"
DATASET_DOI = "https://doi.org/10.24432/C5CG6D"

NEEDED_COLUMNS = ["Date", "transaction_status", "Product", "Country", "Revenue", "Quantity"]


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        v = float(value)
        return v if v == v else None
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    return str(value)


def _load_quality_map(insights_path: Optional[str]) -> Dict[str, str]:
    """prediction_id → forecast_quality de FASE 4B (opcional)."""
    if not insights_path or not os.path.exists(insights_path):
        return {}
    try:
        data = json.load(open(insights_path, encoding="utf-8"))
        return {
            ins.get("prediction_id"): ins.get("forecast_quality")
            for ins in data.get("prediction_insights", [])
            if ins.get("prediction_id")
        }
    except Exception:
        return {}


def build_validation(
    pred: Dict[str, Any],
    index: int,
    df: pd.DataFrame,
    quality_map: Dict[str, str],
    accuracy_threshold_pct: float,
    validation_timestamp: str,
    dataset_max_date: str,
) -> Dict[str, Any]:
    """Construye un PredictionValidation a partir de una predicción de 4A."""
    prediction = copy.deepcopy(pred)  # solo lectura: nunca se toca el original
    prediction_id = prediction.get("prediction_id") or ("PRED-UNK-%d" % index)
    validation_id = "VAL-%s" % prediction_id.replace("PRED-", "")

    trace_base = {
        "prediction_id": prediction_id,
        "source_prediction": {
            k: prediction.get(k)
            for k in (
                "prediction_id", "prediction_type", "entity", "period",
                "forecast_horizon", "prediction_status", "predicted_value",
                "lower_bound", "upper_bound", "confidence_score", "trend",
                "decline_risk", "method", "training_period", "validation_period",
                "metrics", "evidence_quality",
            )
        },
        "validation_timestamp": validation_timestamp,
    }

    # Las predicciones INSUFFICIENT_DATA de 4A no tienen número que validar.
    if prediction.get("prediction_status") != "OK":
        return _jsonable({
            "validation_id": validation_id,
            "prediction_id": prediction_id,
            "prediction_type": prediction.get("prediction_type"),
            "entity": prediction.get("entity"),
            "forecast_period": prediction.get("period"),
            "predicted_value": None,
            "actual_value": None,
            "absolute_error": None,
            "percentage_error": NOT_AVAILABLE,
            "signed_error": None,
            "bias_direction": NOT_AVAILABLE,
            "lower_bound": prediction.get("lower_bound"),
            "upper_bound": prediction.get("upper_bound"),
            "interval_hit": NOT_AVAILABLE,
            "confidence_score": prediction.get("confidence_score"),
            "forecast_quality_original": quality_map.get(prediction_id),
            "realized_forecast_quality": "NOT_EVALUATED",
            "method": prediction.get("method"),
            "validation_status": "NOT_AVAILABLE",
            "evidence_quality": prediction.get("evidence_quality"),
            "trace": {
                **trace_base,
                "actual_data_source": None,
                "matched_period": None,
                "matching_rule": (
                    "La predicción de 4A tiene prediction_status=%r: no "
                    "generó valor numérico, por lo que no hay nada que "
                    "validar." % (prediction.get("prediction_status"),)
                ),
                "source_fields": [],
                "formulas": {},
                "thresholds": {},
            },
        })

    actual_info = compute_actual(df, prediction)
    status = actual_info["status"]

    if status == "PENDING":
        return _jsonable({
            "validation_id": validation_id,
            "prediction_id": prediction_id,
            "prediction_type": prediction.get("prediction_type"),
            "entity": prediction.get("entity"),
            "forecast_period": prediction.get("period"),
            "predicted_value": prediction.get("predicted_value"),
            "actual_value": None,
            "absolute_error": None,
            "percentage_error": NOT_AVAILABLE,
            "signed_error": None,
            "bias_direction": NOT_AVAILABLE,
            "lower_bound": prediction.get("lower_bound"),
            "upper_bound": prediction.get("upper_bound"),
            "interval_hit": NOT_AVAILABLE,
            "confidence_score": prediction.get("confidence_score"),
            "forecast_quality_original": quality_map.get(prediction_id),
            "realized_forecast_quality": "NOT_EVALUATED",
            "method": prediction.get("method"),
            "validation_status": "PENDING",
            "evidence_quality": prediction.get("evidence_quality"),
            "trace": {
                **trace_base,
                "actual_data_source": None,
                "matched_period": actual_info["observable_periods"],
                "matching_rule": actual_info["matching_rule"],
                "source_fields": ["Date", "transaction_status", "Product",
                                  "Country", "Revenue", "Quantity"],
                "formulas": {},
                "thresholds": {},
                "dataset_max_date": dataset_max_date,
                "future_periods": actual_info["future_periods"],
            },
        })

    if status == "INVALID":
        return _jsonable({
            "validation_id": validation_id,
            "prediction_id": prediction_id,
            "prediction_type": prediction.get("prediction_type"),
            "entity": prediction.get("entity"),
            "forecast_period": prediction.get("period"),
            "predicted_value": prediction.get("predicted_value"),
            "actual_value": None,
            "absolute_error": None,
            "percentage_error": NOT_AVAILABLE,
            "signed_error": None,
            "bias_direction": NOT_AVAILABLE,
            "lower_bound": prediction.get("lower_bound"),
            "upper_bound": prediction.get("upper_bound"),
            "interval_hit": NOT_AVAILABLE,
            "confidence_score": prediction.get("confidence_score"),
            "forecast_quality_original": quality_map.get(prediction_id),
            "realized_forecast_quality": "NOT_EVALUATED",
            "method": prediction.get("method"),
            "validation_status": "INVALID",
            "evidence_quality": prediction.get("evidence_quality"),
            "trace": {
                **trace_base,
                "actual_data_source": None,
                "matched_period": None,
                "matching_rule": actual_info["matching_rule"],
                "source_fields": [],
                "formulas": {},
                "thresholds": {},
            },
        })

    # status == "READY" → VALIDATED
    actual = actual_info["actual_value"]
    if prediction.get("predicted_value") is None:
        return _jsonable({
            "validation_id": validation_id,
            "prediction_id": prediction_id,
            "prediction_type": prediction.get("prediction_type"),
            "entity": prediction.get("entity"),
            "forecast_period": prediction.get("period"),
            "predicted_value": None,
            "actual_value": None,
            "absolute_error": None,
            "percentage_error": NOT_AVAILABLE,
            "signed_error": None,
            "bias_direction": NOT_AVAILABLE,
            "lower_bound": prediction.get("lower_bound"),
            "upper_bound": prediction.get("upper_bound"),
            "interval_hit": NOT_AVAILABLE,
            "confidence_score": prediction.get("confidence_score"),
            "forecast_quality_original": quality_map.get(prediction_id),
            "realized_forecast_quality": "NOT_EVALUATED",
            "method": prediction.get("method"),
            "validation_status": "INVALID",
            "evidence_quality": prediction.get("evidence_quality"),
            "trace": {
                **trace_base,
                "actual_data_source": None,
                "matched_period": actual_info["observable_periods"],
                "matching_rule": "INVALID: prediction_status=OK pero "
                                 "predicted_value es None (inconsistente).",
                "source_fields": [],
                "formulas": {},
                "thresholds": {},
            },
        })
    predicted = float(prediction["predicted_value"])
    errors = compute_errors(predicted, actual, accuracy_threshold_pct)
    interval_hit = check_interval(
        actual, prediction.get("lower_bound"), prediction.get("upper_bound")
    )
    rquality = realized_quality(errors["percentage_error"], predicted, actual)

    return _jsonable({
        "validation_id": validation_id,
        "prediction_id": prediction_id,
        "prediction_type": prediction.get("prediction_type"),
        "entity": prediction.get("entity"),
        "forecast_period": prediction.get("period"),
        "predicted_value": predicted,
        "actual_value": actual,
        "absolute_error": errors["absolute_error"],
        "percentage_error": errors["percentage_error"],
        "signed_error": errors["signed_error"],
        "bias_direction": errors["bias_direction"],
        "lower_bound": prediction.get("lower_bound"),
        "upper_bound": prediction.get("upper_bound"),
        "interval_hit": interval_hit,
        "confidence_score": prediction.get("confidence_score"),
        "forecast_quality_original": quality_map.get(prediction_id),
        "realized_forecast_quality": rquality,
        "method": prediction.get("method"),
        "validation_status": "VALIDATED",
        "evidence_quality": prediction.get("evidence_quality"),
        "trace": {
            **trace_base,
            "actual_data_source": "Parquet normalizado FASE 1C (solo lectura)",
            "matched_period": actual_info["observable_periods"],
            "matching_rule": actual_info["matching_rule"],
            "source_fields": ["Date", "transaction_status", "Product",
                              "Country", "Revenue", "Quantity"],
            "formulas": {
                "absolute_error": "abs(predicted_value - actual_value)",
                "signed_error": "predicted_value - actual_value",
                "percentage_error": "abs(predicted_value - actual_value) "
                                    "/ abs(actual_value) * 100 "
                                    "(NOT_AVAILABLE si actual_value == 0)",
                "interval_hit": "lower_bound <= actual_value <= upper_bound",
            },
            "thresholds": {
                "accuracy_threshold_pct": accuracy_threshold_pct,
                "realized_quality": "EXCELLENT<=10, GOOD<=25, FAIR<=50, POOR>50 "
                                    "(sobre percentage_error)",
            },
            "dataset_max_date": dataset_max_date,
            "n_forecast_periods": actual_info["n_forecast_periods"],
        },
    })


def build_validation_report(
    predictions_path: str,
    parquet_path: str,
    company_id: str,
    insights_path: Optional[str] = None,
    accuracy_threshold_pct: float = 5.0,
) -> Dict[str, Any]:
    """Construye el PredictionValidationReport completo."""
    t0 = time.perf_counter()
    validation_timestamp = datetime.now(timezone.utc).isoformat()

    with open(predictions_path, encoding="utf-8") as f:
        pred_data = json.load(f)
    predictions: List[Dict[str, Any]] = pred_data.get("predictions", [])

    # Solo las columnas necesarias; el Parquet se lee una vez.
    df = pd.read_parquet(parquet_path, columns=NEEDED_COLUMNS)
    df["Date"] = pd.to_datetime(df["Date"])
    dataset_max_date = str(df["Date"].max().date())

    quality_map = _load_quality_map(insights_path)

    validations = [
        build_validation(
            pred, i, df, quality_map, accuracy_threshold_pct,
            validation_timestamp, dataset_max_date,
        )
        for i, pred in enumerate(predictions)
    ]

    validated = [v for v in validations if v["validation_status"] == "VALIDATED"]
    overview = performance.aggregate(validated)
    trend = performance.performance_trend(validated)
    status = performance.model_performance_status(validated, overview, trend)

    by_status = {}
    for v in validations:
        by_status[v["validation_status"]] = by_status.get(v["validation_status"], 0) + 1

    summary = {
        "total_predictions": len(validations),
        "validated": by_status.get("VALIDATED", 0),
        "pending": by_status.get("PENDING", 0),
        "not_available": by_status.get("NOT_AVAILABLE", 0),
        "invalid": by_status.get("INVALID", 0),
        "accuracy_rate": overview["accuracy_rate"],
        "interval_hit_rate": overview["interval_hit_rate"],
        "mean_absolute_error": overview["mean_absolute_error"],
        "median_absolute_error": overview["median_absolute_error"],
        "mean_percentage_error": overview["mean_percentage_error"],
        "mean_signed_error": overview["mean_signed_error"],
        "overprediction_rate": overview["overprediction_rate"],
        "underprediction_rate": overview["underprediction_rate"],
        "model_performance_status": status,
    }

    runtime = time.perf_counter() - t0
    report = {
        "report_metadata": {
            "generated_at": validation_timestamp,
            "company_id": company_id,
            "dataset": DATASET_LABEL,
            "dataset_doi": DATASET_DOI,
            "predictions_source": predictions_path,
            "parquet": parquet_path,
            "insights_source": insights_path,
            "accuracy_threshold_pct": accuracy_threshold_pct,
            "runtime_seconds": round(runtime, 2),
            "phase": "4C — Prediction Validation & Learning",
        },
        "summary": summary,
        "validations": validations,
        "performance_overview": overview,
        "performance_by_method": performance.performance_by_method(validated),
        "performance_by_type": performance.performance_by_type(validated),
        "performance_by_entity": performance.performance_by_entity(validated),
        "confidence_calibration": performance.confidence_calibration(validations),
        "performance_trend": trend,
        "model_performance_status": status,
        "trace": {
            "dataset": DATASET_LABEL,
            "dataset_doi": DATASET_DOI,
            "dataset_max_date": dataset_max_date,
            "n_predictions_input": len(predictions),
            "matching": "prediction_type + entity + period exactos; "
                        "agregación idéntica a FASE 4A "
                        "(suma por periodo, filas completed).",
            "no_data_leakage": "El valor real se calcula solo con filas cuyo "
                               "periodo pertenece al horizonte pronosticado; "
                               "las predicciones de 4A se copian sin modificar.",
            "note": "FASE 4C no mejora automáticamente el modelo. "
                    "Primero mide su desempeño real.",
        },
    }
    return _jsonable(report)


def save_report(report: Dict[str, Any], output_path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return output_path
