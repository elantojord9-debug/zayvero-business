"""
ZAYVERO BUSINESS — FASE 2A.

`report`: ensambla el AnomalyReport.

ESTRUCTURA AnomalyReport (JSON-serializable):
    report_metadata  motor, dataset, compañía, fecha, versión
    summary          totales, por severidad, por tipo, período
    anomalies        lista ordenada por prioridad (con anomaly_id ANM-######)
    statistics       agregados del reporte
    methods          metodología documentada por detector
    trace            trazabilidad global del reporte

El motor NUNCA afirma fraude/robo/pérdida/error humano: solo describe
desviaciones ("comportamiento inusual", "requiere revisión").
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any

import pandas as pd

from anomalies import customers as _customers
from anomalies import products as _products
from anomalies import temporal as _temporal
from anomalies.scoring import SEVERITY_ORDER, SEVERITY_WEIGHT
from profiling.trace import to_jsonable

ENGINE_VERSION = "2A"
DATASET_LABEL = "Demo Dataset — UCI Online Retail II"
MAX_ANOMALIES_IN_REPORT = 1000


def _descriptions(df: pd.DataFrame) -> dict[str, str]:
    """Mapa Product -> Description más frecuente (para etiquetas)."""
    if "orig_Description" not in df.columns:
        return {}
    sub = df[df["Product"].notna() & df["orig_Description"].notna()]
    if sub.empty:
        return {}
    mode = (
        sub.groupby("Product", observed=True)["orig_Description"]
        .agg(lambda s: s.mode().iloc[0] if len(s.mode()) else "")
    )
    return {str(k): str(v) for k, v in mode.items()}


def _period_label(df: pd.DataFrame) -> str:
    if len(df) and df["Date"].notna().any():
        dmin, dmax = df["Date"].min(), df["Date"].max()
        return f"{pd.Timestamp(dmin).date()} → {pd.Timestamp(dmax).date()}"
    return "sin fechas válidas"


METHODS_DOC = {
    "temporal": {
        "description": "Picos, caídas y cambios bruscos de ventas por día, "
        "semana ISO y mes.",
        "method": "Serie de revenue (solo completed). Baseline: mediana "
        "móvil + MAD móvil (ventanas 30d/12sem/6m). z robusto = "
        "0.6745*(x - mediana_móvil)/MAD_móvil. Emisión si |z| >= 2.5.",
        "why_robust": "La mediana no se contamina con el propio evento; "
        "el MAD es insensible a valores extremos, a diferencia de la "
        "desviación estándar.",
        "exclusions": "transaction_status != 'completed' excluidas "
        "(las cancelaciones son reversiones, no ventas). Huecos = 0.",
    },
    "product_sales_change": {
        "description": "Cambios bruscos de ventas de un producto vs su "
        "propio historial.",
        "method": "Ventana reciente: últimos 30 días. Baseline: mediana del "
        "revenue diario histórico del producto (mín. 30 días activos y 10 "
        "transacciones). z robusto con MAD del historial. Emisión si "
        "|z| >= 2.5.",
        "why_robust": "Comparación contra sí mismo: un producto que vende "
        "poco pero estable no genera anomalía.",
    },
    "price_outlier": {
        "description": "Precios extremadamente diferentes del histórico "
        "del producto.",
        "method": "Por producto (completed, UnitPrice > 0, mín. 10 precios): "
        "límites Q1 - 3*IQR / Q3 + 3*IQR. Máx. 5 casos por producto.",
        "distinction": "'Precio diferente' (aquí) vs 'precio inválido' "
        "(<=0 o no numérico: Data Quality Engine, excluido).",
    },
    "quantity_outlier": {
        "description": "Cantidades extraordinarias por producto.",
        "method": "Por producto (completed, Quantity > 0, mín. 10): "
        "límite Q3 + 3*IQR (solo cola superior).",
        "distinction": "Quantity <= 0 excluida: pertenece a la lógica de "
        "cancelación/devolución, no es anomalía.",
    },
    "customer_unusual_purchase": {
        "description": "Compras excepcionalmente grandes vs el historial "
        "del propio cliente.",
        "method": "Clientes con >= 5 transacciones. z robusto por "
        "transacción vs mediana/MAD del cliente. Emisión si z >= 4 "
        "(umbral alto: en wholesale una compra grande aislada puede ser "
        "normal). Top 100.",
    },
    "customer_frequency_burst": {
        "description": "Concentración inusual de compras en ventana corta.",
        "method": "Clientes con >= 10 transacciones e intervalo mediano > "
        "30 días. Ráfaga: >= 4 compras en 7 días (two-pointer).",
        "note": "Descriptivo; no es modelo de churn.",
    },
    "severity": {
        "levels": SEVERITY_ORDER,
        "rule": "Umbrales absolutos: HIGH dev>=6, MEDIUM dev>=4, LOW dev>=2.5, "
        "INFO dev>=1.5 (dev = |z| robusto, IQRs más allá del cuartil, o "
        "equivalente). CRITICAL: solo por ranking — top 25 por prioridad "
        "entre dev>=12 y confidence>=75. Así CRITICAL es excepcional y "
        "accionable, nunca una categoría masiva.",
    },
    "confidence": {
        "rule": "0-100 ponderado: historial (30) + magnitud (35) + "
        "consistencia (20) + calidad de datos (15). Cada anomalía explica "
        "sus factores.",
    },
    "ranking": {
        "rule": "priority = peso_severidad * (0.5 + confidence/100) * "
        "(1 + log10(1 + |impacto|)). Orden descendente.",
    },
}


def detect_anomalies(
    parquet_path: str,
    company_id: str,
    max_anomalies: int = MAX_ANOMALIES_IN_REPORT,
) -> dict[str, Any]:
    """
    Ejecuta todos los detectores sobre el Parquet normalizado y devuelve
    el AnomalyReport (dict JSON-serializable).
    """
    if not company_id:
        raise ValueError("company_id es obligatorio (multiempresa).")
    if not os.path.isfile(parquet_path):
        raise FileNotFoundError(f"No existe el Parquet: {parquet_path}")

    t0 = _dt.datetime.now(_dt.timezone.utc)
    df = pd.read_parquet(parquet_path)
    if "company_id" in df.columns:
        df = df[df["company_id"] == company_id].copy()
    if "transaction_status" not in df.columns:
        df["transaction_status"] = "unknown"

    period = _period_label(df)
    descriptions = _descriptions(df)
    n_rows = len(df)

    found: list[dict[str, Any]] = []
    found += _temporal.detect_temporal(df, company_id, DATASET_LABEL, period)
    found += _products.detect_product_sales_change(
        df, company_id, DATASET_LABEL, period, descriptions
    )
    found += _products.detect_price_outliers(
        df, company_id, DATASET_LABEL, period, descriptions
    )
    found += _products.detect_quantity_outliers(
        df, company_id, DATASET_LABEL, period, descriptions
    )
    found += _customers.detect_customers(
        df, company_id, DATASET_LABEL, period
    )

    # Ranking y corte
    found.sort(key=lambda a: a.get("_priority", 0), reverse=True)
    total_found = len(found)

    # Promoción a CRITICAL: solo el top por prioridad entre los que
    # cumplen dev>=12 y confidence>=75. Máximo 25: CRITICAL debe ser
    # excepcional y accionable, no una categoría masiva.
    # (assign_severity ya dejó estos casos en HIGH como base.)
    _critical_slots = 25
    _promoted = 0
    for a in found:
        if _promoted >= _critical_slots:
            break
        if (
            a.get("severity") == "HIGH"
            and (a.get("deviation") or 0) >= 12
            and a.get("confidence_score", 0) >= 75
        ):
            a["severity"] = "CRITICAL"
            # Recalcular prioridad con el nuevo peso de severidad
            from anomalies.scoring import priority_score as _ps

            impact = abs(a.get("difference") or a.get("deviation") or 0)
            a["_priority"] = _ps("CRITICAL", a["confidence_score"], impact)
            _promoted += 1
    # Reordenar tras la promoción
    found.sort(key=lambda a: a.get("_priority", 0), reverse=True)

    kept = found[:max_anomalies]

    # anomaly_id en orden de prioridad
    anomalies: list[dict[str, Any]] = []
    for i, a in enumerate(kept, start=1):
        b = {k: v for k, v in a.items() if not k.startswith("_")}
        b["anomaly_id"] = f"ANM-{i:06d}"
        b["priority_rank"] = i
        anomalies.append(b)

    by_severity: dict[str, int] = {s: 0 for s in SEVERITY_ORDER}
    by_type: dict[str, int] = {}
    conf_sum = 0
    for a in anomalies:
        by_severity[a["severity"]] = by_severity.get(a["severity"], 0) + 1
        by_type[a["type"]] = by_type.get(a["type"], 0) + 1
        conf_sum += a["confidence_score"]
    avg_conf = round(conf_sum / len(anomalies), 1) if anomalies else 0.0

    t1 = _dt.datetime.now(_dt.timezone.utc)
    elapsed = (t1 - t0).total_seconds()

    report = {
        "report_metadata": {
            "engine": f"zayvero-business-anomaly-{ENGINE_VERSION}",
            "dataset_label": DATASET_LABEL,
            "company_id": company_id,
            "source_parquet": parquet_path,
            "generated_at_utc": t1.isoformat(),
            "n_rows_analyzed": n_rows,
            "period_analyzed": period,
            "elapsed_seconds": round(elapsed, 1),
        },
        "summary": {
            "total_anomalies": total_found,
            "anomalies_in_report": len(anomalies),
            "by_severity": by_severity,
            "by_type": by_type,
            "avg_confidence": avg_conf,
            "note": "Una anomalía es una desviación de un patrón esperado; "
            "NO implica fraude, robo, pérdida ni error humano.",
        },
        "anomalies": anomalies,
        "statistics": {
            "rows_analyzed": n_rows,
            "detectors_run": 6,
            "emission_threshold": "|z| >= 2.5 (o equivalente)",
            "max_anomalies_cap": max_anomalies,
            "truncated": total_found > max_anomalies,
            "elapsed_seconds": round(elapsed, 1),
        },
        "methods": METHODS_DOC,
        "trace": {
            "dataset": DATASET_LABEL,
            "company_id": company_id,
            "source_parquet": parquet_path,
            "filters_global": "detectores de ventas usan "
            "transaction_status == 'completed'; precios/cantidades usan "
            "valores > 0",
            "rows_considered": n_rows,
            "period_analyzed": period,
            "notes": "Toda anomalía individual incluye su propio bloque "
            "trace con baseline, fórmula y registros considerados.",
        },
    }
    return to_jsonable(report)


def save_report(
    report: dict[str, Any], company_id: str, base_name: str
) -> str:
    """Guarda el AnomalyReport como JSON en data/anomalies/<company_id>/."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(base_dir, "data", "anomalies", company_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{base_name}_anomalies.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return out_path
