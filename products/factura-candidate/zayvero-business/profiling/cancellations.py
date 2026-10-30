"""
ZAYVERO BUSINESS — FASE 1B.

`cancellations`: análisis descriptivo de transacciones canceladas.

Calcula:
    - count:        filas con transaction_status='cancelled'
    - pct:          % sobre el total de filas
    - units:        Σ Quantity (con signo; suele ser negativo)
    - value:        Σ |Revenue| (valor absoluto = dinero dejado de percibir)
    - top_products: productos con mayor valor cancelado
    - top_periods:  meses con mayor concentración de cancelaciones
    - top_customers: clientes con más valor cancelado (si hay Customer ID)

REGLA DE EVIDENCIA: una cancelación es una cancelación. Este módulo NO
afirma fraude, ni pérdidas anómalas, ni problemas financieros: solo
describe lo observado. La detección de anomalías es una fase posterior.
"""

from __future__ import annotations

import pandas as pd

from profiling.trace import make_trace, to_jsonable

TOP_N = 10


def build_cancellations(df: pd.DataFrame) -> dict:
    n = len(df)
    period = ""
    if n and df["Date"].notna().any():
        dmin, dmax = df["Date"].min(), df["Date"].max()
        period = f"{pd.Timestamp(dmin).date()} → {pd.Timestamp(dmax).date()}"

    cancelled = df[df["transaction_status"] == "cancelled"].copy()
    count = len(cancelled)
    pct = round(count / n * 100, 2) if n else 0.0
    units = float(cancelled["Quantity"].sum(skipna=True)) if count else 0.0
    value = (
        float(cancelled["Revenue"].abs().sum(skipna=True)) if count else 0.0
    )

    top_products: list[dict] = []
    if count:
        gp = (
            cancelled.groupby("Product", observed=True)["Revenue"]
            .apply(lambda s: s.abs().sum(skipna=True))
            .sort_values(ascending=False)
            .head(TOP_N)
        )
        for prod, val in gp.items():
            top_products.append(
                {
                    "product": None if pd.isna(prod) else str(prod),
                    "cancelled_value": round(float(val), 2),
                    "cancelled_rows": int(
                        (cancelled["Product"] == prod).sum()
                    ),
                }
            )

    top_periods: list[dict] = []
    dated = cancelled[cancelled["Date"].notna()]
    if len(dated):
        gm = (
            dated.groupby(dated["Date"].dt.strftime("%Y-%m"), observed=True)
            .size()
            .sort_values(ascending=False)
            .head(TOP_N)
        )
        for month, c in gm.items():
            top_periods.append({"month": str(month), "cancelled_rows": int(c)})

    top_customers: list[dict] = []
    with_cust = cancelled[cancelled["Customer"].notna()]
    if len(with_cust):
        gc = (
            with_cust.groupby("Customer", observed=True)["Revenue"]
            .apply(lambda s: s.abs().sum(skipna=True))
            .sort_values(ascending=False)
            .head(TOP_N)
        )
        for cust, val in gc.items():
            top_customers.append(
                {
                    "customer": str(cust),
                    "cancelled_value": round(float(val), 2),
                    "cancelled_rows": int((with_cust["Customer"] == cust).sum()),
                }
            )

    trace = make_trace(
        formula="count=filas cancelled; pct=count/total; "
        "units=ΣQuantity|signed; value=Σ|Revenue|; tops por Σ|Revenue|",
        filters="transaction_status='cancelled'",
        rows_considered=count,
        period=period,
        notes="Descriptivo únicamente. No se afirma fraude ni anomalía: "
        "la detección de anomalías es una fase posterior.",
    )
    return to_jsonable(
        {
            "count": int(count),
            "pct_of_rows": pct,
            "units": units,
            "value": round(value, 2),
            "top_products": top_products,
            "top_periods": top_periods,
            "top_customers": top_customers,
            "trace": trace,
        }
    )
