"""FASE 8 — Modelos y constantes de la capa de experiencia de producto.

Solo valores presentacionales y estructura. Sin lógica de negocio, sin
cálculos analíticos, sin inteligencia nueva. Los textos visibles viven en
el catálogo del frontend (i18n.js, es/en); aquí solo viajan CLAVES para
que el idioma lo resuelva la configuración 7C de la empresa.

Regla de lenguaje (vinculante para todo el contenido de este paquete):
- "posible oportunidad", nunca "oportunidad garantizada".
- "predicción" como estimación, nunca "resultado seguro".
- Sin causalidad que los datos no demuestren.
- Sin promesas de ventas, ahorro o eliminación de problemas. Sin ROI.
"""

from __future__ import annotations

# ---- Estados del producto (especificación FASE 8, punto 5) ---------------
PRODUCT_STATES = (
    "NO_DATA",
    "PROCESSING",
    "READY",
    "ERROR",
    "INCOMPLETE_CONFIGURATION",
)

# Mensajes exactos por estado (especificación). Se exponen como claves;
# el texto final lo resuelve i18n.js según el idioma de la empresa.
STATE_MESSAGE_KEYS = {
    "NO_DATA": "product.state.no_data",
    "PROCESSING": "product.state.processing",
    "READY": "product.state.ready",
    "ERROR": "product.state.error",
    "INCOMPLETE_CONFIGURATION": "product.state.incomplete_configuration",
}

# Acciones sugeridas por estado (claves de i18n + ruta de destino).
STATE_ACTIONS = {
    "NO_DATA": [
        {"key": "product.action.complete_config", "route": "#/empresa"},
        {"key": "product.action.upload_data", "route": "#/empresa"},
    ],
    "PROCESSING": [
        {"key": "product.action.wait", "route": "#/empresa"},
    ],
    "READY": [
        {"key": "product.action.view_diagnostic", "route": "#/diagnostico"},
        {"key": "product.action.open_intelligence", "route": "#/resumen"},
    ],
    "ERROR": [
        {"key": "product.action.review_data", "route": "#/empresa"},
    ],
    "INCOMPLETE_CONFIGURATION": [
        {"key": "product.action.complete_config", "route": "#/empresa"},
    ],
}

# ---- Onboarding empresarial (7 pasos, especificación punto 2) ------------
ONBOARDING_STEPS = (
    {"step": 1, "key": "company_info", "title_key": "onboarding.step1.title",
     "desc_key": "onboarding.step1.desc"},
    {"step": 2, "key": "regional_config", "title_key": "onboarding.step2.title",
     "desc_key": "onboarding.step2.desc"},
    {"step": 3, "key": "data_upload", "title_key": "onboarding.step3.title",
     "desc_key": "onboarding.step3.desc"},
    {"step": 4, "key": "validation", "title_key": "onboarding.step4.title",
     "desc_key": "onboarding.step4.desc"},
    {"step": 5, "key": "mapping", "title_key": "onboarding.step5.title",
     "desc_key": "onboarding.step5.desc"},
    {"step": 6, "key": "processing", "title_key": "onboarding.step6.title",
     "desc_key": "onboarding.step6.desc"},
    {"step": 7, "key": "first_diagnostic", "title_key": "onboarding.step7.title",
     "desc_key": "onboarding.step7.desc"},
)

STEP_STATUS_DONE = "done"
STEP_STATUS_CURRENT = "current"
STEP_STATUS_PENDING = "pending"

# ---- Secuencia post-onboarding (especificación punto 10) ----------------
POST_SEQUENCE = (
    {"key": "config_complete", "label_key": "sequence.config_complete"},
    {"key": "data_received", "label_key": "sequence.data_received"},
    {"key": "data_processed", "label_key": "sequence.data_processed"},
    {"key": "analysis_available", "label_key": "sequence.analysis_available"},
    {"key": "diagnostic_ready", "label_key": "sequence.diagnostic_ready"},
)

