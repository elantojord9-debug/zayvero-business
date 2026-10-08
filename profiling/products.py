"""
ZAYVERO BUSINESS — FASE 1B.

`products`: perfil por producto (StockCode).

Por producto se calcula (sobre transacciones COMPLETADAS):
    - units:         Σ Quantity
    - transactions:  COUNT de filas
    - revenue:       Σ Revenue
    - avg_price:     media de UnitPrice
    - first_sale / last_sale: min/max de Date
    - frequency_per_30d: transactions / max(1, días entre first y last) * 30

CLASIFICACIÓN POR ACTIVIDAD (regla documentada, basada en deciles de
revenue entre productos con transacciones completadas):
    - alta_actividad:  decil superior (≥ percentil 90 de revenue)
    - media_actividad: percentiles 60–90
    - baja_actividad:  por debajo del p60, con ≥1 transacción completada
    - sin_actividad_suficiente: sin transacciones completadas o sin
      revenue válido

NOTA: no se usa el término "producto muerto": la clasificación de
Dead Stock es un módulo futuro.
"""

from __future__ import annotations

import pandas as pd

from profiling.trace import make_trace, to_jsonable

TOP_N = 50


def build_products(df: pd.DataFrame) -> dict:
    n = len(df)
    period = ""
    if n and df["Date"].notna().any():
        dmin, dmax = df["Date"].min(), df["Date"].max()
        period = f"{pd.Timestamp(dmin).date()} → {pd.Timestamp(dmax).date()}"

    completed = df[df["transaction_status"] == "completed"].copy()
    # Productos que aparecen en cualquier fila (para la clase "sin actividad")
    all_products = df["Product"].dropna().unique()

    profiles: list[dict] = []
    if len(completed):
        g = completed.groupby("Product", observed=True)
        for prod, grp in g:
            rev = grp["Revenue"].sum(skipna=True)
            trx = len(grp)
            dmin_p = grp["Date"].min()
            dmax_p = grp["Date"].max()
            span_days = (
                max(1, (pd.Timestamp(dmax_p) - pd.Timestamp(dmin_p)).days)
                if pd.notna(dmin_p) and pd.notna(dmax_p)
                else 1
            )
            desc = (
                grp["orig_Description"].dropna().mode().iloc[0]
                if "orig_Description" in grp.columns
                and grp["orig_Description"].notna().any()
                else None
            )
            profiles.append(
                {
                    "product": str(prod),
                    "description": None if desc is None else str(desc),
                    "units": float(grp["Quantity"].sum(skipna=True)),
                    "transactions": int(trx),
                    "revenue": round(float(rev), 2) if pd.notna(rev) else 0.0,
                    "avg_price": round(float(grp["UnitPrice"].mean(skipna=True)), 4),
                    "first_sale": dmin_p,
                    "last_sale": dmax_p,
                    "frequency_per_30d": round(trx / span_days * 30, 3),
                    "_revenue_sort": float(rev) if pd.notna(rev) else 0.0,
                }
            )

    # Clasificación por deciles de revenue
    revenues = sorted([p["_revenue_sort"] for p in profiles])
    if revenues:
        import numpy as np

        p90 = float(np.percentile(revenues, 90))
        p60 = float(np.percentile(revenues, 60))
    else:
        p90 = p60 = 0.0

    with_activity = {p["product"] for p in profiles}
    classes = {"alta_actividad": 0, "media_actividad": 0,
               "baja_actividad": 0, "sin_actividad_suficiente": 0}
    for p in profiles:
        r = p["_revenue_sort"]
        if r >= p90 and r > 0:
            p["activity_class"] = "alta_actividad"
        elif r >= p60 and r > 0:
            p["activity_class"] = "media_actividad"
        elif p["transactions"] >= 1 and r > 0:
            p["activity_class"] = "baja_actividad"
        else:
            p["activity_class"] = "sin_actividad_suficiente"
        classes[p["activity_class"]] += 1
        del p["_revenue_sort"]

    # Productos sin ninguna transacción completada
    for prod in all_products:
        if str(prod) not in with_activity:
            classes["sin_actividad_suficiente"] += 1

    profiles.sort(key=lambda p: p["revenue"], reverse=True)
    top = profiles[:TOP_N]

    trace = make_trace(
        formula="por Product: units=ΣQuantity|completed, transactions=COUNT|completed, "
        "revenue=ΣRevenue|completed, avg_price=MEAN(UnitPrice), "
        "frequency=transactions/días_activos*30; clases por deciles de revenue "
        f"(p90={round(p90,2)}, p60={round(p60,2)})",
        filters="transaction_status='completed'; Quantity/Revenue nulos se ignoran",
        rows_considered=int(len(completed)),
        period=period,
        notes=f"{len(profiles)} productos con transacciones completadas; "
        f"{len(all_products)} productos distintos en total. Top {TOP_N} incluidos "
        "en el perfil; la tabla completa puede regenerarse del Parquet.",
    )
    return to_jsonable(
        {
            "total_products": int(len(all_products)),
            "with_completed_transactions": int(len(profiles)),
            "activity_classes": classes,
            "classification_rule": (
                "alta_actividad: decil superior de revenue (≥p90); "
                "media_actividad: p60–p90; baja_actividad: resto con ≥1 "
                "transacción completada y revenue>0; sin_actividad_suficiente: "
                "sin transacciones completadas o sin revenue válido. "
                "NO equivale a 'producto muerto' (módulo futuro)."
            ),
            "top_products": top,
            "trace": trace,
        }
    )
