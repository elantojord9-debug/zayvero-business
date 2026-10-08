"""
ZAYVERO BUSINESS — FASE 2A.

`scoring`: severidad, confidence score, explicaciones y priorización.

SEVERIDAD (por magnitud de desviación `dev`):
    - dev: |z robusto|, IQRs más allá del cuartil, o equivalente mapeado.
    - CRITICAL: dev >= 12 Y confidence >= 75. Reservado para casos
      verdaderamente excepcionales; NO se usa solo porque algo sea raro.
    - HIGH:     dev >= 6
    - MEDIUM:   dev >= 4
    - LOW:      dev >= 2.5
    - INFO:     dev >= 1.5 (solo vigilancia; los detectores emiten
      desde LOW salvo indicación contraria)

CONFIDENCE (0-100), factores ponderados:
    - historial (30 pts): min(n_puntos_historia / 60, 1) * 30
    - magnitud   (35 pts): min(dev / 6, 1) * 35
    - consistencia (20 pts): 20 si el patrón es sostenido
      (>=3 puntos anómalos cercanos o baseline estable), 10 si puntual
    - calidad de datos (15 pts): 15 * (1 - fracción_nula_relevante)

EXPLICACIONES: plantillas factuales en español. Nunca "tiene un problema";
siempre describen observado vs esperado con números.

RANKING: priority_score = peso_severidad * (0.5 + confidence/100)
         * (1 + log10(1 + |impacto|)), donde impacto es monetario si
         existe, si no la desviación. Orden descendente.
"""

from __future__ import annotations

import math
from typing import Any

SEVERITY_ORDER = ["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"]
SEVERITY_WEIGHT = {"INFO": 1, "LOW": 2, "MEDIUM": 3, "HIGH": 4, "CRITICAL": 5}


def ratio_to_dev(ratio: float) -> float:
    """Mapea un ratio >= 1 a una escala comparable al |z|."""
    r = abs(float(ratio))
    if r < 1:
        r = 1 / r if r > 0 else 1.0
    return (r - 1.0) * 2.0


def assign_severity(dev: float, confidence: float) -> str:
    """
    Asigna severidad por umbrales absolutos (máximo HIGH).
    CRITICAL se asigna después por ranking (ver report.py): solo el top
    por prioridad entre dev>=12 y confidence>=75, máximo 25 casos.
    """
    d = abs(float(dev))
    if d >= 6.0:
        return "HIGH"
    if d >= 4.0:
        return "MEDIUM"
    if d >= 2.5:
        return "LOW"
    return "INFO"


def confidence_score(
    n_history: int,
    dev: float,
    sustained: bool,
    null_fraction: float = 0.0,
) -> tuple[int, dict[str, Any]]:
    """
    Calcula confidence 0-100 y devuelve (score, factores_explicados).
    """
    d = abs(float(dev))
    f_history = min(max(n_history, 0) / 60.0, 1.0) * 30.0
    f_magnitude = min(d / 6.0, 1.0) * 35.0
    f_consistency = 20.0 if sustained else 10.0
    nf = min(max(float(null_fraction), 0.0), 1.0)
    f_quality = 15.0 * (1.0 - nf)
    total = f_history + f_magnitude + f_consistency + f_quality
    score = int(round(max(0.0, min(100.0, total))))
    factors = {
        "history_points": int(n_history),
        "history_contribution": round(f_history, 1),
        "deviation": round(d, 2),
        "magnitude_contribution": round(f_magnitude, 1),
        "sustained_pattern": bool(sustained),
        "consistency_contribution": round(f_consistency, 1),
        "null_fraction": round(nf, 4),
        "data_quality_contribution": round(f_quality, 1),
        "explanation": (
            f"Confianza {score}/100: historial {int(n_history)} puntos "
            f"({f_history:.0f}/30), desviación {d:.1f} ({f_magnitude:.0f}/35), "
            f"patrón {'sostenido' if sustained else 'puntual'} "
            f"({f_consistency:.0f}/20), calidad de datos ({f_quality:.0f}/15)."
        ),
    }
    return score, factors


def priority_score(severity: str, confidence: int, impact: float) -> float:
    """Puntaje para ordenar el ranking (mayor = más prioritario)."""
    w = SEVERITY_WEIGHT.get(severity, 1)
    imp = abs(float(impact)) if impact is not None else 0.0
    return w * (0.5 + confidence / 100.0) * (1.0 + math.log10(1.0 + imp))


# Plantillas de explicación (factuales, sin interpretación empresarial)
EXPLANATIONS = {
    "temporal_spike": (
        "Las ventas del {granularity_label} {period_label} fueron "
        "{observed_fmt}, {ratio:.1f} veces superiores a su comportamiento "
        "habitual ({expected_fmt}). Desviación robusta z={z:.1f}."
    ),
    "temporal_drop": (
        "Las ventas del {granularity_label} {period_label} fueron "
        "{observed_fmt}, {ratio:.1f} veces inferiores a su comportamiento "
        "habitual ({expected_fmt}). Desviación robusta z={z:.1f}."
    ),
    "product_sales_change": (
        "Las ventas del producto {entity_label} fueron {ratio:.1f} veces "
        "{direction} a su comportamiento habitual ({expected_fmt} vs "
        "{observed_fmt} observados) en los últimos {window_days} días."
    ),
    "price_outlier": (
        "Se registraron {n} transacciones del producto {entity_label} con "
        "precio {direction} a su rango habitual "
        "({low_fmt} – {high_fmt}): {examples}."
    ),
    "quantity_outlier": (
        "Se registraron {n} transacciones del producto {entity_label} con "
        "cantidad {direction} a su rango habitual "
        "(hasta {high_fmt} unidades): {examples}."
    ),
    "customer_unusual_purchase": (
        "El cliente {entity_label} realizó una compra de {observed_fmt}, "
        "{ratio:.1f} veces superior a su compra habitual ({expected_fmt}). "
        "Requiere revisión."
    ),
    "customer_frequency_burst": (
        "El cliente {entity_label} concentró {n} compras en {window_days} "
        "días, cuando su intervalo habitual entre compras es de "
        "{expected_gap:.0f} días. Comportamiento inusual de frecuencia."
    ),
}


def build_explanation(
    anomaly_type: str, fmt_money, **kwargs: Any
) -> str:
    """Rellena la plantilla del tipo con valores ya formateados."""
    template = EXPLANATIONS.get(anomaly_type, "{entity_label}: desviación detectada.")
    ctx = dict(kwargs)
    for key in ("observed_fmt", "expected_fmt", "low_fmt", "high_fmt"):
        if key in ctx and isinstance(ctx[key], (int, float)):
            ctx[key] = fmt_money(ctx[key])
    return template.format(**ctx)
