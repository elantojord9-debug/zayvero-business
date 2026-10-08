"""FASE 5B — Construcción de la respuesta empresarial estructurada.

Plantillas deterministas por tipo de pregunta. Sin LLM.
"""
from __future__ import annotations

from typing import Any, Dict, List

from .models import BusinessQuestion, RetrievedEvidence
from .reasoning import split_reasoning, no_causality_guard


def _money(v: Any) -> str:
    try:
        return "£{:,.2f}".format(float(v))
    except (TypeError, ValueError):
        return str(v)


def build_answer(question: BusinessQuestion, evidence: List[RetrievedEvidence],
                 reasoning: Dict[str, List[str]], context: Dict[str, Any],
                 uncertainty: Dict[str, Any]) -> Dict[str, str]:
    """Genera answer + executive_summary según el tipo de pregunta."""
    qtype = question.question_type
    # Caso especial: proveedores — el contexto no contiene información de proveedores.
    if "proveedor" in question.normalized_question:
        a = (
            "No tengo suficiente evidencia para determinar por qué aumentó el precio "
            "del proveedor. El contexto actual no contiene información suficiente "
            "para establecer esa causa: no hay datos de proveedores, compras ni "
            "costos de adquisición en el dataset analizado."
        )
        return {"answer": a, "executive_summary": "Sin evidencia sobre proveedores."}
    if not evidence:
        a = (
            "No tengo suficiente evidencia para determinarlo. "
            "El contexto actual de ZAYVERO no contiene información suficiente "
            "para responder esta pregunta con base en evidencia. "
            + _missing_hint(question)
        )
        return {"answer": a, "executive_summary": "Sin evidencia suficiente para responder."}

    handlers = {
        "URGENT_ISSUE": _answer_urgent,
        "FINANCIAL_PROBLEM": _answer_financial,
        "OPPORTUNITY": _answer_opportunity,
        "RECOMMENDATION": _answer_recommendation,
        "EXPLANATION": _answer_explanation,
        "PREDICTION": _answer_prediction,
        "TREND": _answer_trend,
        "RISK": _answer_risk,
        "PRODUCT": _answer_entity,
        "CUSTOMER": _answer_entity,
        "PRICE": _answer_entity,
        "SALES": _answer_sales,
        "GENERAL_BUSINESS": _answer_general,
        "UNKNOWN": _answer_unknown,
    }
    fn = handlers.get(qtype, _answer_unknown)
    ans, summary = fn(question, evidence, reasoning, context, uncertainty)
    return {"answer": no_causality_guard(ans), "executive_summary": summary}


def _find_evidence(evidence, source_type="finding"):
    return [e for e in evidence if e.source_type == source_type]


def _answer_urgent(q, evidence, reasoning, context, uncertainty):
    findings = _find_evidence(evidence)
    f = findings[0]
    item = None
    # Recuperar el finding completo para datos precisos
    ans = (
        "El hallazgo de mayor prioridad actualmente es: " + f.claim + " "
        "Está clasificado como URGENT con el impact score más alto entre los hallazgos urgentes. "
        "Esto significa que, según la evidencia disponible, merece revisión prioritaria. "
        "ZAYVERO no afirma que exista fraude, error o una causa determinada: "
        "los datos muestran una desviación respecto al comportamiento esperado y requieren revisión."
    )
    if uncertainty["uncertainty_level"] in ("HIGH", "UNKNOWN"):
        ans += " La evidencia disponible para contextualizar este hallazgo es limitada."
    return ans, "Hallazgo URGENT de mayor impacto identificado; se recomienda revisión prioritaria."


def _answer_financial(q, evidence, reasoning, context, uncertainty):
    ans = (
        "Con la evidencia disponible, ZAYVERO no puede afirmar que el negocio esté "
        "perdiendo dinero en un concepto concreto. Lo que la evidencia sí muestra son "
        "desviaciones respecto al comportamiento esperado en ventas, precios o cantidades. "
        "Una desviación no es automáticamente una pérdida: requiere revisión para determinar "
        "si corresponde a un evento comercial legítimo, un registro atípico u otro factor operativo."
    )
    findings = _find_evidence(evidence)
    if findings:
        top = findings[:3]
        ans += " Los hallazgos con mayor desviación observada son: " + "; ".join(
            f.claim for f in top) + "."
    return ans, "Sin evidencia suficiente para afirmar una pérdida concreta; se listan las desviaciones más relevantes."


def _answer_opportunity(q, evidence, reasoning, context, uncertainty):
    opps = _find_evidence(evidence, "opportunity")
    if not opps:
        return ("No tengo suficiente evidencia para determinarlo: el contexto no contiene "
                "oportunidades con evidencia suficiente para esta consulta.",
                "Sin oportunidades con evidencia suficiente.")
    listed = "; ".join(o.claim for o in opps[:5])
    ans = (
        "ZAYVERO detectó las siguientes posibles oportunidades (son observaciones basadas "
        "en evidencia, no hechos confirmados): " + listed + " "
        "Cada una requiere exploración adicional antes de considerarse una oportunidad real."
    )
    return ans, "Oportunidades con evidencia listadas como posibles, no como hechos."


