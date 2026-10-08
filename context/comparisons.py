"""
ZAYVERO BUSINESS — FASE 2C: comparaciones contextuales.

Calcula, con operaciones vectorizadas:
- período anterior / durante / posterior
- recurrencia del evento en el historial
- concentración (qué parte del evento explican pocas transacciones)
- tendencia (dirección del comportamiento tras el evento)
- entidades relacionadas (clientes/productos/países del evento)

Nunca inventa valores faltantes. Si no hay información suficiente,
devuelve evidence_quality = LOW con la explicación del porqué.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from context.engine import ContextEngine


def _fmt_money(v: float) -> str:
    return f"£{v:,.2f}"


def recurrence_count(daily: pd.DataFrame, threshold: float,
                     exclude_start: pd.Timestamp | None = None,
                     exclude_end: pd.Timestamp | None = None) -> dict:
    """Cuenta períodos diarios con revenue >= umbral (vectorizado)."""
    if daily.empty or threshold <= 0:
        return {"n_similar": 0, "recurrence": "unknown",
                "note": "Sin historial suficiente para evaluar recurrencia."}
    hit = daily["revenue"] >= threshold
    if exclude_start is not None and exclude_end is not None:
        in_event = (daily["day"] >= exclude_start) & (daily["day"] <= exclude_end)
        other_hits = int((hit & ~in_event).sum())
        event_hits = int((hit & in_event).sum())
    else:
        other_hits = int(hit.sum())
        event_hits = 0
    if other_hits == 0:
        rec = "isolated"
        note = ("No se detectaron otros períodos con magnitud similar en "
                "el historial disponible: evento aislado.")
    elif other_hits <= 3:
        rec = "rarely_recurrent"
        note = (f"Se detectaron {other_hits} período(s) adicional(es) con "
                "magnitud similar: evento poco frecuente.")
    else:
        rec = "recurrent"
        note = (f"Se detectaron {other_hits} períodos adicionales con magnitud "
                "similar: el patrón se repite en el historial.")
    return {"n_similar": other_hits, "event_days": event_hits,
            "recurrence": rec, "note": note}


def concentration(rows: pd.DataFrame, value_col: str = "Revenue",
                  top_n: int = 3) -> dict:
    """Qué fracción del total explican las top_n transacciones."""
    if rows.empty:
        return {"top3_share": 0.0, "top3_n": 0, "total": 0.0,
                "concentrated": False, "note": "Sin transacciones."}
    trx = rows.groupby("Transaction", observed=True)[value_col].sum()
    total = float(trx.sum())
    if total <= 0:
        return {"top3_share": 0.0, "top3_n": 0, "total": total,
                "concentrated": False, "note": "Total no positivo."}
    top = float(trx.nlargest(top_n).sum())
    share = top / total
    return {"top3_share": round(share, 4), "top3_n": min(top_n, len(trx)),
            "total": total, "n_trx": int(len(trx)),
            "concentrated": bool(share >= 0.7),
            "note": (f"El {share:.0%} del total se concentra en "
                     f"{min(top_n, len(trx))} transacción(es).")}


def trend_after(before_avg: float, during_avg: float,
                after_avg: float) -> dict:
    """Dirección del comportamiento tras el evento (descriptivo)."""
    if after_avg <= 0 and before_avg <= 0:
        return {"trend": "unknown",
                "note": "Sin datos suficientes antes/después para evaluar tendencia."}
    if after_avg <= 0:
        return {"trend": "unknown",
                "note": "No hay datos posteriores al evento en el dataset."}
    ratio = after_avg / max(before_avg, 1e-9)
    if ratio >= 1.5:
        t, n = "sustained_high", (
            "Tras el evento, el nivel se mantuvo elevado respecto al "
            "comportamiento previo: posible cambio sostenido que conviene monitorear.")
    elif ratio <= 0.67:
        t, n = "dropped", (
            "Tras el evento, el nivel cayó por debajo del comportamiento previo.")
    elif during_avg > 0 and abs(after_avg - before_avg) / max(before_avg, 1e-9) <= 0.25:
        t, n = "returned_to_normal", (
            "Tras el evento, el comportamiento volvió a niveles similares a los "
            "previos: sugiere un episodio puntual.")
    else:
        t, n = "stable", "Sin cambio direccional claro tras el evento."
    return {"trend": t, "after_vs_before_ratio": round(float(ratio), 3),
            "note": n}


def related_entities(rows: pd.DataFrame, top_n: int = 5) -> dict:
    """Principales clientes, productos y países del evento (vectorizado)."""
    out: dict = {}
    if rows.empty:
        return out
    if "Customer" in rows.columns:
        cust = (rows.groupby("Customer", observed=True)["Revenue"]
                .sum().nlargest(top_n))
        out["top_customers"] = [
            {"id": str(i), "revenue": round(float(v), 2)}
            for i, v in cust.items() if str(i) not in ("", "nan")]
    if "Product" in rows.columns:
        prod = (rows.groupby("Product", observed=True)["Revenue"]
                .sum().nlargest(top_n))
        out["top_products"] = [
            {"id": str(i), "revenue": round(float(v), 2)} for i, v in prod.items()]
    if "Country" in rows.columns:
        ctry = (rows.groupby("Country", observed=True)["Revenue"]
                .sum().nlargest(top_n))
        out["top_countries"] = [
            {"id": str(i), "revenue": round(float(v), 2)} for i, v in ctry.items()]
    return out


def history_depth(n_points: int, min_points: int = 30) -> dict:
    """Evalúa si hay suficiente historial para contextualizar."""
    if n_points >= min_points:
        return {"sufficient": True, "evidence_quality": "HIGH",
                "note": f"Historial suficiente: {n_points} puntos de datos."}
    if n_points >= 10:
        return {"sufficient": True, "evidence_quality": "MEDIUM",
                "note": (f"Historial limitado: {n_points} puntos de datos. "
                         "Las conclusiones deben tomarse con cautela.")}
    return {"sufficient": False, "evidence_quality": "LOW",
            "note": (f"Historial insuficiente: solo {n_points} puntos de datos. "
                     "No existe evidencia suficiente para contextualizar "
                     "el evento con confianza.")}
