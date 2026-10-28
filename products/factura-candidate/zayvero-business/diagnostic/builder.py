"""FASE 7B — Builder determinista del Diagnóstico Ejecutivo ZAYVERO.

El diagnóstico NO crea inteligencia nueva: consume únicamente resultados
existentes (5A, 4B, 4C, findings 2B/2C vía el contexto) y los ensambla en
un documento ejecutivo trazable.

Reglas:
- Sin recálculo de impact_score, priority, confidence ni métricas.
- Sin algoritmos nuevos de detección ni modelos predictivos.
- Lenguaje empresarial, sin alarmismo, sin causalidad afirmada.
- Toda afirmación importante rastreable a un evidence ID real.
- Determinista: mismos inputs → mismo diagnostic_id y contenido
  (generated_at es solo metadata).
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .models import (
    DIAGNOSTIC_VERSION,
    AVAILABLE,
    LIMITED,
    INSUFFICIENT,
    STATUS_NOTES,
    SUBTITLE,
    OPPORTUNITY_DISCLAIMER,
    VALIDATION_PENDING_LABEL,
    VALIDATION_NOT_AVAILABLE_LABEL,
    VALIDATION_INSUFFICIENT_LABEL,
    SUGGESTED_ADVISOR_QUESTIONS,
    PRIORITY_RANK,
    SEVERITY_RANK,
)


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _diagnostic_id(company_id: str, context_id: str) -> str:
    seed = f"{company_id}|{context_id}|{DIAGNOSTIC_VERSION}"
    return "DG-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12]


def _priority_rank(p: str) -> int:
    return PRIORITY_RANK.get(str(p or "").upper(), 99)


def _severity_rank(s: str) -> int:
    return SEVERITY_RANK.get(str(s or "").upper(), 99)


def _collect_evidence(*sections: List[Dict[str, Any]]) -> List[str]:
    """Recolecta evidence IDs reales de las secciones, sin duplicados."""
    seen: set[str] = set()
    ordered: List[str] = []
    for section in sections:
        for item in section or []:
            eid = item.get("evidence_id") if isinstance(item, dict) else None
            if eid and eid not in seen:
                seen.add(eid)
                ordered.append(eid)
    return ordered


def _skeleton(company_id: str, company_name: str, is_demo: bool,
              dataset_state: str, context_id: str = "") -> Dict[str, Any]:
    """Diagnóstico vacío para estados INSUFFICIENT: no fabrica conclusiones."""
    return {
        "diagnostic_id": _diagnostic_id(company_id, context_id or "no-context"),
        "version": DIAGNOSTIC_VERSION,
        "company_id": company_id,
        "company_name": company_name,
        "is_demo": bool(is_demo),
        "generated_at": utcnow_iso(),
        "diagnostic_status": INSUFFICIENT,
        "status_note": STATUS_NOTES[INSUFFICIENT],
        "subtitle": SUBTITLE,
        "data_period": None,
        "data_quality": {"score": None},
        "context_confidence": {"score": None},
        "executive_summary": {
            "text": ("Todavía no hay datos suficientes para generar el "
                     "Diagnóstico Ejecutivo. Agrega un reporte de tu empresa "
                     "en la sección \"Mi empresa\" y ZAYVERO lo analizará."),
            "bullets": [],
        },
        "business_status": {},
        "priority_attention": [],
        "risks": [],
        "opportunities": [],
        "trends": {"observed": [], "projected": []},
        "predictions": [],
        "recommendations": [],
        "limitations": [],
        "next_steps": [
            {
                "step": 1,
                "title": "Agregar datos de tu empresa",
                "detail": ("Sube un reporte (CSV o XLSX) en la sección "
                           "\"Mi empresa\" para que ZAYVERO pueda analizarlo."),
                "ref_id": None,
            },
        ],
        "suggested_advisor_questions": list(SUGGESTED_ADVISOR_QUESTIONS),
        "evidence_used": [],
        "trace": {
            "context_id": context_id or None,
            "dataset_state": dataset_state,
            "rules": ("diagnostic assembly 7b-1.0.0: sin datos suficientes; "
                      "no se generaron conclusiones"),
            "engine": f"diagnostic-builder {DIAGNOSTIC_VERSION}",
            "generated_at": utcnow_iso(),
        },
    }


def _validation_label(status: str) -> str:
    s = str(status or "").upper()
    if s == "VALIDATED":
        return "Validada"
    if s == "NOT_AVAILABLE":
        return VALIDATION_NOT_AVAILABLE_LABEL
    if s == "INVALID":
        return "No válida"
    return VALIDATION_PENDING_LABEL


def build_diagnostic(
    ctx5a: Optional[Dict[str, Any]],
    intel4b: Optional[Dict[str, Any]],
    val4c: Optional[Dict[str, Any]],
    *,
    company_id: str,
    company_name: str,
    is_demo: bool,
    dataset_state: str,
    dataset_info: Optional[Dict[str, Any]] = None,
    source_files: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """Construye el BusinessDiagnostic de forma determinista.

    - ctx5a: BusinessIntelligenceContext (5A) o None si no hay datos listos.
    - intel4b: Prediction Intelligence (4B) o None.
    - val4c: Prediction Validation (4C) o None.
    - company_id/company_name/is_demo: de la sesión autenticada.
    - dataset_state: estado del dataset activo (READY, NO_DATA, ...).
    - dataset_info: registros, periodo, fuente, actualización (datos reales).
    """
    if dataset_state != "READY" or not ctx5a:
        return _skeleton(company_id, company_name, is_demo, dataset_state)

    identity = ctx5a.get("identity") or {}
    context_id = identity.get("context_id") or ""
    attention = ctx5a.get("attention_summary") or {}
    signals = attention.get("signals") or {}
    confidence = ctx5a.get("context_confidence") or {}
    snapshot = ctx5a.get("business_snapshot") or {}
    bperiod = snapshot.get("business_period") or {}

    findings = ctx5a.get("critical_findings") or []
    risks = ctx5a.get("key_risks") or []
    opportunities = ctx5a.get("key_opportunities") or []
    trends = ctx5a.get("key_trends") or []
    recommendations = ctx5a.get("recommendations") or []
    limitations = ctx5a.get("limitations") or []

    insights = (intel4b or {}).get("prediction_insights") or []
    pi_summary = ((intel4b or {}).get("summary")) or {}
    val_summary = ((val4c or {}).get("summary")) or {}
    val_by_pred: Dict[str, Dict[str, Any]] = {}
    for v in (val4c or {}).get("validations") or []:
        pid = v.get("prediction_id")
        if pid:
            val_by_pred[pid] = v

    n_urgent = int(signals.get("urgent_findings") or 0)
    n_important = int(signals.get("important_findings") or 0)
    n_opp = len(opportunities)
    n_pred = int(pi_summary.get("total_predictions") or 0)
    has_evidence = bool(findings) or bool(insights)

    # ---- estado del diagnóstico -------------------------------------
    if not has_evidence:
        status = LIMITED
    else:
        status = AVAILABLE

    diag_id = _diagnostic_id(company_id, context_id)
    generated_at = utcnow_iso()

    # ---- A. Resumen ejecutivo ----------------------------------------
    validated = val_summary.get("validated")
    if validated == 0 or validated is None:
        validation_sentence = (
            "Las predicciones aún no cuentan con resultados reales "
            "posteriores para evaluar su desempeño."
        )
    else:
        validation_sentence = (
            f"Se validaron {validated} predicciones con resultados reales."
        )
    summary_text = (
        f"ZAYVERO identificó {n_urgent} hallazgos urgentes y {n_important} "
        f"hallazgos importantes que podrían requerir revisión. También "
        f"existen {n_opp} posibles oportunidades y {n_pred} predicciones "
        f"disponibles. {validation_sentence}"
    )
    summary_bullets = [
        f"Nivel de atención actual: {attention.get('attention_level') or '—'}.",
        (f"Confianza del análisis: "
         f"{confidence.get('context_confidence_score') if confidence.get('context_confidence_score') is not None else '—'}/100."),
        (f"Calidad de los datos: "
         f"{signals.get('data_quality_score') if signals.get('data_quality_score') is not None else '—'}/100."),
    ]
    if status == LIMITED:
        summary_bullets.append(
            "El diagnóstico está limitado por la información disponible.")

    # ---- B. Estado de la empresa -------------------------------------
    business_status = {
        "attention_level": attention.get("attention_level"),
        "attention_rule": attention.get("rule_triggered"),
        "data_quality_score": signals.get("data_quality_score"),
        "context_confidence_score": confidence.get("context_confidence_score"),
        "context_confidence_formula": confidence.get("formula"),
        "urgent_findings": n_urgent,
        "important_findings": n_important,
        "review_findings": int(signals.get("review_findings") or 0),
        "opportunities": n_opp,
        "predictions": n_pred,
        "high_severity_risks": int(signals.get("high_severity_risks") or 0),
        "high_decline_risk_predictions": int(
            signals.get("high_decline_risk_predictions") or 0),
    }

    # ---- C. Lo que requiere atención ----------------------------------
    ordered_findings = sorted(
        findings,
        key=lambda f: (_priority_rank(f.get("business_priority")),
                       -(f.get("impact_score") or 0)),
    )
    n_priority_total = sum(
        1 for f in findings
        if str(f.get("business_priority") or "").upper() in ("URGENT", "IMPORTANT")
    )
    priority_attention = []
    for f in ordered_findings:
        if str(f.get("business_priority") or "").upper() not in ("URGENT", "IMPORTANT"):
            continue
        entity = f.get("entity") or {}
        priority_attention.append({
            "finding_id": f.get("finding_id"),
            "title": f.get("title"),
            "business_priority": f.get("business_priority"),
            "impact_score": f.get("impact_score"),
            "confidence_score": f.get("confidence_score"),
            "evidence_quality": f.get("evidence_quality"),
            "entity": entity.get("label") if isinstance(entity, dict) else entity,
            "period": f.get("period"),
            "summary": f.get("business_explanation"),
            "evidence_id": f.get("evidence_id"),
        })
        if len(priority_attention) >= 8:
            break

    # ---- D. Riesgos a revisar -----------------------------------------
    ordered_risks = sorted(risks, key=lambda r: _severity_rank(r.get("severity")))
    risks_view = []
    for r in ordered_risks[:10]:
        risks_view.append({
            "risk_id": r.get("risk_id"),
            "risk_type": r.get("risk_type"),
            "severity": r.get("severity"),
            "explanation": r.get("explanation"),
            "recommendation": r.get("recommendation"),
            "evidence": r.get("evidence"),
            "evidence_id": r.get("evidence_id"),
            "source": r.get("source"),
        })

    # ---- E. Posibles oportunidades ------------------------------------
    opps_view = []
    for o in opportunities[:10]:
        opps_view.append({
            "opportunity_id": o.get("opportunity_id"),
            "type": o.get("type"),
            "importance": o.get("importance"),
            "label": "Posible oportunidad",
            "disclaimer": OPPORTUNITY_DISCLAIMER,
            "explanation": o.get("explanation"),
            "evidence": o.get("evidence"),
            "recommendation": o.get("recommendation"),
            "evidence_id": o.get("evidence_id"),
            "source": o.get("source"),
        })

    # ---- F. Tendencias --------------------------------------------------
    observed = [t for t in trends if t.get("trend_type") == "OBSERVED_TREND"]
    projected = [t for t in trends if t.get("trend_type") != "OBSERVED_TREND"]

    def _trend_view(t):
        return {
            "trend_id": t.get("trend_id"),
            "trend_type": t.get("trend_type"),
            "direction": t.get("direction"),
            "period": t.get("period"),
            "interpretation": t.get("interpretation"),
            "confidence": t.get("confidence"),
            "evidence_id": t.get("evidence_id"),
            "source": t.get("source"),
            "note": ("Las proyecciones son estimaciones basadas en el "
                     "historial, no certezas."
                     if t.get("trend_type") != "OBSERVED_TREND"
                     else "Descripción del pasado, no una proyección."),
        }

    # ---- G. Predicciones -------------------------------------------------
    attention_rank = {"URGENT": 0, "IMPORTANT": 1, "REVIEW": 2, "MONITOR": 3}
    ordered_ins = sorted(
        insights,
        key=lambda i: (attention_rank.get(str(i.get("attention_level") or "").upper(), 99),
                       -(i.get("attention_score") or 0)),
    )
    preds_view = []
    for ins in ordered_ins[:8]:
        if str(ins.get("prediction_status") or "").upper() == "INSUFFICIENT_DATA":
            validation_label = VALIDATION_INSUFFICIENT_LABEL
        else:
            v = val_by_pred.get(ins.get("prediction_id")) or {}
            validation_label = _validation_label(v.get("validation_status"))
        entity = ins.get("entity") or {}
        preds_view.append({
            "insight_id": ins.get("insight_id"),
            "prediction_id": ins.get("prediction_id"),
            "prediction_type": ins.get("prediction_type"),
            "entity": entity.get("label") if isinstance(entity, dict) else entity,
            "period": ins.get("period"),
            "forecast_horizon": ins.get("forecast_horizon"),
            "predicted_value": ins.get("predicted_value"),
            "lower_bound": ins.get("lower_bound"),
            "upper_bound": ins.get("upper_bound"),
            "confidence_score": ins.get("confidence_score"),
            "forecast_quality": ins.get("forecast_quality"),
            "uncertainty_level": ins.get("uncertainty_level"),
            "trend": ins.get("trend"),
            "forecast_direction": ins.get("forecast_direction"),
            "trend_discrepancy_note": ins.get("trend_discrepancy_note"),
            "decline_risk": ins.get("decline_risk"),
            "method": ins.get("method"),
            "business_interpretation": ins.get("business_interpretation"),
            "limitations": ins.get("limitations") or [],
            "validation_status": (val_by_pred.get(ins.get("prediction_id")) or {}).get(
                "validation_status", "PENDING"),
            "validation_label": validation_label,
            "note": ("Estimación basada en historial, no un hecho."
                     if str(ins.get("prediction_status") or "").upper()
                     != "INSUFFICIENT_DATA" else "Datos insuficientes."),
        })

    # ---- H. Recomendaciones prioritarias ---------------------------------
    prio_rank = {"URGENT": 0, "HIGH": 0, "IMPORTANT": 1, "MEDIUM": 2,
                 "REVIEW": 3, "LOW": 4}
    ordered_recs = sorted(
        recommendations,
        key=lambda r: (prio_rank.get(str(r.get("priority") or "").upper(), 99),
                       r.get("recommendation_id") or ""),
    )
    recs_view = []
    for r in ordered_recs[:12]:
        recs_view.append({
            "recommendation_id": r.get("recommendation_id"),
            "kind": "RECOMENDACIÓN EXISTENTE",
            "kind_code": "EXISTING_RECOMMENDATION",
            "priority": r.get("priority"),
            "text": r.get("text"),
            "source": r.get("source"),
            "related_finding": r.get("related_finding"),
            "related_prediction": r.get("related_prediction"),
            "evidence_id": r.get("evidence_id"),
            "note": "Sugerencia de revisión; no ejecuta ninguna acción.",
        })

    # ---- I. Calidad y cobertura de los datos -----------------------------
    dq_limitations = [l for l in limitations
                      if str(l.get("limitation_type") or "") == "DATA_QUALITY"]
    data_quality = {
        "score": signals.get("data_quality_score"),
        "period_start": bperiod.get("start"),
        "period_end": bperiod.get("end"),
        "row_count": (dataset_info or {}).get("row_count"),
        "source": (dataset_info or {}).get("source")
        or identity.get("dataset_label"),
        "updated_at": (dataset_info or {}).get("updated_at"),
        "warnings": [l.get("text") for l in dq_limitations],
        "note": "El análisis cubre el periodo disponible en los datos cargados.",
    }

    # ---- J. Limitaciones --------------------------------------------------
    lims_view = [{
        "limitation_id": l.get("limitation_id"),
        "limitation_type": l.get("limitation_type"),
        "text": l.get("text"),
        "evidence_id": l.get("evidence_id"),
        "source": l.get("source"),
    } for l in limitations]

    # ---- K. Próximos pasos sugeridos ---------------------------------------
    next_steps = []
    step = 1
    if priority_attention:
        top = priority_attention[0]
        next_steps.append({
            "step": step,
            "title": "Revisar hallazgos urgentes",
            "detail": (f"Comenzar por \"{top.get('title')}\" "
                       f"(prioridad {top.get('business_priority')}, impacto "
                       f"{top.get('impact_score')})."),
            "ref_id": top.get("finding_id"),
            "ref_kind": "finding",
        })
        step += 1
        if len(priority_attention) > 1:
            next_steps.append({
                "step": step,
                "title": "Investigar comportamientos inusuales relevantes",
                "detail": (f"Continuar con los siguientes "
                           f"{len(priority_attention) - 1} hallazgos prioritarios."),
                "ref_id": None,
                "ref_kind": None,
            })
            step += 1
    if opps_view:
        next_steps.append({
            "step": step,
            "title": "Revisar las posibles oportunidades identificadas",
            "detail": (f"Hay {n_opp} posibles oportunidades. "
                       "Ninguna es una garantía de resultado."),
            "ref_id": opps_view[0].get("opportunity_id"),
            "ref_kind": "opportunity",
        })
        step += 1
    next_steps.append({
        "step": step,
        "title": "Consultar al Advisor",
        "detail": ("Pregunta \"¿Qué debería revisar primero?\" para "
                   "profundizar con base en la evidencia."),
        "ref_id": None,
        "ref_kind": "advisor",
        "suggested_question": "¿Qué debería revisar primero?",
    })

    # ---- conteo de secciones (etiquetas "Mostrando N de M") ---------------
    # Cada sección puede mostrar una selección; los totales permiten al
    # frontend etiquetarlo honestamente sin mezclar categorías.
    section_counts = {
        "priority_attention": {"shown": len(priority_attention),
                               "total": n_priority_total},
        "risks": {"shown": len(risks_view), "total": len(risks)},
        "opportunities": {"shown": len(opps_view), "total": n_opp},
        "predictions": {"shown": len(preds_view), "total": n_pred},
        "recommendations": {"shown": len(recs_view),
                            "total": len(recommendations)},
        "trends_observed": {"shown": len(observed[:4]), "total": len(observed)},
        "trends_projected": {"shown": len(projected[:4]),
                             "total": len(projected)},
    }

    # ---- evidencia y trazabilidad -------------------------------------------
    evidence_used = _collect_evidence(
        priority_attention, risks_view, opps_view,
        [{"evidence_id": t.get("evidence_id")} for t in trends],
        recs_view, lims_view,
    )
    trace = {
        "context_id": context_id,
        "source_files": source_files or [],
        "source_modules": ["FASE_1B_1C", "FASE_2B", "FASE_2C", "FASE_4A",
                           "FASE_4B", "FASE_4C", "FASE_5A"],
        "rules": ("diagnostic assembly 7b-1.0.0: selección y orden "
                  "determinista sobre outputs existentes; sin recálculo de "
                  "impact_score, priority, confidence ni métricas; sin "
                  "algoritmos nuevos"),
        "engine": f"diagnostic-builder {DIAGNOSTIC_VERSION}",
        "generated_at": generated_at,
    }

    return {
        "diagnostic_id": diag_id,
        "version": DIAGNOSTIC_VERSION,
        "company_id": company_id,
        "company_name": company_name,
        "is_demo": bool(is_demo),
        "generated_at": generated_at,
        "diagnostic_status": status,
        "status_note": STATUS_NOTES[status],
        "subtitle": SUBTITLE,
        "data_period": {
            "start": bperiod.get("start"),
            "end": bperiod.get("end"),
            "days_with_activity": bperiod.get("days_with_activity"),
        },
        "data_quality": data_quality,
        "context_confidence": {
            "score": confidence.get("context_confidence_score"),
            "formula": confidence.get("formula"),
            "interpretation": confidence.get("interpretation"),
        },
        "executive_summary": {
            "text": summary_text,
            "bullets": summary_bullets,
        },
        "business_status": business_status,
        "priority_attention": priority_attention,
        "risks": risks_view,
        "opportunities": opps_view,
        "trends": {
            "observed": [_trend_view(t) for t in observed[:4]],
            "projected": [_trend_view(t) for t in projected[:4]],
        },
        "predictions": preds_view,
        "recommendations": recs_view,
        "limitations": lims_view,
        "section_counts": section_counts,
        "next_steps": next_steps,
        "suggested_advisor_questions": list(SUGGESTED_ADVISOR_QUESTIONS),
        "evidence_used": evidence_used,
        "trace": trace,
    }
