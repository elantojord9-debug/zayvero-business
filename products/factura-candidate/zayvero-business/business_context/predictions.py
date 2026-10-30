"""FASE 5A — prediction_intelligence y prediction_validation: resúmenes consumidos.

NO se recalcula nada: se consumen los summary reales de 4B y 4C.
Si 4C tiene validated == 0, se incluye exactamente la frase requerida.
"""

from __future__ import annotations

NO_VALIDATION_TEXT = (
    "No existen suficientes resultados reales posteriores para evaluar todavía "
    "el desempeño de las predicciones."
)


def build_prediction_summary(pi_report: dict, evidence) -> dict:
    summary = dict(pi_report.get("summary", {}) or {})
    ev_id = evidence.add(
        source_module="FASE_4B",
        source_file="data/prediction_intelligence/demo-retail/online_retail_II_prediction_intelligence.json",
        source_record=None,
        field="summary",
        value=summary,
        period=None,
        trace=None,
    )
    return {
        "kind": "PREDICTION",
        "source": "FASE_4B",
        "summary": summary,
        "evidence_id": ev_id,
        "note": "Resumen consumido de FASE 4B sin recalcular.",
    }


def build_validation_summary(validation_report: dict, evidence) -> dict:
    summary = dict(validation_report.get("summary", {}) or {})
    ev_id = evidence.add(
        source_module="FASE_4C",
        source_file="data/prediction_validation/demo-retail/online_retail_II_prediction_validation.json",
        source_record=None,
        field="summary",
        value=summary,
        period=None,
        trace=None,
    )
    note = None
    if summary.get("validated", 0) == 0:
        note = NO_VALIDATION_TEXT
    return {
        "kind": "OBSERVATION",
        "source": "FASE_4C",
        "summary": summary,
        "model_performance_status": validation_report.get("model_performance_status"),
        "validation_note": note,
        "evidence_id": ev_id,
        "note": "Resumen consumido de FASE 4C sin recalcular.",
    }
