"""FASE 5C — Control de alucinaciones (validación posterior del LLM).

Comprueba que la respuesta del LLM no introduzca valores ajenos al contexto
validado entregado: cifras, porcentajes, productos, clientes, fechas,
evidence_ids. También detecta afirmaciones de causalidad y predicciones
presentadas como certezas.

Reglas documentadas:
- Números "significativos": con símbolo £ o %, con decimales, o con 3+ dígitos.
  Los enteros de 1-2 dígitos se consideran incidentales y no se validan.
- Cada número significativo de la respuesta debe existir (valor numérico
  idéntico) en el vocabulario permitido extraído del request.
- Cada ID con formato de evidencia (FND-*, EV-*, RISK-*, OPP-*, INS-*,
  REC-*, ADVISORY-*, PRED-*, VAL-*, CTX-*) debe existir en el contexto.
- Secuencias en MAYÚSCULAS de 2+ palabras (nombres de producto) deben
  aparecer en el contexto.
- Frases de causalidad fuerte y de certeza predictiva están prohibidas.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Set, Tuple

from .models import LLMAdvisorRequest, LLMResponseValidation


NUM_RE = re.compile(r"£?\d[\d,]*(?:\.\d+)?%?")
ID_RE = re.compile(r"\b(?:FND|EV|RISK|OPP|INS|REC|ADVISORY|PRED|VAL|CTX|Q|R|LR)-\w+\b")
UPPER_PHRASE_RE = re.compile(r"\b[A-ZÁÉÍÓÚÑ][A-ZÁÉÍÓÚÑ0-9'&\- ]{3,}\b")
DATE_RE = re.compile(r"\b(?:19|20)\d{2}-\d{2}(?:-\d{2})?\b")

# Causalidad fuerte sobre fenómenos de negocio (no sobre criterios de priorización).
CAUSALITY_PATTERNS = [
    r"ventas?\s+(?:bajaron|subieron|cayeron|aumentaron|disminuyeron)\s+porque",
    r"(?:provocó|provocaron|causó|causaron)\s+(?:la|el|una|un)\s+(?:caída|subida|aumento|disminución|bajada)",
    r"es\s+la\s+causa\s+de",
    r"debido\s+a\s+que\s+los\s+clientes",
    r"porque\s+los\s+clientes\s+están\s+abandonando",
]

# Certeza predictiva prohibida.
CERTAINTY_PATTERNS = [
    r"\blas\s+ventas\s+(?:subirán|bajarán|van\s+a\s+subir|van\s+a\s+bajar)\b",
    r"\bva\s+a\s+ocurrir\b",
    r"\bsucederá\b",
    r"\bdefinitivamente\b",
    r"\bgarantizado[as]?\b",
    r"\bcon\s+total\s+seguridad\b",
    r"\bes\s+seguro\s+que\b",
]


def _norm_num(token: str) -> Tuple[str, float]:
    """Normaliza un token numérico: quita £, comas y %; devuelve (forma, valor)."""
    clean = token.replace("£", "").replace(",", "").replace("%", "")
    try:
        return clean, float(clean)
    except ValueError:
        return token, float("nan")


def build_allowed_vocabulary(request: LLMAdvisorRequest) -> Dict[str, Set]:
    """Extrae el vocabulario permitido del request (evidencia + trace)."""
    blob = json.dumps(
        {"evidence": request.evidence, "trace": request.trace, "advisor": request.advisor_response},
        ensure_ascii=False,
    )
    numbers: Set[float] = set()
    for tok in NUM_RE.findall(blob):
        core = tok.replace("£", "").replace(",", "").replace("%", "")
        digits = re.sub(r"\D", "", core)
        if len(digits) <= 2:  # incidentales
            continue
        _, val = _norm_num(tok)
        if val == val:  # no NaN
            numbers.add(val)
    ids = set(ID_RE.findall(blob))
    upper_phrases = set(p.strip() for p in UPPER_PHRASE_RE.findall(blob))
    dates = set(DATE_RE.findall(blob))
    return {"numbers": numbers, "ids": ids, "upper_phrases": upper_phrases, "dates": dates}


def _significant_numbers(text: str) -> List[str]:
    out = []
    for tok in NUM_RE.findall(text):
        core = tok.replace("£", "").replace(",", "").replace("%", "")
        digits = re.sub(r"\D", "", core)
        if len(digits) <= 2:
            continue
        out.append(tok)
    return out


def validate(
    request: LLMAdvisorRequest,
    answer: str,
    model: str,
    provider_name: str,
) -> LLMResponseValidation:
    """Valida la respuesta del LLM contra el vocabulario permitido del request."""
    vocab = build_allowed_vocabulary(request)
    blob_evidence = json.dumps(request.evidence, ensure_ascii=False)

    numeric_checks: List[Dict[str, Any]] = []
    for tok in _significant_numbers(answer):
        _, val = _norm_num(tok)
        ok = any(abs(val - v) < 1e-9 for v in vocab["numbers"])
        numeric_checks.append({"token": tok, "value": val, "found_in_context": ok})

    evidence_checks: List[Dict[str, Any]] = []
    for eid in ID_RE.findall(answer):
        ok = eid in vocab["ids"]
        evidence_checks.append({"evidence_id": eid, "known": ok})

    # Nombres de producto en mayúsculas: deben existir en el contexto.
    product_checks: List[Dict[str, Any]] = []
    for phrase in set(p.strip() for p in UPPER_PHRASE_RE.findall(answer)):
        if len(phrase.split()) < 2:
            continue
        ok = phrase in blob_evidence or phrase in vocab["upper_phrases"]
        product_checks.append({"phrase": phrase, "found_in_context": ok})

    date_checks: List[Dict[str, Any]] = []
    for d in set(DATE_RE.findall(answer)):
        ok = d in vocab["dates"]
        date_checks.append({"date": d, "found_in_context": ok})

    causality_checks: List[Dict[str, Any]] = []
    low = answer.lower()
    for pat in CAUSALITY_PATTERNS:
        m = re.search(pat, low)
        causality_checks.append({"pattern": pat, "matched": bool(m)})

    prediction_checks: List[Dict[str, Any]] = []
    for pat in CERTAINTY_PATTERNS:
        m = re.search(pat, low)
        prediction_checks.append({"pattern": pat, "matched": bool(m)})

    unsupported: List[Dict[str, Any]] = []
    for c in numeric_checks:
        if not c["found_in_context"]:
            unsupported.append({"kind": "number", "detail": c})
    for c in evidence_checks:
        if not c["known"]:
            unsupported.append({"kind": "evidence_id", "detail": c})
    for c in product_checks:
        if not c["found_in_context"]:
            unsupported.append({"kind": "product_name", "detail": c})
    for c in date_checks:
        if not c["found_in_context"]:
            unsupported.append({"kind": "date", "detail": c})
    for c in causality_checks:
        if c["matched"]:
            unsupported.append({"kind": "causality_claim", "detail": c})
    for c in prediction_checks:
        if c["matched"]:
            unsupported.append({"kind": "certainty_claim", "detail": c})

    validated = [
        {"kind": "number", "count": sum(1 for c in numeric_checks if c["found_in_context"])},
        {"kind": "evidence_id", "count": sum(1 for c in evidence_checks if c["known"])},
    ]
    warnings: List[str] = []
    if request.uncertainty.get("uncertainty_level") in ("HIGH", "UNKNOWN"):
        warnings.append("La respuesta se generó con incertidumbre alta/desconocida en el contexto.")

    # Evidencia insuficiente: el advisor lo declara; el LLM debe conservarlo.
    insufficient = "no tengo suficiente evidencia" in (
        request.advisor_response.get("answer_determinista", "") or ""
    ).lower()
    if insufficient and "no tengo suficiente evidencia" not in low:
        warnings.append("El contexto declara evidencia insuficiente pero la respuesta no lo conserva.")

    if insufficient:
        status = "INSUFFICIENT_EVIDENCE"
    elif unsupported:
        status = "VALIDATION_FAILED"
    else:
        status = "VALID"

    return LLMResponseValidation(
        request_id=request.request_id,
        status=status,
        validated_claims=validated,
        unsupported_claims=unsupported,
        numeric_checks=numeric_checks,
        evidence_checks=evidence_checks,
        causality_checks=causality_checks,
        prediction_checks=prediction_checks,
        warnings=warnings,
        trace={
            "model": model,
            "provider": provider_name,
            "vocab_numbers": len(vocab["numbers"]),
            "vocab_ids": len(vocab["ids"]),
        },
    )
