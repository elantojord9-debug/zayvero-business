"""ZAYVERO FACTURA — configuración central.

Prototipo SaaS de facturación para RD. Datos 100% ficticios.
Sin cobros reales, sin conexión a la DGII.
"""
from __future__ import annotations

import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# --- reuso de motores del Business OS (solo lectura, sin modificar) --------
# Ruta configurable por entorno (FACTURA_BIZ_PATH). Fallback documentado:
# el directorio hermano "zayvero-business" junto a la raíz de este proyecto
# (es decir, <padre-de-BASE_DIR>/zayvero-business).
# Si no se encuentra una ruta válida se lanza un error claro al arrancar,
# en vez de fallar después con un ImportError críptico.
_MODULOS_BIZ = ("tenant", "os_ecf", "catalogo", "os_contab")


def _resolver_biz_path() -> str:
    candidatos = []
    env = os.environ.get("FACTURA_BIZ_PATH", "").strip()
    if env:
        candidatos.append(env)
    # Fallback: checkout hermano del proyecto (convención de instalación).
    candidatos.append(
        os.path.join(os.path.dirname(BASE_DIR.rstrip(os.sep)),
                     "zayvero-business"))

    for c in candidatos:
        if c and os.path.isdir(c) and all(
                os.path.isdir(os.path.join(c, m)) for m in _MODULOS_BIZ):
            return os.path.abspath(c)
    raise RuntimeError(
        "ZAYVERO FACTURA: no se encontró el checkout del Business OS "
        "(módulos requeridos: %s). Defina la variable de entorno "
        "FACTURA_BIZ_PATH con la ruta al checkout de zayvero-business "
        "(se usa en solo lectura), o coloque este proyecto como directorio "
        "hermano de zayvero-business. Ver docs/INSTALACION.md."
        % ", ".join(_MODULOS_BIZ))


BIZ_PATH = _resolver_biz_path()
if BIZ_PATH not in sys.path:
    sys.path.insert(0, BIZ_PATH)

# --- planes (configurables, SIN cobro real) ---------------------------------
# Precios provisionales en RD$: 790 / 1,490 / 2,490. Este prototipo NO cobra
# ni procesa pagos de suscripción (ver tabla factura_suscripciones:
# "pendiente proveedor de pagos").
PLANES = {
    "basico": {
        "nombre": "Básico",
        "precio": 790,
        "moneda": "RD$",
        "usuarios": 1,            # alias informativo (ver max_usuarios)
        "max_usuarios": 1,
        "max_sucursales": 1,
        "max_docs_mes": 200,
        "max_empresas": 1,
        "descripcion": "Para colmados y negocios pequeños: 1 usuario, "
                       "facturación y POS.",
        "features": ["1 usuario", "Facturas y POS", "Inventario básico",
                     "Reportes esenciales"],
        "multisucursal": False,
    },
    "negocio": {
        "nombre": "Negocio",
        "precio": 1490,
        "moneda": "RD$",
        "usuarios": 3,
        "max_usuarios": 3,
        "max_sucursales": 3,
        "max_docs_mes": 2000,
        "max_empresas": 1,
        "descripcion": "Para PYMEs en crecimiento: hasta 3 usuarios, "
                       "cotizaciones y CxC.",
        "features": ["Hasta 3 usuarios", "Cotizaciones", "Cuentas por cobrar",
                     "e-CF en simulación"],
        "multisucursal": False,
    },
    "pro": {
        "nombre": "Pro",
        "precio": 2490,
        "moneda": "RD$",
        "usuarios": 99,
        "max_usuarios": 99,
        "max_sucursales": 99,
        "max_docs_mes": 100000,
        "max_empresas": 1,
        "descripcion": "Para empresas: hasta 99 usuarios y multisucursal.",
        "features": ["Hasta 99 usuarios", "Multisucursal",
                     "Todo lo de Negocio", "Soporte prioritario"],
        "multisucursal": True,
    },
}

# --- prueba gratis -----------------------------------------------------------
# Días de prueba al registrar una empresa nueva. Al vencer, la empresa pasa
# a solo lectura hasta que un superadmin la reactive o se defina el pago.
TRIAL_DIAS = int(os.environ.get("FACTURA_TRIAL_DIAS", "14"))

# --- base de datos ----------------------------------------------------------
# SQLite por defecto (archivo local). Si se define FACTURA_DATABASE_URL (o
# ZAYVERO_OS_DATABASE_URL), se usa PostgreSQL vía psycopg. Ver README.
# Para pruebas se puede cambiar la ruta con FACTURA_SQLITE_PATH.
SQLITE_PATH = os.environ.get("FACTURA_SQLITE_PATH",
                             os.path.join(BASE_DIR, "factura.db"))