def _answer_recommendation(q, evidence, reasoning, context, uncertainty):
    recs = _find_evidence(evidence, "recommendation")
    if not recs:
        return ("No tengo suficiente evidencia para determinarlo: no hay recomendaciones "
                "registradas para esta consulta.", "Sin recomendaciones registradas.")
    listed = "; ".join(r.claim for r in recs[:5])
    ans = (
        "Según la prioridad, el impacto y la confianza de la evidencia, lo primero que "
        "debería revisar es: " + listed + " "
        "Son sugerencias de revisión, no acciones ejecutadas."
    )
    return ans, "Revisiones priorizadas por prioridad, impacto y confianza."


def _answer_explanation(q, evidence, reasoning, context, uncertainty):
    findings = _find_evidence(evidence)
    if not findings:
        return ("No tengo suficiente evidencia para determinarlo.", "Sin evidencia.")
    f = findings[0]
    ans = (
        "La clasificación como urgente responde al criterio determinista del motor de "
        "impacto (FASE 2B): se consideraron la magnitud de la desviación respecto al "
        "comportamiento esperado, la confianza estadística del hallazgo y la calidad "
        "de la evidencia. A mayor desviación e impacto, con evidencia suficiente, "
        "mayor prioridad. " + f.claim
    )
    return ans, "Criterio de priorización explicado con la evidencia del hallazgo."


def _answer_prediction(q, evidence, reasoning, context, uncertainty):
    preds = _find_evidence(evidence, "prediction")
    val = _find_evidence(evidence, "prediction_validation")
    parts = []
    if preds:
        parts.append(preds[0].claim)
    if val:
        parts.append(val[0].claim)
    body = " ".join(parts) if parts else "No hay predicciones registradas."
    ans = (
        "Según las proyecciones disponibles (no son certezas): " + body + " "
        "Toda proyección debe interpretarse considerando su confianza, su incertidumbre "
        "y el error histórico del modelo; úsela como referencia para planificación, "
        "no como cifra garantizada."
    )
    return ans, "Proyecciones disponibles presentadas con sus limitaciones."


def _answer_trend(q, evidence, reasoning, context, uncertainty):
    trends = _find_evidence(evidence, "trend")
    if not trends:
        return ("No tengo suficiente evidencia para determinarlo: no hay tendencias "
                "registradas para esta consulta.", "Sin tendencias registradas.")
    listed = []
    for t in trends[:4]:
        kind = "OBSERVADO" if "OBSERVADO" in t.claim.upper() or "observado" in t.claim.lower() else "PROYECTADO"
        listed.append("[" + kind + "] " + t.claim)
    ans = ("Tendencias según la evidencia (se distingue lo observado de lo proyectado): "
           + "; ".join(listed) + ".")
    return ans, "Tendencias observadas y proyectadas separadas."


def _answer_risk(q, evidence, reasoning, context, uncertainty):
    risks = _find_evidence(evidence, "risk")
    if not risks:
        return ("No tengo suficiente evidencia para determinarlo: no hay riesgos "
                "registrados para esta consulta.", "Sin riesgos registrados.")
    listed = "; ".join(r.claim for r in risks[:5])
    ans = ("Los principales riesgos identificados por la evidencia son: " + listed + " "
           "Son riesgos estimados a partir de la evidencia, no certezas.")
    return ans, "Riesgos principales con evidencia."


def _answer_entity(q, evidence, reasoning, context, uncertainty):
    findings = _find_evidence(evidence)
    if not findings:
        return ("No tengo suficiente evidencia para determinarlo: no hay hallazgos "
                "registrados para la entidad consultada.", "Sin hallazgos para la entidad.")
    listed = "; ".join(f.claim for f in findings[:5])
    ans = ("Hallazgos relacionados con la entidad consultada: " + listed + " "
           "Representan desviaciones respecto al comportamiento esperado y requieren revisión.")
    return ans, "Hallazgos de la entidad consultada."


def _answer_sales(q, evidence, reasoning, context, uncertainty):
    snap = [e for e in evidence if e.source_type == "snapshot"]
    parts = [s.claim for s in snap[:4]]
    findings = _find_evidence(evidence)[:3]
    parts += [f.claim for f in findings]
    ans = ("Estado de las ventas según la evidencia: " + "; ".join(parts) + "."
           if parts else "No tengo suficiente evidencia para determinarlo.")
    return ans, "Estado de ventas con datos observados."


def _answer_general(q, evidence, reasoning, context, uncertainty):
    att = context.get("attention_summary") or {}
    level = att.get("attention_level", "no disponible")
    urgents = _find_evidence(evidence)[:2]
    parts = ["Nivel de atención del negocio: " + str(level) + "."]
    if urgents:
        parts.append("Hallazgo principal: " + urgents[0].claim)
    ans = " ".join(parts) + " Para más detalle, pregunte por un tema específico."
    return ans, "Resumen general del estado del negocio."


def _answer_unknown(q, evidence, reasoning, context, uncertainty):
    return ("No tengo suficiente evidencia para determinarlo. La pregunta no pudo "
            "clasificarse en una categoría conocida. Intente reformularla indicando "
            "producto, cliente, periodo o métrica de interés.",
            "Pregunta no clasificable; se sugiere reformular.")


def _missing_hint(q: BusinessQuestion) -> str:
    if q.question_type == "UNKNOWN":
        return "Intente reformular la pregunta indicando producto, cliente, periodo o métrica."
    return ("Faltaría información como: " + q.intent + ". "
            "El contexto actual no contiene esa información.")
