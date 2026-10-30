"""FASE 7C — Catálogo de textos de interfaz (es/en, extensible a pt).

Base limpia y extensible: `register_language("pt", {...})` agrega un
idioma sin duplicar lógica de negocio. `t(key, language)` devuelve el
texto; si falta la clave en el idioma pedido, cae a español y luego a la
clave misma (nunca rompe la interfaz).

ALCANCE HONESTO: este catálogo cubre la cáscara de la interfaz
(navegación, títulos, configuración, mensajes de estado). El contenido
analítico (hallazgos, riesgos, recomendaciones) conserva su idioma de
origen; la traducción masiva de contenido es trabajo futuro.
"""

from __future__ import annotations

_STRINGS: dict[str, dict[str, str]] = {
    "es": {
        "app.subtitle": "Inteligencia de tu empresa",
        "nav.workspace": "Mi empresa",
        "nav.diagnostic": "Diagnóstico",
        "nav.summary": "Resumen",
        "nav.findings": "Hallazgos",
        "nav.opportunities": "Oportunidades",
        "nav.predictions": "Predicciones",
        "nav.advisor": "Advisor",
        "nav.audit": "Auditoría",
        "login.title": "Iniciar sesión",
        "login.subtitle": "Accede a la inteligencia de tu empresa.",
        "login.email": "Correo electrónico",
        "login.password": "Contraseña",
        "login.submit": "Entrar",
        "login.error": "Credenciales incorrectas",
        "logout": "Cerrar sesión",
        "company.current": "Empresa actual",
        "demo.badge": "DEMO · Datos de demostración",
        "config.title": "Configuración de la empresa",
        "config.subtitle": "Identidad y preferencias regionales de tu empresa.",
        "config.name": "Nombre de la empresa",
        "config.country": "País",
        "config.industry": "Industria",
        "config.language": "Idioma",
        "config.currency": "Moneda",
        "config.timezone": "Zona horaria",
        "config.date_format": "Formato de fecha",
        "config.number_format": "Formato numérico",
        "config.save": "Guardar configuración",
        "config.saved": "Configuración guardada.",
        "config.incomplete": "Configuración incompleta",
        "config.incomplete_detail": "Completa los campos pendientes para una mejor experiencia.",
        "config.no_permission": "Solo OWNER o ADMIN pueden modificar la configuración.",
        "config.demo_locked": "La empresa demo no se puede modificar.",
        "currency.missing": "Moneda no configurada",
        "currency.demo_note": "Moneda del dataset de demostración (UCI Online Retail II).",
        "diagnostic.title": "Diagnóstico Ejecutivo",
        "diagnostic.subtitle": "Una visión basada en los datos disponibles de tu empresa.",
        "advisor.cta": "Pregúntale a ZAYVERO",
        "common.loading": "Cargando…",
        "common.error": "Ocurrió un error. Intenta de nuevo.",
    },
    "en": {
        "app.subtitle": "Your business intelligence",
        "nav.workspace": "My company",
        "nav.diagnostic": "Diagnostic",
        "nav.summary": "Summary",
        "nav.findings": "Findings",
        "nav.opportunities": "Opportunities",
        "nav.predictions": "Predictions",
        "nav.advisor": "Advisor",
        "nav.audit": "Audit",
        "login.title": "Sign in",
        "login.subtitle": "Access your company's intelligence.",
        "login.email": "Email",
        "login.password": "Password",
        "login.submit": "Sign in",
        "login.error": "Incorrect credentials",
        "logout": "Sign out",
        "company.current": "Current company",
        "demo.badge": "DEMO · Demonstration data",
        "config.title": "Company settings",
        "config.subtitle": "Your company's identity and regional preferences.",
        "config.name": "Company name",
        "config.country": "Country",
        "config.industry": "Industry",
        "config.language": "Language",
        "config.currency": "Currency",
        "config.timezone": "Time zone",
        "config.date_format": "Date format",
        "config.number_format": "Number format",
        "config.save": "Save settings",
        "config.saved": "Settings saved.",
        "config.incomplete": "Incomplete configuration",
        "config.incomplete_detail": "Complete the pending fields for a better experience.",
        "config.no_permission": "Only OWNER or ADMIN can change settings.",
        "config.demo_locked": "The demo company cannot be modified.",
        "currency.missing": "Currency not configured",
        "currency.demo_note": "Demonstration dataset currency (UCI Online Retail II).",
        "diagnostic.title": "Executive Diagnostic",
        "diagnostic.subtitle": "A view based on your company's available data.",
        "advisor.cta": "Ask ZAYVERO",
        "common.loading": "Loading…",
        "common.error": "Something went wrong. Please try again.",
    },
}


def register_language(code: str, strings: dict) -> None:
    """Registra un idioma nuevo (p. ej. "pt") sin tocar la lógica."""
    code = (code or "").strip().lower()
    if not code or not isinstance(strings, dict):
        raise ValueError("idioma inválido")
    _STRINGS[code] = dict(strings)


def supported_languages() -> tuple:
    return tuple(_STRINGS.keys())


def t(key: str, language: str | None = "es") -> str:
    """Texto de interfaz. Cae a es y luego a la clave (nunca falla)."""
    lang = (language or "es").strip().lower()
    cat = _STRINGS.get(lang) or {}
    if key in cat:
        return cat[key]
    es = _STRINGS["es"]
    return es.get(key, key)
