"""FASE 7C — Modelos y constantes de internacionalización.

Sin lógica de negocio: solo valores permitidos y valores por defecto.
"""

from __future__ import annotations

# Idiomas con catálogo completo hoy. "pt" queda reservado: la arquitectura
# de i18n.py permite registrarlo después sin tocar la lógica.
SUPPORTED_LANGUAGES = ("es", "en")
RESERVED_FUTURE_LANGUAGES = ("pt",)

# Formatos de fecha permitidos (solo presentación; el almacenamiento
# interno usa ISO y no se toca).
DATE_FORMATS = ("dd/mm/yyyy", "mm/dd/yyyy", "yyyy-mm-dd")

# Formatos numéricos permitidos (agrupador de miles / decimal).
NUMBER_FORMATS = ("es", "en")  # es → 1.234,56 · en → 1,234.56

# Campos de configuración por empresa (identidad + regional).
CONFIG_FIELDS = (
    "name",
    "country",
    "industry",
    "language",
    "currency",
    "timezone",
    "date_format",
    "number_format",
)

# Campos que definen si la configuración está "completa" para operar.
REQUIRED_FOR_COMPLETE = ("country", "language", "currency", "timezone")

# Símbolos de moneda para presentación. La moneda NUNCA se convierte;
# solo se muestra el símbolo/etiqueta de la moneda configurada.
# Monedas no listadas se muestran con su código ISO (honesto, sin inventar).
CURRENCY_SYMBOLS = {
    "USD": "$",
    "DOP": "RD$",
    "EUR": "€",
    "GBP": "£",
    "MXN": "MX$",
    "COP": "COL$",
    "ARS": "AR$",
    "CLP": "CL$",
    "PEN": "S/",
    "BRL": "R$",
    "CAD": "CA$",
    "CHF": "CHF ",
    "JPY": "¥",
    "CNY": "¥",
}

# Configuración de la empresa DEMO (dataset UCI Online Retail II).
# El dataset es de un minorista del Reino Unido: su moneda de contexto es
# GBP. Esto NO es una empresa real del cliente.
DEMO_CONFIG = {
    "country": "Reino Unido",
    "industry": "Retail (datos de demostración)",
    "language": "es",
    "currency": "GBP",
    "timezone": "Europe/London",
    "date_format": "dd/mm/yyyy",
    "number_format": "en",
}
