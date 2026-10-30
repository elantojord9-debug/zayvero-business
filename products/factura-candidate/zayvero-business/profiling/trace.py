"""
ZAYVERO BUSINESS — FASE 1B.

`trazabilidad`: helper para que cada métrica del perfil pueda rastrearse
hasta los datos originales.

Toda sección del BusinessDatasetProfile incluye un bloque `trace` con:
    - formula:  cómo se calculó la métrica
    - filters:  qué filtros se aplicaron (transaction_status, nulos, etc.)
    - rows_considered: cuántos registros entraron al cálculo
    - period:   rango de fechas analizado
    - notes:    aclaraciones (qué se excluyó y por qué)
"""

from __future__ import annotations

from typing import Any


def make_trace(
    formula: str,
    filters: str,
    rows_considered: int,
    period: str,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "formula": formula,
        "filters": filters,
        "rows_considered": int(rows_considered),
        "period": period,
        "notes": notes,
    }


def to_jsonable(obj: Any) -> Any:
    """Convierte valores pandas/numpy/datetime a tipos serializables JSON."""
    import datetime as _dt

    import pandas as pd

    if obj is None or isinstance(obj, (bool, int, float, str)):
        if isinstance(obj, float) and (pd.isna(obj)):
            return None
        return obj
    if isinstance(obj, (_dt.datetime, _dt.date, pd.Timestamp)):
        if pd.isna(obj):
            return None
        return pd.Timestamp(obj).isoformat()
    if isinstance(obj, _dt.timedelta):
        return obj.total_seconds()
    try:
        import numpy as np

        if isinstance(obj, (np.integer,)):
            return int(obj)
        if isinstance(obj, (np.floating,)):
            f = float(obj)
            return None if pd.isna(f) else f
        if isinstance(obj, (np.bool_,)):
            return bool(obj)
        if isinstance(obj, (np.ndarray,)):
            return [to_jsonable(v) for v in obj.tolist()]
    except ImportError:
        pass
    if isinstance(obj, dict):
        return {str(k): to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [to_jsonable(v) for v in obj]
    if pd.isna(obj):
        return None
    return str(obj)
