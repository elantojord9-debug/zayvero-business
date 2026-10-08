"""FASE 7C — Presentación regional de fechas, números y moneda.

SOLO presentación. Nunca convierte monedas, nunca altera el valor real
almacenado, nunca toca los motores de inteligencia (1A–7B intactos).

- format_number: agrupador de miles y decimal según number_format.
- format_date:   reordena una fecha ISO según date_format.
- format_money:  muestra valor + símbolo/etiqueta de la moneda configurada.
                  Si no hay moneda configurada, lo dice HONESTAMENTE:
                  "Moneda no configurada". NUNCA asume RD$, USD, EUR ni GBP.
"""

from __future__ import annotations

from datetime import datetime

from .models import CURRENCY_SYMBOLS

# Etiquetas honestas (es/en). El idioma por defecto es es.
MISSING_CURRENCY = {
    "es": "Moneda no configurada",
    "en": "Currency not configured",
}


def _lang(language: str | None) -> str:
    return language if language in ("es", "en") else "es"


def format_number(value, number_format: str | None = "es", decimals: int = 1) -> str:
    """1.234,56 (es) / 1,234.56 (en). Valor numérico sin cambios."""
    try:
        n = float(value)
    except (TypeError, ValueError):
        return "—"
    nf = number_format if number_format in ("es", "en") else "es"
    neg = "-" if n < 0 else ""
    n = abs(n)
    entero = int(n)
    frac = round((n - entero) * (10 ** decimals))
    # Acarreo del redondeo (p. ej. 1.999 → 2.0)
    if frac >= 10 ** decimals:
        entero += 1
        frac = 0
    ent_str = f"{entero:,}"
    if nf == "es":
        ent_str = ent_str.replace(",", ".")
        sep = ","
    else:
        sep = "."
    if decimals <= 0:
        return f"{neg}{ent_str}"
    frac_str = str(frac).zfill(decimals)
    return f"{neg}{ent_str}{sep}{frac_str}"


def format_date(iso_value: str | None, date_format: str | None = "dd/mm/yyyy") -> str:
    """Reordena una fecha ISO (YYYY-MM-DD o con hora) según date_format."""
    if not iso_value:
        return "—"
    s = str(iso_value).strip()[:10]
    try:
        d = datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        return str(iso_value)
    df = date_format if date_format in ("dd/mm/yyyy", "mm/dd/yyyy", "yyyy-mm-dd") else "dd/mm/yyyy"
    if df == "mm/dd/yyyy":
        return d.strftime("%m/%d/%Y")
    if df == "yyyy-mm-dd":
        return d.strftime("%Y-%m-%d")
    return d.strftime("%d/%m/%Y")


def currency_label(currency: str | None, language: str | None = "es") -> str:
    """Etiqueta honesta de la moneda: símbolo conocido o código ISO.

    Sin moneda configurada → "Moneda no configurada" (nunca se asume una).
    """
    cur = (currency or "").strip().upper()
    if not cur:
        return MISSING_CURRENCY[_lang(language)]
    return CURRENCY_SYMBOLS.get(cur, cur)


def format_money(value, currency: str | None, number_format: str | None = "es",
                language: str | None = "es", decimals: int = 0) -> str:
    """Valor monetario con la moneda de la empresa. Sin conversión.

    Ejemplo: format_money(1234.5, "DOP", "es") → "RD$1.235"
             format_money(1234.5, "USD", "en") → "$1,235"
             format_money(1234.5, "", "es")    → "1.235 (Moneda no configurada)"
    """
    num = format_number(value, number_format, decimals)
    if num == "—":
        return "—"
    cur = (currency or "").strip().upper()
    if not cur:
        return f"{num} ({MISSING_CURRENCY[_lang(language)]})"
    label = currency_label(cur, language)
    return f"{label}{num}"
