"""
ZAYVERO BUSINESS — FASE 2A: pruebas del motor de anomalías.

Casos:
    t1 pico de ventas temporal -> temporal_spike detectado
    t2 caída de ventas temporal -> temporal_drop detectado
    t3 precio atípico -> price_outlier detectado
    t4 cantidad atípica -> quantity_outlier detectado
    t5 cliente con compra inusual -> customer_unusual_purchase detectado
    t6 producto con cambio brusco -> product_sales_change detectado
    t7 dataset insuficiente (20 filas) -> no crashea, reporte válido
    t8 alta contaminación (30% outliers) -> no explota, acotado

Los detectores se prueban con DataFrames sintéticos directamente
(sin Parquet), más una pasada de integración sobre el Parquet real.

Falla con AssertionError si algo no cuadra (nada silencioso).
"""

from __future__ import annotations

import os
import sys

import numpy as np
import pandas as pd

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

from anomalies import customers as _cust  # noqa: E402
from anomalies import products as _prod  # noqa: E402
from anomalies import temporal as _temp  # noqa: E402
from anomalies.report import detect_anomalies  # noqa: E402

COMPANY = "demo-retail"
LABEL = "Demo Dataset — UCI Online Retail II"
PERIOD = "2020-01-01 → 2020-12-31"


def _base_df(n=500, seed=42) -> pd.DataFrame:
    """DataFrame sintético con esquema normalizado."""
    rng = np.random.default_rng(seed)
    dates = pd.date_range("2020-01-01", periods=200, freq="D")
    rows = []
    for i in range(n):
        d = dates[rng.integers(0, 200)]
        qty = int(rng.integers(1, 12))
        price = round(float(rng.uniform(1.0, 10.0)), 2)
        rows.append(
            {
                "company_id": COMPANY,
                "Transaction": f"{500000 + i}",
                "Product": f"P{rng.integers(1, 20):04d}",
                "Customer": f"{18000 + rng.integers(0, 30)}.0",
                "Date": d,
                "Quantity": qty,
                "UnitPrice": price,
                "Country": "United Kingdom",
                "Revenue": qty * price,
                "transaction_status": "completed",
                "source_sheet": "test",
                "source_file": "test.csv",
            }
        )
    return pd.DataFrame(rows)


def test_t1_spike() -> None:
    """Pico de ventas: un día con revenue 10x debe detectarse."""
    df = _base_df(2000)
    spike_day = pd.Timestamp("2020-06-15")
    # Inyectar pico: 50 transacciones grandes ese día
    spike = pd.DataFrame(
        [
            {
                "company_id": COMPANY,
                "Transaction": f"9{i:05d}",
                "Product": "P0001",
                "Customer": "18001.0",
                "Date": spike_day,
                "Quantity": 100,
                "UnitPrice": 50.0,
                "Country": "United Kingdom",
                "Revenue": 5000.0,
                "transaction_status": "completed",
                "source_sheet": "test",
                "source_file": "test.csv",
            }
            for i in range(50)
        ]
    )
    df = pd.concat([df, spike], ignore_index=True)
    found = _temp.detect_temporal(df, COMPANY, LABEL, PERIOD)
    spikes = [a for a in found if a["type"] == "temporal_spike"]
    assert spikes, "t1 FALLÓ: pico no detectado"
    hit = any(
        a["entity"]["id"] == "2020-06-15" for a in spikes
    )
    assert hit, "t1 FALLÓ: pico del 2020-06-15 no identificado"
    print(f"OK t1: pico detectado ({len(spikes)} spikes)")


