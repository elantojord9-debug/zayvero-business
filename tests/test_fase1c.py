"""
ZAYVERO BUSINESS — FASE 1C: pruebas de unificación del dataset completo.

Verifica que el Parquet unificado contenga los 1,067,371 registros
oficiales (Year 2009-2010 + Year 2010-2011) sin pérdidas silenciosas,
con trazabilidad de origen y esquema FASE 1A intacto.

Requiere haber ejecutado:
    .venv/bin/python pipeline.py data/raw/online_retail_II.xlsx \\
        --company demo-retail \\
        --sheets "Year 2009-2010,Year 2010-2011" \\
        --output-name online_retail_II_full

Falla con AssertionError si algo no cuadra (nada silencioso).
"""

from __future__ import annotations

import json
import os
import sys

import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(BASE, ".venv", "bin", "python")
COMPANY = "demo-retail"
PARQUET = os.path.join(
    BASE, "data", "processed", COMPANY, "online_retail_II_full.parquet")

EXPECTED_TOTAL = 1_067_371
EXPECTED_SHEETS = {
    "Year 2009-2010": 525_461,
    "Year 2010-2011": 541_910,
}
EXPECTED_FILE = "online_retail_II.xlsx"

# Columnas del esquema normalizado FASE 1A (+ trazabilidad FASE 1C)
REQUIRED_COLUMNS = [
    "company_id", "Transaction", "Product", "Customer", "Date",
    "Quantity", "UnitPrice", "Country", "Revenue", "transaction_status",
    "source_sheet", "source_file",
]


def load() -> pd.DataFrame:
    assert os.path.isfile(PARQUET), f"No existe el Parquet unificado: {PARQUET}"
    return pd.read_parquet(PARQUET)


def test_total_rows() -> None:
    """El total debe ser EXACTAMENTE 1,067,371: si se pierde un registro, falla."""
    df = load()
    assert len(df) == EXPECTED_TOTAL, (
        f"PÉRDIDA DE REGISTROS: hay {len(df)}, se esperaban {EXPECTED_TOTAL}"
    )
    print(f"OK 1c-1: total_rows == {EXPECTED_TOTAL}")


def test_rows_per_sheet() -> None:
    """Conteo exacto por hoja de origen."""
    df = load()
    counts = df["source_sheet"].value_counts(dropna=False).to_dict()
    for sheet, expected in EXPECTED_SHEETS.items():
        got = int(counts.get(sheet, 0))
        assert got == expected, (
            f"Hoja {sheet!r}: hay {got} filas, se esperaban {expected}"
        )
    assert len(counts) == 2, f"Se esperaban 2 hojas, hay: {list(counts)}"
    print(f"OK 1c-2: filas por hoja {EXPECTED_SHEETS}")


def test_source_file() -> None:
    """Toda fila debe llevar el archivo fuente."""
    df = load()
    vals = df["source_file"].dropna().unique().tolist()
    assert vals == [EXPECTED_FILE], f"source_file inesperado: {vals}"
    assert df["source_file"].isna().sum() == 0, "hay filas sin source_file"
    assert df["source_sheet"].isna().sum() == 0, "hay filas sin source_sheet"
    print(f"OK 1c-3: source_file == {EXPECTED_FILE!r} en todas las filas")


def test_schema_intact() -> None:
    """El esquema FASE 1A debe estar completo (nada roto por FASE 1C)."""
    df = load()
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    assert not missing, f"Columnas faltantes del esquema: {missing}"
    print(f"OK 1c-4: esquema intacto ({len(REQUIRED_COLUMNS)} columnas núcleo)")


def test_cancellation_status() -> None:
    """transaction_status solo con valores válidos; canceladas presentes."""
    df = load()
    vals = set(df["transaction_status"].dropna().unique().tolist())
    assert vals <= {"completed", "cancelled", "unknown"}, \
        f"estados inválidos: {vals}"
    n_canc = int((df["transaction_status"] == "cancelled").sum())
    assert n_canc > 0, "no se detectó ninguna cancelación"
    print(f"OK 1c-5: estados válidos, {n_canc} canceladas detectadas")


def test_company_isolation() -> None:
    """Todas las filas pertenecen a la compañía indicada."""
    df = load()
    vals = df["company_id"].unique().tolist()
    assert vals == [COMPANY], f"company_id inesperado: {vals}"
    print(f"OK 1c-6: company_id == {COMPANY!r} en todas las filas")


def test_audit_no_silent_loss() -> None:
    """La auditoría debe cuadrar: original == procesado, 0 descartadas."""
    sys.path.insert(0, BASE)
    from audit import list_by_company
    target = None
    for e in list_by_company(COMPANY):
        try:
            with open(e["log_path"], encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            continue
        if rec.get("output_path") == PARQUET:
            target = rec
    assert target is not None, \
        "no se encontró el registro de auditoría del Parquet unificado"
    assert target["n_original_rows"] == EXPECTED_TOTAL, \
        f"auditoría: original={target['n_original_rows']}"
    assert target["n_processed_rows"] == EXPECTED_TOTAL, \
        f"auditoría: procesado={target['n_processed_rows']}"
    assert target["rows_dropped"] == 0, \
        f"auditoría: se descartaron {target['rows_dropped']} filas"
    sheets = {s["sheet"]: s for s in (target.get("sheets") or [])}
    for sheet, expected in EXPECTED_SHEETS.items():
        assert sheets.get(sheet, {}).get("n_original_rows") == expected, \
            f"auditoría hoja {sheet}: {sheets.get(sheet)}"
    print("OK 1c-7: auditoría cuadra (1,067,371 original = procesado, 0 descartadas)")


def main() -> int:
    test_total_rows()
    test_rows_per_sheet()
    test_source_file()
    test_schema_intact()
    test_cancellation_status()
    test_company_isolation()
    test_audit_no_silent_loss()
    print("\nTodas las pruebas FASE 1C pasaron.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
