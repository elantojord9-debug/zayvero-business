"""
ZAYVERO BUSINESS — FASE 2B.

`priority`: evidence_quality, business_priority y requires_review.

EVIDENCE_QUALITY (calidad de la evidencia):
    LOW:    historial < 30 puntos o null_fraction > 0.10
    MEDIUM: historial 30-89 puntos
    HIGH:   historial >= 90 puntos y null_fraction <= 0.10
Nunca se fabrica un baseline: si no hay historial suficiente, la calidad
es LOW y la prioridad se reduce.

BUSINESS_PRIORITY (reglas base; report.py aplica además un tope por
ranking: máximo 25 URGENT, el resto de candidatos pasa a IMPORTANT):
    - Con evidencia LOW: nunca URGENT ni IMPORTANT. Si la desviación es
      extrema (impact>=40 o dev>=8) → REVIEW, si no → MONITOR.
    - URGENT:    impact >= 75 y confidence >= 70 (evidencia no LOW)
    - IMPORTANT: impact >= 50 y confidence >= 60 (evidencia no LOW)
    - REVIEW:    impact >= 25, o requires_review, o (LOW con impact>=40)
    - MONITOR:   resto

REQUIRES_REVIEW (casos especiales, no asume errores):
    - precio extremadamente alto: observed > 10 * Q3 del producto
    - cantidad extraordinaria: observed > 50 * Q3 del producto
    - compra excepcionalmente grande: |difference| monetaria >= 10000
    - pico temporal extremo: ratio >= 3
"""

from __future__ import annotations

from typing import Any

PRIORITY_ORDER = ["MONITOR", "REVIEW", "IMPORTANT", "URGENT"]
PRIORITY_WEIGHT = {"MONITOR": 1, "REVIEW": 2, "IMPORTANT": 3, "URGENT": 4}


def evidence_quality(anomaly: dict[str, Any]) -> tuple[str, str]:
    """Devuelve (nivel, explicación)."""
    cf = anomaly.get("confidence_factors") or {}
    try:
        n = int(cf.get("history_points") or 0)
    except (TypeError, ValueError):
        n = 0
    try:
        nf = float(cf.get("null_fraction") or 0.0)
    except (TypeError, ValueError):
        nf = 0.0
    if n < 30 or nf > 0.10:
        return (
            "LOW",
            f"Evidencia limitada: {n} puntos de historial "
            f"(mínimo recomendado 30), fracción nula {nf:.2%}. "
            "No se fabrica baseline: la prioridad queda acotada.",
        )
    if n < 90:
        return (
            "MEDIUM",
            f"Evidencia moderada: {n} puntos de historial, "
            f"fracción nula {nf:.2%}.",
        )
    return (
        "HIGH",
        f"Evidencia sólida: {n} puntos de historial, "
        f"fracción nula {nf:.2%}.",
    )


def requires_review_flag(
    anomaly: dict[str, Any], finding_type: str
) -> tuple[bool, str | None]:
    """
    Casos especiales que merecen revisión humana explícita.
    No asume que sean errores: solo marca REQUIRES_REVIEW.
    """
    ev = anomaly.get("evidence") or {}
    obs = anomaly.get("observed")
    try:
        obs_f = float(obs) if obs is not None else None
    except (TypeError, ValueError):
        obs_f = None

    if finding_type == "PRICE_ANOMALY" and obs_f is not None:
        try:
            q3 = float(ev.get("q3")) if ev.get("q3") is not None else None
        except (TypeError, ValueError):
            q3 = None
        if q3 and q3 > 0 and obs_f > 10 * q3:
            return True, (
                f"Precio extremadamente alto: {obs_f:,.2f} frente a Q3 "
                f"habitual de {q3:,.2f} (>10×). Requiere revisión: podría "
                "ser un precio legítimo especial o un dato a corregir."
            )
    if finding_type == "QUANTITY_ANOMALY" and obs_f is not None:
        try:
            q3 = float(ev.get("q3")) if ev.get("q3") is not None else None
        except (TypeError, ValueError):
            q3 = None
        if q3 and q3 > 0 and obs_f > 50 * q3:
            return True, (
                f"Cantidad extraordinaria: {obs_f:,.0f} unidades frente a "
                f"Q3 habitual de {q3:,.0f} (>50×). Requiere revisión."
            )
    if finding_type == "CUSTOMER_ANOMALY" and obs_f is not None:
        try:
            diff = abs(float(anomaly.get("difference") or 0.0))
        except (TypeError, ValueError):
            diff = 0.0
        if diff >= 10000:
            return True, (
                "Compra excepcionalmente grande: desviación monetaria "
                f"de {diff:,.2f} respecto al comportamiento habitual del "
                "cliente. Requiere revisión."
            )
    if finding_type == "TEMPORAL_ANOMALY":
        try:
            ratio = float(anomaly.get("ratio") or 0.0)
        except (TypeError, ValueError):
            ratio = 0.0
        if ratio >= 3.0 or (ratio > 0 and ratio <= 1 / 3.0):
            return True, (
                f"Pico temporal extremo: ratio de {ratio:.2f} respecto a "
                "la mediana móvil. Requiere revisión para determinar si "
                "refleja un evento real de demanda."
            )
    return False, None


def business_priority(
    impact: float,
    confidence: float,
    evidence_quality_level: str,
    deviation: float,
    requires_review: bool,
) -> str:
    """Asigna MONITOR / REVIEW / IMPORTANT / URGENT según las reglas."""
    imp = float(impact)
    conf = float(confidence)
    dev = abs(float(deviation or 0.0))

    if evidence_quality_level == "LOW":
        # Nunca URGENT/IMPORTANT con evidencia débil.
        if imp >= 40 or dev >= 8 or requires_review:
            return "REVIEW"
        return "MONITOR"
    if imp >= 75 and conf >= 70:
        return "URGENT"
    if imp >= 50 and conf >= 60:
        return "IMPORTANT"
    if imp >= 25 or requires_review:
        return "REVIEW"
    return "MONITOR"


def data_quality_warning(anomaly: dict[str, Any]) -> str | None:
    """Advertencia explícita si el hallazgo depende de datos flojos."""
    cf = anomaly.get("confidence_factors") or {}
    try:
        nf = float(cf.get("null_fraction") or 0.0)
    except (TypeError, ValueError):
        nf = 0.0
    t = anomaly.get("type", "")
    if nf > 0.05:
        return (
            "data_quality_warning: este hallazgo puede verse afectado por "
            f"valores faltantes en los datos fuente (fracción nula {nf:.2%})."
        )
    if t in ("price_outlier", "quantity_outlier") and obs_extreme(anomaly):
        return (
            "data_quality_warning: price/quantity anomaly may be affected "
            "by extreme source values — verificar contra el sistema origen."
        )
    return None


def obs_extreme(anomaly: dict[str, Any]) -> bool:
    """¿El valor observado es extremo respecto a sus cuartiles?"""
    ev = anomaly.get("evidence") or {}
    try:
        obs = float(anomaly.get("observed"))
        q3 = float(ev.get("q3"))
        iqr = float(ev.get("iqr"))
    except (TypeError, ValueError):
        return False
    if iqr <= 0:
        return False
    return (obs - q3) / iqr > 10
