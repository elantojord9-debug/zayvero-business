"""
ZAYVERO BUSINESS — FASE 1A: Data Ingestion Foundation (+ FASE 1C multi-hoja).

`pipeline.py`: orquesta el flujo completo de ingestión.

FLUJO (por hoja):
    1. ingest_sheets(file)     -> DataFrames crudos + resúmenes por hoja
    2. resolve_columns(df)     -> columnas a nombres canónicos (+ reporte)
    3. quality.validate(df)    -> reporte de calidad (solo lectura)
    4. normalize(df, company)  -> esquema normalizado + transformaciones
                                 (+ source_sheet/source_file, FASE 1C)
    5. unir hojas              -> un solo DataFrame
    6. guardar Parquet         -> data/processed/<company_id>/<nombre>.parquet
    7. audit.log_processing()  -> registro JSON (con desglose por hoja)

`company_id` es OBLIGATORIO: sin él no hay procesamiento (multiempresa).

Uso:
    python pipeline.py data/raw/ventas.csv --company demo-retail
    python pipeline.py data/raw/online_retail_II.xlsx --company demo-retail \
        --sheets "Year 2009-2010,Year 2010-2011" --output-name online_retail_II_full
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from audit import log_processing  # noqa: E402
from ingestion import ingest_sheets  # noqa: E402
from ingestion.aliases import resolve_columns  # noqa: E402
from normalize import normalize  # noqa: E402
from quality import validate  # noqa: E402

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
PROCESSED_DIR = os.path.join(BASE_DIR, "data", "processed")


def run_pipeline(
    file_path: str,
    company_id: str,
    cancellation_rules: dict | None = None,
    sheets: list[str] | None = None,
    output_name: str | None = None,
) -> dict:
    """
    Ejecuta el pipeline completo y devuelve el registro de auditoría.

    FASE 1C: si el archivo es un Excel con múltiples hojas, `sheets`
    selecciona cuáles procesar (None = todas las hojas; en CSV se ignora).
    Todas las hojas se normalizan con `source_sheet`/`source_file` y se
    unen en UN solo Parquet. `output_name` permite nombrar la salida
    (por defecto: nombre del archivo fuente sin extensión).
    """
    if not company_id:
        raise ValueError("company_id es obligatorio.")

    errors: list[str] = []
    warnings: list[str] = []
    file_name = os.path.basename(file_path)

    # 1) Ingestión por hojas
    ingested = ingest_sheets(file_path, sheets=sheets)
    sheet_details: list[dict] = []
    norm_frames = []
    all_transformations: list[str] = []

    for sheet_name, df_raw, summary in ingested:
        n_original = len(df_raw)
        label = sheet_name if sheet_name is not None else "(única)"

        # 2) Resolución de columnas (aliases configurables)
        df_canon, resolution = resolve_columns(df_raw)
        if resolution.unmapped:
            warnings.append(
                f"[{label}] Columnas sin mapear: {resolution.unmapped}"
            )
        if resolution.missing_canonical:
            warnings.append(
                f"[{label}] Columnas canónicas ausentes: "
                f"{resolution.missing_canonical}"
            )

        # 3) Calidad (solo lectura)
        quality_report = validate(df_canon)

        # 4) Normalización (con trazabilidad de origen FASE 1C)
        df_norm, transformations = normalize(
            df_canon,
            company_id,
            cancellation_rules=cancellation_rules,
            source_sheet=sheet_name,
            source_file=file_name,
        )
        n_processed = len(df_norm)
        norm_frames.append(df_norm)
        all_transformations.extend(f"[{label}] {t}" for t in transformations)

        sheet_details.append({
            "sheet": sheet_name,
            "n_original_rows": n_original,
            "n_processed_rows": n_processed,
            "rows_dropped": n_original - n_processed,
            "columns": summary.columns,
            "duplicate_rows": summary.duplicate_rows,
            "quality": quality_report.to_dict(),
            "column_resolution": resolution.to_dict(),
        })

    # Unión de todas las hojas en un único DataFrame normalizado
    import pandas as pd

    df_all = pd.concat(norm_frames, ignore_index=True) if len(norm_frames) > 1 \
        else norm_frames[0]
    n_total_original = sum(s["n_original_rows"] for s in sheet_details)
    n_total_processed = len(df_all)
    if n_total_processed != n_total_original:
        # Nunca perder registros silenciosamente: si algo no cuadra, se reporta
        # como error explícito (normalize no elimina filas por diseño).
        errors.append(
            f"Descuadre de filas: original={n_total_original} "
            f"procesado={n_total_processed}"
        )

    # 5) Guardar Parquet unificado (particionado por company_id)
    out_dir = os.path.join(PROCESSED_DIR, company_id)
    os.makedirs(out_dir, exist_ok=True)
    base = output_name or os.path.splitext(file_name)[0]
    out_path = os.path.join(out_dir, f"{base}.parquet")
    if os.path.isfile(out_path):
        warnings.append(
            f"El Parquet {out_path} ya existía y será sobrescrito. "
            f"Use --output-name para no perder la versión anterior."
        )
    df_all.to_parquet(out_path, engine="pyarrow", index=False)

    # 6) Auditoría (con desglose por hoja)
    # Compatibilidad: con una sola hoja, el reporte de calidad y la resolución
    # de columnas van también a nivel superior (como en FASE 1A) para que los
    # consumidores existentes (quality_score, etc.) no cambien de comportamiento.
    first = sheet_details[0] if len(sheet_details) == 1 else None
    record = log_processing(
        file_name=file_name,
        company_id=company_id,
        n_original=n_total_original,
        n_processed=n_total_processed,
        ingestion_summary={
            "sheets_processed": [s["sheet"] for s in sheet_details],
            "per_sheet": [
                {k: s[k] for k in ("sheet", "n_original_rows",
                                   "n_processed_rows", "rows_dropped",
                                   "duplicate_rows")}
                for s in sheet_details
            ],
        },
        column_resolution=(first["column_resolution"] if first else None),
        quality_report=(first["quality"].to_dict()
                        if first and hasattr(first["quality"], "to_dict")
                        else (first["quality"] if first else None)),
        transformations=all_transformations,
        errors=errors,
        warnings=warnings,
        output_path=out_path,
        sheet_details=sheet_details,
    )
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description="ZAYVERO BUSINESS — pipeline FASE 1A/1C")
    parser.add_argument("file", help="Archivo CSV o XLSX a procesar")
    parser.add_argument("--company", required=True, help="company_id (obligatorio)")
    parser.add_argument(
        "--sheets",
        default=None,
        help="Hojas Excel a procesar, separadas por coma. "
             "Por defecto: todas las hojas (CSV: se ignora).",
    )
    parser.add_argument(
        "--output-name",
        default=None,
        help="Nombre base del Parquet de salida (sin extensión). "
             "Por defecto: nombre del archivo fuente.",
    )
    args = parser.parse_args()

    sheets = [s.strip() for s in args.sheets.split(",")] if args.sheets else None
    record = run_pipeline(
        args.file, args.company, sheets=sheets, output_name=args.output_name
    )
    print(f"OK: {record['n_processed_rows']}/{record['n_original_rows']} filas")
    if record.get("sheets"):
        for s in record["sheets"]:
            print(f"  Hoja {s['sheet']!r}: "
                  f"{s['n_processed_rows']}/{s['n_original_rows']} filas")
    print(f"Parquet: {record['output_path']}")
    print(f"Auditoría: {record['log_path']}")
    if record["warnings"]:
        print("Advertencias:")
        for w in record["warnings"]:
            print(f"  - {w}")
    if record["errors"]:
        print("ERRORES:")
        for e in record["errors"]:
            print(f"  - {e}")


if __name__ == "__main__":
    main()