# --- sesiones ---------------------------------------------------------------
SESSION_COOKIE = "factura_sess"
SESSION_HOURS = 8
SECRET_ENV = "FACTURA_SECRET"
SECRET_FILE = os.path.join(BASE_DIR, ".factura_secret")

# --- negocio ----------------------------------------------------------------
ITBIS_TASA_DEFAULT = "18"   # tasa de referencia RD; configurable por empresa
MONEDA = "DOP"
STOCK_BAJO_UMBRAL = 5

ROLES = ("admin", "cajero", "contador")

# --- matriz de permisos por rol (FASE 1) -------------------------------------
# admin: todo. cajero: POS/ventas/clientes, inventario solo lectura, caja y
#   devoluciones; SIN config/usuarios/reportes financieros.
# contador: reportes, clientes e inventario (lectura), auditoría; SIN caja,
#   SIN config, SIN gestión de usuarios.
PERMISOS = {
    "panel": ("admin", "cajero", "contador"),
    "pos": ("admin", "cajero"),
    "crear_ventas": ("admin", "cajero"),
    "anular_ventas": ("admin",),
    "ver_ventas": ("admin", "cajero", "contador"),
    "clientes_ver": ("admin", "cajero", "contador"),
    "clientes_gestion": ("admin", "cajero"),
    "inventario_ver": ("admin", "cajero", "contador"),
    "inventario_gestion": ("admin",),
    "caja": ("admin", "cajero"),
    "devoluciones": ("admin", "cajero"),
    "cxc_ver": ("admin", "cajero", "contador"),
    "cxc_gestion": ("admin", "cajero"),
    "reportes": ("admin", "contador"),
    "contador_ia_ver": ("admin", "contador"),
    "exportar": ("admin", "contador"),
    "gastos_ver": ("admin", "contador"),
    "gastos_gestion": ("admin",),
    "proveedores_ver": ("admin", "cajero", "contador"),
    "proveedores_gestion": ("admin",),
    "ecf_ver": ("admin", "cajero", "contador"),
    "ecf_gestion": ("admin", "cajero"),   # aprobar/emitir/anular: solo admin
    "sucursales_gestion": ("admin",),
    "sucursales_ver": ("admin", "cajero", "contador"),
    "config": ("admin",),
    "usuarios": ("admin",),
    "auditoria": ("admin", "contador"),
    "suscripcion": ("admin",),
    "importar": ("admin",),
    "soporte": ("admin", "cajero", "contador"),
    "bienvenida": ("admin", "cajero", "contador"),
    "superadmin": (),                      # nunca por rol de empresa
}


def tiene_permiso(rol: str, permiso: str) -> bool:
    """True si el rol tiene el permiso (admin tiene todo)."""
    if rol == "admin":
        return True
    return rol in PERMISOS.get(permiso, ())

# --- superadmin local (FASE 4) ------------------------------------------------
# Panel del sistema en /superadmin (suspender/reactivar empresas, ver
# solicitudes de demo). Credenciales por entorno; el valor por defecto es
# solo para desarrollo local y DEBE cambiarse en cualquier despliegue.
#
# CIERRE PRE-STAGING (P0 #2): en modo cloud (FACTURA_ENV=cloud|staging|
# production) el arranque se NIEGA si las credenciales no vienen del
# entorno, si siguen siendo los valores de desarrollo, o si la clave tiene
# menos de 16 caracteres. Ver validar_superadmin_arranque() (la llama
# app.py antes de servir).
SUPERADMIN_EMAIL = os.environ.get("FACTURA_SUPERADMIN_EMAIL",
                                  "superadmin@local")
SUPERADMIN_PASS = os.environ.get("FACTURA_SUPERADMIN_PASS",
                                 "cambiar-esta-clave")
SUPERADMIN_COOKIE = "factura_super"
SUPERADMIN_MIN_PASS = 16
_SUPERADMIN_DEV_EMAIL = "superadmin@local"
_SUPERADMIN_DEV_PASS = "cambiar-esta-clave"

