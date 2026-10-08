"""FASE 5B — Interpretación determinista de preguntas en lenguaje natural.

Clasifica la pregunta, extrae entidades, métrica y periodo sin LLM.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, List, Optional, Tuple

from .models import BusinessQuestion

# Reglas de clasificación: (question_type, lista de patrones). Orden = prioridad.
# Todas las coincidencias se evalúan en texto normalizado (minúsculas, sin acentos).
_CLASSIFICATION_RULES: List[Tuple[str, List[str]]] = [
    ("EXPLANATION", [
        "por que esto aparece", "por que aparece", "por que es urgente",
        "por que esta clasificado", "explicame", "explica",
    ]),
    ("URGENT_ISSUE", [
        "problema mas urgente", "mas urgente", "problema urgente",
        "urgente", "prioridad", "atencion inmediata",
    ]),
    ("FINANCIAL_PROBLEM", [
        "perdiendo dinero", "pierdo dinero", "perdida", "perdidas",
        "dinero perdido", "fuga de dinero", "no gano",
    ]),
    ("OPPORTUNITY", [
        "oportunidad", "oportunidades", "oportunida",
    ]),
    ("RECOMMENDATION", [
        "que deberia revisar", "que reviso", "recomiendas", "recomendacion",
        "que hago", "que debo hacer", "que deberia hacer", "por donde empiezo",
    ]),
    ("PREDICTION", [
        "van a subir", "van a bajar", "van a crecer", "van a caer",
        "prediccion", "predicciones", "proyeccion", "proyecta",
        "pronostico", "futuro", "proximos meses", "proximo trimestre",
    ]),
    ("TREND", [
        "tendencia", "tendencias",
    ]),
    ("RISK", [
        "riesgo", "riesgos",
    ]),
    ("CUSTOMER", [
        "cliente", "clientes",
    ]),
    ("PRODUCT", [
        "producto", "productos",
    ]),
    ("PRICE", [
        "precio", "precios", "costo", "costos",
    ]),
    ("SALES", [
        "ventas", "venta", "ingresos", "demanda", "facturacion",
    ]),
]

_METRIC_KEYWORDS = {
    "revenue": ["ingreso", "ingresos", "revenue", "facturacion", "ventas"],
    "quantity": ["cantidad", "cantidades", "unidades", "volumen"],
    "price": ["precio", "precios"],
    "customers": ["clientes", "compradores"],
}

_PERIOD_RE = re.compile(r"(20\d{2}[-/]\d{2}(?:[-/]\d{2})?)")

_INTENT_MAP = {
    "URGENT_ISSUE": "identificar el hallazgo de mayor prioridad",
    "FINANCIAL_PROBLEM": "evaluar si existe evidencia de problemas financieros",
    "OPPORTUNITY": "listar oportunidades con evidencia",
    "RECOMMENDATION": "priorizar revisiones sugeridas por la evidencia",
    "EXPLANATION": "explicar el criterio de priorizacion",
    "PREDICTION": "consultar proyecciones disponibles",
    "TREND": "resumir tendencias observadas y proyectadas",
    "RISK": "resumir riesgos identificados",
    "PRODUCT": "consultar hallazgos sobre un producto",
    "CUSTOMER": "consultar hallazgos sobre un cliente",
    "PRICE": "consultar hallazgos de precios",
    "SALES": "consultar el estado de las ventas",
    "GENERAL_BUSINESS": "resumen general del negocio",
    "UNKNOWN": "la pregunta no pudo clasificarse",
}


def normalize(text: str) -> str:
    """Normaliza: minúsculas, sin acentos, espacios simples."""
    t = text.lower().strip()
    for a, b in (("á", "a"), ("é", "e"), ("í", "i"), ("ó", "o"), ("ú", "u"),
                 ("ü", "u"), ("ñ", "n"), ("¿", ""), ("?", ""), ("!", "")):
        t = t.replace(a, b)
    t = re.sub(r"\s+", " ", t)
    return t


def classify(normalized: str) -> str:
    """Clasifica por reglas deterministas; prioridad = orden de la lista."""
    for qtype, patterns in _CLASSIFICATION_RULES:
        for p in patterns:
            if p in normalized:
                return qtype
    # ¿Es una pregunta general de negocio?
    if any(w in normalized for w in ("negocio", "empresa", "como va", "estado",
                                     "que esta pasando", "resumen", "panorama")):
        return "GENERAL_BUSINESS"
    return "UNKNOWN"


def extract_entities(normalized: str, context: Dict[str, Any]) -> List[Dict[str, str]]:
    """Extrae entidades mencionadas: productos, clientes, países, periodos.

    Busca coincidencias de IDs y etiquetas conocidos del contexto.
    """
    entities: List[Dict[str, str]] = []
    seen = set()
    findings = context.get("critical_findings", []) or []
    for f in findings:
        ent = f.get("entity") or {}
        eid = str(ent.get("id") or "")
        label = str(ent.get("label") or "")
        for cand, kind in ((eid, ent.get("kind", "entity")), (label, ent.get("kind", "entity"))):
            if cand and cand.strip() and cand.lower() in normalized:
                key = (kind, cand)
                if key not in seen:
                    seen.add(key)
                    entities.append({
                        "kind": kind, "id": eid, "label": label,
                        "source": f.get("finding_id", ""),
                    })
    return entities


def extract_period(original: str) -> Optional[str]:
    m = _PERIOD_RE.search(original)
    return m.group(1) if m else None


def extract_metric(normalized: str) -> Optional[str]:
    for metric, words in _METRIC_KEYWORDS.items():
        if any(w in normalized for w in words):
            return metric
    return None


def build_question(original_question: str, context: Dict[str, Any]) -> BusinessQuestion:
    """Construye un BusinessQuestion determinista desde el texto original."""
    norm = normalize(original_question)
    qtype = classify(norm)
    entities = extract_entities(norm, context)
    period = extract_period(original_question)
    metric = extract_metric(norm)
    qid = "Q-" + hashlib.sha256(norm.encode("utf-8")).hexdigest()[:12]
    return BusinessQuestion(
        question_id=qid,
        original_question=original_question,
        normalized_question=norm,
        question_type=qtype,
        entities=entities,
        requested_period=period,
        requested_metric=metric,
        intent=_INTENT_MAP.get(qtype, "consulta general"),
        trace={
            "classification_method": "reglas de palabras clave deterministas (orden de prioridad)",
            "normalization": "minusculas, sin acentos, sin signos de interrogacion",
            "patterns_checked": len(_CLASSIFICATION_RULES),
        },
    )
