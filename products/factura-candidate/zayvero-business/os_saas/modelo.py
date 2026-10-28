"""BLOQUE PLATAFORMA SAAS (2026-10-13) — Modelo de dominio puro.

Define, SIN tocar base de datos:
- `PLANES`: los 4 planes con precios de REFERENCIA y LÍMITES explícitos.
- `ErrorSaas`: excepción de dominio con código HTTP y código de máquina.
- Validaciones y normalizaciones del registro (nombre, identificador
  fiscal, moneda, sucursales).
- Pasos del onboarding.

REGLA DURA: sin facturación real. Los precios son de referencia para
preparación local; ningún código de este bloque genera cargos, checkout
ni suscripciones reales (ver docs/bloque-saas.md).
"""
from __future__ import annotations

import re
import unicodedata


class ErrorSaas(Exception):
    """Error de dominio SaaS. `manejar()` lo mapea a (status, dict)."""

    def __init__(self, status: int, codigo: str, mensaje: str):
        super().__init__(mensaje)
        self.status = status
        self.codigo = codigo
        self.mensaje = mensaje


# ---------------------------------------------------------------------------
# Planes y límites (sin facturación real — preparación local)
# ---------------------------------------------------------------------------
# Cada plan trae límites EXPLÍCITOS y VERIFICABLES en código. El módulo de
# rutas los HACE CUMPLIR (ver os_saas/rutas.py y tests/test_saas_*.py).
#
#   max_empresas_por_cuenta : empresas que una cuenta puede crear
#   max_usuarios            : usuarios activos por empresa
#   max_sucursales          : sucursales/almacenes configurados por empresa
#   max_ia_mes              : ejecuciones de IA por empresa y mes
#   max_almacenamiento_mb   : almacenamiento por empresa
#   max_llamadas_api_mes    : llamadas a la API por empresa y mes
#   modulos                 : módulos habilitados para la empresa
SIN_FACTURACION = (
    "sin facturación real — preparación local: los precios son de "
    "referencia y ningún código de este bloque genera cargos, checkout "
    "ni suscripciones reales"
)

PLANES = {
    "starter": {
        "id": "starter",
        "nombre": "Starter",
        "precio_usd": 49,
        "facturacion": SIN_FACTURACION,
        "limites": {
            "max_empresas_por_cuenta": 2,
            "max_usuarios": 5,
            "max_sucursales": 2,
            "max_ia_mes": 200,
            "max_almacenamiento_mb": 1024,
            "max_llamadas_api_mes": 10_000,
        },
        "modulos": [
            "inventario", "compras", "crm", "ventas", "cobranzas",
            "tesoreria", "finanzas", "reportes",
        ],
    },
    "business": {
        "id": "business",
        "nombre": "Business",
        "precio_usd": 149,
        "facturacion": SIN_FACTURACION,
        "limites": {
            "max_empresas_por_cuenta": 5,
            "max_usuarios": 25,
            "max_sucursales": 10,
            "max_ia_mes": 2_000,
            "max_almacenamiento_mb": 10_240,
            "max_llamadas_api_mes": 100_000,
        },
        "modulos": [
            "inventario", "compras", "crm", "ventas", "cobranzas",
            "tesoreria", "finanzas", "reportes", "catalogo",
            "documentos", "shield", "aprobaciones",
        ],
    },
    "professional": {
        "id": "professional",
        "nombre": "Professional",
        "precio_usd": 349,
        "facturacion": SIN_FACTURACION,
        "limites": {
            "max_empresas_por_cuenta": 15,
            "max_usuarios": 100,
            "max_sucursales": 30,
            "max_ia_mes": 10_000,
            "max_almacenamiento_mb": 51_200,
            "max_llamadas_api_mes": 500_000,
        },
        "modulos": [
            "inventario", "compras", "crm", "ventas", "cobranzas",
            "tesoreria", "finanzas", "reportes", "catalogo",
            "documentos", "shield", "aprobaciones", "simulator",
            "price_advisor", "contador",
        ],
    },
    "enterprise": {
        "id": "enterprise",
        "nombre": "Enterprise",
        "precio_usd": 799,
        "facturacion": SIN_FACTURACION,
        "nota_precio": (
            "precio base 'desde US$799': el precio final se define por "
            "contrato (sin facturación real en esta fase)"
        ),
        "limites": {
            "max_empresas_por_cuenta": 100,
            "max_usuarios": 1000,
            "max_sucursales": 200,
            "max_ia_mes": 100_000,
            "max_almacenamiento_mb": 512_000,
            "max_llamadas_api_mes": 5_000_000,
        },
        "modulos": [
            "inventario", "compras", "crm", "ventas", "cobranzas",
            "tesoreria", "finanzas", "reportes", "catalogo",
            "documentos", "shield", "aprobaciones", "simulator",
            "price_advisor", "contador", "soporte_dedicado",
        ],
    },
}