def test_t2_drop() -> None:
    """Caída: una semana con revenue ~0 debe detectarse como drop."""
    df = _base_df(3000)
    # Vaciar una semana completa en mitad del período
    drop_start = pd.Timestamp("2020-08-01")
    drop_end = pd.Timestamp("2020-08-07")
    df = df[
        ~((df["Date"] >= drop_start) & (df["Date"] <= drop_end))
    ].copy()
    # Añadir un solo día con 1 unidad para que la semana exista pero baja
    filler = pd.DataFrame(
        [
            {
                "company_id": COMPANY,
                "Transaction": "800001",
                "Product": "P0001",
                "Customer": "18001.0",
                "Date": pd.Timestamp("2020-08-03"),
                "Quantity": 1,
                "UnitPrice": 1.0,
                "Country": "United Kingdom",
                "Revenue": 1.0,
                "transaction_status": "completed",
                "source_sheet": "test",
                "source_file": "test.csv",
            }
        ]
    )
    df = pd.concat([df, filler], ignore_index=True)
    found = _temp.detect_temporal(df, COMPANY, LABEL, PERIOD)
    drops = [a for a in found if a["type"] == "temporal_drop"]
    assert drops, "t2 FALLÓ: caída no detectada"
    print(f"OK t2: caída detectada ({len(drops)} drops)")


def test_t3_price() -> None:
    """Precio 50x el habitual del producto -> price_outlier."""
    df = _base_df(1000)
    # Producto P0001 con precio normal ~5; inyectar precio 250
    bad = df[df["Product"] == "P0001"].head(3).copy()
    bad["UnitPrice"] = 250.0
    bad["Revenue"] = bad["Quantity"] * 250.0
    bad["Transaction"] = ["777001", "777002", "777003"]
    df = pd.concat([df, bad], ignore_index=True)
    found = _prod.detect_price_outliers(df, COMPANY, LABEL, PERIOD, {})
    assert found, "t3 FALLÓ: precio atípico no detectado"
    assert any(
        a["entity"]["id"] == "P0001" for a in found
    ), "t3 FALLÓ: producto P0001 no identificado"
    print(f"OK t3: precio atípico detectado ({len(found)} casos)")


def test_t4_quantity() -> None:
    """Cantidad 500x la habitual -> quantity_outlier."""
    df = _base_df(1000)
    bad = df[df["Product"] == "P0002"].head(2).copy()
    bad["Quantity"] = 5000
    bad["Revenue"] = 5000 * bad["UnitPrice"]
    bad["Transaction"] = ["888001", "888002"]
    df = pd.concat([df, bad], ignore_index=True)
    found = _prod.detect_quantity_outliers(df, COMPANY, LABEL, PERIOD, {})
    assert found, "t4 FALLÓ: cantidad atípica no detectada"
    print(f"OK t4: cantidad atípica detectada ({len(found)} casos)")


def test_t5_customer() -> None:
    """Cliente con una compra 30x su mediana -> customer_unusual_purchase."""
    df = _base_df(800, seed=7)
    # Cliente 18001 con historial: darle 6 compras normales + 1 gigante
    cust_rows = df[df["Customer"] == "18001.0"].head(6).copy()
    big = cust_rows.head(1).copy()
    big["Quantity"] = 2000
    big["UnitPrice"] = 100.0
    big["Revenue"] = 200000.0
    big["Transaction"] = "999001"
    df = pd.concat([df, big], ignore_index=True)
    found = _cust.detect_unusual_purchases(df, COMPANY, LABEL, PERIOD)
    assert found, "t5 FALLÓ: compra inusual no detectada"
    print(f"OK t5: compra inusual detectada ({len(found)} casos)")


def test_t6_product_change() -> None:
    """Producto con 10x ventas en últimos 30 días -> product_sales_change."""
    rng = np.random.default_rng(11)
    rows = []
    # Producto XP001: 100 días de historia estable + 30 días con 10x
    for d in pd.date_range("2020-01-01", periods=100, freq="D"):
        q = int(rng.integers(8, 12))
        rows.append(
            {
                "company_id": COMPANY, "Transaction": f"6{d.dayofyear:05d}",
                "Product": "XP001", "Customer": "18001.0", "Date": d,
                "Quantity": q, "UnitPrice": 10.0, "Country": "United Kingdom",
                "Revenue": q * 10.0, "transaction_status": "completed",
                "source_sheet": "test", "source_file": "test.csv",
            }
        )
    for d in pd.date_range("2020-04-10", periods=30, freq="D"):
        q = int(rng.integers(80, 120))
        rows.append(
            {
                "company_id": COMPANY, "Transaction": f"7{d.dayofyear:05d}",
                "Product": "XP001", "Customer": "18002.0", "Date": d,
                "Quantity": q, "UnitPrice": 10.0, "Country": "United Kingdom",
                "Revenue": q * 10.0, "transaction_status": "completed",
                "source_sheet": "test", "source_file": "test.csv",
            }
        )
    df = pd.DataFrame(rows)
    found = _prod.detect_product_sales_change(df, COMPANY, LABEL, PERIOD, {})
    assert found, "t6 FALLÓ: cambio brusco no detectado"
    assert any(
        a["entity"]["id"] == "XP001" for a in found
    ), "t6 FALLÓ: producto XP001 no identificado"
    print(f"OK t6: cambio brusco detectado ({len(found)} casos)")


