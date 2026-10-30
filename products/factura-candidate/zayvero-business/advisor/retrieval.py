"""FASE 5B — Carga del contexto y retrieval de evidencia relevante.

Solo recupera evidencia existente del BusinessIntelligenceContext.
Nunca crea evidencia nueva.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any, Dict, List, Optional

from .models import BusinessQuestion, RetrievedEvidence

_PRIORITY_WEIGHT = {"URGENT": 30.0, "IMPORTANT": 18.0, "REVIEW": 8.0, "MONITOR": 2.0}
_SEVERITY_WEIGHT = {"HIGH": 25.0, "MEDIUM": 12.0, "LOW": 4.0, "CRITICAL": 30.0}

# Peso base por sección y tipo de pregunta (multiplicador de la puntuación base).
_SECTION_PRIORITY = {
    "URGENT_ISSUE": {"finding": 1.0, "risk": 0.6, "recommendation": 0.5},
    "FINANCIAL_PROBLEM": {"finding": 1.0, "risk": 0.9, "trend": 0.4, "snapshot": 0.3},
    "OPPORTUNITY": {"opportunity": 1.0, "trend": 0.5, "finding": 0.2},
    "RISK": {"risk": 1.0, "finding": 0.7},
    "PREDICTION": {"prediction": 1.0, "trend": 0.6, "opportunity": 0.3},
    "TREND": {"trend": 1.0, "prediction": 0.5},
    "RECOMMENDATION": {"recommendation": 1.0, "finding": 0.8, "risk": 0.5},
    "EXPLANATION": {"finding": 1.0, "recommendation": 0.4},
    "PRODUCT": {"finding": 1.0, "risk": 0.5, "trend": 0.3},
    "CUSTOMER": {"finding": 1.0, "risk": 0.5},
    "PRICE": {"finding": 1.0, "risk": 0.5},
    "SALES": {"finding": 1.0, "trend": 0.7, "snapshot": 0.5},
    "GENERAL_BUSINESS": {"snapshot": 1.0, "finding": 0.8, "risk": 0.6, "opportunity": 0.5},
    "UNKNOWN": {},
}

_WORD_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = {
    "el", "la", "los", "las", "de", "del", "en", "y", "a", "que", "es", "se",
    "por", "con", "un", "una", "para", "mi", "mis", "tu", "sus", "esta",
    "este", "esto", "como", "hay", "son", "estan", "tengo", "tiene", "the",
}


def tokens(text: str) -> List[str]:
    """Tokens + formas con raiz (stemming simple para plurales en español)."""
    out: List[str] = []
    for w in _WORD_RE.findall(text.lower()):
        if w in _STOPWORDS or len(w) <= 2:
            continue
        out.append(w)
        # stemming simple: oportunidades -> oportunidad
        if len(w) > 4:
            if w.endswith("es") and not w.endswith("aes"):
                out.append(w[:-2])
            elif w.endswith("s"):
                out.append(w[:-1])
    return out


class ContextStore:
    """Carga el BusinessIntelligenceContext una sola vez y lo indexa."""

    def __init__(self, context_path: str):
        if not os.path.exists(context_path):
            raise FileNotFoundError(f"No existe el contexto: {context_path}")
        with open(context_path, "r", encoding="utf-8") as f:
            self.context: Dict[str, Any] = json.load(f)
        self.path = context_path
        self._index: List[Dict[str, Any]] = []
        self._build_index()

    @property
    def context_id(self) -> str:
        return str(self.context.get("identity", {}).get("context_id", "unknown"))

    def _entry(self, source_type: str, item: Dict[str, Any], uid: str,
               claim: str, value: Any, period: Optional[str],
               search_text: str, entity_tokens: List[str]) -> None:
        self._index.append({
            "source_type": source_type, "uid": uid, "item": item,
            "claim": claim, "value": value, "period": period,
            "search_text": search_text, "entity_tokens": entity_tokens,
        })

    def _entity_tokens(self, entity: Any) -> List[str]:
        out: List[str] = []
        if isinstance(entity, dict):
            for k in ("id", "label"):
                v = entity.get(k)
                if v:
                    out += tokens(str(v))
        elif isinstance(entity, str):
            out += tokens(entity)
        return out

    def _build_index(self) -> None:
        ctx = self.context
        for f in ctx.get("critical_findings", []) or []:
            txt = " ".join([str(f.get("title", "")), str(f.get("business_explanation", ""))])
            self._entry(
                "finding", f, str(f.get("finding_id")),
                str(f.get("title", "")), f.get("observed_value"),
                str((f.get("period") or {}).get("start", "")) if isinstance(f.get("period"), dict) else None,
                txt, self._entity_tokens(f.get("entity")),
            )
        for r in ctx.get("key_risks", []) or []:
            self._entry("risk", r, str(r.get("risk_id")),
                        str(r.get("explanation", ""))[:180], None, None,
                        str(r.get("explanation", "")) + " " + str(r.get("risk_type", "")),
                        self._entity_tokens((r.get("evidence") or {}).get("title", "")))
        for o in ctx.get("key_opportunities", []) or []:
            self._entry("opportunity", o, str(o.get("opportunity_id")),
                        str(o.get("explanation", ""))[:180], None, None,
                        str(o.get("explanation", "")) + " " + str(o.get("type", "")),
                        [])
        for t in ctx.get("key_trends", []) or []:
            self._entry("trend", t, str(t.get("trend_id")),
                        str(t.get("interpretation", ""))[:180],
                        (t.get("value") or {}).get("pct_change") if isinstance(t.get("value"), dict) else t.get("value"),
                        str(t.get("period", "")), str(t.get("interpretation", "")),
                        [])
        for rec in ctx.get("recommendations", []) or []:
            self._entry("recommendation", rec, str(rec.get("recommendation_id")),
                        str(rec.get("text", ""))[:180], None, None,
                        str(rec.get("text", "")),
                        self._entity_tokens(str(rec.get("related_finding", ""))))
        pi = ctx.get("prediction_intelligence") or {}
        pi_summary = pi.get("summary") or {}
        self._entry("prediction", pi, "PREDICTION-SUMMARY",
                    "Resumen de predicciones del modelo (proyecciones, no certezas): "
                    + str(pi_summary.get("total_predictions", 0)) + " predicciones; "
                    + "calidad alta " + str(pi_summary.get("high_quality", 0)) + ", "
                    + "moderada " + str(pi_summary.get("moderate_quality", 0)) + ", "
                    + "baja " + str(pi_summary.get("low_quality", 0)) + ", "
                    + "insuficientes " + str(pi_summary.get("insufficient", 0)) + "; "
                    + "confianza promedio " + str(pi_summary.get("average_confidence", "")) + "/100.",
                    None, None,
                    "predicciones proyecciones ventas futuro modelo estimacion " + json.dumps(pi_summary, ensure_ascii=False), [])
        # Insights individuales (4B) desde advisor_safe_data
        asd = ctx.get("advisor_safe_data") or {}
        for ins in (asd.get("predictions") or []):
            if not isinstance(ins, dict):
                continue
            ent = ins.get("entity") or {}
            claim = str(ins.get("business_interpretation", ""))[:220]
            if not claim:
                claim = "Proyección " + str(ins.get("prediction_type", "")) + " para " + str(ent.get("label", ent.get("id", "")))
            self._entry("prediction", ins, str(ins.get("insight_id") or ins.get("prediction_id")),
                        claim, ins.get("predicted_value"),
                        str(ins.get("period", "")),
                        claim + " " + str(ent.get("label", "")) + " " + str(ins.get("trend", "")) + " prediccion proyeccion ventas",
                        self._entity_tokens(ent))
        pv = ctx.get("prediction_validation") or {}
        self._entry("prediction_validation", pv, "VALIDATION-SUMMARY",
                    str(pv.get("validation_note", ""))[:180], None, None,
                    str(pv.get("validation_note", "")), [])
        snap = ctx.get("business_snapshot") or {}
        metrics = snap.get("metrics") or {}
        if isinstance(metrics, dict):
            for name, m in metrics.items():
                if isinstance(m, dict):
                    self._entry("snapshot", m, f"SNAP-{name}",
                                f"{name}: {m.get('value')}", m.get("value"),
                                str(m.get("period", "")), f"{name} {m.get('value')}", [])
        for lim in ctx.get("limitations", []) or []:
            self._entry("limitation", lim, str(lim.get("limitation_id")),
                        str(lim.get("text", ""))[:180], None, None,
                        str(lim.get("text", "")), [])

    # ------------------------------------------------------------------
    def score(self, entry: Dict[str, Any], question: BusinessQuestion) -> float:
        q_tokens = set(tokens(question.normalized_question))
        e_tokens = set(tokens(entry["search_text"]))
        overlap = len(q_tokens & e_tokens)
        score = overlap * 5.0
        # Coincidencia de entidad
        ent_tokens = set(entry["entity_tokens"])
        ent_match = len(q_tokens & ent_tokens)
        score += ent_match * 25.0
        # Coincidencia de tipo de finding con la pregunta
        item = entry["item"]
        if isinstance(item, dict):
            ftype = str(item.get("finding_type", "")).lower()
            if ftype and ftype.replace("_anomaly", "") in question.normalized_question:
                score += 15.0
            score += _PRIORITY_WEIGHT.get(str(item.get("business_priority", "")), 0.0)
            score += _SEVERITY_WEIGHT.get(str(item.get("severity", "")), 0.0)
            try:
                score += float(item.get("impact_score", 0) or 0) / 5.0
            except (TypeError, ValueError):
                pass
            try:
                score += float(item.get("confidence_score", 0) or 0) / 10.0
            except (TypeError, ValueError):
                pass
        # Multiplicador por sección según tipo de pregunta
        mult = _SECTION_PRIORITY.get(question.question_type, {}).get(entry["source_type"], 0.3)
        return score * mult

    # ------------------------------------------------------------------
    def retrieve(self, question: BusinessQuestion, top_k: int = 12) -> List[RetrievedEvidence]:
        # Preguntas no clasificables: no hay base de evidencia confiable -> vacía.
        # El motor responderá con "No tengo suficiente evidencia".
        if question.question_type == "UNKNOWN":
            return []
        mults = _SECTION_PRIORITY.get(question.question_type, {})
        # Secciones prioritarias para este tipo de pregunta: cuota mínima garantizada
        priority_sections = {st for st, m in mults.items() if m >= 0.5}
        scored = [(e, self.score(e, question)) for e in self._index]
        scored = [s for s in scored if s[1] > 0]
        scored.sort(key=lambda s: s[1], reverse=True)

        picked: Dict[str, tuple] = {}
        # 1) Cuota por sección prioritaria: hasta 4 mejores de cada una
        by_section: Dict[str, list] = {}
        for entry, s in scored:
            by_section.setdefault(entry["source_type"], []).append((entry, s))
        for st in priority_sections:
            for entry, s in by_section.get(st, [])[:4]:
                picked[entry["uid"]] = (entry, s)
        # 2) Completar con el top global por puntuación
        for entry, s in scored:
            if len(picked) >= top_k:
                break
            picked.setdefault(entry["uid"], (entry, s))
        final = sorted(picked.values(), key=lambda s: s[1], reverse=True)[:top_k]
        out: List[RetrievedEvidence] = []
        for entry, s in final:
            out.append(RetrievedEvidence(
                evidence_id=entry["uid"],
                source=str(entry["item"].get("source", "BusinessIntelligenceContext")) if isinstance(entry["item"], dict) else "BusinessIntelligenceContext",
                source_type=entry["source_type"],
                relevance_score=round(s, 2),
                claim=entry["claim"],
                value=entry["value"],
                period=entry["period"],
                trace={
                    "uid": entry["uid"],
                    "scoring_method": "overlap de tokens + peso de prioridad/severidad/impacto/confianza + multiplicador por tipo de pregunta",
                },
            ))
        return out

    def top_findings(self, priority: str, n: int = 5) -> List[Dict[str, Any]]:
        """Findings de una prioridad ordenados por impacto (sin hardcoding)."""
        fs = [f for f in (self.context.get("critical_findings") or [])
              if f.get("business_priority") == priority]
        fs.sort(key=lambda f: float(f.get("impact_score", 0) or 0), reverse=True)
        return fs[:n]
