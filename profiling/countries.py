"""
ZAYVERO BUSINESS — FASE 1B.

`countries`: análisis por país.

Por país (sobre COMPLETED + CANCELLED, con Revenue con signo):
    - revenue:      Σ Revenue
    - customers:    COUNT DISTINCT Customer
    - units:        Σ Quantity
    - transactions: COUNT DISTINCT Transaction

Mercados:
    - top_markets:   países que acumulan el 80% del revenue (ordenados por
                     revenue desc, corte de Pareto)
    - small_markets: el resto

Crecimiento/decrecimiento (solo si hay ≥12 meses de historial):
    compara revenue de los últimos 6 meses completos vs los 6 anteriores,
    por país. Variación dentro de ±5% = "estable".
    NO se interpreta causalidad: son solo variaciones observadas.
"""

from __future__ import annotations

import pandas as pd

from profiling.trace import make_trace, to_jsonable


def build_countries(df: pd.DataFrame) -> dict:
    n = len(df)
    period = ""
    if n and df["Date"].notna().any():
        dmin, dmax = df["Date"].min(), df["Date"].max()
        period = f"{pd.Timestamp(dmin).date()} → {pd.Timestamp(dmax).date()}"

    valid = df[
        df["transaction_status"].isin(["completed", "cancelled"])
        & df["Country"].notna()
    ].copy()

    ranking: list[dict] = []
    if len(valid):
        g = valid.groupby("Country", observed=True)
        for country, grp in g:
            rev = grp["Revenue"].sum(skipna=True)
            ranking.append(
                {
                    "country": str(country),
                    "revenue": round(float(rev), 2) if pd.notna(rev) else 0.0,
                    "customers": int(grp["Customer"].nunique()),
                    "units": float(grp["Quantity"].sum(skipna=True)),
                    "transactions": int(grp["Transaction"].nunique()),
                }
            )
    ranking.sort(key=lambda r: r["revenue"], reverse=True)

    # Corte de Pareto: países que acumulan el 80% del revenue
    total_rev = sum(r["revenue"] for r in ranking)
    top_markets: list[str] = []
    small_markets: list[str] = []
    if total_rev > 0:
        acc = 0.0
        for r in ranking:
            if acc < 0.8 * total_rev:
                top_markets.append(r["country"])
                acc += r["revenue"]
            else:
                small_markets.append(r["country"])
    else:
        small_markets = [r["country"] for r in ranking]

    # Crecimiento: últimos 6 meses completos vs 6 anteriores (si ≥12 meses)
    growth: list[dict] = []
    growth_note = "Historial insuficiente (<12 meses): no se calcula variación."
    if len(valid) and valid["Date"].notna().any():
        months = valid["Date"].dt.to_period("M")
        n_months = months.nunique()
        if n_months >= 12:
            vmax = valid["Date"].max().normalize().replace(day=1)
            # Últimos 6 meses completos: [vmax-6M, vmax) ; anteriores: [vmax-12M, vmax-6M)
            recent_start = vmax - pd.DateOffset(months=6)
            prev_start = vmax - pd.DateOffset(months=12)
            recent = valid[(valid["Date"] >= recent_start) & (valid["Date"] < vmax)]
            prev = valid[(valid["Date"] >= prev_start) & (valid["Date"] < recent_start)]
            rev_recent = recent.groupby("Country", observed=True)["Revenue"].sum()
            rev_prev = prev.groupby("Country", observed=True)["Revenue"].sum()
            for country in set(rev_recent.index) | set(rev_prev.index):
                r0 = float(rev_prev.get(country, 0.0))
                r1 = float(rev_recent.get(country, 0.0))
                if r0 > 0:
                    pct = (r1 - r0) / r0 * 100
                elif r1 > 0:
                    pct = 100.0  # mercado nuevo en el período reciente
                else:
                    pct = 0.0
                if pct > 5:
                    trend = "crecimiento"
                elif pct < -5:
                    trend = "decrecimiento"
                else:
                    trend = "estable"
                growth.append(
                    {
                        "country": str(country),
                        "revenue_prev_6m": round(r0, 2),
                        "revenue_last_6m": round(r1, 2),
                        "pct_change": round(pct, 2),
                        "trend": trend,
                    }
                )
            growth.sort(key=lambda x: x["pct_change"], reverse=True)
            growth_note = (
                "Variación = revenue últimos 6 meses completos vs 6 anteriores. "
                "±5% = estable. Solo variación observada, sin interpretación causal."
            )

    trace = make_trace(
        formula="por Country: revenue=ΣRevenue, customers=COUNT DISTINCT Customer, "
        "units=ΣQuantity, transactions=COUNT DISTINCT Transaction; "
        "top_markets=corte Pareto 80% del revenue",
        filters="transaction_status IN (completed, cancelled); Country no nulo",
        rows_considered=int(len(valid)),
        period=period,
        notes="Crecimiento/decrecimiento solo descriptivo cuando hay ≥12 meses.",
    )
    return to_jsonable(
        {
            "total_countries": int(len(ranking)),
            "ranking": ranking,
            "top_markets": top_markets,
            "small_markets": small_markets,
            "growth_6m_vs_prev_6m": growth,
            "growth_note": growth_note,
            "trace": trace,
        }
    )
