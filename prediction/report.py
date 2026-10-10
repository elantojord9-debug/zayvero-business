"""
ZAYVERO BUSINESS — FASE 4A: Prediction Engine MVP.

`report`: ensambla el PredictionReport con objetos BusinessPrediction.

ESTRUCTURA BusinessPrediction (JSON-serializable):
    prediction_id, prediction_type, entity, period, forecast_horizon,
    prediction_status, predicted_value, lower_bound, upper_bound,
    confidence_score, trend, decline_risk, stockout_status, method,
    training_period, validation_period, metrics, evidence_quality,
    explanation, limitations, trace

prediction_status ∈ {OK, INSUFFICIENT_DATA}.
Cuando es INSUFFICIENT_DATA, predicted_value es None y el motivo queda
en limitations + explanation. La honestidad analítica tiene prioridad
sobre producir más predicciones.

Flujo por serie:
    1. check_sufficiency → si no, INSUFFICIENT_DATA (sin número).
    2. temporal_split → backtest → mejor método por RMSE.
    3. detect_trend, decline_risk, confidence_score.
    4. forecast_final sobre la serie completa + intervalo.
    5. Ensamblar BusinessPrediction con trace completo.
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import time

import numpy as np
import pandas as pd

from prediction import backtesting, confidence as conf_mod, forecasting, risk, trends

DATASET_LABEL = "Demo Dataset — UCI Online Retail II"
DATASET_DOI = "https://doi.org/10.24432/C5CG6D"

# Plan de predicciones del MVP: (tipo, entidad, frecuencia, columna, top_n)
PREDICTION_PLAN = [
    ("DEMAND_REVENUE", "GLOBAL", "M", "Revenue", None),
    ("DEMAND_QUANTITY", "GLOBAL", "M", "Quantity", None),
    ("DEMAND_REVENUE", "GLOBAL", "W", "Revenue", None),
    ("DEMAND_REVENUE", "PRODUCT", "M", "Revenue", 25),
    ("DEMAND_REVENUE", "COUNTRY", "M", "Revenue", 10),
]

ENTITY_LABEL = {"GLOBAL": "negocio completo", "PRODUCT": "producto", "COUNTRY": "país"}
METRIC_LABEL = {"DEMAND_REVENUE": "revenue", "DEMAND_QUANTITY": "unidades"}
UNIT_LABEL = {"DEMAND_REVENUE": "£", "DEMAND_QUANTITY": "uds"}


def _jsonable(value):
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        v = float(value)
        return None if math.isnan(v) or math.isinf(v) else v
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if isinstance(value, (pd.Period,)):
        return str(value)
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    return value


def _entity_label(entity_kind: str, entity_id: str | None, df: pd.DataFrame) -> str:
    if entity_kind == "GLOBAL":
        return "negocio completo"
    return str(entity_id)


def _build_prediction(
    pred_id: str,
    prediction_type: str,
    entity_kind: str,
    entity_id: str | None,
    series: pd.Series,
    freq: str,
    company_id: str,
    parquet_path: str,
) -> dict:
    horizon = forecasting.FORECAST_HORIZON[freq]
    entity_label = _entity_label(entity_kind, entity_id, None)
    freq_sing = forecasting.FREQ_LABEL[freq]
    freq_pl = forecasting.FREQ_LABEL_PLURAL[freq]

    sufficient, reason = forecasting.check_sufficiency(series, freq, horizon)
    cont = forecasting.continuity(series)

    base_trace = {
        "dataset": DATASET_LABEL,
        "dataset_doi": DATASET_DOI,
        "entity": {"kind": entity_kind, "id": entity_id, "label": entity_label},
        "source_records": "filas completed del Parquet normalizado FASE 1C",
        "filters": {"transaction_status": "completed"},
        "aggregation": f"suma por {freq_sing} ({prediction_type})",
        "n_periods": len(series),
        "series_start": str(series.index.min()) if len(series) else None,
        "series_end": str(series.index.max()) if len(series) else None,
        "forecast_horizon": horizon,
        "company_id": company_id,
    }

    if not sufficient:
        return {
            "prediction_id": pred_id,
            "prediction_type": prediction_type,
            "entity": {"kind": entity_kind, "id": entity_id, "label": entity_label},
            "period": None,
            "forecast_horizon": horizon,
            "prediction_status": "INSUFFICIENT_DATA",
            "predicted_value": None,
            "lower_bound": None,
            "upper_bound": None,
            "confidence_score": 0.0,
            "trend": "INSUFFICIENT_DATA",
            "decline_risk": "INSUFFICIENT_DATA",
            "stockout_status": risk.stockout_status()["stockout_status"],
            "method": None,
            "training_period": None,
            "validation_period": None,
            "metrics": None,
            "evidence_quality": "LOW",
            "explanation": (
                f"ZAYVERO no genera una estimación para {entity_label} "
                f"({METRIC_LABEL[prediction_type]}, por {freq_sing}): {reason}."
            ),
            "limitations": [reason, "no se generó ningún valor numérico por falta de evidencia"],
            "trace": base_trace,
        }

    # 1. Backtesting temporal (sin fuga de información futura).
    bt = backtesting.backtest(series, horizon, freq)
    train, validation = bt["train"], bt["validation"]
    method = bt["best_method"]
    m = bt["best_metrics"]

    # 2. Tendencia y confianza (el riesgo de caída se calcula en el
    #    paso 4, cuando ya existe el pronóstico y su cambio porcentual).
    trend_info = trends.detect_trend(train)
    conf, factors = conf_mod.confidence_score(train, validation, m["rmse"], trend_info["trend"], method)
    ev_quality = conf_mod.evidence_quality(conf, len(train), cont)
    stock = risk.stockout_status()

    # 3. Pronóstico final sobre la serie completa + intervalo.
    forecast_vals = forecasting.forecast_final(series, method, horizon, freq)
    lower, upper = forecasting.prediction_interval(
        validation.to_numpy(dtype=float), bt["results"][method]["predicted"], forecast_vals
    )
    labels = forecasting.forecast_period_labels(series.index.max(), horizon)

    method_desc = {
        "naive": "naive (repite el último valor)",
        "moving_average": "promedio móvil de 3 periodos",
        "rolling_median": "mediana móvil de 3 periodos",
        "seasonal_naive": "seasonal naive (mismo periodo del ciclo anterior)",
    }[method]

    predicted_total = float(np.sum(forecast_vals))
    unit = UNIT_LABEL[prediction_type]

    # 4. Riesgo de caída en el horizonte proyectado: el cambio proyectado
    #    es la señal principal (ver prediction/risk.py para los umbrales
    #    documentados). Nivel reciente = últimos `horizon` observados,
    #    misma escala que el pronóstico.
    _obs = series.to_numpy(dtype=float)
    _recent_level = float(np.sum(_obs[-horizon:])) if len(_obs) >= horizon else float(np.sum(_obs))
    forecast_pct = (
        (predicted_total - _recent_level) / abs(_recent_level) * 100.0
        if _recent_level != 0 else None
    )
    decline = risk.decline_risk(trend_info, train, conf, forecast_pct=forecast_pct)

    explanation = (
        f"ZAYVERO estima {METRIC_LABEL[prediction_type]} de {unit}{predicted_total:,.0f} "
        f"para los próximos {horizon} {freq_pl} ({labels[0]} a {labels[-1]}) "
        f"utilizando el historial de {len(train)} {freq_pl} "
        f"({train.index.min()} a {train.index.max()}). "
        f"Método: {method_desc}, elegido por backtesting temporal "
        f"(MAE {m['mae']:,.1f}, RMSE {m['rmse']:,.1f}). "
        f"Confianza estimada: {conf}/100. "
        f"Tendencia: {trend_info['trend']}. Riesgo de caída: {decline['decline_risk']}."
    )
    limitations = [
        "esto es una estimación basada en historial, no un hecho",
        f"MAPE {'no válido: ' + m['mape_invalid_reason'] if not m['mape_valid'] else f'{m['mape']:.1f}% en validación'}",
        "el intervalo ±1.28·std(residuos) es empírico (~80%), no una garantía",
        "eventos no presentes en el historial (cambios de catálogo, mercado) no están modelados",
    ]

    trace = dict(base_trace)
    trace.update(
        {
            "training_period": f"{train.index.min()} a {train.index.max()} ({len(train)} {freq_pl})",
            "validation_period": f"{validation.index.min()} a {validation.index.max()} ({len(validation)} {freq_pl})",
            "method": method,
            "method_description": method_desc,
            "candidate_methods": list(bt["results"].keys()),
            "baseline": "selección por menor RMSE en validación temporal",
            "metrics": {
                k: _jsonable(v)
                for k, v in m.items()
                if k in ("mae", "rmse", "mape", "mape_valid", "relative_rmse")
            },
            "observed_data": {
                "train_last_6": [round(float(x), 2) for x in train.to_numpy()[-6:]],
                "validation_actual": [round(float(x), 2) for x in validation.to_numpy()],
                "validation_predicted": [round(float(x), 2) for x in bt["results"][method]["predicted"]],
            },
            "predicted_data": {
                "periods": labels,
                "values": [round(float(x), 2) for x in forecast_vals],
                "lower_bound": [round(float(x), 2) for x in lower],
                "upper_bound": [round(float(x), 2) for x in upper],
                "interval": "±1.28·std(residuos de validación), límite inferior acotado a 0",
            },
            "confidence_factors": factors,
            "confidence_formula": "100*(0.30*H + 0.25*S + 0.25*E + 0.10*T + 0.05*Sea + 0.05*Q); ver prediction/confidence.py",
            "trend_detail": trend_info,
            "decline_signals": decline["signals"],
            "forecast_pct": round(forecast_pct, 4) if forecast_pct is not None else None,
            "forecast_pct_formula": "(pronóstico − nivel_reciente) / |nivel_reciente| * 100; nivel_reciente = suma de los últimos `horizon` observados",
        }
    )

    return {
        "prediction_id": pred_id,
        "prediction_type": prediction_type,
        "entity": {"kind": entity_kind, "id": entity_id, "label": entity_label},
        "period": f"{labels[0]} a {labels[-1]}",
        "forecast_horizon": horizon,
        "prediction_status": "OK",
        "predicted_value": round(predicted_total, 2),
        "lower_bound": round(float(np.sum(lower)), 2),
        "upper_bound": round(float(np.sum(upper)), 2),
        "confidence_score": conf,
        "trend": trend_info["trend"],
        "decline_risk": decline["decline_risk"],
        "stockout_status": stock["stockout_status"],
        "method": method,
        "training_period": f"{train.index.min()} a {train.index.max()}",
        "validation_period": f"{validation.index.min()} a {validation.index.max()}",
        "metrics": {
            "mae": round(m["mae"], 2),
            "rmse": round(m["rmse"], 2),
            "mape": round(m["mape"], 2) if m["mape_valid"] else None,
            "mape_valid": m["mape_valid"],
            "mape_invalid_reason": m["mape_invalid_reason"],
            "relative_rmse": round(m["relative_rmse"], 4) if m["relative_rmse"] is not None else None,
        },
        "evidence_quality": ev_quality,
        "explanation": explanation,
        "limitations": limitations,
        "trace": trace,
    }


def build_predictions(
    parquet_path: str,
    company_id: str,
    output_dir: str | None = None,
    top_products: int = 25,
    top_countries: int = 10,
) -> dict:
    """Construye el PredictionReport sobre el Parquet normalizado.

    Devuelve el reporte (dict 100% serializable a JSON) y, si se indica
    output_dir, lo guarda como <output_dir>/<company_id>/<base>_predictions.json.
    """
    t0 = time.perf_counter()
    df = forecasting.load_completed(parquet_path)

    predictions: list[dict] = []
    counter = 0

    def next_id() -> str:
        nonlocal counter
        counter += 1
        return f"PRED-{counter:06d}"

    for prediction_type, entity_kind, freq, value_col, top_n in PREDICTION_PLAN:
        n_top = {"PRODUCT": top_products, "COUNTRY": top_countries}.get(entity_kind, top_n)
        if entity_kind == "GLOBAL":
            series_list = [(None, forecasting.series_global(df, freq, value_col))]
        else:
            entity_col = "Product" if entity_kind == "PRODUCT" else "Country"
            series_list = forecasting.series_top_entities(df, entity_col, n_top or 10, freq, value_col)
        for entity_id, series in series_list:
            pred = _build_prediction(
                next_id(), prediction_type, entity_kind, entity_id,
                series, freq, company_id, parquet_path,
            )
            predictions.append(pred)

    ok = [p for p in predictions if p["prediction_status"] == "OK"]
    insuf = [p for p in predictions if p["prediction_status"] == "INSUFFICIENT_DATA"]
    confs = [p["confidence_score"] for p in ok]
    methods_used: dict[str, int] = {}
    for p in ok:
        methods_used[p["method"]] = methods_used.get(p["method"], 0) + 1

    runtime_s = round(time.perf_counter() - t0, 1)
    report = {
        "report_metadata": {
            "generated_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
            "company_id": company_id,
            "dataset": DATASET_LABEL,
            "dataset_doi": DATASET_DOI,
            "parquet": parquet_path,
            "n_rows_input": len(df),
            "runtime_seconds": runtime_s,
            "phase": "4A — Prediction Engine MVP",
        },
        "summary": {
            "n_predictions": len(predictions),
            "n_ok": len(ok),
            "n_insufficient_data": len(insuf),
            "methods_used": methods_used,
            "confidence": {
                "min": round(min(confs), 1) if confs else None,
                "max": round(max(confs), 1) if confs else None,
                "mean": round(sum(confs) / len(confs), 1) if confs else None,
                "distribution": {
                    "0-39": sum(1 for c in confs if c < 40),
                    "40-69": sum(1 for c in confs if 40 <= c < 70),
                    "70-100": sum(1 for c in confs if c >= 70),
                },
            },
            "trends": {t: sum(1 for p in ok if p["trend"] == t) for t in
                       ("UPWARD", "DOWNWARD", "STABLE", "UNSTABLE", "INSUFFICIENT_DATA")},
            "decline_risk": {r: sum(1 for p in ok if p["decline_risk"] == r) for r in
                             ("LOW", "MEDIUM", "HIGH", "INSUFFICIENT_DATA")},
        },
        "predictions": [_jsonable(p) for p in predictions],
        "methods": {
            "baselines": list(__import__("prediction.baselines", fromlist=["METHODS"]).METHODS.keys()),
            "selection": "menor RMSE en validación temporal (últimos H periodos); desempate por simplicidad",
            "backtesting": "split temporal estricto: train < validation, sin fuga de información futura",
            "confidence_formula": "100*(0.30*H + 0.25*S + 0.25*E + 0.10*T + 0.05*Sea + 0.05*Q)",
            "interval": "±1.28·std(residuos de validación) (~80% empírico), límite inferior acotado a 0",
        },
        "trace": {
            "input": parquet_path,
            "filters": {"transaction_status": "completed"},
            "note": "solo filas completed; las canceladas son reversiones, no demanda",
        },
    }

    if output_dir:
        base = os.path.splitext(os.path.basename(parquet_path))[0]
        dest_dir = os.path.join(output_dir, company_id)
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, f"{base}_predictions.json")
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
        report["report_metadata"]["output_path"] = dest

    return report


def save_report(report: dict, path: str) -> str:
    """Guarda el reporte en JSON."""
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)
    return path
