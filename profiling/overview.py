"""
ZAYVERO BUSINESS — FASE 1B.

`overview`: perfil general del negocio.

REGLAS DE FACTURACIÓN (separación estricta, sin mezclar):
    - BRUTO:     Σ Revenue de transacciones COMPLETADAS (Revenue válido).
    - CANCELADO: Σ |Revenue| de transacciones CANCELADAS (valor positivo =
                 dinero que se dejó de percibir; se reporta en valor absoluto).
    - NETO:      Σ Revenue (con signo) de COMPLETADAS + CANCELADAS.
                 Las cancelaciones suelen registrarse con Revenue negativo,
                 por lo que se netean de forma natural. Si un dataset las
                 registrara en positivo, NETO quedaría sobrestimado: por eso
                 BRUTO y CANCELADO se reportan siempre por separado.
    - DESCONOCIDO: Σ Revenue de transaction_status='unknown' se reporta
                 aparte y NO entra en NETO.

Unidades siguen la misma lógica (completed / cancelled / net con signo).
"""

from __future__ import annotations

import pandas as pd

from profiling.trace import make_trace, to_jsonable


def build_overview(df: pd.DataFrame, company_id: str) -> dict:
    n = len(df)
    period = ""
    empty = n == 0

    if empty:
        date_min = date_max = None
        days_active = 0
    else:
        dmin = df["Date"].min()
        dmax = df["Date"].max()
        date_min = None if pd.isna(dmin) else dmin
        date_max = None if pd.isna(dmax) else dmax
        period = (
            f"{pd.Timestamp(dmin).date()} → {pd.Timestamp(dmax).date()}"
            if pd.notna(dmin) and pd.notna(dmax)
            else "sin fechas válidas"
        )
        days_active = (
            int(df["Date"].dt.normalize().nunique()) if pd.notna(dmin) else 0
        )

    completed = df[df["transaction_status"] == "completed"]
    cancelled = df[df["transaction_status"] == "cancelled"]
    unknown = df[df["transaction_status"] == "unknown"]

    rev_completed = completed["Revenue"].dropna()
    rev_cancelled = cancelled["Revenue"].dropna()
    rev_unknown = unknown["Revenue"].dropna()

    gross_revenue = float(rev_completed.sum()) if len(rev_completed) else 0.0
    cancelled_revenue = float(rev_cancelled.abs().sum()) if len(rev_cancelled) else 0.0
    unknown_revenue = float(rev_unknown.sum()) if len(rev_unknown) else 0.0
    # NETO: suma con signo de completed + cancelled (las cancelaciones
    # normalmente son negativas y se netean solas)
    net_revenue = (
        float(rev_completed.sum() + rev_cancelled.sum())
        if (len(rev_completed) or len(rev_cancelled))
        else 0.0
    )

    qty_completed = completed["Quantity"].dropna()
    qty_cancelled = cancelled["Quantity"].dropna()
    units_gross = float(qty_completed.sum()) if len(qty_completed) else 0.0
    units_net = (
        float(qty_completed.sum() + qty_cancelled.sum())
        if (len(qty_completed) or len(qty_cancelled))
        else 0.0
    )

    n_transactions = int(df["Transaction"].nunique()) if not empty else 0

    return to_jsonable(
        {
            "company_id": company_id,
            "empty": empty,
            "date_range": {
                "min_date": date_min,
                "max_date": date_max,
                "days_with_activity": days_active,
                "trace": make_trace(
                    formula="min/max de Date; días = COUNT DISTINCT DATE(Date)",
                    filters="Date válida (no NaT)",
                    rows_considered=n,
                    period=period,
                    notes="Días con actividad = días que tienen ≥1 fila con fecha válida.",
                ),
            },
            "transactions": {
                "total_rows": n,
                "unique_transactions": n_transactions,
                "completed_rows": int(len(completed)),
                "cancelled_rows": int(len(cancelled)),
                "unknown_rows": int(len(unknown)),
                "trace": make_trace(
                    formula="conteo de filas por transaction_status; "
                    "transacciones únicas = COUNT DISTINCT Transaction",
                    filters="ninguno (todas las filas)",
                    rows_considered=n,
                    period=period,
                ),
            },
            "entities": {
                "customers": int(df["Customer"].nunique()) if not empty else 0,
                "products": int(df["Product"].nunique()) if not empty else 0,
                "countries": int(df["Country"].nunique()) if not empty else 0,
                "trace": make_trace(
                    formula="COUNT DISTINCT de Customer / Product / Country",
                    filters="valores no nulos",
                    rows_considered=n,
                    period=period,
                    notes="Los nulos no se cuentan como entidad.",
                ),
            },
            "sales": {
                # Separación estricta: nunca mezclar estos tres valores.
                "gross_revenue": round(gross_revenue, 2),
                "cancelled_revenue": round(cancelled_revenue, 2),
                "net_revenue": round(net_revenue, 2),
                "unknown_status_revenue": round(unknown_revenue, 2),
                "units_sold_gross": units_gross,
                "units_sold_net": units_net,
                "trace": make_trace(
                    formula="BRUTO=Σ Revenue|completed; CANCELADO=Σ|Revenue||cancelled; "
                    "NETO=Σ Revenue|completed+cancelled (con signo)",
                    filters="Revenue no nulo; transaction_status según bloque",
                    rows_considered=int(rev_completed.count() + rev_cancelled.count()),
                    period=period,
                    notes="CANCELADO se reporta en valor absoluto (dinero dejado de "
                    "percibir). NETO usa los signos originales: si las cancelaciones "
                    "vienen en negativo se netean solas; si vinieran en positivo, "
                    "usar BRUTO − CANCELADO.",
                ),
            },
        }
    )
