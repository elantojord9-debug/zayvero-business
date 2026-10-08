"""
ZAYVERO BUSINESS — FASE 1A.

Paquete `audit`: registro de auditoría del procesamiento.

Cada archivo procesado genera un registro JSON en `audit/log/` con:
    - nombre del archivo
    - fecha de procesamiento (UTC ISO)
    - company_id
    - registros originales / registros procesados
    - resumen de ingestión, resolución de columnas, reporte de calidad
    - transformaciones realizadas
    - errores y advertencias

Además mantiene `audit/index.json` con la lista de todos los procesamientos
(fuente rápida para futuros filtros por company_id).
"""

from __future__ import annotations

import datetime as _dt
import json
import os
from typing import Any

LOG_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "log")
INDEX_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "index.json")


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _ensure_dirs() -> None:
    os.makedirs(LOG_DIR, exist_ok=True)


def _load_index() -> list[dict[str, Any]]:
    if os.path.isfile(INDEX_FILE):
        with open(INDEX_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _save_index(entries: list[dict[str, Any]]) -> None:
    with open(INDEX_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2, default=str)


def log_processing(
    file_name: str,
    company_id: str,
    n_original: int,
    n_processed: int,
    ingestion_summary: dict[str, Any] | None = None,
    column_resolution: dict[str, Any] | None = None,
    quality_report: dict[str, Any] | None = None,
    transformations: list[str] | None = None,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
    output_path: str | None = None,
    sheet_details: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Crea el registro de auditoría y lo guarda. Devuelve el registro."""
    _ensure_dirs()
    record: dict[str, Any] = {
        "file_name": file_name,
        "company_id": company_id,
        "processed_at_utc": _utcnow(),
        "n_original_rows": n_original,
        "n_processed_rows": n_processed,
        "rows_dropped": n_original - n_processed,
        "ingestion_summary": ingestion_summary,
        "column_resolution": column_resolution,
        "quality_report": quality_report,
        "transformations": transformations or [],
        "errors": errors or [],
        "warnings": warnings or [],
        "output_path": output_path,
        # FASE 1C: desglose por hoja (None en procesamientos de una sola pasada)
        "sheets": sheet_details,
    }
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in file_name)
    stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    log_path = os.path.join(LOG_DIR, f"{stamp}_{company_id}_{safe_name}.json")
    with open(log_path, "w", encoding="utf-8") as f:
        json.dump(record, f, ensure_ascii=False, indent=2, default=str)
    record["log_path"] = log_path

    index = _load_index()
    index.append({
        "file_name": file_name,
        "company_id": company_id,
        "processed_at_utc": record["processed_at_utc"],
        "n_original_rows": n_original,
        "n_processed_rows": n_processed,
        "log_path": log_path,
    })
    _save_index(index)
    return record


def list_by_company(company_id: str) -> list[dict[str, Any]]:
    """Filtra el índice por company_id (base para el aislamiento multiempresa)."""
    return [e for e in _load_index() if e.get("company_id") == company_id]
