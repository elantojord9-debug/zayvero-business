"""FASE 5A — key_risks: riesgos empresariales derivados de evidencia.

Reglas deterministas y documentadas (sin inventar riesgos):

1. Hallazgos 2B: todos los URGENT + los IMPORTANT con impact_score >= 75.
   - risk_type según el tipo de hallazgo:
     SALES_ANOMALY/TEMPORAL_ANOMALY → REVENUE_RISK
     PRODUCT_ANOMALY → PRODUCT_RISK
     CUSTOMER_ANOMALY → CUSTOMER_RISK
     PRICE_ANOMALY → PRICE_RISK
     QUANTITY_ANOMALY → QUANTITY_RISK
     otros → OTHER
   - severity: URGENT → HIGH, IMPORTANT → MEDIUM.
2. Predicciones 4B: decline_risk HIGH → PREDICTION_RISK HIGH;
   decline_risk MEDIUM → PREDICTION_RISK MEDIUM; uncertainty HIGH con
   attention IMPORTANT/URGENT (y sin decline HIGH/MEDIUM ya cubierto) →
   PREDICTION_RISK MEDIUM.
3. Calidad de datos 1B: cada deducción del data quality score →
   DATA_QUALITY_RISK; severity MEDIUM si points >= 5, LOW en otro caso.

No se afirma causalidad: el lenguaje describe la evidencia y el riesgo.
"""

from __future__ import annotations

_TYPE_TO_RISK = {
    "SALES_ANOMALY": "REVENUE_RISK",
    "TEMPORAL_ANOMALY": "REVENUE_RISK",
    "PRODUCT_ANOMALY": "PRODUCT_RISK",
    "CUSTOMER_ANOMALY": "CUSTOMER_RISK",
    "PRICE_ANOMALY": "PRICE_RISK",
    "QUANTITY_ANOMALY": "QUANTITY_RISK",
}

_SEVERITY_BY_PRIORITY = {"URGENT": "HIGH", "IMPORTANT": "MEDIUM"}


def _elabel(entity) -> str:
    if isinstance(entity, dict):
        return str(entity.get("label") or entity.get("id") or entity)
    return str(entity)


def build_key_risks(findings_report, pi_report, profile, evidence) -> list[dict]:
    risks: list[dict] = []
    counter = 0

    def add(risk_type, severity, explanation, source, recommendation, source_record, field, value):
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
        risks.append(
            {
                "kind": "RISK",
                "risk_id": f"RISK-{counter:04d}",
                "risk_type": risk_type,
                "severity": severity,
                "explanation": explanation,
                "evidence": value,
                "source": source,
                "recommendation": recommendation,
                "evidence_id": ev_id,
                "trace": {"rule": "deterministic risk extraction (FASE 5A risks.py)",
                          "source_record": source_record},
            }
        )

    # 1) Hallazgos 2B
    findings = findings_report.get("findings", []) or []
    for f in sorted(findings, key=lambda x: x.get("priority_rank", 10**9)):
        pri = f.get("business_priority")
        score = f.get("impact_score") or 0
        if not (pri == "URGENT" or (pri == "IMPORTANT" and score >= 75)):
            continue
        risk_type = _TYPE_TO_RISK.get(f.get("type"), "OTHER")
        explanation = (
            f"Hallazgo {pri} (impact score {score}/100): {f.get('title')}. "
            f"Desviación observada de {f.get('difference')} respecto al "
            f"comportamiento esperado ({f.get('expected_value')}). "
            "Requiere revisión."
        )
        add(
            risk_type=risk_type,
            severity=_SEVERITY_BY_PRIORITY.get(pri, "MEDIUM"),
            explanation=explanation,
            source="FASE_2B:data/findings/demo-retail/online_retail_II_full_findings.json",
            recommendation=f.get("recommended_review"),
            source_record=f.get("finding_id"),
            field="business_finding",
            value={"title": f.get("title"), "impact_score": score,
                   "observed_value": f.get("observed_value"),
                   "expected_value": f.get("expected_value"),
                   "difference": f.get("difference")},
        )

    # 2) Predicciones 4B
    insights = pi_report.get("prediction_insights", []) or []
    for ins in sorted(insights, key=lambda x: x.get("insight_id", "")):
        dr = ins.get("decline_risk")
        unc = ins.get("uncertainty_level")
        att = ins.get("attention_level")
        if dr in ("HIGH", "MEDIUM"):
            add(
                risk_type="PREDICTION_RISK",
                severity="HIGH" if dr == "HIGH" else "MEDIUM",
                explanation=(
                    f"El modelo de predicción identifica decline_risk={dr} para "
                    f"{_elabel(ins.get('entity'))} en el periodo {ins.get('period')} "
                    "según el comportamiento histórico analizado. "
                    "La proyección debe contrastarse con resultados reales."
                ),
                source="FASE_4B:data/prediction_intelligence/demo-retail/online_retail_II_prediction_intelligence.json",
                recommendation=(ins.get("recommendations") or [None])[0],
                source_record=ins.get("insight_id"),
                field="decline_risk",
                value={"entity": ins.get("entity"), "decline_risk": dr,
                       "trend": ins.get("trend"), "confidence_score": ins.get("confidence_score")},
            )
        elif unc == "HIGH" and att in ("IMPORTANT", "URGENT"):
            add(
                risk_type="PREDICTION_RISK",
                severity="MEDIUM",
                explanation=(
                    f"La predicción para {_elabel(ins.get('entity'))} en {ins.get('period')} "
                    f"presenta incertidumbre HIGH (intervalo amplio) con attention {att}. "
                    "La estimación debe utilizarse con cautela para planificación."
                ),
                source="FASE_4B:data/prediction_intelligence/demo-retail/online_retail_II_prediction_intelligence.json",
                recommendation=(ins.get("recommendations") or [None])[0],
                source_record=ins.get("insight_id"),
                field="uncertainty_level",
                value={"entity": ins.get("entity"), "uncertainty_level": unc,
                       "attention_level": att},
            )

    # 3) Calidad de datos 1B
    deductions = (profile.get("data_quality", {}) or {}).get("deductions", []) or []
    for d in deductions:
        points = d.get("points", 0)
        add(
            risk_type="DATA_QUALITY_RISK",
            severity="MEDIUM" if points >= 5 else "LOW",
            explanation=(
                f"El Data Quality Score (FASE 1B) dedujo {points} puntos por "
                f"{d.get('component')}: {d.get('detail')} "
                "Los hallazgos que dependan de estos registros deben interpretarse con cautela."
            ),
            source="FASE_1B:data/profiles/demo-retail/online_retail_II_full_profile.json",
            recommendation="Revisar los registros afectados antes de basar decisiones en ellos.",
            source_record=d.get("component"),
            field="data_quality_deduction",
            value=d,
        )

    return risks
