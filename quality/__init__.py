"""
ZAYVERO BUSINESS — FASE 1A.

Paquete `quality`: validación de calidad de datos.
SOLO REPORTA: nunca modifica el DataFrame.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pandas as pd


@dataclass
class QualityReport:
    n_rows: int
    n_cols: int
    columns: list[str]
    dtypes: dict[str, str]
    null_counts: dict[str, int]
    null_pct: dict[str, float]
    duplicate_rows: int
    invalid_dates: dict[str, int]      # columna -> conteo no parseable
    invalid_quantities: int            # Quantity no numérica o NaN
    invalid_prices: int                # UnitPrice no numérico o NaN
    negative_quantities: int
    zero_or_negative_prices: int
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_rows": self.n_rows,
            "n_cols": self.n_cols,
            "columns": self.columns,
            "dtypes": self.dtypes,
            "null_counts": self.null_counts,
            "null_pct": self.null_pct,
            "duplicate_rows": self.duplicate_rows,
            "invalid_dates": self.invalid_dates,
            "invalid_quantities": self.invalid_quantities,
            "invalid_prices": self.invalid_prices,
            "negative_quantities": self.negative_quantities,
            "zero_or_negative_prices": self.zero_or_negative_prices,
            "notes": self.notes,
        }


def _coerce_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _coerce_datetime(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce", dayfirst=False)


def validate(
    df: pd.DataFrame,
    date_columns: list[str] | None = None,
    quantity_column: str = "Quantity",
    price_column: str = "UnitPrice",
) -> QualityReport:
    """
    Valida el DataFrame y devuelve un reporte. No modifica nada.
    Columnas de fecha/ cantidad/ precio se buscan por nombre canónico;
    si no existen, se reporta en notes y esos chequeos se omiten.
    """
    n_rows = len(df)
    notes: list[str] = []
    null_counts = df.isna().sum()

    # --- fechas inválidas ---
    invalid_dates: dict[str, int] = {}
    date_cols = date_columns or ["InvoiceDate"]
    for col in date_cols:
        if col not in df.columns:
            notes.append(f"Columna de fecha '{col}' no encontrada; chequeo omitido.")
            continue
        parsed = _coerce_datetime(df[col])
        # no-parseable = valor original no nulo pero parseo nulo
        bad = int(((df[col].notna()) & (parsed.isna())).sum())
        invalid_dates[col] = bad

    # --- cantidades ---
    invalid_quantities = 0
    negative_quantities = 0
    if quantity_column in df.columns:
        q = _coerce_numeric(df[quantity_column])
        invalid_quantities = int(((df[quantity_column].notna()) & (q.isna())).sum())
        negative_quantities = int((q < 0).sum())
    else:
        notes.append(f"Columna '{quantity_column}' no encontrada; chequeo omitido.")

    # --- precios ---
    invalid_prices = 0
    zero_or_negative_prices = 0
    if price_column in df.columns:
        p = _coerce_numeric(df[price_column])
        invalid_prices = int(((df[price_column].notna()) & (p.isna())).sum())
        zero_or_negative_prices = int((p <= 0).sum())
    else:
        notes.append(f"Columna '{price_column}' no encontrada; chequeo omitido.")

    return QualityReport(
        n_rows=n_rows,
        n_cols=len(df.columns),
        columns=list(df.columns),
        dtypes={c: str(t) for c, t in df.dtypes.items()},
        null_counts={c: int(v) for c, v in null_counts.items()},
        null_pct={c: round(float(v) / n_rows * 100, 2) if n_rows else 0.0
                  for c, v in null_counts.items()},
        duplicate_rows=int(df.duplicated().sum()),
        invalid_dates=invalid_dates,
        invalid_quantities=invalid_quantities,
        invalid_prices=invalid_prices,
        negative_quantities=negative_quantities,
        zero_or_negative_prices=zero_or_negative_prices,
        notes=notes,
    )
