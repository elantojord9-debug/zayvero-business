"""
ZAYVERO BUSINESS — FASE 1A: Data Ingestion Foundation.

Paquete `ingestion`: detección de formato, lectura de archivos y resumen inicial.
NO modifica datos: solo lee e inspecciona.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

SUPPORTED_FORMATS = (".csv", ".xlsx", ".xls")
# PDF se agregará en una fase posterior (estructura preparada, ver detect_format).
FUTURE_FORMATS = (".pdf",)


def detect_format(file_path: str) -> str:
    """Devuelve el formato normalizado ('csv', 'xlsx', 'xls') o lanza ValueError."""
    ext = os.path.splitext(file_path)[1].lower()
    if ext in FUTURE_FORMATS:
        raise ValueError(
            f"Formato '{ext}' reconocido pero aún no implementado "
            "(ingestión PDF prevista para una fase posterior)."
        )
    if ext not in SUPPORTED_FORMATS:
        raise ValueError(
            f"Formato no soportado: '{ext}'. Soportados: {SUPPORTED_FORMATS}"
        )
    return ext.lstrip(".")


def read_file(file_path: str, **kwargs: Any) -> pd.DataFrame:
    """Lee un CSV o Excel y devuelve un DataFrame crudo (sin transformar)."""
    if not os.path.isfile(file_path):
        raise FileNotFoundError(f"No existe el archivo: {file_path}")
    fmt = detect_format(file_path)
    if fmt == "csv":
        # sep=None + engine python = detección automática de delimitador.
        return pd.read_csv(file_path, sep=None, engine="python", **kwargs)
    # Excel
    return pd.read_excel(file_path, **kwargs)


@dataclass
class IngestionSummary:
    file_name: str
    format: str
    n_rows: int
    n_cols: int
    columns: list[str] = field(default_factory=list)
    dtypes: dict[str, str] = field(default_factory=dict)
    empty_columns: list[str] = field(default_factory=list)
    null_counts: dict[str, int] = field(default_factory=dict)
    null_pct: dict[str, float] = field(default_factory=dict)
    duplicate_rows: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_name": self.file_name,
            "format": self.format,
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "columns": self.columns,
            "dtypes": self.dtypes,
            "empty_columns": self.empty_columns,
            "null_counts": self.null_counts,
            "null_pct": self.null_pct,
            "duplicate_rows": self.duplicate_rows,
        }


def summarize(df: pd.DataFrame, file_path: str) -> IngestionSummary:
    """Genera el resumen inicial del dataset (solo inspección, sin modificar)."""
    n_rows = len(df)
    null_counts = df.isna().sum()
    return IngestionSummary(
        file_name=os.path.basename(file_path),
        format=detect_format(file_path),
        n_rows=n_rows,
        n_cols=len(df.columns),
        columns=list(df.columns),
        dtypes={c: str(t) for c, t in df.dtypes.items()},
        empty_columns=[c for c in df.columns if df[c].isna().all()],
        null_counts={c: int(v) for c, v in null_counts.items()},
        null_pct={c: round(float(v) / n_rows * 100, 2) if n_rows else 0.0
                  for c, v in null_counts.items()},
        duplicate_rows=int(df.duplicated().sum()),
    )


def ingest(file_path: str, **kwargs: Any) -> tuple[pd.DataFrame, IngestionSummary]:
    """Punto de entrada: lee el archivo y devuelve (DataFrame crudo, resumen)."""
    df = read_file(file_path, **kwargs)
    return df, summarize(df, file_path)


def list_sheets(file_path: str) -> list[str]:
    """Devuelve los nombres de hoja de un Excel. Para CSV devuelve []."""
    fmt = detect_format(file_path)
    if fmt == "csv":
        return []
    return pd.ExcelFile(file_path).sheet_names


def ingest_sheets(
    file_path: str, sheets: list[str] | None = None, **kwargs: Any
) -> list[tuple[str, pd.DataFrame, IngestionSummary]]:
    """
    Ingiere un archivo por hojas.

    - CSV: una sola "hoja" con nombre None.
    - XLSX/XLS: si `sheets` es None se procesan TODAS las hojas;
      si se da una lista, solo esas (en ese orden).
    Devuelve lista de (sheet_name, DataFrame crudo, resumen).
    NO modifica datos: solo lee e inspecciona.
    """
    fmt = detect_format(file_path)
    if fmt == "csv":
        if sheets:
            raise ValueError("Un CSV no tiene hojas; 'sheets' solo aplica a Excel.")
        df = read_file(file_path, **kwargs)
        return [(None, df, summarize(df, file_path))]
    available = list_sheets(file_path)
    wanted = sheets if sheets is not None else available
    unknown = [s for s in wanted if s not in available]
    if unknown:
        raise ValueError(
            f"Hojas no encontradas en {file_path}: {unknown}. "
            f"Disponibles: {available}"
        )
    result = []
    for sheet in wanted:
        df = read_file(file_path, sheet_name=sheet, **kwargs)
        result.append((sheet, df, summarize(df, file_path)))
    return result
