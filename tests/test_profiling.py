"""
ZAYVERO BUSINESS — FASE 1B: batería de pruebas.

Pruebas:
  2. sintético pequeño (200 filas, esquema UCI)
  3. valores faltantes (nulos en campos críticos)
  4. cancelaciones (facturas con prefijo C)
  5. precios inválidos (texto y ceros)
  6. dataset vacío (solo cabeceras, 0 filas)

Cada prueba: genera CSV -> pipeline.py -> profile.py -> valida el perfil.
Falla con AssertionError si algo sale mal (nada silencioso).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENV_PY = os.path.join(BASE, ".venv", "bin", "python")
RAW = os.path.join(BASE, "data", "raw")
COMPANY = "test-profiling"

COLS = ["Invoice", "StockCode", "Description", "Quantity",
        "InvoiceDate", "UnitPrice", "Customer ID", "Country"]


def run(cmd: list[str]) -> subprocess.CompletedProcess:
    r = subprocess.run(cmd, capture_output=True, text=True, cwd=BASE)
    if r.returncode != 0:
        raise AssertionError(f"Falló: {' '.join(cmd)}\n{r.stderr[-2000:]}")
    return r


def process_and_profile(name: str, df: pd.DataFrame) -> dict:
    csv_path = os.path.join(RAW, f"{name}.csv")
    df.to_csv(csv_path, index=False)
    run([VENV_PY, "pipeline.py", csv_path, "--company", COMPANY])
    parquet = os.path.join(
        BASE, "data", "processed", COMPANY, f"{name}.parquet")
    run([VENV_PY, "profile.py", parquet, "--company", COMPANY])
    profile_path = os.path.join(
        BASE, "data", "profiles", COMPANY, f"{name}_profile.json")
    with open(profile_path, encoding="utf-8") as f:
        return json.load(f)


def check_common(p: dict, name: str) -> None:
    for key in ("dataset_metadata", "date_range", "transactions", "customers",
                "products", "countries", "sales", "cancellations",
                "data_quality", "temporal_metrics"):
        assert key in p, f"[{name}] falta sección {key}"
        trace = p[key].get("trace")
        assert trace, f"[{name}] sección {key} sin trace"
        for tk in ("formula", "filters", "rows_considered", "period"):
            assert tk in trace, f"[{name}] trace de {key} sin {tk}"
    # JSON-serializable ya garantizado al cargar; verifica tipos básicos
    assert isinstance(p["data_quality"]["score"], (int, float))
    assert 0 <= p["data_quality"]["score"] <= 100


def test_small_synthetic() -> None:
    rows = []
    for i in range(200):
        rows.append([
            f"INV{i:05d}", f"P{i % 20:04d}", f"Producto {i % 20}",
            (i % 5) + 1, f"2024-{(i % 12) + 1:02d}-15 10:00:00",
            round(10.0 + (i % 7), 2), 1000 + (i % 30), "España",
        ])
    p = process_and_profile("t2_small", pd.DataFrame(rows, columns=COLS))
    check_common(p, "t2_small")
    assert p["transactions"]["total_rows"] == 200
    assert p["sales"]["gross_revenue"] > 0
    assert p["entities"]["products"] == 20
    assert len(p["temporal_metrics"]["monthly"]) == 12
    print("OK t2: sintético pequeño (200 filas)")


def test_missing_values() -> None:
    rows = []
    for i in range(100):
        rows.append([
            f"INV{i:05d}" if i % 10 else None,      # 10% sin Invoice
            f"P{i % 10:04d}", f"Producto {i % 10}",
            (i % 4) + 1 if i % 7 else None,          # nulos en Quantity
            "2024-03-10 10:00:00" if i % 5 else "fecha-mala",
            9.99 if i % 3 else None,                # nulos en precio
            2000 + (i % 15) if i % 4 else None,      # nulos en cliente
            "México",
        ])
    p = process_and_profile("t3_missing", pd.DataFrame(rows, columns=COLS))
    check_common(p, "t3_missing")
    dq = p["data_quality"]
    assert dq["score"] < 100, "con nulos el score debe bajar de 100"
    comps = {d["component"] for d in dq["deductions"]}
    assert "missing_critical" in comps, f"falta deducción missing: {comps}"
    assert "invalid_dates" in comps
    print(f"OK t3: faltantes (score={dq['score']})")


def test_cancellations() -> None:
    rows = []
    for i in range(120):
        cancelled = i % 4 == 0  # 25% canceladas
        inv = f"C{i:05d}" if cancelled else f"INV{i:05d}"
        qty = -2 if cancelled else 2
        rows.append([inv, "P0001", "Prod", qty, "2024-05-01 10:00:00",
                     25.0, 3001, "Chile"])
    p = process_and_profile("t4_cancel", pd.DataFrame(rows, columns=COLS))
    check_common(p, "t4_cancel")
    c = p["cancellations"]
    assert c["count"] == 30, f"esperaba 30 canceladas, hay {c['count']}"
    assert p["transactions"]["cancelled_rows"] == 30
    # NETO debe ser menor que BRUTO (las canceladas restan)
    assert p["sales"]["net_revenue"] < p["sales"]["gross_revenue"]
    assert p["sales"]["cancelled_revenue"] > 0
    print(f"OK t4: cancelaciones (neto={p['sales']['net_revenue']}, "
          f"bruto={p['sales']['gross_revenue']})")


def test_invalid_prices() -> None:
    rows = []
    for i in range(80):
        price = "no-es-precio" if i % 4 == 0 else (0.0 if i % 4 == 1 else 12.5)
        rows.append([f"INV{i:05d}", "P0002", "Prod", 3,
                     "2024-06-01 10:00:00", price, 4001, "Perú"])
    p = process_and_profile("t5_badprices", pd.DataFrame(rows, columns=COLS))
    check_common(p, "t5_badprices")
    comps = {d["component"] for d in p["data_quality"]["deductions"]}
    assert "invalid_prices" in comps, f"falta invalid_prices: {comps}"
    assert "non_positive_prices" in comps
    print(f"OK t5: precios inválidos (score={p['data_quality']['score']})")


def test_empty() -> None:
    df = pd.DataFrame(columns=COLS)
    p = process_and_profile("t6_empty", df)
    check_common(p, "t6_empty")
    assert p["dataset_metadata"]["n_rows"] == 0
    assert p["data_quality"]["score"] == 0
    assert p["transactions"]["total_rows"] == 0
    assert p["temporal_metrics"]["daily"] == []
    assert p["products"]["top_products"] == []
    print("OK t6: dataset vacío manejado con gracia (score 0, sin crash)")


def main() -> None:
    os.makedirs(RAW, exist_ok=True)
    test_small_synthetic()
    test_missing_values()
    test_cancellations()
    test_invalid_prices()
    test_empty()
    print("\nTodas las pruebas 2–6 pasaron.")


if __name__ == "__main__":
    sys.exit(main())
