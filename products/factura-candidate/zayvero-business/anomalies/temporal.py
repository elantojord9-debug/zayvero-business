"""
ZAYVERO BUSINESS — FASE 2A.

`temporal`: anomalías temporales (picos, caídas, cambios bruscos).

METODOLOGÍA:
    - Serie de revenue por día / semana ISO / mes, SOLO transacciones
      `completed` (las canceladas son reversiones, no ventas; documentado).
    - Baseline: mediana móvil + MAD móvil (ventana 30d / 12sem / 6m).
      La mediana no se contamina con el propio evento (robusta).
    - z robusto = 0.6745 * (x - mediana_móvil) / MAD_móvil.
    - Emisión: |z| >= 2.5 (LOW+). Clasificación spike (z>0) / drop (z<0).
    - Cambio brusco adicional: ratio día vs día anterior >= 3 (o <= 1/3)
      con revenue > 2 * mediana global diaria (evita ruido en días flojos).
    - Requiere min_periods de historia; sin historia suficiente no se emite
      (mejor silencio que falso positivo).

Cada anomalía incluye trace completo con baseline utilizado.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from anomalies import stats as _st
from anomalies.scoring import (
    assign_severity,
    build_explanation,
    confidence_score,
    priority_score,
    ratio_to_dev,
)

GRANULARITY_LABEL = {"day": "día", "week": "semana", "month": "mes"}
WINDOW = {"day": 30, "week": 12, "month": 6}
MIN_HISTORY = {"day": 15, "week": 8, "month": 4}


def _series(df: pd.DataFrame, granularity: str) -> pd.DataFrame:
    """Serie de revenue completado por período."""
    valid = df[
        (df["transaction_status"] == "completed")
        & df["Date"].notna()
        & df["Revenue"].notna()
    ].copy()
    if valid.empty:
        return pd.DataFrame(columns=["period_start", "revenue"])
    if granularity == "day":
        key = valid["Date"].dt.floor("D")
        freq = "D"
    elif granularity == "week":
        # Semana ISO: lunes como inicio
        key = valid["Date"].dt.to_period("W").dt.start_time
        freq = "W-MON"
    else:
        key = valid["Date"].dt.to_period("M").dt.start_time
        freq = "MS"
    s = valid.groupby(key)["Revenue"].sum()
    # Reindexar al rango completo para no ocultar huecos (hueco = 0 ventas)
    full_idx = pd.date_range(s.index.min(), s.index.max(), freq=freq)
    s = s.reindex(full_idx, fill_value=0.0)
    return pd.DataFrame({"period_start": s.index, "revenue": s.to_numpy()})


def _fmt_money(v: float) -> str:
    return f"£{v:,.2f}"


def detect_temporal(
    df: pd.DataFrame,
    company_id: str,
    dataset_label: str,
    period_label: str,
) -> list[dict[str, Any]]:
    """Detecta anomalías temporales en las 3 granularidades."""
    anomalies: list[dict[str, Any]] = []
    n_rows = len(df)
    null_frac = float(
        df["Revenue"].isna().mean() if n_rows else 0.0
    )

    for granularity in ("day", "week", "month"):
        ser = _series(df, granularity)
        if len(ser) < MIN_HISTORY[granularity] + 2:
            continue
        window = WINDOW[granularity]
        rb = _st.rolling_robust(
            ser.set_index("period_start")["revenue"], window=window
        )
        ser = ser.set_index("period_start").join(
            rb[["roll_median", "roll_mad", "z_robust"]]
        ).reset_index()
        ser["n_history"] = (
            ser["revenue"].rolling(window=window, min_periods=1).count()
        )
        global_median = float(ser["revenue"].median())

        for _, row in ser.iterrows():
            z = row["z_robust"]
            if not np.isfinite(z) or abs(z) < 2.5:
                continue
            if row["n_history"] < MIN_HISTORY[granularity]:
                continue
            observed = float(row["revenue"])
            expected = float(row["roll_median"])
            if expected <= 0 and observed <= 0:
                continue
            ratio = (observed / expected) if expected > 0 else float("inf")
            is_spike = z > 0
            atype = "temporal_spike" if is_spike else "temporal_drop"
            # z puede ser inf cuando MAD=0 (ventana constante): en ese caso
            # la desviación se mide por ratio para no saturar la severidad.
            dev = abs(float(z))
            if not np.isfinite(dev):
                dev = ratio_to_dev(ratio) if np.isfinite(ratio) else 10.0
                dev = min(dev, 15.0)
            # Sostenido: >=2 puntos anómalos en la ventana cercana
            idx = ser.index[ser["period_start"] == row["period_start"]][0]
            lo = max(0, idx - window // 2)
            hi = min(len(ser), idx + window // 2 + 1)
            near = ser.iloc[lo:hi]
            sustained = bool((near["z_robust"].abs() >= 2.5).sum() >= 3)

            conf, conf_factors = confidence_score(
                n_history=int(row["n_history"]),
                dev=dev,
                sustained=sustained,
                null_fraction=null_frac,
            )
            severity = assign_severity(dev, conf)
            pdate = pd.Timestamp(row["period_start"])
            if granularity == "day":
                plabel = pdate.strftime("%Y-%m-%d")
            elif granularity == "week":
                iso = pdate.isocalendar()
                plabel = f"{iso.year}-W{iso.week:02d}"
            else:
                plabel = pdate.strftime("%Y-%m")
            explanation = build_explanation(
                atype,
                _fmt_money,
                granularity_label=GRANULARITY_LABEL[granularity],
                period_label=plabel,
                observed_fmt=observed,
                expected_fmt=expected,
                ratio=ratio if np.isfinite(ratio) else 999.0,
                z=float(z),
            )
            anomalies.append(
                {
                    "type": atype,
                    "severity": severity,
                    "entity": {
                        "kind": "period",
                        "id": plabel,
                        "label": f"{GRANULARITY_LABEL[granularity]} {plabel}",
                    },
                    "period": {
                        "start": pdate.isoformat(),
                        "end": pdate.isoformat(),
                        "granularity": granularity,
                    },
                    "observed": round(observed, 2),
                    "expected": round(expected, 2),
                    "difference": round(observed - expected, 2),
                    "ratio": round(float(ratio), 3)
                    if np.isfinite(ratio)
                    else None,
                    "z_robust": round(float(z), 2),
                    "deviation": round(dev, 2),
                    "evidence": {
                        "window_days": window,
                        "baseline_median": round(expected, 2),
                        "baseline_mad": round(float(row["roll_mad"]), 2)
                        if pd.notna(row["roll_mad"])
                        else None,
                        "history_points": int(row["n_history"]),
                        "sustained": sustained,
                        "global_median_revenue": round(global_median, 2),
                    },
                    "explanation": explanation,
                    "confidence_score": conf,
                    "confidence_factors": conf_factors,
                    "trace": {
                        "dataset": dataset_label,
                        "company_id": company_id,
                        "method": (
                            "rolling median + MAD (ventana "
                            f"{window}), z robusto = 0.6745*(x-mediana)/MAD"
                        ),
                        "filters": "transaction_status == 'completed', "
                        "Revenue no nulo, Date no nula",
                        "rows_considered": int(n_rows),
                        "baseline": (
                            f"mediana móvil {window} períodos = {expected:,.2f}"
                        ),
                        "formula": "z = 0.6745*(observado - mediana_móvil)/MAD_móvil; "
                        "emisión si |z| >= 2.5",
                        "period_analyzed": period_label,
                        "notes": "Las cancelaciones se excluyen: son reversiones, "
                        "no ventas. Huecos sin ventas cuentan como 0.",
                    },
                    "_priority": priority_score(
                        severity, conf, observed - expected
                    ),
                }
            )

        # Cambio brusco día a día (solo granularidad diaria)
        if granularity == "day" and len(ser) >= 3:
            prev = ser["revenue"].shift(1)
            ratio_dd = ser["revenue"] / prev.replace(0, np.nan)
            abrupt = ser[
                (ser["revenue"] > 2 * global_median)
                & ((ratio_dd >= 3) | (ratio_dd <= 1 / 3))
                & ser["z_robust"].abs().ge(2.5)
            ]
            # Ya cubierto por spike/drop; se marca en evidencia si coincide
            for _, row in abrupt.iterrows():
                for a in anomalies:
                    if (
                        a["entity"]["id"]
                        == pd.Timestamp(row["period_start"]).strftime("%Y-%m-%d")
                        and a["period"]["granularity"] == "day"
                    ):
                        a["evidence"]["abrupt_day_over_day"] = True
                        a["evidence"]["day_over_day_ratio"] = round(
                            float(ratio_dd.loc[row.name]), 2
                        )
    return anomalies
