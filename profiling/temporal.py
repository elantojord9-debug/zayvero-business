"""
ZAYVERO BUSINESS — FASE 1B.

`temporal`: agregaciones diaria, semanal y mensual.

Para cada período se calcula (cuando los datos lo permiten):
    - revenue:   Σ Revenue con signo (completed + cancelled)
    - units:     Σ Quantity con signo
    - transactions: COUNT DISTINCT Transaction
    - customers: COUNT DISTINCT Customer
    - avg_ticket: revenue / transactions
    - products_sold: COUNT DISTINCT Product

Etiquetas: diaria "YYYY-MM-DD", semanal "YYYY-Www" (ISO, semana empieza
lunes), mensual "YYYY-MM".

Estructura preparada para tendencias futuras: cada serie es una lista
ordenada cronológicamente de dicts homogéneos.
"""

from __future__ import annotations

import pandas as pd

from profiling.trace import make_trace, to_jsonable


def _aggregate(df: pd.DataFrame, key: pd.Series, label: str) -> list[dict]:
    if df.empty:
        return []
    work = df[["Revenue", "Quantity", "Transaction", "Customer", "Product"]].copy()
    work["_k"] = key
    # Solo filas con algún identificador de período válido
    work = work[work["_k"].notna()]
    if work.empty:
        return []
    g = work.groupby("_k", observed=True)
    rows = []
    for k, grp in g:
        rev = grp["Revenue"].sum(skipna=True)
        trx = grp["Transaction"].nunique()
        rows.append(
            {
                "period": str(k),
                "revenue": round(float(rev), 2) if pd.notna(rev) else 0.0,
                "units": float(grp["Quantity"].sum(skipna=True)),
                "transactions": int(trx),
                "customers": int(grp["Customer"].nunique()),
                "avg_ticket": round(float(rev) / trx, 2)
                if trx and pd.notna(rev)
                else 0.0,
                "products_sold": int(grp["Product"].nunique()),
            }
        )
    rows.sort(key=lambda r: r["period"])
    return rows


def build_temporal(df: pd.DataFrame) -> dict:
    n = len(df)
    period = ""
    if n and df["Date"].notna().any():
        dmin, dmax = df["Date"].min(), df["Date"].max()
        period = f"{pd.Timestamp(dmin).date()} → {pd.Timestamp(dmax).date()}"

    valid = df[df["Date"].notna()].copy()
    daily = _aggregate(valid, valid["Date"].dt.strftime("%Y-%m-%d"), "daily")
    # Semana ISO: YYYY-Www
    iso = valid["Date"].dt.isocalendar()
    weekly_key = (
        iso["year"].astype(str) + "-W" + iso["week"].astype(str).str.zfill(2)
    )
    weekly = _aggregate(valid, weekly_key, "weekly")
    monthly = _aggregate(valid, valid["Date"].dt.strftime("%Y-%m"), "monthly")

    trace = make_trace(
        formula="por período: revenue=ΣRevenue(signo), units=ΣQuantity, "
        "transactions=COUNT DISTINCT Transaction, customers=COUNT DISTINCT "
        "Customer, avg_ticket=revenue/transactions, "
        "products_sold=COUNT DISTINCT Product",
        filters="Date válida; Revenue/Quantity nulos se ignoran en la suma",
        rows_considered=int(len(valid)),
        period=period,
        notes=f"{len(valid)} de {n} filas tienen fecha válida y entran en "
        "las series. Listas ordenadas cronológicamente, listas para "
        "cálculo de tendencias en fases futuras.",
    )
    return to_jsonable(
        {
            "daily": daily,
            "weekly": weekly,
            "monthly": monthly,
            "n_periods": {
                "daily": len(daily),
                "weekly": len(weekly),
                "monthly": len(monthly),
            },
            "trace": trace,
        }
    )