def test_t7_insufficient() -> None:
    """Dataset de 20 filas: no debe crashear, reporte válido."""
    df = _base_df(20)
    report = detect_anomalies.__wrapped__ if hasattr(
        detect_anomalies, "__wrapped__"
    ) else None
    # Llamar a los detectores directamente (sin Parquet)
    from anomalies.report import (
        _descriptions, _period_label, detect_anomalies as _da,
    )
    # Simular vía detectores individuales para no requerir archivo
    found = []
    found += _temp.detect_temporal(df, COMPANY, LABEL, PERIOD)
    found += _prod.detect_product_sales_change(
        df, COMPANY, LABEL, PERIOD, {}
    )
    # Lo importante: no lanzó excepción y devolvió lista
    assert isinstance(found, list), "t7 FALLÓ: no devolvió lista"
    print(f"OK t7: dataset insuficiente manejado ({len(found)} anomalías)")


def test_t8_contamination() -> None:
    """30% de outliers: el motor no debe explotar ni colgarse."""
    df = _base_df(1000, seed=99)
    n_bad = 300
    bad = df.head(n_bad).copy()
    bad["UnitPrice"] = bad["UnitPrice"] * 50
    bad["Revenue"] = bad["Quantity"] * bad["UnitPrice"]
    bad["Transaction"] = [f"5{i:05d}" for i in range(n_bad)]
    df = pd.concat([df, bad], ignore_index=True)
    found = _prod.detect_price_outliers(df, COMPANY, LABEL, PERIOD, {})
    assert isinstance(found, list), "t8 FALLÓ"
    assert len(found) <= 300, f"t8 FALLÓ: {len(found)} > cap 300"
    print(f"OK t8: contaminación alta acotada ({len(found)} casos)")


def test_t9_integration() -> None:
    """Integración: reporte completo sobre el Parquet real."""
    parquet = os.path.join(
        BASE, "data", "processed", COMPANY, "online_retail_II_full.parquet"
    )
    assert os.path.isfile(parquet), f"No existe {parquet}"
    report = detect_anomalies(parquet, COMPANY)
    assert report["summary"]["total_anomalies"] > 0, "t9: 0 anomalías"
    assert report["anomalies"], "t9: lista vacía"
    # Estructura de la primera anomalía
    a = report["anomalies"][0]
    for field in (
        "anomaly_id", "type", "severity", "entity", "period",
        "observed", "expected", "difference", "evidence", "explanation",
        "confidence_score", "trace",
    ):
        assert field in a, f"t9 FALLÓ: falta campo {field}"
    assert a["anomaly_id"] == "ANM-000001", "t9: IDs no ordenados"
    # Severidades válidas
    valid = {"INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"}
    assert all(
        x["severity"] in valid for x in report["anomalies"]
    ), "t9: severidad inválida"
    # Sin lenguaje prohibido
    banned = ["fraude", "robo", "pérdida", "error humano"]
    blob = str(report["anomalies"]).lower()
    for w in banned:
        assert w not in blob, f"t9 FALLÓ: lenguaje prohibido '{w}'"
    print(
        f"OK t9: integración ({report['summary']['total_anomalies']} "
        f"anomalías, severidades {report['summary']['by_severity']})"
    )


if __name__ == "__main__":
    test_t1_spike()
    test_t2_drop()
    test_t3_price()
    test_t4_quantity()
    test_t5_customer()
    test_t6_product_change()
    test_t7_insufficient()
    test_t8_contamination()
    test_t9_integration()
    print("\nTodas las pruebas FASE 2A pasaron.")
