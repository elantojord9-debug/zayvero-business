"""
ZAYVERO BUSINESS — FASE 2A.

`products`: anomalías de productos.

Tres detectores (todos vectorizados con groupby):

1. Cambio brusco de ventas (`product_sales_change`):
   - Ventana reciente: últimos 30 días calendario del dataset.
   - Historial: días anteriores (se exigen >= 30 días activos de historia
     y >= 10 transacciones en el historial: sin historia no hay baseline).
   - Métrica: revenue diario promedio reciente vs mediana del revenue
     diario histórico. dev = |z robusto| sobre la serie diaria del producto.
   - Emisión si dev >= 2.5. NO se marca un producto solo por vender poco:
     se exige historial y la comparación es contra SÍ MISMO.

2. Precio atípico (`price_outlier`):
   - Por producto, sobre transacciones completed con UnitPrice > 0.
   - Límites: Q1 - 3*IQR / Q3 + 3*IQR (extremos lejanos; k=3 reduce ruido).
   - Se exigen >= 10 precios distintos de historia.
   - DISTINCIÓN: "precio diferente" (aquí) vs "precio inválido"
     (<= 0 o no numérico: eso ya lo cubre el Data Quality Engine y se
     excluye explícitamente).

3. Cantidad atípica (`quantity_outlier`):
   - Por producto, sobre transacciones completed con Quantity > 0.
   - Las cantidades negativas/0 NO son anomalías: pertenecen a la lógica
     de cancelación/devolución y se excluyen (documentado).
   - Límites: Q3 + 3*IQR (solo cola superior: cantidades extraordinarias).
   - Se exigen >= 10 cantidades de historia.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from anomalies import stats as _st
from anomalies.scoring import (
    assign_severity,
    build_explanation,
    confidence_score,
    priority_score,
    ratio_to_dev,
)

RECENT_DAYS = 30
MIN_HISTORY_DAYS = 30
MIN_HISTORY_TRX = 10


def _fmt_money(v: float) -> str:
    return f"£{v:,.2f}"


def _product_label(code: str, descriptions: dict[str, str]) -> str:
    desc = descriptions.get(str(code), "")
    desc = (desc or "").strip()
    if desc:
        short = desc if len(desc) <= 45 else desc[:42] + "..."
        return f"{code} ({short})"
    return str(code)


def detect_product_sales_change(
    df: pd.DataFrame,
    company_id: str,
    dataset_label: str,
    period_label: str,
    descriptions: dict[str, str],
) -> list[dict[str, Any]]:
    """Cambios bruscos de ventas por producto vs su propio historial."""
    valid = df[
        (df["transaction_status"] == "completed")
        & df["Date"].notna()
        & df["Revenue"].notna()
        & df["Product"].notna()
    ].copy()
    n_rows = len(df)
    if valid.empty:
        return []
    max_date = valid["Date"].max()
    cutoff = max_date - pd.Timedelta(days=RECENT_DAYS)
    valid["day"] = valid["Date"].dt.floor("D")

    daily = (
        valid.groupby(["Product", "day"], observed=True)["Revenue"]
        .sum()
        .reset_index()
    )
    hist = daily[daily["day"] < cutoff]
    recent = daily[daily["day"] >= cutoff]

    h_stats = (
        hist.groupby("Product", observed=True)["Revenue"]
        .agg(hist_median="median", hist_days="size", hist_trx="count")
        .reset_index()
    )
    # Transacciones del historial por producto (para el mínimo)
    h_trx = (
        valid[valid["day"] < cutoff]
        .groupby("Product", observed=True)
        .size()
        .rename("hist_trx_n")
        .reset_index()
    )
    h_stats = h_stats.merge(h_trx, on="Product", how="left")
    r_stats = (
        recent.groupby("Product", observed=True)["Revenue"]
        .agg(recent_sum="sum", recent_days="size")
        .reset_index()
    )
    m = h_stats.merge(r_stats, on="Product", how="inner")
    m = m[
        (m["hist_days"] >= MIN_HISTORY_DAYS)
        & (m["hist_trx_n"] >= MIN_HISTORY_TRX)
        & (m["hist_median"] > 0)
    ]
    if m.empty:
        return []

    m["recent_daily_avg"] = m["recent_sum"] / RECENT_DAYS
    # MAD del historial por producto (vectorizado vía transform)
    hist_mad = (
        hist.merge(
            h_stats[["Product", "hist_median"]], on="Product", how="inner"
        )
    )
    hist_mad["absdev"] = (hist_mad["Revenue"] - hist_mad["hist_median"]).abs()
    mad_by_p = (
        hist_mad.groupby("Product", observed=True)["absdev"]
        .median()
        .rename("hist_mad")
        .reset_index()
    )
    m = m.merge(mad_by_p, on="Product", how="left")
    # z robusto; si MAD=0 (ventas diarias constantes en el historial),
    # se usa el ratio como medida de desviación en vez de infinito.
    m["z_raw"] = np.where(
        m["hist_mad"] > 0,
        0.6745 * (m["recent_daily_avg"] - m["hist_median"]) / m["hist_mad"],
        np.where(
            m["recent_daily_avg"] != m["hist_median"],
            np.sign(m["recent_daily_avg"] - m["hist_median"]) * np.inf,
            0.0,
        ),
    )
    m["ratio"] = m["recent_daily_avg"] / m["hist_median"]
    m["z"] = np.where(
        np.isfinite(m["z_raw"]),
        m["z_raw"],
        np.sign(m["z_raw"])
        * m["ratio"].apply(lambda r: ratio_to_dev(r) if np.isfinite(r) else 10.0),
    )
    flagged = m[m["z"].abs() >= 2.5].copy()
    if flagged.empty:
        return []

    null_frac = float(valid["Revenue"].isna().mean()) if n_rows else 0.0
    anomalies: list[dict[str, Any]] = []
    for _, r in flagged.iterrows():
        z = float(r["z"])
        dev = abs(z)
        ratio = float(r["ratio"])
        direction = "superiores" if z > 0 else "inferiores"
        expected = float(r["hist_median"]) * RECENT_DAYS
        observed = float(r["recent_sum"])
        sustained = bool(r["recent_days"] >= 3)
        conf, conf_factors = confidence_score(
            n_history=int(r["hist_days"]),
            dev=dev,
            sustained=sustained,
            null_fraction=null_frac,
        )
        severity = assign_severity(dev, conf)
        label = _product_label(r["Product"], descriptions)
        explanation = build_explanation(
            "product_sales_change",
            _fmt_money,
            entity_label=label,
            ratio=ratio if np.isfinite(ratio) else 999.0,
            direction=direction,
            expected_fmt=expected,
            observed_fmt=observed,
            window_days=RECENT_DAYS,
        )
        anomalies.append(
            {
                "type": "product_sales_change",
                "severity": severity,
                "entity": {
                    "kind": "product",
                    "id": str(r["Product"]),
                    "label": label,
                },
                "period": {
                    "start": cutoff.isoformat(),
                    "end": pd.Timestamp(max_date).isoformat(),
                    "granularity": "30d_window",
                },
                "observed": round(observed, 2),
                "expected": round(expected, 2),
                "difference": round(observed - expected, 2),
                "ratio": round(ratio, 3) if np.isfinite(ratio) else None,
                "z_robust": round(z, 2) if np.isfinite(z) else None,
                "deviation": round(dev, 2) if np.isfinite(dev) else None,
                "evidence": {
                    "history_days": int(r["hist_days"]),
                    "history_transactions": int(r["hist_trx_n"]),
                    "history_median_daily": round(float(r["hist_median"]), 2),
                    "recent_days_active": int(r["recent_days"]),
                    "sustained": sustained,
                },
                "explanation": explanation,
                "confidence_score": conf,
                "confidence_factors": conf_factors,
                "trace": {
                    "dataset": dataset_label,
                    "company_id": company_id,
                    "method": "revenue diario por producto; baseline = mediana "
                    "del revenue diario histórico; z robusto con MAD del "
                    "historial del propio producto",
                    "filters": "transaction_status == 'completed'; historial >= "
                    f"{MIN_HISTORY_DAYS} días activos y >= {MIN_HISTORY_TRX} "
                    "transacciones; mediana histórica > 0",
                    "rows_considered": int(n_rows),
                    "baseline": f"mediana diaria histórica × {RECENT_DAYS} días",
                    "formula": "z = 0.6745*(promedio_diario_reciente - "
                    "mediana_histórica)/MAD_histórico; emisión si |z| >= 2.5",
                    "period_analyzed": period_label,
                    "notes": "Comparación contra el propio historial del "
                    "producto, nunca contra otros productos. Un producto que "
                    "vende poco pero estable NO genera anomalía.",
                },
                "_priority": priority_score(
                    severity, conf, observed - expected
                ),
            }
        )
    return anomalies


def _outlier_detector(
    df: pd.DataFrame,
    column: str,
    anomaly_type: str,
    company_id: str,
    dataset_label: str,
    period_label: str,
    descriptions: dict[str, str],
    upper_only: bool = False,
    min_history: int = 10,
    max_per_product: int = 5,
    max_anomalies: int = 300,
) -> list[dict[str, Any]]:
    """
    Detector genérico de outliers por producto con límites IQR k=3.
    upper_only=True: solo cola superior (cantidades extraordinarias).
    """
    base = df[
        (df["transaction_status"] == "completed")
        & df["Product"].notna()
        & df[column].notna()
        & (df[column] > 0)
    ][["Product", "Transaction", "Date", "Customer", column]].copy()
    n_rows = len(df)
    if base.empty:
        return []
    # Descripción para etiquetas
    q = base.groupby("Product", observed=True)[column].agg(
        q1=lambda s: s.quantile(0.25),
        q3=lambda s: s.quantile(0.75),
        n="size",
    )
    q["iqr"] = q["q3"] - q["q1"]
    q = q[q["n"] >= min_history]
    q = q[q["iqr"] > 0]
    if q.empty:
        return []
    q["lower"] = q["q1"] - 3.0 * q["iqr"]
    q["upper"] = q["q3"] + 3.0 * q["iqr"]

    work = base.merge(
        q[["lower", "upper", "q1", "q3"]],
        left_on="Product",
        right_index=True,
        how="inner",
    )
    if upper_only:
        mask = work[column] > work["upper"]
    else:
        mask = (work[column] < work["lower"]) | (work[column] > work["upper"])
    out = work[mask].copy()
    if out.empty:
        return []

    # Desviación relativa al límite excedido (para severidad)
    over = (out[column] - out["upper"]).clip(lower=0)
    under = (out["lower"] - out[column]).clip(lower=0)
    span = (out["upper"] - out["lower"]).replace(0, np.nan)
    out["_excess"] = (over + under) / span
    out["_excess"] = out["_excess"].fillna(0)

    # Limitar por producto (los más extremos) y en total
    out = out.sort_values("_excess", ascending=False)
    out = out.groupby("Product", observed=True).head(max_per_product)
    out = out.sort_values("_excess", ascending=False).head(max_anomalies)

    hist_n = base.groupby("Product", observed=True).size().to_dict()
    anomalies: list[dict[str, Any]] = []
    for _, r in out.iterrows():
        val = float(r[column])
        upper = float(r["upper"])
        lower = float(r["lower"])
        iqr = float(r["q3"] - r["q1"])
        if val > upper:
            direction = "superior" if column == "UnitPrice" else "extraordinariamente alta"
            ref, ratio = upper, val / upper if upper > 0 else float("inf")
            # Desviación en unidades de IQR más allá de Q3 (medida honesta
            # para outliers IQR: estar justo sobre el límite no es extremo).
            dev = (val - float(r["q3"])) / iqr if iqr > 0 else 0.0
        else:
            direction = "inferior"
            ref, ratio = lower, lower / val if val > 0 else float("inf")
            dev = (float(r["q1"]) - val) / iqr if iqr > 0 else 0.0
        dev = max(float(dev), 0.0)
        n_hist = int(hist_n.get(r["Product"], 0))
        conf, conf_factors = confidence_score(
            n_history=n_hist, dev=dev, sustained=False, null_fraction=0.0
        )
        severity = assign_severity(dev, conf)
        label = _product_label(r["Product"], descriptions)
        col_label = "precio" if column == "UnitPrice" else "cantidad"
        examples = f"{col_label} {val:,.2f} en transacción {r['Transaction']}"
        explanation = build_explanation(
            anomaly_type,
            _fmt_money if column == "UnitPrice" else (lambda v: f"{v:,.0f}"),
            entity_label=label,
            n=1,
            direction=direction,
            low_fmt=lower,
            high_fmt=upper,
            examples=examples,
        )
        anomalies.append(
            {
                "type": anomaly_type,
                "severity": severity,
                "entity": {
                    "kind": "product",
                    "id": str(r["Product"]),
                    "label": label,
                },
                "period": {
                    "start": pd.Timestamp(r["Date"]).isoformat()
                    if pd.notna(r["Date"])
                    else None,
                    "end": pd.Timestamp(r["Date"]).isoformat()
                    if pd.notna(r["Date"])
                    else None,
                    "granularity": "transaction",
                },
                "observed": round(val, 2),
                "expected": round(ref, 2),
                "difference": round(val - ref, 2),
                "ratio": round(float(ratio), 3)
                if np.isfinite(ratio)
                else None,
                "deviation": round(float(dev), 2),
                "evidence": {
                    "column": column,
                    "history_points": n_hist,
                    "q1": round(float(r["q1"]), 2),
                    "q3": round(float(r["q3"]), 2),
                    "iqr": round(float(r["q3"] - r["q1"]), 2),
                    "bounds": [round(lower, 2), round(upper, 2)],
                    "transaction": str(r["Transaction"]),
                    "customer": str(r["Customer"])
                    if pd.notna(r["Customer"])
                    else None,
                },
                "explanation": explanation,
                "confidence_score": conf,
                "confidence_factors": conf_factors,
                "trace": {
                    "dataset": dataset_label,
                    "company_id": company_id,
                    "method": f"IQR por producto sobre {column} "
                    "(k=3, extremos lejanos)",
                    "filters": "transaction_status == 'completed', "
                    f"{column} > 0. "
                    + (
                        "Precios <= 0 excluidos: pertenecen al Data Quality "
                        "Engine (precio inválido ≠ precio diferente)."
                        if column == "UnitPrice"
                        else "Cantidades <= 0 excluidas: pertenecen a la "
                        "lógica de cancelación/devolución, no son anomalías."
                    ),
                    "rows_considered": int(n_rows),
                    "baseline": "Q1/Q3 del propio producto",
                    "formula": "límite = Q3 + 3*IQR (superior)"
                    + (", Q1 - 3*IQR (inferior)" if not upper_only else ""),
                    "period_analyzed": period_label,
                    "notes": f"Máximo {max_per_product} casos por producto, "
                    f"{max_anomalies} en total (los más extremos).",
                },
                "_priority": priority_score(severity, conf, val - ref),
            }
        )
    return anomalies


def detect_price_outliers(
    df: pd.DataFrame,
    company_id: str,
    dataset_label: str,
    period_label: str,
    descriptions: dict[str, str],
) -> list[dict[str, Any]]:
    return _outlier_detector(
        df, "UnitPrice", "price_outlier", company_id, dataset_label,
        period_label, descriptions, upper_only=False,
    )


def detect_quantity_outliers(
    df: pd.DataFrame,
    company_id: str,
    dataset_label: str,
    period_label: str,
    descriptions: dict[str, str],
) -> list[dict[str, Any]]:
    return _outlier_detector(
        df, "Quantity", "quantity_outlier", company_id, dataset_label,
        period_label, descriptions, upper_only=True,
    )
