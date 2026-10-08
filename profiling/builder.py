"""
ZAYVERO BUSINESS — FASE 1B.

`builder`: ensambla el BusinessDatasetProfile.

ESTRUCTURA BusinessDatasetProfile (JSON-serializable):
    dataset_metadata   identificación, fuente, compañía, versión del motor
    date_range         min/max de fecha, días con actividad (+trace)
    transactions       totales/completadas/canceladas/desconocidas (+trace)
    customers          perfiles, activos, 1-compra, recurrentes (+trace)
    products           perfiles, clases de actividad, top (+trace)
    countries          ranking, mercados top/pequeños, crecimiento (+trace)
    sales              BRUTO / CANCELADO / NETO / desconocido, unidades (+trace)
    cancellations      análisis descriptivo de cancelaciones (+trace)
    data_quality       score 0–100 con explicación (+trace)
    temporal_metrics   series diaria/semanal/mensual (+trace)

Toda métrica lleva su bloque `trace` (fórmula, filtros, registros
considerados, período). Nada se inventa: lo que no puede calcularse
se reporta como null/0/vacío con su nota.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any

import pandas as pd

from profiling import cancellations as _canc
from profiling import countries as _countries
from profiling import customers as _customers
from profiling import overview as _overview
from profiling import products as _products
from profiling import quality_score as _qs
from profiling import temporal as _temporal
from profiling.trace import make_trace, to_jsonable

ENGINE_VERSION = "1B"
DATASET_LABEL = "Demo Dataset — UCI Online Retail II"


def _find_audit_record(
    company_id: str, parquet_path: str
) -> dict[str, Any] | None:
    """Busca el registro de auditoría de FASE 1A para este archivo+empresa."""
    import sys

    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sys.path.insert(0, base_dir)
    try:
        from audit import list_by_company
    except ImportError:
        return None
    base = os.path.splitext(os.path.basename(parquet_path))[0]
    candidates = list_by_company(company_id)
    # El parquet se llama <base>.parquet donde base = nombre del fuente sin ext
    for entry in reversed(candidates):  # más reciente primero
        log_path = entry.get("log_path")
        if not log_path or not os.path.isfile(log_path):
            continue
        try:
            with open(log_path, "r", encoding="utf-8") as f:
                record = json.load(f)
        except (OSError, ValueError):
            continue
        out = record.get("output_path") or ""
        if os.path.basename(out) == os.path.basename(parquet_path):
            return record
        # Fallback: el nombre del fuente coincide con el del parquet
        src = os.path.splitext(record.get("file_name", ""))[0]
        if src == base:
            return record
    return None


def build_profile(parquet_path: str, company_id: str,
                dataset_label: str | None = None) -> dict[str, Any]:
    """Construye el BusinessDatasetProfile desde el Parquet normalizado.

    `dataset_label`: etiqueta del dataset. Por defecto conserva la etiqueta
    del dataset de referencia ("Demo Dataset — UCI Online Retail II");
    FASE 7A pasa el nombre del reporte del cliente para no etiquetar
    datos de clientes como si fueran el demo.
    """
    if not company_id:
        raise ValueError("company_id es obligatorio (multiempresa).")
    if not os.path.isfile(parquet_path):
        raise FileNotFoundError(f"No existe el Parquet: {parquet_path}")
    label = dataset_label or DATASET_LABEL

    df = pd.read_parquet(parquet_path)
    # Aislamiento multiempresa: solo filas de esta compañía
    if "company_id" in df.columns:
        df = df[df["company_id"] == company_id].copy()
    # Garantiza transaction_status aunque el Parquet no lo traiga
    if "transaction_status" not in df.columns:
        df["transaction_status"] = "unknown"

    audit_record = _find_audit_record(company_id, parquet_path)

    overview = _overview.build_overview(df, company_id)

    profile = {
        "dataset_metadata": to_jsonable(
            {
                "dataset_label": label,
                "engine": f"zayvero-business-profiling-{ENGINE_VERSION}",
                "generated_at_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
                "company_id": company_id,
                "source_parquet": parquet_path,
                "n_rows": len(df),
                "audit_record_found": audit_record is not None,
                "trace": make_trace(
                    formula="perfil construido del Parquet normalizado de FASE 1A",
                    filters=f"company_id == '{company_id}'",
                    rows_considered=len(df),
                    period=(
                        overview["date_range"].get("min_date", ""),
                    ),
                    notes=f"Etiqueta del dataset: '{label}'.",
                ),
            }
        ),
        "date_range": overview["date_range"],
        "transactions": overview["transactions"],
        "entities": overview["entities"],
        "customers": _customers.build_customers(df),
        "products": _products.build_products(df),
        "countries": _countries.build_countries(df),
        "sales": overview["sales"],
        "cancellations": _canc.build_cancellations(df),
        "data_quality": _qs.compute_quality_score(df, audit_record),
        "temporal_metrics": _temporal.build_temporal(df),
    }
    return to_jsonable(profile)


def save_profile(
    profile: dict[str, Any], company_id: str, base_name: str
) -> str:
    """Guarda el perfil como JSON en data/profiles/<company_id>/."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(base_dir, "data", "profiles", company_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{base_name}_profile.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(profile, f, ensure_ascii=False, indent=2)
    return out_path
