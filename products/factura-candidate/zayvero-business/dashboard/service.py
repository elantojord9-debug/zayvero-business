"""FASE 3 — BUSINESS LOGIC.

Funciones puras sobre la vista unificada de 2B+2C que entrega el adapter:
conteos, ordenamiento, filtros, búsqueda, panorama y detalle.
Sin dependencia del transporte (HTTP) ni de la UI.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from .adapter import PRIORITIES, PRIORITY_ORDER

PERIOD_BUCKETS = [
    ("all", "Todos los períodos", None),
    ("last_90d", "Últimos 90 días", 90),
    ("last_6m", "Últimos 6 meses", 180),
    ("last_1y", "Último año", 365),
    ("older", "Anteriores", None),
]

RECURRENT_SET = {"recurrent", "rarely_recurrent"}
ISOLATED_SET = {"isolated"}


def _parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None


def period_end(view):
    """Fin del período del hallazgo (datetime o None)."""
    return _parse_dt((view.get("period") or {}).get("end"))


def reference_date(findings):
    """Fecha de referencia: el fin de período más reciente en los datos."""
    ends = [period_end(v) for v in findings]
    ends = [e for e in ends if e]
    return max(ends) if ends else None


def sort_key(view):
    """Orden: business_priority → impact_score → confidence_score."""
    prio = PRIORITY_ORDER.get(view.get("business_priority"), 99)
    impact = view.get("impact_score")
    conf = view.get("confidence_score")
    return (prio, -(impact if impact is not None else -1),
            -(conf if conf is not None else -1))


def sort_findings(findings):
    return sorted(findings, key=sort_key)


def priority_counts(findings):
    """Conteo por prioridad calculado desde los datos reales."""
    counts = {p: 0 for p in PRIORITIES}
    for v in findings:
        p = v.get("business_priority")
        if p in counts:
            counts[p] += 1
        else:
            counts[p] = counts.get(p, 0) + 1
    return counts


def type_counts(findings):
    counts = {}
    for v in findings:
        t = v.get("type")
        counts[t] = counts.get(t, 0) + 1
    return counts


def in_period_bucket(view, bucket_id, ref):
    """¿El hallazgo cae en el bucket de período? 'older' = antes del año."""
    if bucket_id in (None, "", "all"):
        return True
    end = period_end(view)
    if end is None or ref is None:
        return bucket_id == "all"
    if bucket_id == "older":
        return end < ref - timedelta(days=365)
    days = next((d for bid, _label, d in PERIOD_BUCKETS if bid == bucket_id), None)
    if days is None:
        return True
    return end >= ref - timedelta(days=days)


def matches_search(view, query):
    """Búsqueda por producto, cliente, tipo de hallazgo o título."""
    q = (query or "").strip().lower()
    if not q:
        return True
    entity = view.get("entity") or {}
    haystack = " ".join([
        str(view.get("title") or ""),
        str(view.get("finding_id") or ""),
        str(view.get("type") or ""),
        str(view.get("type_label") or ""),
        str(entity.get("id") or ""),
        str(entity.get("label") or ""),
    ]).lower()
    return all(token in haystack for token in q.split())


def filter_findings(findings, priority=None, ftype=None,
                    period=None, query=None, ref=None):
    """Aplica los filtros de la UI (todos opcionales)."""
    ref = ref if ref is not None else reference_date(findings)
    result = []
    for v in findings:
        if priority and priority != "all" and v.get("business_priority") != priority:
            continue
        if ftype and ftype != "all" and v.get("type") != ftype:
            continue
        if not in_period_bucket(v, period, ref):
            continue
        if not matches_search(v, query):
            continue
        result.append(v)
    return result


def _avg(values):
    vals = [v for v in values if v is not None]
    return round(sum(vals) / len(vals), 1) if vals else None


def panorama(findings):
    """Valores del bloque 'Panorama actual', calculados desde datos reales."""
    counts = priority_counts(findings)
    recurrence = {"recurrent": 0, "isolated": 0, "other": 0}
    for v in findings:
        r = v.get("recurrence")
        if r in RECURRENT_SET:
            recurrence["recurrent"] += 1
        elif r in ISOLATED_SET:
            recurrence["isolated"] += 1
        else:
            recurrence["other"] += 1
    return {
        "total": len(findings),
        "by_priority": counts,
        "avg_impact_score": _avg([v.get("impact_score") for v in findings]),
        "avg_confidence": _avg([v.get("confidence_score") for v in findings]),
        "recurrent": recurrence["recurrent"],
        "isolated": recurrence["isolated"],
    }


def get_detail(findings, finding_id):
    """Vista completa de un hallazgo para la pantalla de detalle."""
    for v in findings:
        if v.get("finding_id") == finding_id:
            return v
    return None