# --- entorno y cookies seguras (P0 #2/#3) ------------------------------------
# FACTURA_ENV: local (defecto) | cloud | staging | production.
# FACTURA_HTTPS=1: activa el flag Secure en las cookies de sesión. En
# producción el servicio va detrás de TLS; en local (HTTP) queda
# desactivado para no romper la demo.
FACTURA_ENV = os.environ.get("FACTURA_ENV", "local").strip().lower()
FACTURA_HTTPS = os.environ.get("FACTURA_HTTPS", "0").strip() == "1"
ANON_CSRF_COOKIE = "factura_anon"


def validar_superadmin_arranque() -> None:
    """Niega el arranque en modo cloud si las credenciales de superadmin
    no están definidas por entorno, son los valores de desarrollo o son
    débiles (mínimo SUPERADMIN_MIN_PASS caracteres).

    Lanza RuntimeError con el motivo; app.py la invoca antes de servir.
    En modo local no bloquea (desarrollo). Lee el entorno en cada llamada
    (importación perezosa de db para no crear un ciclo con config).
    """
    from factura_persist import db as _db
    if not _db.es_modo_cloud():
        return
    email_def = "FACTURA_SUPERADMIN_EMAIL" in os.environ
    pass_def = "FACTURA_SUPERADMIN_PASS" in os.environ
    if not (email_def and pass_def):
        raise RuntimeError(
            "CIERRE DE SEGURIDAD: en modo cloud (FACTURA_ENV="
            f"{FACTURA_ENV}) debe definir FACTURA_SUPERADMIN_EMAIL y "
            "FACTURA_SUPERADMIN_PASS en el entorno; las credenciales por "
            "defecto de desarrollo están prohibidas.")
    if (SUPERADMIN_EMAIL == _SUPERADMIN_DEV_EMAIL
            or SUPERADMIN_PASS == _SUPERADMIN_DEV_PASS):
        raise RuntimeError(
            "CIERRE DE SEGURIDAD: en modo cloud no se permiten los valores "
            "de desarrollo de FACTURA_SUPERADMIN_EMAIL/PASS.")
    if len(SUPERADMIN_PASS or "") < SUPERADMIN_MIN_PASS:
        raise RuntimeError(
            "CIERRE DE SEGURIDAD: FACTURA_SUPERADMIN_PASS debe tener al "
            f"menos {SUPERADMIN_MIN_PASS} caracteres en modo cloud.")


# --- origen público para enlaces sensibles (P0: anti Host-header) ------------
# FACTURA_PUBLIC_ORIGIN: origen público fijo usado para construir enlaces
# sensibles (recuperación de contraseña). El encabezado Host NUNCA se usa
# para esto: es controlable por el atacante (Host header injection).
#
# - Modo local (FACTURA_ENV=local): opcional; si no se define, el enlace
#   usa "http://localhost". Se permite http para desarrollo local.
# - Modo cloud (FACTURA_ENV=cloud|staging|production): OBLIGATORIO, debe
#   ser https:// y pasar validación estricta (solo esquema://host[:puerto],
#   sin path, sin query, sin credenciales, host con caracteres válidos).
#   Si falta o es inválido, el arranque se NIEGA (no sirve) vía
#   validar_origen_publico_arranque(), llamada por app.py antes de servir.


def _normalizar_origen_publico(valor: str, exigir_https: bool) -> str:
    """Valida y normaliza un origen público.

    Acepta solo "esquema://host[:puerto]" (path vacío o "/", sin query ni
    fragmento, sin userinfo). El host debe ser un nombre DNS/IPv4/IPv6
    válido (letras, dígitos, puntos, guiones; corchetes para IPv6). Con
    exigir_https=True solo se acepta https. Devuelve el origen
    normalizado ("esquema://host[:puerto]"). Lanza ValueError con el
    motivo si es inválido.
    """
    from urllib.parse import urlparse
    crudo = (valor or "").strip()
    if not crudo:
        raise ValueError("origen vacío")
    p = urlparse(crudo)
    esquema = (p.scheme or "").lower()
    if esquema not in ("http", "https"):
        raise ValueError(
            f"esquema '{p.scheme}' no permitido (solo http|https)")
    if exigir_https and esquema != "https":
        raise ValueError("el origen debe usar https:// (sin TLS)")
    if p.username or p.password or "@" in (p.netloc or ""):
        raise ValueError("no se permiten credenciales en el origen")
    if (p.path or "") not in ("", "/") or p.query or p.fragment:
        raise ValueError("no se permite path, query ni fragmento")
    neto = p.netloc or ""
    if neto.startswith("["):
        # IPv6 literal, con o sin puerto: [::1] | [::1]:8443
        if neto.endswith("]"):
            host_crudo, puerto = neto[1:-1], None
        else:
            dentro, _, resto = neto[1:].partition("]:")
            if not resto:
                raise ValueError(f"netloc inválido: {neto}")
            host_crudo, puerto = dentro, resto or None
        es_ipv6 = True
    elif neto.count(":") == 1:
        host_crudo, _, resto = neto.partition(":")
        puerto, es_ipv6 = resto or None, False
    else:
        host_crudo, puerto, es_ipv6 = neto, None, False
    host = host_crudo.strip().lower()
    if not host:
        raise ValueError("host vacío")
    if es_ipv6:
        import ipaddress
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            raise ValueError(f"IPv6 inválida: {host_crudo}")
    elif not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}"
                          r"[A-Za-z0-9])?", host):
        raise ValueError(f"host inválido: {host}")
    if puerto is not None:
        if not puerto.isdigit() or not 1 <= int(puerto) <= 65535:
            raise ValueError(f"puerto inválido: {puerto}")
    normalizado = f"{esquema}://{'[' + host + ']' if es_ipv6 else host}"
    if puerto is not None:
        normalizado += f":{int(puerto)}"
    return normalizado


