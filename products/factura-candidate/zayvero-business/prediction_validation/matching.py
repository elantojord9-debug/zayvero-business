"""
ZAYVERO BUSINESS — FASE 4C: matching determinista predicción ↔ dato real.

Reproduce exactamente la agregación de FASE 4A para obtener el valor real
correspondiente a cada predicción:

- FASE 4A agrega con ``df["Date"].dt.to_period(freq)`` y suma la columna de
  valor (Revenue / Quantity) sobre filas con ``transaction_status ==
  "completed"``. Los periodos sin ventas registradas se tratan como 0.0
  (dato observado, no inventado), igual que en 4A.
- El matching exige coincidencia exacta de prediction_type, entity y cada
  periodo del horizonte pronosticado. Nunca se mezclan periodos.

Un periodo de pronóstico es *observable* solo si su fin (``period.end_time``)
es anterior o igual a la fecha máxima del dataset. Si algún periodo del
horizonte aún no ocurrió en los datos → PENDING (el dato real no existe
todavía). Si un periodo observable no tiene filas → 0.0 (cero observado).

Anti data-leakage: el cálculo del valor real filtra el DataFrame a las filas
cuyo periodo pertenece al horizonte pronosticado; ninguna otra fila del
dataset influye en el resultado.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

VALUE_COLUMN = {
    "DEMAND_REVENUE": "Revenue",
    "DEMAND_QUANTITY": "Quantity",
}

PERIOD_SEPARATOR = " a "


def parse_period_label(label: str) -> Optional[pd.Period]:
    """Convierte una etiqueta de periodo de 4A en pd.Period.

    - "2012-01"            → Period('2012-01', 'M')
    - "2011-12-12/2011-12-18" → Period semanal 'W-SUN'
    """
    label = (label or "").strip()
    if not label:
        return None
    try:
        if "/" in label:
            return pd.Period(label, freq="W-SUN")
        return pd.Period(label, freq="M")
    except Exception:
        return None


def forecast_periods(period_str: Optional[str]) -> Optional[List[pd.Period]]:
    """Lista de periodos del horizonte pronosticado, o None si no parsea.

    Formatos de 4A: "2012-01 a 2012-03" (mensual) o
    "2011-12-12/2011-12-18 a 2012-01-02/2012-01-08" (semanal).
    """
    if not period_str or not isinstance(period_str, str):
        return None
    parts = period_str.split(PERIOD_SEPARATOR)
    if len(parts) != 2:
        return None
    start = parse_period_label(parts[0])
    end = parse_period_label(parts[1])
    if start is None or end is None:
        return None
    if start.freq != end.freq:
        return None
    if start > end:
        return None
    return list(pd.period_range(start, end, freq=start.freq))


def entity_filter(df: pd.DataFrame, entity: Dict[str, Any]) -> Optional[pd.Series]:
    """Máscara booleana para la entidad de la predicción.

    GLOBAL  → sin filtro (todas las filas).
    PRODUCT → df["Product"] == id.
    COUNTRY → df["Country"] == id.
    Devuelve None si el tipo de entidad es desconocido.
    """
    kind = (entity or {}).get("kind")
    entity_id = (entity or {}).get("id")
    if kind == "GLOBAL":
        return pd.Series(True, index=df.index)
    if kind == "PRODUCT":
        return df["Product"].astype(str) == str(entity_id)
    if kind == "COUNTRY":
        return df["Country"].astype(str) == str(entity_id)
    return None


def compute_actual(
    df: pd.DataFrame, prediction: Dict[str, Any]
) -> Dict[str, Any]:
    """Calcula el valor real correspondiente a una predicción de 4A.

    Devuelve un dict con:
      - status: "READY" (valor real calculable), "PENDING" (algún periodo del
        horizonte aún no ocurrió en los datos), o "INVALID" (tipo de
        predicción, entidad o periodo no reconocibles).
      - actual_value: suma sobre el horizonte (0.0 si no hubo actividad).
      - observable_periods / future_periods: listas de etiquetas.
      - matching_rule: descripción de cómo se encontró el resultado real.
      - freq: "M" o "W".
    """
    ptype = prediction.get("prediction_type")
    value_col = VALUE_COLUMN.get(ptype)
    if value_col is None:
        return _invalid("prediction_type desconocido: %r" % (ptype,))

    periods = forecast_periods(prediction.get("period"))
    if not periods:
        return _invalid("period no parseable: %r" % (prediction.get("period"),))

    mask = entity_filter(df, prediction.get("entity") or {})
    if mask is None:
        return _invalid(
            "entity kind desconocido: %r" % ((prediction.get("entity") or {}).get("kind"),)
        )

    freq = "M" if periods[0].freqstr == "M" else "W"
    sub = df[mask & (df["transaction_status"] == "completed")].copy()
    max_date = df["Date"].max()

    # Un periodo solo es observable si terminó dentro del rango del dataset.
    future = [p for p in periods if p.end_time > max_date]
    if future:
        return {
            "status": "PENDING",
            "actual_value": None,
            "observable_periods": [str(p) for p in periods if p not in future],
            "future_periods": [str(p) for p in future],
            "matching_rule": (
                "El dataset termina en %s; los periodos %s aún no ocurrieron "
                "en los datos. Sin dato real todavía: PENDING."
                % (max_date.date(), ", ".join(str(p) for p in future))
            ),
            "freq": freq,
            "n_forecast_periods": len(periods),
        }

    # Solo filas cuyo periodo pertenece al horizonte (anti data-leakage).
    sub["_fperiod"] = sub["Date"].dt.to_period("M" if freq == "M" else "W")
    in_horizon = sub["_fperiod"].isin(periods)
    actual = float(sub.loc[in_horizon, value_col].sum())

    return {
        "status": "READY",
        "actual_value": actual,
        "observable_periods": [str(p) for p in periods],
        "future_periods": [],
        "matching_rule": (
            "MATCH VÁLIDO: prediction_type=%s, entity=%s/%s, periodos %s. "
            "Suma de %s por periodo (filas completed), periodos sin "
            "actividad = 0.0. Solo filas del horizonte contribuyen al valor."
            % (
                ptype,
                (prediction.get("entity") or {}).get("kind"),
                (prediction.get("entity") or {}).get("id"),
                ", ".join(str(p) for p in periods),
                value_col,
            )
        ),
        "freq": freq,
        "n_forecast_periods": len(periods),
    }


def _invalid(reason: str) -> Dict[str, Any]:
    return {
        "status": "INVALID",
        "actual_value": None,
        "observable_periods": [],
        "future_periods": [],
        "matching_rule": "NO MATCH: %s" % reason,
        "freq": None,
        "n_forecast_periods": 0,
    }