# ---- Primera entrada (especificación punto 1) ----------------------------
FIRST_ENTRY = {
    "main_key": "product.hero.main",
    "secondary_key": "product.hero.secondary",
    "ready_key": "product.hero.ready",
}

# Fuentes de datos que ZAYVERO puede trabajar hoy. NO se prometen
# integraciones que no existen.
DATA_SOURCES = (
    {"key": "csv", "label_key": "product.source.csv"},
    {"key": "xlsx", "label_key": "product.source.xlsx"},
    {"key": "authorized_reports", "label_key": "product.source.reports"},
    {"key": "future_integrations", "label_key": "product.source.future"},
)

# ---- Beneficios reales (especificación punto 12) ------------------------
# Lenguaje prudente: "detectar", "priorizar", "posibles", "analizar",
# "consultar", "recomendaciones basadas en evidencia".
BENEFITS = (
    {"key": "unusual_behavior", "label_key": "benefits.unusual_behavior"},
    {"key": "prioritize", "label_key": "benefits.prioritize"},
    {"key": "opportunities", "label_key": "benefits.opportunities"},
    {"key": "trends", "label_key": "benefits.trends"},
    {"key": "ask", "label_key": "benefits.ask"},
    {"key": "evidence_recs", "label_key": "benefits.evidence_recs"},
    {"key": "executive_diagnostic", "label_key": "benefits.executive_diagnostic"},
)

# Frases PROHIBIDAS en todo el contenido comercial (tests las verifican).
FORBIDDEN_CLAIMS = (
    "te garantizamos más ventas",
    "te garantizamos ahorrar dinero",
    "elimina todos tus problemas",
    "garantizamos más ventas",
    "oportunidad garantizada",
    "resultado seguro",
    "we guarantee more sales",
    "guaranteed opportunity",
    "guaranteed result",
)

# ---- Planes (especificación punto 13) -----------------------------------
# SOLO arquitectura/presentación. Sin Stripe, sin checkout, sin pagos.
# Los precios son placeholders marcados explícitamente como NO DEFINITIVOS.
PLANS = (
    {
        "key": "starter",
        "name_key": "plans.starter.name",
        "price": None,
        "price_note_key": "plans.price.not_definitive",
        "features_keys": [
            "plans.starter.f1",
            "plans.starter.f2",
            "plans.starter.f3",
        ],
    },
    {
        "key": "professional",
        "name_key": "plans.professional.name",
        "price": None,
        "price_note_key": "plans.price.not_definitive",
        "features_keys": [
            "plans.professional.f1",
            "plans.professional.f2",
            "plans.professional.f3",
            "plans.professional.f4",
        ],
    },
    {
        "key": "enterprise",
        "name_key": "plans.enterprise.name",
        "price": None,
        "price_note_key": "plans.price.not_definitive",
        "features_keys": [
            "plans.enterprise.f1",
            "plans.enterprise.f2",
            "plans.enterprise.f3",
            "plans.enterprise.f4",
        ],
    },
)

# ---- Jerarquía de la experiencia (especificación punto 8) ----------------
EXPERIENCE_HIERARCHY = (
    {"key": "diagnostic", "label_key": "hierarchy.diagnostic",
     "route": "#/diagnostico"},
    {"key": "intelligence", "label_key": "hierarchy.intelligence",
     "route": "#/resumen"},
    {"key": "findings", "label_key": "hierarchy.findings",
     "route": "#/hallazgos"},
    {"key": "advisor", "label_key": "hierarchy.advisor",
     "route": "#/advisor"},
)

# Línea explicativa del Advisor (especificación punto 9).
ADVISOR_LINE_KEY = "advisor.explainer"

# Entrada de la demo comercial (especificación punto 11).
DEMO_ENTRY = {
    "title_key": "demo.explore.title",
    "desc_key": "demo.explore.desc",
    "cta_key": "demo.explore.cta",
    "badge_key": "demo.badge",
}