def origen_publico() -> str:
    """Origen público efectivo para enlaces sensibles.

    Lee FACTURA_PUBLIC_ORIGIN del entorno en cada llamada. En modo cloud
    es obligatorio y debe ser https válido; en local es opcional
    (defecto: "http://localhost"). Lanza RuntimeError si falta (cloud) o
    es inválido.
    """
    from factura_persist import db as _db
    cloud = _db.es_modo_cloud()
    crudo = os.environ.get("FACTURA_PUBLIC_ORIGIN", "").strip()
    if not crudo:
        if cloud:
            raise RuntimeError(
                "CIERRE DE SEGURIDAD: en modo cloud (FACTURA_ENV="
                f"{_db.env_actual()}) debe definir FACTURA_PUBLIC_ORIGIN "
                "con el origen público https:// (p. ej. "
                "https://factura.midominio.com). El encabezado Host nunca "
                "se usa para enlaces sensibles.")
        return "http://localhost"
    try:
        return _normalizar_origen_publico(crudo, exigir_https=cloud)
    except ValueError as exc:
        raise RuntimeError(
            f"CIERRE DE SEGURIDAD: FACTURA_PUBLIC_ORIGIN inválido: {exc}. "
            "Debe ser un origen esquema://host[:puerto] válido"
            + (" con https://" if cloud else "")
            + " (sin path, sin credenciales).")


def validar_origen_publico_arranque() -> None:
    """Niega el arranque si el origen público falta (cloud) o es inválido.

    app.py la invoca antes de servir (serve y preflight). Lanza
    RuntimeError con el motivo. Lee el entorno en cada llamada.
    """
    origen_publico()  # lanza RuntimeError con el motivo si hay problema
TIPOS_VENTA = ("factura", "cotizacion", "pos")
ESTADOS_VENTA = ("pagada", "pendiente", "anulada")
METODOS_PAGO = ("efectivo", "tarjeta", "transferencia")

# FASE 2: catálogo y tipos de ítems
TIPOS_PRODUCTO = ("producto", "servicio")
CATEGORIAS_GASTO = ("alquiler", "nómina", "servicios", "mercancía",
                    "transporte", "impuestos", "publicidad", "personal",
                    "otros")
CATEGORIAS_INGRESO = ("otros ingresos", "intereses", "venta de activos",
                      "otros")

# Prefijos de numeración por tipo de documento
SEQ_PREFIJOS = {
    "FACTURA": "F-",
    "COTIZACION": "C-",
    "POS": "P-",
}

# e-CF: modos soportados por empresa
ECF_MODES = ("disabled", "mock")

AVISO_SIMULACION_ECF = (
    "SIMULACIÓN — SIN VALIDEZ FISCAL ANTE DGII. Este módulo genera "
    "comprobantes de PRUEBA con respuestas sintéticas; no está certificado "
    "por la DGII ni por ningún proveedor."
)


def validar_seguridad_lanzamiento():
    from factura_persist import db
    if not db.es_modo_cloud():
        return
    if len(os.environ.get("FACTURA_SECRET", "")) < 32:
        raise RuntimeError("FACTURA_SECRET estable de al menos 32 caracteres es obligatorio")
    if os.environ.get("FACTURA_HTTPS", "") != "1":
        raise RuntimeError("FACTURA_HTTPS=1 y TLS real son obligatorios en cloud")
