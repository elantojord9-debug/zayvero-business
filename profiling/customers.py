"""
ZAYVERO BUSINESS — FASE 1B.

`customers`: perfil por cliente (Customer ID).

Por cliente se calcula (sobre transacciones COMPLETADAS):
    - purchases:     COUNT de filas
    - units:         Σ Quantity
    - revenue:       Σ Revenue
    - first_purchase / last_purchase: min/max de Date
    - frequency_per_30d: purchases / max(1, días entre first y last) * 30
    - countries:     lista de Country distintos

Agregados:
    - active:           última compra dentro de los 90 días previos a la
                        fecha máxima del dataset
    - single_purchase: exactamente 1 compra completada
    - recurrent:       ≥2 compras completadas

Filas sin Customer ID se cuentan aparte (unknown_customer_rows) y NO
entran en los perfiles individuales.

NOTA: no se construye modelo de churn; 'active' es solo un corte
descriptivo por recencia, no una predicción.
"""

from __future__ import annotations

import pandas as pd

from profiling.trace import make_trace, to_jsonable

TOP_N = 50
ACTIVE_WINDOW_DAYS = 90


def build_customers(df: pd.DataFrame) -> dict:
    n = len(df)
    period = ""
    max_date = None
    if n and df["Date"].notna().any():
        dmin, dmax = df["Date"].min(), df["Date"].max()
        max_date = pd.Timestamp(dmax)
        period = f"{pd.Timestamp(dmin).date()} → {max_date.date()}"

    completed = df[df["transaction_status"] == "completed"].copy()
    unknown_customer_rows = int(completed["Customer"].isna().sum())
    known = completed[completed["Customer"].notna()].copy()

    profiles: list[dict] = []
    if len(known):
        g = known.groupby("Customer", observed=True)
        for cust, grp in g:
            dmin_c = grp["Date"].min()
            dmax_c = grp["Date"].max()
            span_days = (
                max(1, (pd.Timestamp(dmax_c) - pd.Timestamp(dmin_c)).days)
                if pd.notna(dmin_c) and pd.notna(dmax_c)
                else 1
            )
            purchases = len(grp)
            profiles.append(
                {
                    "customer": str(cust),
                    "purchases": int(purchases),
                    "units": float(grp["Quantity"].sum(skipna=True)),
                    "revenue": round(float(grp["Revenue"].sum(skipna=True)), 2),
                    "first_purchase": dmin_c,
                    "last_purchase": dmax_c,
                    "frequency_per_30d": round(purchases / span_days * 30, 3),
                    "countries": sorted(
                        str(c) for c in grp["Country"].dropna().unique()
                    ),
                }
            )

    active_cutoff = (
        max_date - pd.Timedelta(days=ACTIVE_WINDOW_DAYS) if max_date else None
    )
    n_active = n_single = n_recurrent = 0
    for p in profiles:
        last = p["last_purchase"]
        last_ts = pd.Timestamp(last) if pd.notna(last) else None
        p["is_active"] = bool(
            active_cutoff is not None and last_ts is not None and last_ts >= active_cutoff
        )
        if p["is_active"]:
            n_active += 1
        if p["purchases"] == 1:
            n_single += 1
        elif p["purchases"] >= 2:
            n_recurrent += 1

    profiles.sort(key=lambda p: p["revenue"], reverse=True)
    top = profiles[:TOP_N]

    trace = make_trace(
        formula="por Customer: purchases=COUNT|completed, units=ΣQuantity, "
        "revenue=ΣRevenue, frequency=purchases/días*30; "
        f"active=last_purchase ≥ max_date−{ACTIVE_WINDOW_DAYS}d; "
        "single=purchases==1; recurrent=purchases≥2",
        filters="transaction_status='completed'; Customer no nulo",
        rows_considered=int(len(known)),
        period=period,
        notes=f"{unknown_customer_rows} filas completadas sin Customer ID "
        "excluidas de perfiles individuales (contadas aparte). 'active' es "
        "un corte descriptivo por recencia, NO un modelo de churn.",
    )
    return to_jsonable(
        {
            "total_customers": int(len(profiles)),
            "unknown_customer_rows": unknown_customer_rows,
            "active_customers": int(n_active),
            "single_purchase_customers": int(n_single),
            "recurrent_customers": int(n_recurrent),
            "active_window_days": ACTIVE_WINDOW_DAYS,
            "top_customers": top,
            "trace": trace,
        }
    )
