"""
ZAYVERO BUSINESS — FASE 4A: Prediction Engine MVP.

`risk`: indicadores de riesgo basados en señales observables.

decline_risk (LOW / MEDIUM / HIGH / INSUFFICIENT_DATA):
    Riesgo de que el indicador DISMINUYA en el horizonte PROYECTADO.

    REDEFINICIÓN (corrección de calidad): antes se calculaba solo con
    señales históricas y se presentaba junto a la proyección, lo que
    llevaba a lecturas contradictorias (p. ej. pronóstico de −42 % con
    "riesgo bajo"). Ahora la señal principal es el cambio proyectado;
    las señales históricas solo pueden ELEVAR el nivel (nunca bajarlo)
    y quedan documentadas en "signals".

    Señales consideradas:
    - cambio proyectado pct = (pronóstico − nivel_reciente) /
      |nivel_reciente| * 100  (lo calcula quien llama, con los valores
      del pronóstico; None si no hay referencia válida):
        pct <= -40 %                → HIGH   (caída proyectada severa)
        -40 % < pct <= -20 %        → MEDIUM (caída proyectada material)
        pct > -20 %                 → LOW    (sin caída material:
                                             incluye 0 % y crecimientos)
      Umbrales ±20 %/±40 %: banda de materialidad consistente con el
      módulo de atención (attention.py: |pct| >= 20 % = cambio material).
    - señales históricas (tendencia DOWNWARD, aceleración negativa,
      descenso estable): elevan UN escalón (LOW→MEDIUM, MEDIUM→HIGH),
      nunca reducen el nivel; se listan en "signals".
    - confianza del pronóstico: si confidence < 40, el nivel se limita
      a MEDIUM como máximo ("no se afirma riesgo alto con un pronóstico
      poco fiable"); se documenta en "signals".

    Limitaciones documentadas:
    - Los umbrales ±20 %/±40 % son bandas de materialidad convencionales,
      no probabilidades calibradas. decline_risk NO es P(caída).
    - Si forecast_pct es None (llamadas sin pronóstico disponible), se
      usa el modo histórico anterior (solo señales del pasado) y se
      indica en "detail". Es un fallback de compatibilidad, no el modo
      principal.

stockout_status:
    El dataset UCI Online Retail II NO contiene inventario confiable
    (no hay columna de stock). ZAYVERO NO inventa un stock actual a
    partir de las ventas. Por tanto, stockout_status = NOT_AVAILABLE
    con explicación explícita. Si un dataset futuro aportara inventario,
    este módulo es el punto de extensión (documentado, no implementado).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

STABLE_DECLINE_CV = 0.4
SEVERE_DROP_FRACTION = 0.25
# Umbrales de materialidad del cambio proyectado (documentados arriba).
FORECAST_SEVERE_DROP_PCT = -40.0
FORECAST_MATERIAL_DROP_PCT = -20.0
LOW_CONFIDENCE_CAP = 40.0


def _historical_signals(trend_info: dict, values: np.ndarray) -> tuple[list[str], bool, bool, bool]:
    """Señales históricas de deterioro. Devuelve (signals, stable_decline,
    acceleration_negative, severe_drop)."""
    trend = trend_info.get("trend")
    signals: list[str] = []
    recent = values[-6:] if len(values) >= 6 else values
    mean_r = float(np.mean(np.abs(recent))) if len(recent) else 0.0
    cv_r = (float(np.std(recent, ddof=1)) / mean_r) if (mean_r > 0 and len(recent) > 1) else 0.0

    # Aceleración: pendiente de la mitad reciente vs. mitad anterior.
    acceleration_negative = False
    if len(values) >= 12:
        x1 = np.arange(6)
        s_recent = float(np.polyfit(x1, values[-6:], 1)[0])
        s_prev = float(np.polyfit(x1, values[-12:-6], 1)[0])
        acceleration_negative = s_recent < s_prev < 0 or (s_prev >= 0 > s_recent)
        if acceleration_negative:
            signals.append("aceleración negativa: la caída se intensifica en la ventana reciente")

    drop_fraction = 0.0
    if trend == "DOWNWARD":
        signals.append(f"tendencia histórica descendente ({trend_info.get('detail', '')})")
        if len(values) >= 12:
            older_mean = float(np.mean(np.abs(values[-12:-6])))
            if older_mean > 0:
                drop_fraction = (older_mean - mean_r) / older_mean
                if drop_fraction > 0:
                    signals.append(f"caída de {drop_fraction:.0%} vs. la ventana anterior")

    stable_decline = trend == "DOWNWARD" and cv_r < STABLE_DECLINE_CV
    if stable_decline:
        signals.append(f"descenso histórico estable (CV {cv_r:.2f} < {STABLE_DECLINE_CV})")
    severe_drop = drop_fraction > SEVERE_DROP_FRACTION

    return signals, stable_decline, acceleration_negative, severe_drop


def decline_risk(trend_info: dict, series: pd.Series, confidence: float,
                 forecast_pct: float | None = None) -> dict:
    """Calcula el riesgo de caída en el horizonte proyectado.

    forecast_pct: cambio porcentual proyectado
    ((pronóstico − nivel_reciente) / |nivel_reciente| * 100). Si es None,
    se usa el modo histórico anterior (compatibilidad).
    """
    trend = trend_info.get("trend")
    if trend == "INSUFFICIENT_DATA":
        return {
            "decline_risk": "INSUFFICIENT_DATA",
            "signals": [],
            "detail": "sin tendencia sustentada; no se puede estimar riesgo de caída",
        }

    values = series.to_numpy(dtype=float)
    signals, stable_decline, acceleration_negative, severe_drop = _historical_signals(trend_info, values)

    if forecast_pct is None:
        # Modo histórico (compatibilidad con llamadas sin pronóstico).
        if trend == "DOWNWARD" and stable_decline and (acceleration_negative or severe_drop):
            level = "HIGH"
            detail = ("modo histórico: descenso sostenido y estable, con "
                      "aceleración o magnitud severa (sin cambio proyectado disponible)")
        elif trend == "DOWNWARD":
            level = "MEDIUM"
            detail = ("modo histórico: señales de deterioro sin cambio "
                      "proyectado disponible")
        else:
            level = "LOW"
            detail = "modo histórico: sin señales observables de caída sostenida"
        return {"decline_risk": level, "signals": signals, "detail": detail}

    # ---- Modo proyectado (principal) ----------------------------------
    try:
        pct = float(forecast_pct)
    except (TypeError, ValueError):
        return {
            "decline_risk": "INSUFFICIENT_DATA",
            "signals": signals,
            "detail": "cambio proyectado no válido; no se puede estimar el riesgo",
        }
    if isinstance(pct, float) and np.isnan(pct):
        # NaN: todas las comparaciones son falsas y caería en LOW de forma
        # engañosa. (±inf sí se clasifica bien por comparación directa.)
        return {
            "decline_risk": "INSUFFICIENT_DATA",
            "signals": signals,
            "detail": "cambio proyectado no definido (NaN); no se puede estimar el riesgo",
        }

    if pct <= FORECAST_SEVERE_DROP_PCT:
        level = "HIGH"
        detail = (f"caída proyectada severa ({pct:.1f}% <= "
                  f"{FORECAST_SEVERE_DROP_PCT:.0f}%)")
    elif pct <= FORECAST_MATERIAL_DROP_PCT:
        level = "MEDIUM"
        detail = (f"caída proyectada material ({pct:.1f}% entre "
                  f"{FORECAST_SEVERE_DROP_PCT:.0f}% y {FORECAST_MATERIAL_DROP_PCT:.0f}%)")
    else:
        level = "LOW"
        detail = (f"sin caída material proyectada ({pct:.1f}% > "
                  f"{FORECAST_MATERIAL_DROP_PCT:.0f}%)")

    # Las señales históricas solo pueden ELEVAR el nivel, nunca bajarlo.
    order = ["LOW", "MEDIUM", "HIGH"]
    if (stable_decline or acceleration_negative) and level != "HIGH":
        level = order[order.index(level) + 1]
        signals.append("señal histórica de deterioro eleva el nivel un escalón")
        detail += "; elevado por señales históricas de deterioro"

    # Tope por confianza baja: no afirmar riesgo alto sin fiabilidad.
    try:
        conf = float(confidence)
    except (TypeError, ValueError):
        conf = 0.0
    if conf < LOW_CONFIDENCE_CAP and level == "HIGH":
        level = "MEDIUM"
        signals.append(
            f"confianza del pronóstico baja ({conf:.0f} < {LOW_CONFIDENCE_CAP:.0f}): "
            "no se afirma riesgo alto")
        detail += "; limitado a MEDIUM por confianza baja del pronóstico"

    return {"decline_risk": level, "signals": signals, "detail": detail}


def stockout_status() -> dict:
    """Estado de riesgo de agotamiento.

    El dataset no contiene inventario confiable: NO se fabrica un stock
    actual a partir de ventas. Siempre NOT_AVAILABLE en esta fase.
    """
    return {
        "stockout_status": "NOT_AVAILABLE",
        "detail": (
            "el dataset no contiene información de inventario confiable; "
            "ZAYVERO no estima un stock actual a partir de las ventas"
        ),
    }
