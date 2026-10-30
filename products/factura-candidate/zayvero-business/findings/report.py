"""
ZAYVERO BUSINESS — FASE 2B.

`report`: ensambla el BusinessFindingReport.

ESTRUCTURA BusinessFinding (JSON-serializable):
    finding_id, type, title, severity, business_priority, impact_score,
    confidence_score, evidence_quality, entity, period, observed_value,
    expected_value, difference (absolute_difference), percentage_difference,
    statistical_explanation, business_explanation, recommended_review,
    data_quality_warning, requires_review, contributing_detectors,
    contributing_anomaly_ids, n_merged, trace

ESTRUCTURA del reporte:
    report_metadata, summary, top_findings (presentación, top 100),
    findings (TODOS los calculados), methods, trace

Flujo:
    1. Cargar AnomalyReport (JSON de FASE 2A) o dict.
    2. Deduplicar anomalías del mismo evento.
    3. Calcular P95 de |diferencias| (dos pasadas) para el impact_score.
    4. Construir cada BusinessFinding.
    5. Ordenar por (business_priority, impact_score, confidence_score).
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
from typing import Any

from anomalies import detect_anomalies as _detect_anomalies
from findings import dedup as _dedup
from findings import explain as _explain
from findings import impact as _impact
from findings import priority as _priority
from findings.priority import PRIORITY_ORDER, PRIORITY_WEIGHT
from profiling.trace import to_jsonable

ENGINE_VERSION = "2B"
DATASET_LABEL = "Demo Dataset — UCI Online Retail II"
TOP_FINDINGS_LIMIT = 100  # límite SOLO de presentación

METHODS_DOC = {
    "phase_2a_vs_2b": {
        "fase_2a": "Anomalía estadística: ¿qué comportamiento es inusual? "
        "(z robusto, IQR, MAD).",
        "fase_2b": "Hallazgo empresarial: ¿qué tan importante podría ser "
        "para el negocio? (impact_score, business_priority, evidencia).",
        "note": "Una anomalía estadística NO es automáticamente un "
        "riesgo empresarial.",
    },
    "impact_score": {
        "formula": "35*M + 25*D + 20*C + 10*S + 10*Q (0-100)",
        "components": {
            "M": "magnitud monetaria: log10(1+|diff|)/log10(1+P95), "
            "P95 guiado por los datos",
            "D": "desviación estadística: min(dev/12, 1)",
            "C": "confianza: confidence_score/100 (FASE 2A)",
            "S": "alcance: log10(1+n_affected)/log10(1+50)",
            "Q": "calidad: 1 - null_fraction",
        },
        "note": "No se usa solo el z-score.",
    },
    "business_priority": {
        "levels": PRIORITY_ORDER,
        "rules": "URGENT: impact>=75 y conf>=70 (evidencia no LOW), con "
        "tope por ranking: máximo 25 URGENT (los más altos por impacto); "
        "el resto de candidatos pasa a IMPORTANT. Así URGENT es "
        "excepcional y accionable, igual que el CRITICAL de FASE 2A. "
        "IMPORTANT: impact>=50 y conf>=60. REVIEW: impact>=25 o "
        "requires_review, o evidencia LOW con desviación extrema. "
        "MONITOR: resto. Con evidencia LOW nunca URGENT/IMPORTANT.",
    },
    "dedup": {
        "rule": "Misma (tipo_hallazgo, entidad) = mismo evento → un solo "
        "hallazgo. Se guardan los detectores y anomalías contribuyentes. "
        "La diferencia total es la suma de |diferencias| (transacciones "
        "distintas).",
    },
    "monetary_language": {
        "rule": "La diferencia monetaria se llama 'desviación respecto al "
        "comportamiento esperado'. NUNCA 'pérdida', 'ganancia' ni "
        "'dinero perdido'.",
    },
    "finding_types": {
        "PRODUCT_ANOMALY": "ventas inusuales de un producto vs su historial",
        "PRICE_ANOMALY": "precios atípicos vs rango habitual del producto",
        "QUANTITY_ANOMALY": "cantidades extraordinarias vs rango habitual",
        "CUSTOMER_ANOMALY": "comportamiento de compra inusual del cliente",
        "TEMPORAL_ANOMALY": "período con ventas fuera de patrón",
        "SALES_ANOMALY": "reservado: ventas agregadas (sin uso actual)",
    },
}


def _load_anomaly_report(source: str | dict[str, Any]) -> dict[str, Any]:
    if isinstance(source, dict):
        return source
    with open(source, encoding="utf-8") as f:
        return json.load(f)


def _entity_label(entity: Any) -> str:
    if isinstance(entity, dict):
        return str(entity.get("label") or entity.get("id") or "?")
    return str(entity)


def build_findings(
    anomalies_source: str | dict[str, Any],
    company_id: str,
    top_limit: int = TOP_FINDINGS_LIMIT,
) -> dict[str, Any]:
    """
    Construye el BusinessFindingReport desde el AnomalyReport de FASE 2A.
    anomalies_source: ruta al JSON de anomalías o dict ya cargado.
    """
    if not company_id:
        raise ValueError("company_id es obligatorio (multiempresa).")
    t0 = _dt.datetime.now(_dt.timezone.utc)

    areport = _load_anomaly_report(anomalies_source)
    anomalies = areport.get("anomalies", [])
    src_label = (
        (areport.get("report_metadata") or {}).get("dataset_label")
        or DATASET_LABEL
    )

    # 1. Deduplicar
    groups = _dedup.dedup_anomalies(anomalies)
    n_anomalies_in = len(anomalies)
    n_merged_total = sum(g["n_merged"] for g in groups)

    # 2. P95 de |diferencias totales| (dos pasadas, guiado por datos)
    p95 = _impact.compute_p95([g["total_difference"] for g in groups])

    # 3. Construir hallazgos
    findings: list[dict[str, Any]] = []
    for g in groups:
        primary = g["primary"]
        ftype = g["finding_type"]
        entity = primary.get("entity") or {}
        label = _entity_label(entity)
        period = primary.get("period")

        money = _impact.monetary_impact(
            primary.get("observed"),
            primary.get("expected"),
            g["total_difference"]
            if g["n_merged"] > 1
            else primary.get("difference"),
        )
        iscore, icomp = _impact.impact_score(
            primary,
            p95,
            total_difference=g["total_difference"],
            n_merged=g["n_merged"],
        )
        eq, eq_expl = _priority.evidence_quality(primary)
        req, req_reason = _priority.requires_review_flag(primary, ftype)
        dev = abs(float(primary.get("deviation") or 0.0))
        conf = int(primary.get("confidence_score") or 0)
        prio = _priority.business_priority(
            iscore, conf, eq, dev, req
        )
        dq_warn = _priority.data_quality_warning(primary)

        trace = dict(primary.get("trace") or {})
        trace["fase_2b"] = {
            "impact_formula": icomp["formula"],
            "p95_reference": icomp["p95_reference"],
            "evidence_quality": eq,
            "evidence_quality_explanation": eq_expl,
            "priority_rule": METHODS_DOC["business_priority"]["rules"],
            "dedup": f"{g['n_merged']} anomalía(s) 2A agrupadas",
            "monetary_label": money["label"],
        }

        findings.append(
            {
                "type": ftype,
                "title": _explain.build_title(ftype, label),
                "severity": primary.get("severity"),
                "business_priority": prio,
                "impact_score": iscore,
                "impact_components": icomp,
                "confidence_score": conf,
                "evidence_quality": eq,
                "entity": entity,
                "period": period,
                "observed_value": money["observed_value"],
                "expected_value": money["expected_value"],
                "difference": money["absolute_difference"],
                "percentage_difference": money["percentage_difference"],
                "statistical_explanation": primary.get("explanation"),
                "business_explanation": _explain.business_explanation(ftype),
                "recommended_review": _explain.recommended_review(
                    ftype, label, period, g["n_merged"]
                ),
                "data_quality_warning": dq_warn,
                "requires_review": bool(req),
                "requires_review_reason": req_reason,
                "contributing_detectors": g["contributing_detectors"],
                "contributing_anomaly_ids": g["contributing_anomaly_ids"],
                "n_merged": g["n_merged"],
                "_sort_key": (
                    PRIORITY_WEIGHT.get(prio, 0),
                    iscore,
                    conf,
                ),
                "trace": trace,
            }
        )

    # 4. Ordenar: prioridad > impacto > confianza
    findings.sort(key=lambda f: f["_sort_key"], reverse=True)

    # Tope URGENT por ranking (máx. 25): URGENT debe ser excepcional y
    # accionable. Los candidatos URGENT que no entran en el top 25 por
    # (impacto, confianza) pasan a IMPORTANT.
    _urgent_slots = 25
    _urgent_seen = 0
    for f in findings:
        if f["business_priority"] != "URGENT":
            continue
        _urgent_seen += 1
        if _urgent_seen > _urgent_slots:
            f["business_priority"] = "IMPORTANT"
            f["_sort_key"] = (
                PRIORITY_WEIGHT.get("IMPORTANT", 0),
                f["impact_score"],
                f["confidence_score"],
            )
    # Reordenar tras el ajuste
    findings.sort(key=lambda f: f["_sort_key"], reverse=True)
    for i, f in enumerate(findings, start=1):
        f["finding_id"] = f"FND-{i:06d}"
        f["priority_rank"] = i
        del f["_sort_key"]

    # 5. Resumen
    by_priority: dict[str, int] = {p: 0 for p in PRIORITY_ORDER}
    by_type: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    imp_sum = 0.0
    conf_sum = 0
    for f in findings:
        by_priority[f["business_priority"]] += 1
        by_type[f["type"]] = by_type.get(f["type"], 0) + 1
        by_severity[f["severity"]] = by_severity.get(f["severity"], 0) + 1
        imp_sum += f["impact_score"]
        conf_sum += f["confidence_score"]
    n = len(findings)

    t1 = _dt.datetime.now(_dt.timezone.utc)
    elapsed = (t1 - t0).total_seconds()

    report = {
        "report_metadata": {
            "engine": f"zayvero-business-findings-{ENGINE_VERSION}",
            "dataset_label": src_label,
            "company_id": company_id,
            "generated_at_utc": t1.isoformat(),
            "elapsed_seconds": round(elapsed, 1),
            "anomalies_consumed": n_anomalies_in,
        },
        "summary": {
            "total_findings": n,
            "anomalies_deduped": n_anomalies_in - n,
            "dedup_note": f"{n_anomalies_in} anomalías 2A → {n} hallazgos "
            "(misma entidad+tipo = mismo evento)",
            "by_priority": by_priority,
            "by_type": by_type,
            "by_severity": by_severity,
            "avg_impact_score": round(imp_sum / n, 1) if n else 0.0,
            "avg_confidence": round(conf_sum / n, 1) if n else 0.0,
            "note": "Un hallazgo es una desviación priorizada por su "
            "importancia potencial para el negocio; NO implica fraude, "
            "robo, pérdida ni error humano.",
        },
        "top_findings": findings[:top_limit],
        "findings": findings,
        "statistics": {
            "p95_difference_reference": round(p95, 2),
            "top_limit_presentation_only": top_limit,
            "all_findings_computed": n,
            "elapsed_seconds": round(elapsed, 1),
        },
        "methods": METHODS_DOC,
        "trace": {
            "dataset": src_label,
            "company_id": company_id,
            "source": "AnomalyReport FASE 2A",
            "notes": "Cada hallazgo conserva el trace de su anomalía 2A "
            "más los parámetros de FASE 2B (fórmula de impacto, calidad "
            "de evidencia, regla de prioridad, deduplicación).",
        },
    }
    return to_jsonable(report)


def build_from_parquet(
    parquet_path: str,
    company_id: str,
    max_anomalies: int = 100000,
    top_limit: int = TOP_FINDINGS_LIMIT,
) -> dict[str, Any]:
    """
    Atajo: ejecuta FASE 2A sobre el Parquet (sin límite práctico de
    anomalías) y luego construye los hallazgos. Así FASE 2B calcula
    sobre TODAS las anomalías disponibles, no solo las 1,000 del
    reporte guardado.
    """
    areport = _detect_anomalies(
        parquet_path, company_id, max_anomalies=max_anomalies
    )
    return build_findings(areport, company_id, top_limit=top_limit)


def save_report(
    report: dict[str, Any], company_id: str, base_name: str
) -> str:
    """Guarda el BusinessFindingReport como JSON en data/findings/<company_id>/."""
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = os.path.join(base_dir, "data", "findings", company_id)
    os.makedirs(out_dir, exist_ok=True)
    out_path = os.path.join(out_dir, f"{base_name}_findings.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    return out_path