PLAN_POR_DEFECTO = "starter"


def plan_de(plan_id: str) -> dict:
    """Devuelve el plan o lanza ErrorSaas 400 si no existe."""
    plan = PLANES.get((plan_id or "").strip().lower())
    if plan is None:
        raise ErrorSaas(
            400, "plan_invalido",
            f"Plan '{plan_id}' no existe. Planes válidos: "
            + ", ".join(sorted(PLANES)))
    return plan


def limite_de(plan: dict, metrica: str) -> int:
    """Límite numérico de un plan para una métrica. Falla duro si la
    métrica no existe (error de programación, no de usuario)."""
    try:
        return int(plan["limites"][metrica])
    except KeyError:
        raise ErrorSaas(
            500, "metrica_desconocida",
            f"Métrica de límite desconocida: {metrica!r}")


# ---------------------------------------------------------------------------
# Normalización y validación del registro
# ---------------------------------------------------------------------------

def normalizar_email(email: str) -> str:
    return (email or "").strip().lower()


def validar_email(email: str) -> str:
    norm = normalizar_email(email)
    if not norm or "@" not in norm or len(norm) > 254:
        raise ErrorSaas(400, "email_invalido",
                        "El correo electrónico no es válido.")
    return norm


def normalizar_nombre(nombre: str) -> str:
    """Nombre de empresa normalizado para unicidad: sin tildes, sin
    puntuación, una sola línea de espacios, minúsculas.
    ('Mi Empresa S.A.' == 'MI EMPRESA sa')."""
    base = (nombre or "").strip()
    base = " ".join(base.split())
    sin_tildes = "".join(
        c for c in unicodedata.normalize("NFKD", base)
        if not unicodedata.combining(c))
    # La puntuación (puntos, guiones, etc.) no distingue empresas:
    # 'Mi Empresa S.A.' debe colisionar con 'MI EMPRESA sa'.
    sin_punt = "".join(c for c in sin_tildes if c.isalnum() or c.isspace())
    return " ".join(sin_punt.split()).casefold()


def validar_nombre(nombre: str) -> str:
    limpio = " ".join((nombre or "").split())
    if len(limpio) < 2 or len(limpio) > 100:
        raise ErrorSaas(400, "nombre_invalido",
                        "El nombre de la empresa debe tener entre 2 y 100 "
                        "caracteres.")
    return limpio


_FISCAL_RE = re.compile(r"^[A-Z0-9]{5,20}$")


def normalizar_fiscal(identificador: str) -> str:
    """Identificador fiscal normalizado: mayúsculas, sin espacios, guiones
    ni puntos. NO hay verificación fiscal real (documentado)."""
    base = (identificador or "").strip().upper()
    base = re.sub(r"[\s\-./]", "", base)
    return base


def validar_fiscal(identificador: str) -> str:
    norm = normalizar_fiscal(identificador)
    if not _FISCAL_RE.match(norm):
        raise ErrorSaas(
            400, "fiscal_invalido",
            "El identificador fiscal debe tener entre 5 y 20 caracteres "
            "alfanuméricos. (Nota: en esta fase no se verifica contra "
            "ninguna autoridad fiscal.)")
    return norm


# Subconjunto de ISO 4217 (códigos activos) aceptado para moneda base.
MONEDAS_ISO_4217 = frozenset("""
    USD EUR DOP MXN COP ARS CLP PEN GTQ HNL NIO CRC PAB CUP HTG JMD TTD BBD
    XCD AWG ANG SRD GYD BZD BRL BOB PYG UYU GBP CHF SEK NOK DKK PLN CZK HUF
    RON BGN HRK ISK TRY RUB UAH GEL AMD AZN KZT UZS KGS TJS TMT BYN MDL ALL
    MKD BAM RSD JPY CNY HKD SGD TWD KRW INR PKR BDT LKR NPR MVR BTN MMK THB
    LAK KHR VND IDR MYR PHP AUD NZD FJD PGK WST TOP SBD VUV XPF SAR AED QAR
    KWD BHD OMR JOD ILS EGP MAD TND DZD LYD SDG ETB KES NGN GHS ZAR NAD BWP
    MUR SCR CAD XAF XOF
""".split())


