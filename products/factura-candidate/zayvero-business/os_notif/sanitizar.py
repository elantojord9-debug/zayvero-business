"""BLOQUE B (2026-10-13) — Sanitización de contenido de notificaciones.

REGLA: ningún dato sensible viaja en el contenido de una
notificación ni en su payload (roadmap P1-8: "sin exponer datos
sensibles").

Qué se considera sensible (ver docs/bloque-notificaciones.md para la
matriz completa):
- Identificadores de contacto: emails, teléfonos (WhatsApp/Telegram).
- Secretos: tokens, api_keys, contraseñas/claves, PIN, OTP.
- Datos financieros personales: cuentas bancarias, tarjetas, IBAN.
- Identidad: cédula/DNI, RNC, pasaporte.

`sanitizar()` es pura y recursiva: recorre dicts/listas y enmascara
por CLAVE (patrón de nombre) y por VALOR (emails y teléfonos en texto
libre). Los IDs internos de negocio (producto_id, venta_id,
cliente_id, proveedor_id) NO se enmascaran: son referencias
operativas necesarias para actuar sobre la alerta (no son datos
sensibles por sí solos).
"""
from __future__ import annotations

import hashlib
import re

MASCARA = "***"

# Claves cuyo valor se enmascara completo.
_PATRON_CLAVE = re.compile(
    r"(email|e_mail|correo|mail|tel[eé]fono|phone|celular|movil|"
    r"whatsapp|telegram|cuenta|account|iban|tarjeta|card|"
    r"token|secret|password|contrase[ñn]a|clave|api[_-]?key|"
    r"pin|otp|c[oó]digo|cedula|c[eé]dula|dni|rnc|pasaporte|"
    r"direcci[oó]n|address|banco|bank|beneficiario)",
    re.IGNORECASE)

# Email en texto libre.
_RE_EMAIL = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

# Teléfono tipo RD/EE.UU.: 10 dígitos con o sin separadores y prefijo
# opcional (+1). Evita enmascarar montos: exige dígitos contiguos o con
# separadores típicos (espacio, guion, paréntesis), sin comas ni puntos.
_RE_TELEFONO = re.compile(
    r"(?<![\d.,])"                      # no es parte de un número mayor
    r"(?:\+\d{1,3}[\s\-]?)?"            # +1 / +1809 opcional
    r"(?:\(?\d{3}\)?[\s\-]?)?"          # área opcional
    r"\d{3}[\s\-]?\d{4}"                # 7 dígitos locales
    r"(?![\d])")


def enmascarar_valor(valor: str) -> str:
    """Enmascara emails/teléfonos dentro de un texto libre."""
    v = _RE_EMAIL.sub(lambda m: _enmascarar_email(m.group(0)), valor)
    return _RE_TELEFONO.sub(MASCARA, v)


def _enmascarar_email(email: str) -> str:
    local, _, dominio = email.partition("@")
    if not dominio:
        return MASCARA
    primero = local[:1] if local else ""
    return f"{primero}{MASCARA}@{dominio}"


def sanitizar(obj):
    """Devuelve una copia sanitizada de obj (dict/list/str/otros).

    - dict: si la clave coincide con _PATRON_CLAVE -> MASCARA.
    - str: se enmascaran emails/teléfonos en texto libre.
    - El resto se copia sin cambios.
    """
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if _PATRON_CLAVE.search(str(k)):
                out[k] = MASCARA
            else:
                out[k] = sanitizar(v)
        return out
    if isinstance(obj, (list, tuple)):
        return [sanitizar(v) for v in obj]
    if isinstance(obj, str):
        return enmascarar_valor(obj)
    return obj


def mascara_destino(canal: str, destino: str) -> tuple[str, str]:
    """(destino_mask, destino_hash) para os_notif_canales.

    El destino real NO se persiste en claro: se guarda una máscara
    legible para el usuario (últimos 4 dígitos del teléfono / primer
    carácter + dominio del email) y el sha256 para verificación
    futura. La máscara NO permite reconstruir el destino.
    """
    d = (destino or "").strip()
    h = hashlib.sha256(d.encode("utf-8")).hexdigest()
    if canal == "email":
        m = _enmascarar_email(d) if "@" in d else MASCARA
    elif canal in ("whatsapp", "telegram"):
        digitos = re.sub(r"\D", "", d)
        m = (f"+{MASCARA}{digitos[-4:]}" if len(digitos) >= 4
             else MASCARA)
    else:
        m = MASCARA
    return m, h
