"""FASE 5A — key_opportunities: oportunidades SOLO con evidencia.

Reglas deterministas y documentadas:

1. Crecimiento proyectado 4B: insights con trend UPWARD, forecast_quality
   en (HIGH, MODERATE) y attention_level en (REVIEW, IMPORTANT).
   Lenguaje: "posible oportunidad". No se afirma certeza.
2. Producto con comportamiento positivo recurrente 2B/2C: hallazgos de tipo
   PRODUCT_ANOMALY con percentage_difference > 0 (observado mayor que lo
   esperado), recurrence == "recurrent" y evidence_quality en (HIGH, MEDIUM).
   NO se asume que una anomalía positiva sea automáticamente una
   oportunidad: se etiqueta como "posible oportunidad" para explorar.
3. Mercado con crecimiento observado 1B: países con pct_change > 50 en
   growth_6m_vs_prev_6m (últimos 6 meses completos vs 6 anteriores).

Cada oportunidad lleva opportunity_id, type, importance, evidence,
explanation, recommendation y trace.
"""

from __future__ import annotations


def _elabel(entity) -> str:
    if isinstance(entity, dict):
        return str(entity.get("label") or entity.get("id") or entity)
    return str(entity)


def build_key_opportunities(pi_report, findings_report, context_report, profile, evidence) -> list[dict]:
    opps: list[dict] = []
    counter = 0

    def add(otype, importance, explanation, source, recommendation, source_record, field, value):
        nonlocal counter
        counter += 1
        ev_id = evidence.add(
            source_module=source.split(":")[0],
            source_file=source.split(":", 1)[1] if ":" in source else source,
            source_record=source_record,
            field=field,
            value=value,
            period=None,
            trace=None,
        )
        opps.append(
            {
                "kind": "OBSERVATION",
                "opportunity_id": f"OPP-{counter:04d}",
                "type": otype,
                "importance": importance,
                "explanation": explanation,
                "evidence": value,
                "source": source,
                "recommendation": recommendation,
                "evidence_id": ev_id,
                "trace": {"rule": "deterministic opportunity extraction (FASE 5A opportunities.py)",
                          "source_record": source_record},
            }
        )

    # 1) Crecimiento proyectado (4B)
    for ins in sorted((pi_report.get("prediction_insights", []) or []),
                      key=lambda x: x.get("insight_id", "")):
        if (ins.get("trend") == "UPWARD"
                and ins.get("forecast_quality") in ("HIGH", "MODERATE")
                and ins.get("attention_level") in ("REVIEW", "IMPORTANT")):
            add(
                otype="PROJECTED_GROWTH",
                importance="MEDIUM" if ins.get("forecast_quality") == "MODERATE" else "HIGH",
                explanation=(
                    f"Posible oportunidad: el modelo proyecta una tendencia de crecimiento "
                    f"para {_elabel(ins.get('entity'))} durante {ins.get('period')} "
                    f"(confianza {ins.get('confidence_score')}/100, calidad "
                    f"{ins.get('forecast_quality')}). Debe interpretarse considerando el "
                    "error histórico del modelo y contrastarse con resultados reales."
                ),
                source="FASE_4B:data/prediction_intelligence/demo-retail/online_retail_II_prediction_intelligence.json",
                recommendation=(ins.get("recommendations") or [None])[0],
                source_record=ins.get("insight_id"),
                field="trend",
                value={"entity": ins.get("entity"), "trend": "UPWARD",
                       "forecast_quality": ins.get("forecast_quality"),
                       "predicted_value": ins.get("predicted_value")},
            )

    # 2) Producto con comportamiento positivo recurrente (2B/2C)
    ctx_by_id = {c.get("finding_id"): c
                 for c in (context_report.get("context_findings", []) or [])}
    for f in sorted((findings_report.get("findings", []) or []),
                    key=lambda x: x.get("priority_rank", 10**9)):
        if f.get("type") != "PRODUCT_ANOMALY":
            continue
        pct = f.get("percentage_difference")
        if pct is None or pct <= 0:
            continue
        ctx = ctx_by_id.get(f.get("finding_id"), {})
        if ctx.get("recurrence") != "recurrent":
            continue
        if f.get("evidence_quality") not in ("HIGH", "MEDIUM"):
            continue
        add(
            otype="RECURRING_POSITIVE_BEHAVIOR",
            importance="MEDIUM",
            explanation=(
                f"Posible oportunidad: el producto {(f.get('entity') or {}).get('label')} "
                f"presenta un comportamiento por encima de lo esperado de forma recurrente "
                f"({pct}% sobre el comportamiento esperado, evidencia "
                f"{f.get('evidence_quality')}). Podría merecer exploración comercial; "
                "no se afirma que sea una oportunidad confirmada."
            ),
            source="FASE_2B_2C:data/findings + data/context (demo-retail)",
            recommendation=f.get("recommended_review"),
            source_record=f.get("finding_id"),
            field="percentage_difference",
            value={"entity": (f.get("entity") or {}).get("label"),
                   "percentage_difference": pct, "recurrence": "recurrent"},
        )

    # 3) Mercados con crecimiento observado (1B)
    growth = (profile.get("countries", {}) or {}).get("growth_6m_vs_prev_6m", []) or []
    for g in sorted(growth, key=lambda x: x.get("pct_change", 0), reverse=True):
        if (g.get("pct_change") or 0) > 50:
            add(
                otype="MARKET_GROWTH_OBSERVED",
                importance="LOW",
                explanation=(
                    f"Posible oportunidad: el mercado {g.get('country')} muestra un "
                    f"crecimiento observado de {g.get('pct_change')}% en ingresos "
                    "(últimos 6 meses completos vs 6 anteriores). Es solo variación "
                    "observada, sin interpretación causal."
                ),
                source="FASE_1B:data/profiles/demo-retail/online_retail_II_full_profile.json",
                recommendation="Explorar el comportamiento de este mercado con datos más recientes antes de planificar.",
                source_record=g.get("country"),
                field="pct_change",
                value=g,
            )

    return opps
