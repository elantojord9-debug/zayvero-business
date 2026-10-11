"""FASE 7A — "Revisión de datos" antes de procesar.

Lee el archivo original SIN modificarlo y produce un reporte empresarial:

  ✓ Archivo recibido
  ✓ Formato reconocido
  ✓ Columnas detectadas
  ✓ Fechas detectadas
  ✓ Registros detectados

Advertencias visibles (nunca ocultas):

  ⚠ Columnas faltantes
  ⚠ Valores vacíos
  ⚠ Valores negativos
  ⚠ Duplicados
  ⚠ Posibles cancelaciones

Reutiliza los pasos 1-3 del pipeline (ingestión, aliases, calidad) sin
llegar a normalizar. Lenguaje empresarial, sin jerga técnica.
"""

from __future__ import annotations

from typing import Any, Dict, List

import pandas as pd

# Columnas originales (no canónicas) que el pipeline utiliza en
# validaciones internas aunque no se asignen al esquema estándar.
# Se conservan como orig_* durante la normalización; la detección de
# gastos usa orig_Tipo y orig_Gasto_DOP.
INTERNAL_VALIDATION_COLUMNS = frozenset({"Tipo", "Gasto_DOP"})


def _clip_columns(cols, n=10):
    base = ", ".join(cols[:n])
    if len(cols) > n:
        base += f" (+{len(cols) - n} más)"
    return base


def unmapped_columns_detail(all_unmapped):
    """Texto veraz para la advertencia de columnas no reconocidas.

    Distingue entre columnas que no se asignan a ningún campo del esquema
    canónico y columnas originales que se conservan y pueden seguir
    utilizándose en validaciones internas (p. ej. detección de gastos).
    """
    internal = [c for c in all_unmapped
                if c in INTERNAL_VALIDATION_COLUMNS]
    rest = [c for c in all_unmapped
            if c not in INTERNAL_VALIDATION_COLUMNS]
    detail = ("Estas columnas no se asignaron a ningún campo del esquema "
              "estándar")
    if rest:
        detail += ": " + _clip_columns(rest)
    detail += ". Los datos originales se conservan"
    if internal:
        detail += (" y " + _clip_columns(internal) + " se utilizan en "
                   "validaciones internas (detección de gastos)")
    return detail + "."

# Imports diferidos para no cargar pandas a nivel de paquete.


def _to_float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def build_review(file_path: str) -> Dict[str, Any]:
    """Construye la revisión de datos de un archivo CSV/XLSX."""
    from ingestion import ingest_sheets
    from ingestion.aliases import resolve_columns
    from quality import validate

    ingested = ingest_sheets(file_path)
    if not ingested:
        return {"ok": False, "error": "No se pudo leer el archivo."}

    all_warnings: List[Dict[str, str]] = []
    sheets_info = []
    total_rows = 0
    all_unmapped: List[str] = []
    all_missing: List[str] = []
    date_min = None
    date_max = None
    n_nulls = 0
    n_neg_qty = 0
    n_bad_price = 0
    n_duplicates = 0
    n_cancellations = 0

    for sheet_name, df_raw, summary in ingested:
        label = sheet_name if sheet_name else "única"
        n_rows = len(df_raw)
        total_rows += n_rows

        df_canon, resolution = resolve_columns(df_raw)
        quality_report = validate(df_canon)
        qd = quality_report.to_dict()

        for col in resolution.unmapped:
            if col not in all_unmapped:
                all_unmapped.append(col)
        for col in resolution.missing_canonical:
            if col not in all_missing:
                all_missing.append(col)

        n_nulls += int(sum((qd.get("null_counts") or {}).values()))
        n_neg_qty += int(qd.get("negative_quantities", 0) or 0)
        n_bad_price += int(qd.get("zero_or_negative_prices", 0) or 0)
        n_duplicates += int(qd.get("duplicate_rows", 0) or 0)

        # Fechas detectadas (columna canónica InvoiceDate)
        if "InvoiceDate" in df_canon.columns:
            dates = pd.to_datetime(df_canon["InvoiceDate"], errors="coerce")
            valid = dates.dropna()
            if len(valid):
                mn, mx = valid.min(), valid.max()
                date_min = mn if date_min is None or mn < date_min else date_min
                date_max = mx if date_max is None or mx > date_max else date_max

        # Posibles cancelaciones: Invoice que empieza con "C"
        if "Invoice" in df_canon.columns:
            inv = df_canon["Invoice"].astype(str)
            n_cancellations += int(inv.str.startswith("C").sum())

        sheets_info.append({
            "sheet": label,
            "rows": n_rows,
            "columns": list(df_raw.columns),
            "columns_detected": len(df_raw.columns),
        })

    checks = [
        {"key": "file", "label": "Archivo recibido", "ok": True},
        {"key": "format", "label": "Formato reconocido", "ok": True},
        {"key": "columns", "label": "Columnas detectadas",
         "ok": bool(sheets_info and sheets_info[0]["columns_detected"]),
         "detail": f"{sheets_info[0]['columns_detected']} columnas" if sheets_info else ""},
        {"key": "dates", "label": "Fechas detectadas", "ok": date_min is not None,
         "detail": (f"{date_min.date()} → {date_max.date()}"
                    if date_min is not None else "No se detectaron fechas")},
        {"key": "rows", "label": "Registros detectados", "ok": total_rows > 0,
         "detail": f"{total_rows:,} registros".replace(",", ".")},
    ]

    if all_missing:
        all_warnings.append({
            "type": "missing_columns",
            "label": "Columnas faltantes",
            "detail": ("No encontramos estas columnas esperadas: "
                       + ", ".join(all_missing)
                       + ". El análisis puede quedar limitado."),
        })
    if all_unmapped:
        all_warnings.append({
            "type": "unmapped_columns",
            "label": "Columnas no reconocidas",
            "detail": unmapped_columns_detail(all_unmapped),
        })
    if n_nulls:
        all_warnings.append({
            "type": "nulls",
            "label": "Valores vacíos",
            "detail": f"Se encontraron {n_nulls} valores vacíos.",
        })
    if n_neg_qty:
        all_warnings.append({
            "type": "negative_quantities",
            "label": "Valores negativos",
            "detail": (f"Se encontraron {n_neg_qty} cantidades negativas "
                       "(pueden ser devoluciones)."),
        })
    if n_bad_price:
        all_warnings.append({
            "type": "bad_prices",
            "label": "Precios en cero o negativos",
            "detail": f"Se encontraron {n_bad_price} precios en cero o negativos.",
        })
    if n_duplicates:
        all_warnings.append({
            "type": "duplicates",
            "label": "Registros duplicados",
            "detail": f"Se encontraron {n_duplicates} registros duplicados.",
        })
    if n_cancellations:
        all_warnings.append({
            "type": "cancellations",
            "label": "Posibles cancelaciones",
            "detail": (f"Se detectaron {n_cancellations} registros que parecen "
                       "cancelaciones. Se conservarán y se marcarán."),
        })

    return {
        "ok": True,
        "checks": checks,
        "warnings": all_warnings,
        "total_rows": total_rows,
        "columns": sheets_info[0]["columns"] if sheets_info else [],
        "sheets": sheets_info,
        "date_min": date_min.isoformat() if date_min is not None else None,
        "date_max": date_max.isoformat() if date_max is not None else None,
        "missing_canonical": all_missing,
        "unmapped": all_unmapped,
    }
