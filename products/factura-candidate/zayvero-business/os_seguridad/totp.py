"""BLOQUE A SEGURIDAD — TOTP (RFC 6238 / RFC 4226) solo con stdlib.

No hay pyotp instalado: se implementa con hmac + hashlib + base64.

- Secreto: 20 bytes aleatorios (160 bits, lo estándar para TOTP).
- Paso: 30 segundos. Ventana de verificación: ±1 paso (cubre el desfase
  de reloj habitual entre el servidor y la app autenticadora).
- `momento` es inyectable (epoch segundos) para tests deterministas.

El secreto NUNCA se loguea ni se persiste en claro: ver
os_seguridad/envoltorio.py para su custodia.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

PASO_SEGUNDOS = 30
DIGITOS = 6
VENTANA_PASOS = 1          # ±1 paso de 30 s
SECRETO_BYTES = 20         # 160 bits


def generar_secreto(n_bytes: int = SECRETO_BYTES) -> bytes:
    """20 bytes criptográficamente aleatorios para el TOTP."""
    if n_bytes < 16:
        raise ValueError("el secreto TOTP necesita al menos 128 bits")
    return secrets.token_bytes(n_bytes)


def a_base32(secreto: bytes) -> str:
    """Base32 sin padding, como lo esperan las apps autenticadoras."""
    return base64.b32encode(secreto).decode("ascii").rstrip("=")


def uri_otpauth(secreto: bytes, etiqueta: str,
                emisor: str = "ZAYVERO") -> str:
    """URI otpauth:// para capturar con la app autenticadora.

    Se entrega UNA sola vez durante el enrolamiento (en memoria) y NUNCA
    se persiste ni se registra en auditoría: contiene el secreto en claro.
    """
    etiqueta = (etiqueta or "").strip() or "usuario"
    return (
        f"otpauth://totp/{quote(emisor)}:{quote(etiqueta)}"
        f"?secret={a_base32(secreto)}"
        f"&issuer={quote(emisor)}"
        f"&algorithm=SHA1&digits={DIGITOS}&period={PASO_SEGUNDOS}"
    )


def _hotp(secreto: bytes, contador: int, digitos: int = DIGITOS) -> str:
    msg = struct.pack(">Q", contador)
    hs = hmac.new(secreto, msg, hashlib.sha1).digest()
    offset = hs[-1] & 0x0F
    codigo = struct.unpack(">I", hs[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(codigo % (10 ** digitos)).zfill(digitos)


def codigo_totp(secreto: bytes, momento: float | None = None,
                paso: int = PASO_SEGUNDOS,
                digitos: int = DIGITOS) -> str:
    """Código TOTP para `momento` (epoch s; None = ahora)."""
    if not secreto or not isinstance(secreto, (bytes, bytearray)):
        raise ValueError("secreto inválido")
    t = time.time() if momento is None else float(momento)
    return _hotp(bytes(secreto), int(t // paso), digitos)


def verificar_totp(secreto: bytes, codigo: str,
                   momento: float | None = None,
                   paso: int = PASO_SEGUNDOS,
                   ventana: int = VENTANA_PASOS) -> bool:
    """¿El código es válido en la ventana ±`ventana` pasos? (fail-closed).

    Comparación en tiempo constante. Formato inválido -> False.
    NOTA: dentro de la ventana un código es re-utilizable (propio del
    TOTP); la ventana corta (±60 s) y la auditoría de cada verificación
    lo mitigan (ver docs/bloque-seguridad.md § limitaciones).
    """
    if not secreto or not isinstance(secreto, (bytes, bytearray)):
        return False
    codigo = (codigo or "").strip().replace(" ", "")
    if len(codigo) != DIGITOS or not codigo.isdigit():
        return False
    t = time.time() if momento is None else float(momento)
    base = int(t // paso)
    for desfase in range(-ventana, ventana + 1):
        contador = base + desfase
        if contador < 0:
            continue  # antes del epoch: no hay código válido (fail-closed)
        esperado = _hotp(bytes(secreto), contador)
        if hmac.compare_digest(esperado, codigo):
            return True
    return False
