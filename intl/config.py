"""FASE 7C — Normalización y validación de la configuración por empresa.

La configuración vive en el modelo Company (tenant) y siempre se lee con
el company_id del TenantContext. Esta capa solo normaliza y valida.
"""

from __future__ import annotations

import re

from .models import (
    CONFIG_FIELDS,
    DATE_FORMATS,
    NUMBER_FORMATS,
    REQUIRED_FOR_COMPLETE,
    SUPPORTED_LANGUAGES,
)

_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")


def _valid_timezone(tz: str) -> bool:
    """Valida zona horaria IANA usando la base de datos del sistema."""
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        ZoneInfo(tz)
        return True
    except Exception:
        return False


def normalize_config(data: dict | None) -> tuple[dict, list]:
    """Normaliza y valida un dict de configuración.

    Devuelve (config_normalizada, errores). Los campos ausentes quedan
    como "" (sin configurar). Los inválidos se reportan en errores y se
    descartan (no se guardan valores inválidos).
    """
    data = data or {}
    config: dict = {}
    errors: list = []

    def _text(key, max_len=80):
        v = data.get(key, "")
        v = str(v).strip() if v is not None else ""
        if len(v) > max_len:
            errors.append({"field": key, "error": "demasiado largo"})
            return ""
        return v

    config["name"] = _text("name", 120)
    config["country"] = _text("country", 80)
    config["industry"] = _text("industry", 80)

    lang = _text("language", 8).lower()
    if lang and lang not in SUPPORTED_LANGUAGES:
        errors.append({"field": "language", "error": "idioma no soportado"})
        lang = ""
    config["language"] = lang

    cur = _text("currency", 3).upper()
    if cur and not _CURRENCY_RE.match(cur):
        errors.append({"field": "currency", "error": "código de moneda inválido"})
        cur = ""
    config["currency"] = cur

    tz = _text("timezone", 64)
    if tz and not _valid_timezone(tz):
        errors.append({"field": "timezone", "error": "zona horaria inválida"})
        tz = ""
    config["timezone"] = tz

    df = _text("date_format", 16).lower()
    if df and df not in DATE_FORMATS:
        errors.append({"field": "date_format", "error": "formato de fecha inválido"})
        df = ""
    config["date_format"] = df

    nf = _text("number_format", 8).lower()
    if nf and nf not in NUMBER_FORMATS:
        errors.append({"field": "number_format", "error": "formato numérico inválido"})
        nf = ""
    config["number_format"] = nf

    return config, errors


def company_config(company) -> dict:
    """Configuración efectiva de una empresa (Company) para el frontend.

    Incluye `complete` (bool) y `missing` (lista de campos regionales sin
    configurar). Determinista: mismos campos → mismo resultado.
    """
    cfg = {f: (getattr(company, f, "") or "") for f in CONFIG_FIELDS}
    missing = [f for f in REQUIRED_FOR_COMPLETE if not cfg.get(f)]
    cfg["complete"] = not missing
    cfg["missing"] = missing
    return cfg


def config_view(company) -> dict:
    """Vista pública de la configuración (para /api/me y /api/company/config).

    Sin secretos. Solo identidad y preferencias regionales.
    """
    return company_config(company)
