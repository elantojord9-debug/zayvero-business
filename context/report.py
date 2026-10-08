"""
ZAYVERO BUSINESS — FASE 2C: ensamblador del BusinessContextFindingReport.

Consume Business Findings de FASE 2B + dataset Parquet normalizado y
produce BusinessContextFinding por cada hallazgo relevante.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import pandas as pd

from context import comparisons as cmp
from context import explanations as expl
from context import recommendations as reco
from context.engine import ContextEngine


def _parse_dt(v):
    if v is None:
        return None
    try:
        return pd.to_datetime(v)
    except Exception:
        return None


def _fmt_money(v) -> str:
    try:
        return f"£{float(v):,.2f}"
    except Exception:
        return "£0.00"


def _fact(text: str, source: str) -> dict:
    return {"kind": "FACT", "text": text, "source": source}


def _obs(text: str, basis: str) -> dict:
    return {"kind": "OBSERVATION", "text": text, "basis": basis}


def contextualize_finding(f: dict, eng: ContextEngine) -> dict:
    """Construye un BusinessContextFinding a partir de un Business Finding."""
    fid = f.get("finding_id")
    ftype = f.get("type", "")
    entity = f.get("entity", {}) or {}
    kind = entity.get("kind", "")
    eid = str(entity.get("id", ""))
    label = entity.get("label", eid)
    period = f.get("period", {}) or {}
    p_start = _parse_dt(period.get("start"))
    p_end = _parse_dt(period.get("end"))
    observed = f.get("observed_value")
    expected = f.get("expected_value")

    facts: list[dict] = []
    observations: list[dict] = []
    ctx: dict = {"finding_type": ftype, "entity_label": label}

    # ---- Hecho base heredado de 2B (verificable) ----
    facts.append(_fact(
        f"FASE 2B detectó: {f.get('title', '')} "
        f"(observado {_fmt_money(observed)} vs. esperado {_fmt_money(expected)}).",
        "Business Finding FASE 2B"))

    # ---- Contexto según tipo ----
    bda = {"before": {}, "during": {}, "after": {}, "has_after": False}
    event_rows = eng.df.iloc[0:0]
    threshold = 0.0

    if ftype == "PRODUCT_ANOMALY" and p_start is not None and p_end is not None:
        daily = eng.product_daily_series(eid)
        bda = eng.before_during_after(daily, p_start.normalize(),
                                      p_end.normalize())
        event_rows = eng.product_rows(eid)
        event_rows = event_rows[(event_rows["day"] >= p_start.normalize())
                                & (event_rows["day"] <= p_end.normalize())]
        threshold = (bda["during"]["avg_daily"] * 0.5
                     if bda["during"]["avg_daily"] > 0 else 0.0)
        depth = cmp.history_depth(len(daily))
        ctx["recurrence"] = cmp.recurrence_count(
            daily, threshold, p_start.normalize(), p_end.normalize())
        period_txt = f"{p_start.date()} → {p_end.date()}"
    elif ftype in ("PRICE_ANOMALY", "QUANTITY_ANOMALY"):
        trx_id = eng.extract_transaction_id(f.get("statistical_explanation"))
        ctx["transaction_id"] = trx_id
        event_rows = (eng.transaction_rows(trx_id) if trx_id
                       else eng.df.iloc[0:0])
        prod = eng.product_rows(eid)
        daily = eng.product_daily_series(eid)
        depth = cmp.history_depth(len(daily))
        # Ventana alrededor de la transacción para antes/después
        if not event_rows.empty:
            d0 = event_rows["day"].min()
            bda = eng.before_during_after(daily, d0, d0)
            period_txt = str(d0.date())
            ctx["recurrence"] = cmp.recurrence_count(daily, threshold or 1e9)
        else:
            period_txt = "el período analizado"
            ctx["recurrence"] = {"recurrence": "unknown", "n_similar": 0,
                                 "note": "No se localizó la transacción."}
    elif ftype == "CUSTOMER_ANOMALY":
        event_rows = eng.find_customer_event(eid, observed or 0)
        cust = eng.customer_rows(eid)
        n_trx = int(cust["Transaction"].nunique())
        depth = cmp.history_depth(n_trx, min_points=10)
        if not event_rows.empty:
            d0 = event_rows["day"].min()
            period_txt = str(d0.date())
            # Recurrencia: otras compras >= 50% del observado
            trx_rev = cust.groupby("Transaction", observed=True)["Revenue"].sum()
            others = trx_rev[trx_rev >= (observed or 0) * 0.5]
            n_other = int((others.index.to_numpy() != str(
                event_rows["Transaction"].iloc[0])).sum()) \
                if len(event_rows) else int(len(others))
            ctx["recurrence"] = {
                "recurrence": ("isolated" if n_other == 0
                               else "rarely_recurrent" if n_other <= 2
                               else "recurrent"),
                "n_similar": n_other,
                "note": f"{n_other} compra(s) adicional(es) de magnitud similar."}
            # Antes/después del cliente
            before_c = cust[cust["day"] < d0]
            after_c = cust[cust["day"] > d0]
            bda = {"before": {"revenue": float(before_c["Revenue"].sum()),
                              "n_trx": int(before_c["Transaction"].nunique())},
                   "during": {"revenue": float(event_rows["Revenue"].sum()),
                              "n_trx": int(event_rows["Transaction"].nunique())},
                   "after": {"revenue": float(after_c["Revenue"].sum()),
                             "n_trx": int(after_c["Transaction"].nunique())},
                   "has_after": not after_c.empty}
        else:
            period_txt = "el período analizado"
            ctx["recurrence"] = {"recurrence": "unknown", "n_similar": 0,
                                 "note": "No se localizó la transacción."}
    elif ftype == "TEMPORAL_ANOMALY" and p_start is not None:
        gran = period.get("granularity", "day")
        d0 = p_start.normalize()
        bda = eng.before_during_after(eng.daily_total, d0, d0)
        event_rows = eng.completed[eng.completed["day"] == d0]
        threshold = bda["during"]["revenue"] * 0.5
        depth = cmp.history_depth(len(eng.daily_total))
        ctx["recurrence"] = cmp.recurrence_count(
            eng.daily_total, threshold, d0, d0)
        period_txt = str(d0.date())
    else:
        depth = {"sufficient": False, "evidence_quality": "LOW",
                 "note": "Tipo de hallazgo o período no contextualizable."}
        ctx["recurrence"] = {"recurrence": "unknown", "n_similar": 0, "note": ""}
        period_txt = "el período analizado"

    ctx["period_txt"] = period_txt
    ctx["history_depth"] = depth

    # ---- Comparaciones ----
    conc = cmp.concentration(event_rows)
    ctx["concentration"] = conc
    b, d, a = bda["before"], bda["during"], bda["after"]
    ctx["trend"] = cmp.trend_after(b.get("avg_daily", b.get("revenue", 0) or 0),
                                   d.get("avg_daily", d.get("revenue", 0) or 0),
                                   a.get("avg_daily", a.get("revenue", 0) or 0))
    ctx["related_entities"] = cmp.related_entities(event_rows)
    ctx["before_during_after"] = {
        k: {kk: (round(float(vv), 2) if isinstance(vv, (int, float)) else vv)
            for kk, vv in v.items()} for k, v in
        (("before", b), ("during", d), ("after", a))}

    # ---- Hechos del contexto ----
    if d.get("revenue"):
        facts.append(_fact(
            f"En el período {period_txt}, el revenue asociado fue "
            f"{_fmt_money(d['revenue'])} en {d.get('n_trx', 0)} transacción(es).",
            "dataset normalizado (FASE 1A/1C)"))
    if b.get("revenue") or b.get("n_trx"):
        facts.append(_fact(
            f"En el período previo comparable, el revenue fue "
            f"{_fmt_money(b.get('revenue', 0))}.",
            "dataset normalizado (FASE 1A/1C)"))
    if bda.get("has_after"):
        facts.append(_fact(
            f"En el período posterior disponible, el revenue fue "
            f"{_fmt_money(a.get('revenue', 0))}.",
            "dataset normalizado (FASE 1A/1C)"))
    else:
        observations.append(_obs(
            "No hay datos posteriores al evento en el dataset.",
            "rango del dataset"))

    # ---- Observaciones ----
    if conc.get("concentrated"):
        observations.append(_obs(
            f"El {conc['top3_share']:.0%} del total del evento se concentra en "
            f"{conc['top3_n']} transacción(es): comportamiento concentrado.",
            "distribución por transacción"))
    observations.append(_obs(ctx["recurrence"].get("note", ""),
                             "análisis de recurrencia"))
    if ctx["trend"].get("note") and ctx["trend"].get("trend") != "unknown":
        observations.append(_obs(ctx["trend"]["note"], "comparación antes/después"))
    observations.append(_obs(depth.get("note", ""), "profundidad de historial"))

    # Deduplicar observaciones por texto
    seen_o, uniq_o = set(), []
    for o in observations:
        if o["text"] not in seen_o:
            seen_o.add(o["text"])
            uniq_o.append(o)
    observations = uniq_o

    # ---- Explicaciones y recomendaciones ----
    explanations = expl.build_explanations(ctx)
    recommendations = reco.build_recommendations(ctx)

    # ---- Estado de contexto y calidad ----
    evq = f.get("evidence_quality", "MEDIUM")
    if not depth.get("sufficient"):
        evq = "LOW"
    context_status = ("contextualized" if depth.get("sufficient")
                      else "insufficient_context")

    # ---- Trace ----
    trace = {
        "dataset": "Demo Dataset — UCI Online Retail II",
        "company_id": eng.company_id,
        "period": {"start": str(p_start), "end": str(p_end),
                   "granularity": period.get("granularity")},
        "filters": f"entity_kind={kind}; entity_id={eid}",
        "records": int(len(event_rows)),
        "entity": entity,
        "baseline": f.get("expected_value"),
        "observed_value": observed,
        "comparison_value": {
            "before": b, "during": d, "after": a,
            "concentration": {k: v for k, v in conc.items()
                              if k in ("top3_share", "top3_n", "n_trx")},
            "recurrence": ctx["recurrence"].get("recurrence"),
        },
        "method": ("FASE 2C context engine: ventanas antes/durante/después, "
                   "recurrencia por umbral, concentración top-3, tendencia "
                   "post-evento; todo vectorizado sobre el Parquet normalizado"),
        "evidence_used": {
            "n_history_points": depth.get("note", ""),
            "sources": ["parquet normalizado FASE 1C",
                        "Business Finding FASE 2B"],
        },
        "fase_2b_trace": f.get("trace", {}),
    }

    return {
        "finding_id": fid,
        "context_status": context_status,
        "facts": facts,
        "observations": observations,
        "possible_explanations": explanations,
        "recommendations": recommendations,
        "related_entities": ctx["related_entities"],
        "historical_context": ctx["before_during_after"],
        "trend_context": ctx["trend"],
        "recurrence": ctx["recurrence"].get("recurrence"),
        "recurrence_detail": ctx["recurrence"],
        "concentration": {k: v for k, v in conc.items() if k != "note"},
        "evidence_quality": evq,
        "evidence_quality_note": depth.get("note", ""),
        "confidence_score": f.get("confidence_score"),
        "business_priority": f.get("business_priority"),
        "impact_score": f.get("impact_score"),
        "severity": f.get("severity"),
        "type": ftype,
        "title": f.get("title"),
        "trace": trace,
    }


def build_context_findings(findings_report_or_path, parquet_path: str,
                           company_id: str,
                           max_findings: int | None = None) -> dict:
    """Construye el BusinessContextFindingReport completo."""
    if isinstance(findings_report_or_path, str):
        with open(findings_report_or_path, encoding="utf-8") as fh:
            rep = json.load(fh)
        findings = rep.get("findings", [])
        findings_source = "findings_report_2B"
    elif isinstance(findings_report_or_path, list):
        findings = findings_report_or_path
        findings_source = "in_memory_list"
    else:
        rep = findings_report_or_path
        findings = rep.get("findings", [])
        findings_source = "in_memory"
    if max_findings is not None:
        findings = findings[:max_findings]

    eng = ContextEngine(parquet_path, company_id)
    out = [contextualize_finding(f, eng) for f in findings]

    by_status: dict[str, int] = {}
    by_rec: dict[str, int] = {}
    for c in out:
        by_status[c["context_status"]] = by_status.get(c["context_status"], 0) + 1
        by_rec[c["recurrence"]] = by_rec.get(c["recurrence"], 0) + 1

    return {
        "report_metadata": {
            "engine": "zayvero-business-context-2C",
            "company_id": company_id,
            "dataset": "Demo Dataset — UCI Online Retail II",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "n_findings_in": len(findings),
            "n_contextualized": len(out),
        },
        "summary": {
            "total": len(out),
            "by_context_status": by_status,
            "by_recurrence": by_rec,
            "note": ("Cada hallazgo contextualizado distingue FACT / OBSERVATION / "
                     "POSSIBLE_EXPLANATION / RECOMMENDATION. Ninguna hipótesis se "
                     "presenta como hecho y no se afirma causalidad."),
        },
        "context_findings": out,
        "methods": {
            "context_engine": ("Ventanas antes/durante/después (90d), recurrencia "
                               "por umbral ≥50% del evento, concentración top-3 "
                               "transacciones, tendencia post-evento."),
            "explanations": ("Hipótesis basadas en patrones observables; cada una "
                             "documenta su basis y se etiqueta POSSIBLE_EXPLANATION."),
            "recommendations": ("Solo recomendaciones de REVISIÓN; nunca acciones."),
        },
        "trace": {
            "parquet": parquet_path,
            "findings_source": findings_source,
            "rows_in_dataset": int(len(eng.df)),
        },
    }


def save_report(report: dict, company_id: str, base: str) -> str:
    outdir = os.path.join("data", "context", company_id)
    os.makedirs(outdir, exist_ok=True)
    path = os.path.join(outdir, f"{base}_context.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, ensure_ascii=False, indent=2, default=str)
    return path
