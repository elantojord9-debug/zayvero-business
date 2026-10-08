"""
ZAYVERO BUSINESS — FASE 2A.

`customers`: anomalías de clientes.

Dos detectores (vectorizados):

1. Compra inusual (`customer_unusual_purchase`):
   - Clientes con >= 5 transacciones completed (sin historia no hay baseline).
   - Por transacción: revenue vs mediana y MAD del propio cliente.
   - z robusto >= 4 (umbral más alto: una sola compra grande es común
     en wholesale; se exige desviación fuerte).
   - Top 100 por z (acotado).

2. Ráfaga de frecuencia (`customer_frequency_burst`):
   - Clientes con >= 10 transacciones: intervalo mediano entre compras.
   - Ráfaga: >= 4 compras en 7 días cuando el intervalo habitual > 30 días.
   - NO es modelo de churn: solo describe concentración inusual.

Sin CustomerID no hay análisis (se documenta en trace).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from anomalies.scoring import (
    assign_severity,
    build_explanation,
    confidence_score,
    priority_score,
    ratio_to_dev,
)

MIN_TRX_PURCHASE = 5
MIN_TRX_BURST = 10
Z_PURCHASE = 4.0
BURST_N = 4
BURST_DAYS = 7
BURST_MIN_GAP = 30


def _fmt_money(v: float) -> str:
    return f"£{v:,.2f}"


def detect_unusual_purchases(
    df: pd.DataFrame,
    company_id: str,
    dataset_label: str,
    period_label: str,
) -> list[dict[str, Any]]:
    """Compras excepcionalmente grandes vs el historial del propio cliente."""
    base = df[
        (df["transaction_status"] == "completed")
        & df["Customer"].notna()
        & df["Revenue"].notna()
    ][["Customer", "Transaction", "Date", "Revenue"]].copy()
    n_rows = len(df)
    if base.empty:
        return []
    trx_rev = (
        base.groupby(["Customer", "Transaction"], observed=True)["Revenue"]
        .sum()
        .reset_index()
    )
    cust = trx_rev.groupby("Customer", observed=True)["Revenue"].agg(
        med="median", n="size"
    )
    cust = cust[cust["n"] >= MIN_TRX_PURCHASE]
    if cust.empty:
        return []
    mad = (
        trx_rev.merge(cust[["med"]], left_on="Customer", right_index=True)
        .assign(absdev=lambda d: (d["Revenue"] - d["med"]).abs())
        .groupby("Customer", observed=True)["absdev"]
        .median()
        .rename("mad")
    )
    cust = cust.join(mad)
    work = trx_rev.merge(cust, left_on="Customer", right_index=True, how="inner")
    work["z"] = np.where(
        work["mad"] > 0,
        0.6745 * (work["Revenue"] - work["med"]) / work["mad"],
        np.where(
            work["Revenue"] != work["med"],
            np.sign(work["Revenue"] - work["med"]) * np.inf,
            0.0,
        ),
    )
    # Si MAD=0 (compras idénticas en el historial), medir por ratio.
    _ratio = work["Revenue"] / work["med"].replace(0, np.nan)
    work["z"] = np.where(
        np.isfinite(work["z"]),
        work["z"],
        np.sign(work["z"])
        * _ratio.apply(lambda r: ratio_to_dev(r) if pd.notna(r) and np.isfinite(r) else 10.0),
    )
    flagged = work[work["z"] >= Z_PURCHASE].copy()
    if flagged.empty:
        return []
    flagged = flagged.sort_values("z", ascending=False).head(100)

    anomalies: list[dict[str, Any]] = []
    for _, r in flagged.iterrows():
        z = float(r["z"])
        observed = float(r["Revenue"])
        expected = float(r["med"])
        ratio = observed / expected if expected > 0 else float("inf")
        dev = abs(z)
        conf, conf_factors = confidence_score(
            n_history=int(r["n"]), dev=dev, sustained=False,
            null_fraction=0.0,
        )
        severity = assign_severity(dev, conf)
        label = str(r["Customer"])
        explanation = build_explanation(
            "customer_unusual_purchase",
            _fmt_money,
            entity_label=label,
            observed_fmt=observed,
            expected_fmt=expected,
            ratio=ratio if np.isfinite(ratio) else 999.0,
        )
        anomalies.append(
            {
                "type": "customer_unusual_purchase",
                "severity": severity,
                "entity": {
                    "kind": "customer",
                    "id": label,
                    "label": f"cliente {label}",
                },
                "period": {
                    "start": None,
                    "end": None,
                    "granularity": "transaction",
                },
                "observed": round(observed, 2),
                "expected": round(expected, 2),
                "difference": round(observed - expected, 2),
                "ratio": round(float(ratio), 3)
                if np.isfinite(ratio)
                else None,
                "z_robust": round(z, 2) if np.isfinite(z) else None,
                "deviation": round(dev, 2),
                "evidence": {
                    "transaction": str(r["Transaction"]),
                    "customer_history_transactions": int(r["n"]),
                    "customer_median_purchase": round(expected, 2),
                },
                "explanation": explanation,
                "confidence_score": conf,
                "confidence_factors": conf_factors,
                "trace": {
                    "dataset": dataset_label,
                    "company_id": company_id,
                    "method": "z robusto por transacción vs mediana/MAD del "
                    "propio cliente",
                    "filters": "transaction_status == 'completed', "
                    f"clientes con >= {MIN_TRX_PURCHASE} transacciones",
                    "rows_considered": int(n_rows),
                    "baseline": "mediana de compras del cliente",
                    "formula": f"z = 0.6745*(revenue - mediana)/MAD; "
                    f"emisión si z >= {Z_PURCHASE}",
                    "period_analyzed": period_label,
                    "notes": "Umbral alto (z>=4): en wholesale una compra "
                    "grande aislada puede ser normal. Top 100 por z.",
                },
                "_priority": priority_score(
                    severity, conf, observed - expected
                ),
            }
        )
    return anomalies


def detect_frequency_bursts(
    df: pd.DataFrame,
    company_id: str,
    dataset_label: str,
    period_label: str,
) -> list[dict[str, Any]]:
    """Concentración inusual de compras en ventana corta."""
    base = df[
        (df["transaction_status"] == "completed")
        & df["Customer"].notna()
        & df["Date"].notna()
    ][["Customer", "Transaction", "Date"]].copy()
    n_rows = len(df)
    if base.empty:
        return []
    first = (
        base.groupby(["Customer", "Transaction"], observed=True)["Date"]
        .min()
        .reset_index()
        .sort_values(["Customer", "Date"])
    )
    # Intervalo mediano entre compras por cliente
    first["prev"] = first.groupby("Customer", observed=True)["Date"].shift(1)
    first["gap_days"] = (first["Date"] - first["prev"]).dt.total_seconds() / 86400
    gap = (
        first.groupby("Customer", observed=True)["gap_days"]
        .agg(med_gap="median", n="size")
        .reset_index()
    )
    candidates = gap[(gap["n"] >= MIN_TRX_BURST) & (gap["med_gap"] > BURST_MIN_GAP)]
    if candidates.empty:
        return []

    anomalies: list[dict[str, Any]] = []
    cand_ids = set(candidates["Customer"])
    sub = first[first["Customer"].isin(cand_ids)].copy()
    # Ventana móvil de 7 días: contar compras por cliente
    for cust, grp in sub.groupby("Customer", observed=True):
        dates = grp["Date"].sort_values().reset_index(drop=True)
        if len(dates) < BURST_N:
            continue
        # two-pointer: máxima concentración en 7 días
        best = 0
        j = 0
        for i in range(len(dates)):
            while (dates[j] - dates[i]).total_seconds() / 86400 > BURST_DAYS:
                j += 1
            best = max(best, i - j + 1)
        if best < BURST_N:
            continue
        med_gap = float(
            candidates.loc[candidates["Customer"] == cust, "med_gap"].iloc[0]
        )
        n_total = int(
            candidates.loc[candidates["Customer"] == cust, "n"].iloc[0]
        )
        exp_in_window = BURST_DAYS / med_gap if med_gap > 0 else 0.0
        # Desviación: exceso de compras relativo a al menos 1 esperada.
        # (Usar el esperado crudo explotaría cuando es << 1.)
        dev = (best - exp_in_window) / max(exp_in_window, 1.0)
        dev = max(float(dev), 0.0)
        ratio = best / exp_in_window if exp_in_window > 0 else float("inf")
        if dev < 2.5:
            continue
        conf, conf_factors = confidence_score(
            n_history=n_total, dev=dev, sustained=True, null_fraction=0.0
        )
        severity = assign_severity(dev, conf)
        label = str(cust)
        explanation = build_explanation(
            "customer_frequency_burst",
            _fmt_money,
            entity_label=label,
            n=best,
            window_days=BURST_DAYS,
            expected_gap=med_gap,
        )
        anomalies.append(
            {
                "type": "customer_frequency_burst",
                "severity": severity,
                "entity": {
                    "kind": "customer",
                    "id": label,
                    "label": f"cliente {label}",
                },
                "period": {
                    "start": None,
                    "end": None,
                    "granularity": f"{BURST_DAYS}d_window",
                },
                "observed": best,
                "expected": round(BURST_DAYS / med_gap, 2),
                "difference": round(best - BURST_DAYS / med_gap, 2),
                "ratio": round(float(ratio), 3)
                if np.isfinite(ratio)
                else None,
                "deviation": round(float(dev), 2),
                "evidence": {
                    "max_purchases_in_window": best,
                    "window_days": BURST_DAYS,
                    "median_gap_days": round(med_gap, 1),
                    "total_transactions": n_total,
                },
                "explanation": explanation,
                "confidence_score": conf,
                "confidence_factors": conf_factors,
                "trace": {
                    "dataset": dataset_label,
                    "company_id": company_id,
                    "method": "ventana móvil de 7 días (two-pointer) vs "
                    "intervalo mediano entre compras del cliente",
                    "filters": "transaction_status == 'completed', "
                    f"clientes con >= {MIN_TRX_BURST} transacciones e "
                    f"intervalo mediano > {BURST_MIN_GAP} días",
                    "rows_considered": int(n_rows),
                    "baseline": "intervalo mediano entre compras del cliente",
                    "formula": f"ráfaga si >= {BURST_N} compras en "
                    f"{BURST_DAYS} días con intervalo habitual > "
                    f"{BURST_MIN_GAP} días",
                    "period_analyzed": period_label,
                    "notes": "Descriptivo de concentración; no es modelo "
                    "de churn.",
                },
                "_priority": priority_score(severity, conf, dev),
            }
        )
        if len(anomalies) >= 100:
            break
    return anomalies


def detect_customers(
    df: pd.DataFrame,
    company_id: str,
    dataset_label: str,
    period_label: str,
) -> list[dict[str, Any]]:
    return detect_unusual_purchases(
        df, company_id, dataset_label, period_label
    ) + detect_frequency_bursts(df, company_id, dataset_label, period_label)
