"""
ZAYVERO BUSINESS — FASE 4B: interpretación empresarial determinista.

Todo el texto se genera con plantillas y reglas (sin LLM).
Separación estricta entre HECHO OBSERVADO y PROYECCIÓN:
- OBSERVADO: "Los datos históricos muestran..."
- PROYECTADO: "El modelo estima..."

Nunca se mezclan ambas cosas como si fueran hechos, y nunca se
presenta una predicción como certeza ni se afirma causalidad.
"""

from __future__ import annotations

from typing import Any, Dict, List


def fmt_money(value: float | None) -> str:
    if value is None:
        return "no disponible"
    return "£{:,.0f}".format(value)


def fmt_units(value: float | None) -> str:
    if value is None:
        return "no disponible"
    return "{:,.0f} unidades".format(value)


def type_descriptor(prediction_type: str) -> Dict[str, str]:
    mapping = {
        "DEMAND_REVENUE": {
            "what": "demanda de ingresos",
            "unit_word": "ingresos",
            "format": "money",
        },
        "DEMAND_QUANTITY": {
            "what": "demanda en unidades",
            "unit_word": "volumen de unidades",
            "format": "units",
        },
    }
    return mapping.get(
        prediction_type,
        {"what": "demanda (%s)" % prediction_type, "unit_word": "indicador",
         "format": "units"},
    )


def entity_descriptor(entity: Dict[str, Any]) -> str:
    kind = (entity or {}).get("kind", "GLOBAL")
    eid = (entity or {}).get("id")
    if kind == "GLOBAL":
        return "el negocio completo"
    if kind == "PRODUCT":
        return "el producto %s" % eid
    if kind == "COUNTRY":
        return "el país %s" % eid
    return "la entidad %s" % (eid or kind)


def fmt_value(prediction_type: str, value: float | None) -> str:
    desc = type_descriptor(prediction_type)
    if desc["format"] == "money":
        return fmt_money(value)
    return fmt_units(value)


def interpret_error(pred: Dict[str, Any]) -> str:
    """Interpreta MAE/RMSE/MAPE con cuidado. Nunca 'X% de precisión'."""
    metrics = pred.get("metrics") or {}
    mape = metrics.get("mape")
    mae = metrics.get("mae")
    rmse = metrics.get("rmse")
    mape_valid = bool(metrics.get("mape_valid")) and mape is not None
    parts: List[str] = []
    if mape_valid:
        m = float(mape)
        if m <= 20:
            nivel = "buena para planificación de referencia"
        elif m <= 40:
            nivel = "moderada/limitada para planificación"
        else:
            nivel = "limitada"
        parts.append(
            "El modelo presentó un error porcentual absoluto medio (MAPE) de "
            "%.1f%% durante la validación histórica. Esto indica una precisión "
            "%s y recomienda utilizar la predicción como referencia, no como "
            "cifra garantizada." % (m, nivel)
        )
    else:
        reason = metrics.get("mape_invalid_reason") or \
            "no pudo calcularse de forma válida"
        parts.append(
            "El MAPE no es válido en esta predicción (%s); la evaluación del "
            "error se basa en MAE y RMSE." % reason
        )
    if mae is not None:
        parts.append("Error absoluto medio en validación (MAE): %s." % fmt_money(float(mae)))
    if rmse is not None:
        parts.append("Raíz del error cuadrático medio (RMSE): %s." % fmt_money(float(rmse)))
    return " ".join(parts)


def observed_statement(pred: Dict[str, Any], comparison: Dict[str, Any]) -> str:
    """HECHO OBSERVADO (datos históricos, no proyección)."""
    desc = type_descriptor(pred.get("prediction_type", ""))
    trace = pred.get("trace") or {}
    n = comparison.get("n_observed_periods") or 0
    baseline = comparison.get("historical_baseline")
    recent = comparison.get("recent_actual_value")
    window = comparison.get("recent_window_periods") or 1
    if n and baseline is not None and recent is not None:
        return (
            "OBSERVADO: Los datos históricos muestran un promedio de %s por "
            "periodo en los últimos %d periodos observados, y un total de %s "
            "en los últimos %d periodo(s) observados." % (
                fmt_value(pred.get("prediction_type", ""), baseline), n,
                fmt_value(pred.get("prediction_type", ""), recent), window)
        )
    return (
        "OBSERVADO: No hay suficientes valores observados disponibles en el "
        "output de FASE 4A para describir el comportamiento histórico de %s." % desc["unit_word"]
    )


