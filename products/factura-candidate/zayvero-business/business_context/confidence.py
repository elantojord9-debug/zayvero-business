"""FASE 5A — context_confidence_score (0–100).

Representa qué tan completo y confiable es el CONTEXTO empresarial
disponible, NO la confianza de una sola predicción.

Fórmula documentada (pesos suman 1.0):

    score = 0.40 * data_quality_score
          + 0.25 * evidence_coverage      # % hallazgos con evidencia HIGH/MEDIUM
          + 0.20 * prediction_quality    # % insights con forecast_quality HIGH/MODERATE
                                         #   (sobre los que tienen predicción numérica)
          + 0.15 * validation_available  # 100 si validated > 0, 0 si no

Cada componente está en 0–100. Si falta un input, el componente se
considera 0 (reduce la confianza, no la infla).
"""

from __future__ import annotations


def compute_context_confidence(profile, findings_report, pi_report, validation_report) -> dict:
    dq = (profile.get("data_quality", {}) or {}).get("score") or 0

    findings = findings_report.get("findings", []) or []
    n_f = len(findings)
    ev_cov = (100.0 * sum(1 for f in findings if f.get("evidence_quality") in ("HIGH", "MEDIUM")) / n_f) if n_f else 0.0

    insights = pi_report.get("prediction_insights", []) or []
    numeric = [i for i in insights if i.get("prediction_status") == "OK"]
    pred_q = (100.0 * sum(1 for i in numeric if i.get("forecast_quality") in ("HIGH", "MODERATE")) / len(numeric)) if numeric else 0.0

    validated = (validation_report.get("summary", {}) or {}).get("validated", 0) or 0
    val_avail = 100.0 if validated > 0 else 0.0

    score = 0.40 * dq + 0.25 * ev_cov + 0.20 * pred_q + 0.15 * val_avail
    score = round(max(0.0, min(100.0, score)), 1)

    return {
        "context_confidence_score": score,
        "components": {
            "data_quality_score": {"value": round(dq, 1), "weight": 0.40},
            "evidence_coverage": {"value": round(ev_cov, 1), "weight": 0.25},
            "prediction_quality": {"value": round(pred_q, 1), "weight": 0.20},
            "validation_available": {"value": val_avail, "weight": 0.15},
        },
        "formula": ("0.40*data_quality_score + 0.25*evidence_coverage "
                    "+ 0.20*prediction_quality + 0.15*validation_available"),
        "interpretation": (
            "El contexto empresarial disponible tiene una completitud/confianza de "
            f"{score}/100. Un valor bajo indica que faltan datos importantes "
            "(calidad, evidencia, predicciones de calidad o validación real)."
        ),
    }
