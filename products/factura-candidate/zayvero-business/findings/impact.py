"""
ZAYVERO BUSINESS — FASE 2B.

`impact`: cálculo del impacto monetario y del impact_score.

IMPACT_SCORE (0-100) — fórmula documentada:
    impact = 35*M + 25*D + 20*C + 10*S + 10*Q

    M (magnitud monetaria, 0-1):
        min(log10(1 + |total_difference|) / log10(1 + P95), 1)
        P95 = percentil 95 de |total_difference| sobre todos los hallazgos
        (cálculo en dos pasadas; escala guiada por los datos, no arbitraria).
        Para hallazgos no monetarios (ej. ráfaga de frecuencia) se usa la
        diferencia en sus unidades nativas; queda documentado en el trace.

    D (desviación estadística, 0-1):
        min(dev / 12, 1)   (dev = |z robusto| o IQRs equivalentes)

    C (confianza, 0-1):
        confidence_score / 100   (viene de FASE 2A)

    S (alcance, 0-1):
        min(log10(1 + n_affected) / log10(1 + 50), 1)
        n_affected = transacciones/entidades involucradas (según tipo).

    Q (calidad de datos, 0-1):
        1 - null_fraction   (null_fraction de FASE 2A, acotada a [0,1])

NO se usa solo el z-score: la magnitud monetaria pesa más (35) que la
desviación pura (25), y el alcance y la calidad también cuentan.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np


def monetary_impact(
    observed: Any, expected: Any, difference: Any = None
) -> dict[str, Any]:
    """
    Calcula observed/expected/absolute_difference/percentage_difference
    cuando es matemáticamente válido. Nunca lo llama "pérdida"/"ganancia":
    es "desviación respecto al comportamiento esperado".
    """
    def _num(x: Any) -> float | None:
        try:
            v = float(x)
            return v if math.isfinite(v) else None
        except (TypeError, ValueError):
            return None

    obs = _num(observed)
    exp = _num(expected)
    diff = _num(difference)
    if diff is None and obs is not None and exp is not None:
        diff = obs - exp
    pct: float | None = None
    if diff is not None and exp is not None and exp != 0:
        pct = diff / abs(exp) * 100.0
    return {
        "observed_value": obs,
        "expected_value": exp,
        "absolute_difference": diff,
        "percentage_difference": round(pct, 2) if pct is not None else None,
        "label": "desviación respecto al comportamiento esperado",
    }


def _affected_count(anomaly: dict[str, Any], finding_type: str) -> int:
    """Heurística documentada de n_affected por tipo de anomalía."""
    ev = anomaly.get("evidence") or {}
    t = anomaly.get("type", "")
    try:
        if t in ("price_outlier", "quantity_outlier"):
            # cada anomalía 2A = 1 transacción atípica (antes de dedup)
            return 1
        if t == "product_sales_change":
            return int(ev.get("recent_days_active") or 1)
        if t in ("temporal_spike", "temporal_drop"):
            return int(ev.get("window_days") or 1)
        if t == "customer_unusual_purchase":
            return 1
        if t == "customer_frequency_burst":
            return int(ev.get("max_purchases_in_window") or 1)
    except (TypeError, ValueError):
        pass
    return 1


def compute_p95(diffs: list[float]) -> float:
    """P95 de |diferencias| para escalar el componente monetario."""
    vals = [abs(float(d)) for d in diffs if d is not None]
    vals = [v for v in vals if math.isfinite(v)]
    if not vals:
        return 1.0
    p95 = float(np.percentile(vals, 95))
    return p95 if p95 > 0 else 1.0


def impact_score(
    anomaly: dict[str, Any],
    p95_diff: float,
    total_difference: float | None = None,
    n_merged: int = 1,
) -> tuple[float, dict[str, Any]]:
    """
    Calcula impact_score 0-100 y devuelve (score, componentes_explicados).

    total_difference: para hallazgos deduplicados, suma de |diferencias|;
    si es None se usa la diferencia de la anomalía.
    n_merged: nº de anomalías 2A agrupadas en el hallazgo.
    """
    diff = total_difference
    if diff is None:
        diff = anomaly.get("difference")
    try:
        adiff = abs(float(diff)) if diff is not None else 0.0
    except (TypeError, ValueError):
        adiff = 0.0

    dev = abs(float(anomaly.get("deviation") or anomaly.get("z_robust") or 0.0))
    conf = float(anomaly.get("confidence_score") or 0.0)
    cf = anomaly.get("confidence_factors") or {}
    try:
        nf = min(max(float(cf.get("null_fraction") or 0.0), 0.0), 1.0)
    except (TypeError, ValueError):
        nf = 0.0

    m = min(math.log10(1.0 + adiff) / math.log10(1.0 + p95_diff), 1.0)
    d = min(dev / 12.0, 1.0)
    c = min(max(conf / 100.0, 0.0), 1.0)
    n_aff = _affected_count(anomaly, "") * max(int(n_merged), 1)
    s = min(math.log10(1.0 + n_aff) / math.log10(1.0 + 50.0), 1.0)
    q = 1.0 - nf

    score = 35.0 * m + 25.0 * d + 20.0 * c + 10.0 * s + 10.0 * q
    score = round(max(0.0, min(100.0, score)), 1)
    components = {
        "monetary": round(35.0 * m, 1),
        "deviation": round(25.0 * d, 1),
        "confidence": round(20.0 * c, 1),
        "scope": round(10.0 * s, 1),
        "data_quality": round(10.0 * q, 1),
        "formula": "35*M + 25*D + 20*C + 10*S + 10*Q "
        "(M=log-monetario vs P95, D=dev/12, C=conf/100, "
        "S=log-alcance, Q=1-null_fraction)",
        "p95_reference": round(p95_diff, 2),
        "n_affected": int(n_aff),
        "n_merged": int(n_merged),
    }
    return score, components
