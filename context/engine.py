"""
ZAYVERO BUSINESS — FASE 2C: Context Engine.

Carga el dataset normalizado (Parquet) una sola vez y precomputa las
series agregadas necesarias para contextualizar cada Business Finding
con operaciones vectorizadas (sin loops por fila).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd


_TRX_RE = re.compile(r"transacci[óo]n\s+([A-Za-z0-9]+)", re.IGNORECASE)


@dataclass
class ContextEngine:
    """Motor de contexto sobre el dataset normalizado."""

    parquet_path: str
    company_id: str
    df: pd.DataFrame = field(init=False, repr=False)
    completed: pd.DataFrame = field(init=False, repr=False)
    prod_daily: pd.DataFrame = field(init=False, repr=False)
    daily_total: pd.DataFrame = field(init=False, repr=False)
    dataset_start: pd.Timestamp = field(init=False)
    dataset_end: pd.Timestamp = field(init=False)

    def __post_init__(self) -> None:
        df = pd.read_parquet(self.parquet_path)
        if "company_id" in df.columns:
            df = df[df["company_id"] == self.company_id].copy()
        df["Date"] = pd.to_datetime(df["Date"])
        df["day"] = df["Date"].dt.floor("D")
        self.df = df
        comp = df[df["transaction_status"] == "completed"].copy()
        self.completed = comp
        self.dataset_start = df["Date"].min()
        self.dataset_end = df["Date"].max()
        # Serie diaria por producto (vectorizada, una sola vez)
        self.prod_daily = (
            comp.groupby(["Product", "day"], observed=True)
            .agg(revenue=("Revenue", "sum"),
                 n_trx=("Transaction", "nunique"),
                 n_customers=("Customer", "nunique"))
            .reset_index()
        )
        # Serie diaria total
        self.daily_total = (
            comp.groupby("day", observed=True)
            .agg(revenue=("Revenue", "sum"),
                 n_trx=("Transaction", "nunique"),
                 n_customers=("Customer", "nunique"))
            .reset_index()
        )

    # ------------------------------------------------------------------
    # Utilidades de extracción
    # ------------------------------------------------------------------
    @staticmethod
    def extract_transaction_id(statistical_explanation: str | None) -> str | None:
        """Extrae el ID de transacción mencionado en la explicación de 2B."""
        if not statistical_explanation:
            return None
        m = _TRX_RE.search(statistical_explanation)
        return m.group(1) if m else None

    def product_rows(self, product_id: str) -> pd.DataFrame:
        return self.completed[self.completed["Product"] == product_id]

    def customer_rows(self, customer_id: str) -> pd.DataFrame:
        return self.completed[self.completed["Customer"] == customer_id]

    def product_daily_series(self, product_id: str) -> pd.DataFrame:
        s = self.prod_daily[self.prod_daily["Product"] == product_id]
        return s.sort_values("day").reset_index(drop=True)

    def find_customer_event(self, customer_id: str,
                            observed: float) -> pd.DataFrame:
        """Localiza la(s) transacción(es) del cliente con revenue ≈ observado."""
        rows = self.customer_rows(customer_id)
        if rows.empty or observed is None or observed <= 0:
            return rows.iloc[0:0]
        tol = max(abs(observed) * 0.02, 1.0)
        # Agrupa por transacción (una compra puede tener varias líneas)
        trx = rows.groupby("Transaction", observed=True).agg(
            revenue=("Revenue", "sum"), day=("day", "min"))
        trx = trx[trx["revenue"].between(observed - tol, observed + tol)]
        if trx.empty:
            # Fallback: la transacción de mayor revenue del cliente
            trx = rows.groupby("Transaction", observed=True).agg(
                revenue=("Revenue", "sum"), day=("day", "min"))
            trx = trx.nlargest(1, "revenue")
        ids = trx.index.tolist()
        return rows[rows["Transaction"].isin(ids)]

    def transaction_rows(self, transaction_id: str) -> pd.DataFrame:
        return self.df[self.df["Transaction"] == transaction_id]

    # ------------------------------------------------------------------
    # Ventanas temporales
    # ------------------------------------------------------------------
    @staticmethod
    def _window_stats(daily: pd.DataFrame, start: pd.Timestamp,
                      end: pd.Timestamp) -> dict:
        w = daily[(daily["day"] >= start) & (daily["day"] <= end)]
        if w.empty:
            return {"revenue": 0.0, "n_trx": 0, "n_days_active": 0,
                    "avg_daily": 0.0, "max_daily": 0.0}
        return {"revenue": float(w["revenue"].sum()),
                "n_trx": int(w["n_trx"].sum()),
                "n_days_active": int(len(w)),
                "avg_daily": float(w["revenue"].mean()),
                "max_daily": float(w["revenue"].max())}

    def before_during_after(self, daily: pd.DataFrame,
                            start: pd.Timestamp, end: pd.Timestamp,
                            before_days: int = 90,
                            after_days: int = 90) -> dict:
        """Compara el evento contra el antes y el después (vectorizado)."""
        before_start = start - pd.Timedelta(days=before_days)
        before = self._window_stats(
            daily, before_start, start - pd.Timedelta(days=1))
        during = self._window_stats(daily, start, end)
        after_end = min(end + pd.Timedelta(days=after_days), self.dataset_end)
        after = self._window_stats(
            daily, end + pd.Timedelta(days=1), after_end)
        return {"before": before, "during": during, "after": after,
                "before_days": before_days, "after_days": after_days,
                "has_after": after["n_days_active"] > 0}
