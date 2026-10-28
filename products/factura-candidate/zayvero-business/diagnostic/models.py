"""FASE 7B — Estructura BusinessDiagnostic (documentación + validación).

El Diagnóstico Ejecutivo ZAYVERO NO crea inteligencia nueva: consume
únicamente resultados existentes (1A/1B/1C, 2B, 2C, 4A, 4B, 4C, 5A) y los
ensambla en un documento ejecutivo determinista y trazable.

Estructura (100% serializable a JSON):

    diagnostic_id        # determinista: DG-<sha256(company+context+version)[:12]>
    version              # "7b-1.0.0"
    company_id           # de la sesión autenticada (TenantContext)
    company_name
    is_demo
    generated_at         # metadata; no afecta el contenido analítico
    diagnostic_status    # AVAILABLE | LIMITED | INSUFFICIENT
    status_note          # explicación empresarial del estado
    data_period          # periodo analizado (de 5A business_snapshot)
    data_quality         # calidad y cobertura de los datos
    context_confidence   # confianza del contexto (de 5A, sin recalcular)
    executive_summary    # resumen breve con números reales
    business_status      # estado de la empresa (métricas existentes)
    priority_attention   # hallazgos URGENT/IMPORTANT (orden existente)
    risks                # riesgos de 5A (severidad existente)
    opportunities        # oportunidades de 5A ("posible oportunidad")
    trends               # {"observed": [...], "projected": [...]} de 5A
    predictions          # insights 4B + estado de validación 4C
    recommendations      # recomendaciones 5A (RECOMENDACIÓN EXISTENTE)
    limitations          # limitaciones 5A (nunca ocultadas)
    next_steps           # pasos sugeridos desde prioridades existentes
    evidence_used        # evidence IDs reales utilizados
    trace                # trazabilidad: fuentes, reglas, versiones

Lenguaje: empresarial, sin alarmismo. Ninguna hipótesis se presenta como
hecho; ninguna predicción como certeza.
"""

from __future__ import annotations

DIAGNOSTIC_VERSION = "7b-1.0.0"

# Secciones fijas del diagnóstico (sin secciones adicionales sin autorización).
SECTIONS = (
    "executive_summary",      # A. Resumen ejecutivo
    "business_status",        # B. Estado de la empresa
    "priority_attention",     # C. Lo que requiere atención
    "risks",                  # D. Riesgos a revisar
    "opportunities",          # E. Posibles oportunidades
    "trends",                 # F. Tendencias
    "predictions",            # G. Predicciones
    "recommendations",        # H. Recomendaciones prioritarias
    "data_quality",           # I. Calidad y cobertura de los datos
    "limitations",            # J. Limitaciones
    "next_steps",             # K. Próximos pasos sugeridos
)

REQUIRED_TOP_LEVEL = (
    "diagnostic_id", "version", "company_id", "company_name", "is_demo",
    "generated_at", "diagnostic_status", "status_note", "data_period",
    "data_quality", "context_confidence",
) + SECTIONS + (
    "evidence_used", "trace",
)

# Estados del diagnóstico.
AVAILABLE = "AVAILABLE"      # evidencia suficiente para un diagnóstico completo
LIMITED = "LIMITED"          # evidencia parcial: "limitado por la información disponible"
INSUFFICIENT = "INSUFFICIENT"  # sin datos suficientes: no se fabrica diagnóstico

STATUS_NOTES = {
    AVAILABLE: "",
    LIMITED: "El diagnóstico está limitado por la información disponible.",
    INSUFFICIENT: "Información insuficiente para generar determinadas conclusiones.",
}

# Textos empresariales fijos (deterministas).
SUBTITLE = "Una visión basada en los datos disponibles de tu empresa."
OPPORTUNITY_DISCLAIMER = "Posible oportunidad. No es una garantía de resultado."
NO_CAUSALITY = "Los datos disponibles no permiten establecer la causa."
VALIDATION_PENDING_LABEL = "Pendiente de validación"
VALIDATION_NOT_AVAILABLE_LABEL = "No disponible"
VALIDATION_INSUFFICIENT_LABEL = "Datos insuficientes"

# Preguntas sugeridas para el CTA "Preguntarle a ZAYVERO" (flujo 5B → 5C).
SUGGESTED_ADVISOR_QUESTIONS = (
    "¿Por qué este hallazgo es importante?",
    "¿Qué debería revisar primero?",
    "Explícame esta oportunidad.",
    "¿Qué información falta?",
)

# Orden determinista de prioridades y severidades (sin recalcular).
PRIORITY_RANK = {"URGENT": 0, "IMPORTANT": 1, "REVIEW": 2, "MONITOR": 3}
SEVERITY_RANK = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}


def validate(diagnostic: dict) -> list[str]:
    """Verifica que el diagnóstico tenga la estructura esperada.

    Devuelve la lista de problemas (vacía si es válido). Usado por tests.
    """
    problems: list[str] = []
    for field in REQUIRED_TOP_LEVEL:
        if field not in diagnostic:
            problems.append(f"falta campo: {field}")
    if diagnostic.get("diagnostic_status") not in (AVAILABLE, LIMITED, INSUFFICIENT):
        problems.append("diagnostic_status inválido")
    pa = diagnostic.get("priority_attention")
    if not isinstance(pa, list):
        problems.append("priority_attention debe ser lista")
    ev = diagnostic.get("evidence_used")
    if not isinstance(ev, list):
        problems.append("evidence_used debe ser lista")
    return problems
