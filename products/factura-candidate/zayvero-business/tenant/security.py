"""FASE SEG-01/02/03 — Defensas HTTP compartidas (solo stdlib).

SEG-01 (cookie Secure):
  La cookie de sesión lleva `Secure` cuando el operador lo configura
  explícitamente:

    ZAYVERO_COOKIE_SECURE=1   → Secure siempre (recomendado en producción HTTPS)
    (sin definir)             → nunca Secure (desarrollo local HTTP)

  Es una declaración del operador ("este despliegue se sirve por HTTPS"),
  no una detección automática.

SEG-02 (cabeceras de seguridad):
  CSP estricta sin 'unsafe-inline'/'unsafe-eval', X-Frame-Options,
  X-Content-Type-Options y Referrer-Policy en TODAS las respuestas.
  HSTS se emite ÚNICAMENTE con evidencia confiable de HTTPS público:

    ZAYVERO_HSTS=1            → Strict-Transport-Security (recomendado en
                                producción HTTPS)

  La decisión de HSTS está SEPARADA de la cookie Secure: son dos
  protecciones distintas con dos interruptores distintos.

  NOTA SOBRE PROXIES (X-Forwarded-Proto): deliberadamente NO se usa para
  decidir HSTS ni Secure. Esa cabecera la puede fijar cualquier cliente
  que alcance al servidor directamente, así que confiar en ella "porque
  una variable está habilitada" no es evidencia confiable. Solo sería
  válida si se cumplen TODAS estas condiciones:

    1. El proxy inverso es el ÚNICO ingreso posible (el puerto de la app
       no es accesible desde internet ni desde otras redes).
    2. El proxy REESCRIBE X-Forwarded-Proto con el esquema real que vio
       del cliente (no reenvía el valor que traía la petición).
    3. La conexión proxy→app viaja por una red confiable.

  Render cumple 1–3 (su proxy es el único ingreso y fija el esquema
  real), pero aun así se optó por banderas EXPLÍCITAS del operador:
  con configuración explícita no existe superficie de spoofing y el
  comportamiento no depende de la arquitectura del despliegue. Si en el
  futuro se quisiera detección por proxy, habría que validar además la
  IP de origen contra las del proxy confiable, no solo una variable.

SEG-03 (CSRF): ver tenant/auth.py (emisión) y tenant/authorization.py
  (require_csrf). Aquí solo la construcción del token y el origen
  público canónico para validar Origin/Referer en el login.
"""

from __future__ import annotations

import hmac
import os
import secrets


# ---------------------------------------------------------------------------
# SEG-01 — cookie Secure (decisión explícita del operador)
# ---------------------------------------------------------------------------

def cookie_secure_enabled() -> bool:
    """True solo si ZAYVERO_COOKIE_SECURE=1.

    El operador declara: "este despliegue se sirve por HTTPS". Nunca se
    infiere de cabeceras de la petición.
    """
    return os.environ.get("ZAYVERO_COOKIE_SECURE", "").strip() == "1"


def warn_if_insecure():
    """Aviso de arranque si las protecciones de producción no están activas.

    Evita que queden desactivadas por olvido: el operador ve el aviso en
    los logs del despliegue.
    """
    msgs = []
    if not cookie_secure_enabled():
        msgs.append("la cookie de sesión NO llevará el atributo Secure "
                    "(configure ZAYVERO_COOKIE_SECURE=1 en producción HTTPS)")
    if not hsts_enabled():
        msgs.append("NO se emitirá Strict-Transport-Security "
                    "(configure ZAYVERO_HSTS=1 en producción HTTPS)")
    if msgs:
        print("ADVERTENCIA DE SEGURIDAD (SEG-01/02): " + "; ".join(msgs) + ".")


# ---------------------------------------------------------------------------
# SEG-02 — cabeceras de seguridad
# ---------------------------------------------------------------------------

# CSP diseñada contra el frontend real (verificado 2026-10-10):
# - scripts: solo app.js e i18n.js por <script src> (sin inline, sin eval)
# - estilos: solo style.css por <link> (sin <style>, sin style="...")
# - sin <img>, sin fuentes externas, sin recursos de otros orígenes
# - fetch solo al mismo origen; formularios manejados por JS
# No se usa 'unsafe-inline' ni 'unsafe-eval' en ninguna directiva.
CONTENT_SECURITY_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self'; "
    "img-src 'self'; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'none'"
)

BASE_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    # La app nunca necesita vivir dentro de un iframe (verificado).
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
}

# HSTS: duración prudente (1 año). Sin includeSubDomains ni preload: no se
# han verificado todos los subdominios ni hay autorización.
HSTS_VALUE = "max-age=31536000"


def hsts_enabled() -> bool:
    """True solo si ZAYVERO_HSTS=1.

    Evidencia confiable de HTTPS público = declaración EXPLÍCITA del
    operador, no cabeceras controlables por el cliente. Intencionalmente
    separada de la cookie Secure: una protege la cookie, la otra ordena
    al navegador usar siempre HTTPS.
    """
    return os.environ.get("ZAYVERO_HSTS", "").strip() == "1"


def security_headers(*, hsts: bool) -> dict:
    """Cabeceras de seguridad para cada respuesta.

    HSTS solo cuando `hsts` es True (origen público HTTPS confirmado por
    configuración). Nunca se emite por defecto en HTTP local.
    """
    headers = dict(BASE_SECURITY_HEADERS)
    headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    if hsts:
        headers["Strict-Transport-Security"] = HSTS_VALUE
    return headers


def build_set_cookie(name: str, value: str, *, secure: bool) -> str:
    """Construye el valor de Set-Cookie para la sesión.

    Siempre HttpOnly + SameSite=Lax. Secure solo si el operador lo activó.
    """
    parts = [f"{name}={value}", "Path=/", "HttpOnly", "SameSite=Lax"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


def build_clear_cookie(name: str, *, secure: bool) -> str:
    """Cookie de borrado: mismos atributos que la de sesión (los navegadores
    exigen coincidencia de Path/Domain; Secure debe coincidir para que el
    borrado aplique a la cookie segura)."""
    parts = [f"{name}=", "Path=/", "HttpOnly", "SameSite=Lax", "Max-Age=0"]
    if secure:
        parts.append("Secure")
    return "; ".join(parts)


# ---------------------------------------------------------------------------
# SEG-03 — token CSRF (construcción) y origen público canónico
# ---------------------------------------------------------------------------

def new_csrf_token() -> str:
    """Token CSRF impredecible, ligado a la sesión en el servidor."""
    return secrets.token_urlsafe(32)


def csrf_tokens_match(provided: str | None, expected: str | None) -> bool:
    """Comparación en tiempo constante. Vacío/nulo → siempre False."""
    if not provided or not expected:
        return False
    return hmac.compare_digest(str(provided), str(expected))


def public_origin() -> str:
    """Origen público canónico para validar Origin/Referer en el login.

    Configuración explícita del operador (NO se deduce de la petición):

        ZAYVERO_PUBLIC_ORIGIN=https://zayvero-business.onrender.com

    En Render, TLS termina en su proxy: el servidor local solo ve HTTP,
    así que el esquema+host públicos DEBEN venir de configuración. Sin
    esta variable, el login solo acepta http:// contra el propio Host
    (desarrollo local). Devuelve "" si no está configurada.
    """
    return os.environ.get("ZAYVERO_PUBLIC_ORIGIN", "").strip().rstrip("/").lower()