def validar_moneda(moneda: str) -> str:
    cod = (moneda or "").strip().upper()
    if cod not in MONEDAS_ISO_4217:
        raise ErrorSaas(400, "moneda_invalida",
                        f"Moneda '{moneda}' no válida: use un código "
                        "ISO 4217 (p. ej. USD, DOP, EUR).")
    return cod


def validar_sucursales(sucursales, max_sucursales: int) -> list:
    """Valida la configuración inicial de sucursales/almacenes."""
    if sucursales is None:
        return []
    if not isinstance(sucursales, list):
        raise ErrorSaas(400, "sucursales_invalidas",
                        "Las sucursales deben enviarse como una lista.")
    if len(sucursales) > max_sucursales:
        raise ErrorSaas(
            403, "limite_plan",
            f"El plan permite hasta {max_sucursales} sucursales/almacenes "
            f"y se pidieron {len(sucursales)}. Quite algunas o suba de plan "
            f"({SIN_FACTURACION}).")
    limpias = []
    for i, s in enumerate(sucursales):
        if not isinstance(s, dict):
            raise ErrorSaas(400, "sucursales_invalidas",
                            f"La sucursal #{i + 1} debe ser un objeto con "
                            "'nombre'.")
        nombre = " ".join(str(s.get("nombre", "")).split())
        if len(nombre) < 2 or len(nombre) > 80:
            raise ErrorSaas(400, "sucursales_invalidas",
                            f"La sucursal #{i + 1} necesita un nombre de "
                            "2 a 80 caracteres.")
        tipo = str(s.get("tipo", "sucursal")).strip().lower()
        if tipo not in ("sucursal", "almacen"):
            raise ErrorSaas(400, "sucursales_invalidas",
                            f"La sucursal #{i + 1} tiene tipo inválido: use "
                            "'sucursal' o 'almacen'.")
        limpias.append({"nombre": nombre, "tipo": tipo})
    return limpias


# ---------------------------------------------------------------------------
# Onboarding: checklist de puesta en marcha
# ---------------------------------------------------------------------------

PASOS_ONBOARDING = (
    ("crear_empresa", "Crear la empresa",
     "La empresa quedó registrada en ZAYVERO Business OS."),
    ("invitar_usuarios", "Invitar usuarios",
     "Invita a tu equipo para que trabajen en la misma empresa."),
    ("cargar_catalogo", "Cargar el catálogo",
     "Registra tus productos con SKU, precios y costos."),
    ("primera_venta", "Registrar la primera venta",
     "Emite tu primera venta desde el módulo de ventas."),
    ("primer_reporte", "Generar el primer reporte",
     "Descarga un reporte (inventario, finanzas o compras) en CSV, "
     "XLSX o PDF."),
)

ESTADOS_PASO = ("pendiente", "completado")


def validar_paso(paso: str) -> str:
    ids = [p[0] for p in PASOS_ONBOARDING]
    if paso not in ids:
        raise ErrorSaas(400, "paso_invalido",
                        f"Paso '{paso}' no existe. Pasos: {', '.join(ids)}.")
    return paso


def validar_estado_paso(estado: str) -> str:
    est = (estado or "").strip().lower()
    if est not in ESTADOS_PASO:
        raise ErrorSaas(400, "estado_invalido",
                        f"Estado '{estado}' inválido: use "
                        f"{' o '.join(ESTADOS_PASO)}.")
    return est


# ---------------------------------------------------------------------------
# Períodos de métricas (YYYY-MM)
# ---------------------------------------------------------------------------

_PERIODO_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def validar_periodo(periodo: str) -> str:
    p = (periodo or "").strip()
    if not _PERIODO_RE.match(p):
        raise ErrorSaas(400, "periodo_invalido",
                        f"Período '{periodo}' inválido: use formato YYYY-MM.")
    return p


def periodo_actual() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m")