def projected_statement(pred: Dict[str, Any]) -> str:
    """PROYECCIÓN (estimación del modelo, no hecho)."""
    ptype = pred.get("prediction_type", "")
    desc = type_descriptor(ptype)
    entity = entity_descriptor(pred.get("entity") or {})
    value = fmt_value(ptype, pred.get("predicted_value"))
    period = pred.get("period") or "el periodo proyectado"
    return (
        "PROYECTADO: El modelo estima %s de %s para %s (%s)." % (
            value, desc["what"], entity, period)
    )


def uncertainty_explanation(
    pred: Dict[str, Any],
    uncertainty_level: str,
    uncertainty_evidence: Dict[str, Any],
) -> str:
    lower = fmt_value(pred.get("prediction_type", ""), pred.get("lower_bound"))
    upper = fmt_value(pred.get("prediction_type", ""), pred.get("upper_bound"))
    center = fmt_value(pred.get("prediction_type", ""), pred.get("predicted_value"))
    width_pct = uncertainty_evidence.get("interval_width_pct")
    base = (
        "La estimación central es %s, con un intervalo de %s a %s." % (center, lower, upper)
    )
    if uncertainty_level == "HIGH":
        return base + (
            " El intervalo es amplio (%.1f%% del valor central): la "
            "incertidumbre es elevada y la estimación debe leerse como un "
            "rango, no como un valor único." % (width_pct or 0)
        )
    if uncertainty_level == "MEDIUM":
        return base + (
            " La amplitud es moderada (%.1f%% del valor central): conviene "
            "planificar considerando el rango." % (width_pct or 0)
        )
    if uncertainty_level == "LOW":
        return base + (
            " El intervalo es relativamente estrecho (%.1f%% del valor "
            "central), lo que limita la incertidumbre de la estimación." % (width_pct or 0)
        )
    return base + " No se dispone de límites de intervalo para evaluar la incertidumbre."


def evidence_summary(
    pred: Dict[str, Any],
    forecast_quality: str,
    quality_reasons: List[str],
) -> str:
    trace = pred.get("trace") or {}
    n_periods = trace.get("n_periods") or "no indicado"
    training = pred.get("training_period") or "no indicado"
    validation = pred.get("validation_period") or "no indicado"
    method = pred.get("method") or "no indicado"
    return (
        "Evidencia: estimación basada en %s periodos de historial "
        "(entrenamiento: %s), validada sobre %s con el método '%s'. "
        "Calidad de la predicción: %s. %s" % (
            n_periods, training, validation, method, forecast_quality,
            " ".join(quality_reasons))
    )


