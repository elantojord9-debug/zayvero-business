"""FASE 5A — limitations: consolidación automática de limitaciones.

Se recogen limitaciones existentes de 1B (deducciones de calidad), 2C
(hallazgos con contexto insuficiente / evidencia LOW), 4B (baja calidad y
alta incertidumbre) y 4C (sin validación todavía). Las agregadas por conteo
evitan miles de entradas idénticas. Nunca se ocultan limitaciones.
"""

from __future__ import annotations


def build_limitations(profile, context_report, pi_report, validation_report, evidence) -> list[dict]:
    lims: list[dict] = []
    counter = 0

    def add(kind, text, source, source_record, field, value):
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
        lims.append(
            {
                "kind": "LIMITATION",
                "limitation_id": f"LIM-{counter:04d}",
                "limitation_type": kind,
                "text": text,
                "source": source,
                "evidence_id": ev_id,
                "trace": {"rule": "consolidación automática (FASE 5A limitations.py)"},
            }
        )

    # 1B: deducciones de calidad
    for d in (profile.get("data_quality", {}) or {}).get("deductions", []) or []:
        add("DATA_QUALITY",
            f"El Data Quality Score es {(profile.get('data_quality', {}) or {}).get('score')}/100: "
            f"se dedujeron {d.get('points')} puntos por {d.get('component')} ({d.get('detail')}). "
            "Los hallazgos que dependan de estos registros tienen evidencia limitada.",
            "FASE_1B:data/profiles/demo-retail/online_retail_II_full_profile.json",
            d.get("component"), "data_quality_deduction", d)

    # 2C: contexto insuficiente / evidencia LOW (agregado por conteo)
    ctx = context_report.get("context_findings", []) or []
    n_insuf = sum(1 for c in ctx if c.get("context_status") == "insufficient_context")
    n_low = sum(1 for c in ctx if c.get("evidence_quality") == "LOW")
    if n_insuf:
        add("INSUFFICIENT_CONTEXT",
            f"{n_insuf} hallazgos no pudieron contextualizarse por falta de historial suficiente. "
            "ZAYVERO no dispone de suficiente información para esos casos.",
            "FASE_2C:data/context/demo-retail/online_retail_II_full_context.json",
            None, "context_status_count", {"insufficient_context": n_insuf})
    if n_low:
        add("LOW_EVIDENCE",
            f"{n_low} hallazgos tienen evidence_quality LOW: sus conclusiones deben "
            "interpretarse con cautela y su prioridad está acotada.",
            "FASE_2C:data/context/demo-retail/online_retail_II_full_context.json",
            None, "evidence_quality_count", {"low": n_low})

    # 4B: calidad e incertidumbre
    pi_sum = pi_report.get("summary", {}) or {}
    if pi_sum.get("low_quality"):
        add("LOW_FORECAST_QUALITY",
            f"{pi_sum.get('low_quality')} predicciones tienen forecast_quality LOW y "
            f"{pi_sum.get('high_uncertainty')} presentan incertidumbre HIGH. "
            "Deben utilizarse como referencia para planificación, no como cifras garantizadas.",
            "FASE_4B:data/prediction_intelligence/demo-retail/online_retail_II_prediction_intelligence.json",
            None, "quality_uncertainty", {"low_quality": pi_sum.get("low_quality"),
                                          "high_uncertainty": pi_sum.get("high_uncertainty")})
    if pi_sum.get("insufficient"):
        add("INSUFFICIENT_PREDICTION_DATA",
            f"{pi_sum.get('insufficient')} predicciones quedaron en INSUFFICIENT_DATA: "
            "no existe historial suficiente para estimarlas.",
            "FASE_4B:data/prediction_intelligence/demo-retail/online_retail_II_prediction_intelligence.json",
            None, "insufficient", pi_sum.get("insufficient"))

    # 4C: sin validación todavía
    v_sum = validation_report.get("summary", {}) or {}
    if v_sum.get("validated", 0) == 0:
        add("NO_VALIDATION_YET",
            "No existen suficientes resultados reales posteriores para evaluar todavía "
            "el desempeño de las predicciones. No se afirma que sean correctas ni incorrectas.",
            "FASE_4C:data/prediction_validation/demo-retail/online_retail_II_prediction_validation.json",
            None, "validated", 0)

    # stockout no disponible
    add("NO_STOCKOUT",
        "El dataset no contiene información de inventario confiable: el riesgo de "
        "agotamiento (stockout) no puede estimarse (NOT_AVAILABLE).",
        "FASE_4A:data/predictions/demo-retail/online_retail_II_full_predictions.json",
        None, "stockout_status", "NOT_AVAILABLE")

    return lims
