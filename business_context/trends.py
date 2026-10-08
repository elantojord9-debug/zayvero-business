"""FASE 5A — key_trends: tendencias observadas vs proyectadas, separadas.

- OBSERVED_TREND: calculada de las métricas mensuales reales del profile
  (FASE 1B): compara el revenue de los últimos 3 meses completos contra los
  3 anteriores. direction ∈ {UPWARD, DOWNWARD, STABLE}.
  Regla: |pct_change| <= 5 → STABLE (mismo umbral que 1B usa para mercados).
- PROJECTED_TREND: tomada de los insights de 4B (trend de 4A). No se
  recalcula; se conserva con su fuente.

Nunca se mezclan: cada trend lleva trend_type OBSERVED_TREND o
PROJECTED_TREND y su interpretación usa lenguaje observado/proyectado.
"""

from __future__ import annotations


def _elabel(entity) -> str:
    if isinstance(entity, dict):
        return str(entity.get("label") or entity.get("id") or entity)
    return str(entity)


def _observed_revenue_trend(profile: dict, evidence):
    monthly = (profile.get("temporal_metrics", {}) or {}).get("monthly", []) or []
    rows = [m for m in monthly if m.get("revenue") is not None]
    if len(rows) < 6:
        return None
    last3 = sum(m["revenue"] for m in rows[-3:])
    prev3 = sum(m["revenue"] for m in rows[-6:-3])
    if prev3 == 0:
        return None
    pct = (last3 - prev3) / abs(prev3) * 100
    direction = "STABLE" if abs(pct) <= 5 else ("UPWARD" if pct > 0 else "DOWNWARD")
    period = f"{rows[-6]['period']} → {rows[-1]['period']}"
    ev_id = evidence.add(
        source_module="FASE_1B",
        source_file="data/profiles/demo-retail/online_retail_II_full_profile.json",
        source_record="temporal_metrics.monthly",
        field="revenue_trend_3m_vs_prev_3m",
        value={"last_3m_revenue": last3, "prev_3m_revenue": prev3, "pct_change": round(pct, 2)},
        period=period,
        trace={"formula": "Σ revenue últimos 3 meses vs Σ revenue 3 anteriores; |pct|≤5 → STABLE"},
    )
    return {
        "kind": "OBSERVATION",
        "trend_id": "TRD-OBS-0001",
        "trend_type": "OBSERVED_TREND",
        "direction": direction,
        "period": period,
        "value": {"last_3m_revenue": last3, "prev_3m_revenue": prev3,
                  "pct_change": round(pct, 2)},
        "confidence": None,
        "source": "FASE_1B:data/profiles/demo-retail/online_retail_II_full_profile.json",
        "interpretation": (
            "OBSERVADO: el comportamiento histórico muestra que los ingresos de los "
            f"últimos 3 meses variaron {round(pct, 2)}% respecto a los 3 meses "
            f"anteriores (dirección: {direction}). Es una descripción del pasado, "
            "no una proyección."
        ),
        "evidence_id": ev_id,
        "trace": {"formula": "Σ revenue últimos 3 meses vs Σ revenue 3 anteriores",
                  "threshold": "|pct| <= 5 → STABLE"},
    }


def build_key_trends(profile: dict, pi_report: dict, evidence) -> list[dict]:
    trends: list[dict] = []

    observed = _observed_revenue_trend(profile, evidence)
    if observed:
        trends.append(observed)

    # PROJECTED_TREND desde 4B (no se recalcula)
    n = 1
    for ins in sorted((pi_report.get("prediction_insights", []) or []),
                      key=lambda x: x.get("insight_id", "")):
        if ins.get("prediction_status") != "OK":
            continue
        n += 1
        ev_id = evidence.add(
            source_module="FASE_4B",
            source_file="data/prediction_intelligence/demo-retail/online_retail_II_prediction_intelligence.json",
            source_record=ins.get("insight_id"),
            field="trend",
            value={"entity": ins.get("entity"), "trend": ins.get("trend"),
                   "confidence_score": ins.get("confidence_score")},
            period=ins.get("period"),
            trace=None,
        )
        trends.append(
            {
                "kind": "PREDICTION",
                "trend_id": f"TRD-PROJ-{n:04d}",
                "trend_type": "PROJECTED_TREND",
                "direction": ins.get("trend"),
                "period": ins.get("period"),
                "value": {"predicted_value": ins.get("predicted_value"),
                          "percentage_change": ins.get("percentage_change")},
                "confidence": ins.get("confidence_score"),
                "source": "FASE_4B:data/prediction_intelligence/demo-retail/online_retail_II_prediction_intelligence.json",
                "interpretation": (
                    "PROYECTADO: el modelo estima una dirección "
                    f"{ins.get('trend')} para {_elabel(ins.get('entity'))} durante "
                    f"{ins.get('period')}. Es una estimación, no un hecho; debe "
                    "contrastarse con resultados reales."
                ),
                "evidence_id": ev_id,
                "trace": {"source_insight": ins.get("insight_id")},
            }
        )
    return trends