def business_interpretation(
    pred: Dict[str, Any],
    comparison: Dict[str, Any],
    forecast_quality: str,
    uncertainty_level: str,
    trend_text: str,
    risk_text: str,
    forecast_direction_text: str | None = None,
    discrepancy_note_text: str | None = None,
    period_display: str | None = None,
) -> str:
    """Interpretación empresarial neutral. Sin causalidad, sin certezas.

    Estructura (corrección de calidad): la tendencia HISTÓRICA y la
    dirección del PRONÓSTICO se presentan por separado, con nota de
    discrepancia cuando difieren. Nunca se describe una caída
    proyectada como "crecimiento".
    """
    ptype = pred.get("prediction_type", "")
    desc = type_descriptor(ptype)
    entity = entity_descriptor(pred.get("entity") or {})
    confidence = pred.get("confidence_score")
    period = period_display or pred.get("period") or "el horizonte analizado"

    sentences: List[str] = []
    sentences.append(
        "ZAYVERO estima %s para %s durante %s." % (
            desc["what"], entity, period)
    )
    # Señal 1: pasado observado (tendencia histórica).
    sentences.append(trend_text)

    # Señal 2: futuro estimado (dirección del pronóstico).
    pct = comparison.get("percentage_change")
    if forecast_direction_text:
        sentences.append(forecast_direction_text)
    if pct is not None:
        if abs(pct) < 0.05:
            sentences.append(
                "El modelo proyecta %s de %s (%.1f%%) respecto a los "
                "niveles recientes observados: un cambio esperado futuro "
                "prácticamente nulo, no un cambio ya observado." % (
                    "sin cambio esperado",
                    fmt_value(ptype, abs(comparison.get("absolute_change") or 0)),
                    abs(pct))
            )
        else:
            if pct >= 0:
                change_word, change_adj = "incremento", "esperado"
            else:
                change_word, change_adj = "disminución", "esperada"
            sentences.append(
                "El modelo proyecta un%s %s %s de %s (%.1f%%) respecto a los "
                "niveles recientes observados. Se trata de un cambio esperado "
                "futuro, no de un cambio ya observado." % (
                    "a" if change_word == "disminución" else "",
                    change_word, change_adj,
                    fmt_value(ptype, abs(comparison.get("absolute_change") or 0)),
                    abs(pct))
            )
    else:
        sentences.append(
            "No se pudo calcular el cambio porcentual esperado por falta de "
            "referencia histórica válida."
        )

    # Discrepancia historial vs. pronóstico (solo cuando difieren).
    if discrepancy_note_text:
        sentences.append(discrepancy_note_text)

    sentences.append(
        "La confianza del modelo es %s/100 y la calidad de la predicción se "
        "clasifica como %s. %s" % (confidence, forecast_quality, interpret_error(pred))
    )
    sentences.append(risk_text)
    if uncertainty_level == "HIGH":
        sentences.append(
            "Dada la incertidumbre elevada, la estimación debe utilizarse "
            "como referencia para planificación, no como cifra garantizada."
        )
    return " ".join(sentences)


def possible_implication(pred: Dict[str, Any], trend: str, decline_risk: str) -> str:
    """Posible implicación: hipótesis de negocio, nunca un hecho ni causa."""
    ptype = pred.get("prediction_type", "")
    desc = type_descriptor(ptype)
    if trend == "UPWARD":
        base = (
            "Si el comportamiento observado se mantiene dentro del rango "
            "histórico, %s durante el horizonte analizado podrían ubicarse "
            "por encima de los niveles recientes. La proyección sirve como "
            "referencia para planificación, no como resultado asegurado."
        ) % desc["unit_word"]
    elif trend == "DOWNWARD":
        base = (
            "Si el comportamiento observado se mantiene dentro del rango "
            "histórico, %s durante el horizonte analizado podrían ubicarse "
            "por debajo de los niveles recientes. Conviene revisar las "
            "variables comerciales relacionadas y contrastar la proyección "
            "con resultados reales conforme avance el periodo."
        ) % desc["unit_word"]
    elif trend == "STABLE":
        base = (
            "El modelo no anticipa un cambio relevante; %s podrían mantenerse "
            "en niveles similares a los recientes. Útil como línea base para "
            "comparar el desempeño real del periodo."
        ) % desc["unit_word"]
    elif trend == "UNSTABLE":
        base = (
            "La variabilidad histórica limita lo que puede anticiparse; "
            "cualquier desviación de %s debería compararse contra el rango "
            "observado antes de interpretarla como un cambio de dirección."
        ) % desc["unit_word"]
    else:
        base = (
            "No existe suficiente información para derivar implicaciones "
            "confiables sobre %s." % desc["unit_word"]
        )
    if decline_risk == "HIGH":
        base += (
            " El riesgo elevado de disminución sugiere dar seguimiento "
            "cercano al indicador en el próximo periodo."
        )
    return "Posible implicación (hipótesis, no hecho): " + base
