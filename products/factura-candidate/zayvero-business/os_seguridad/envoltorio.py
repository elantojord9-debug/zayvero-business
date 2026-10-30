"""BLOQUE A SEGURIDAD — Custodia del secreto TOTP ligada a la contraseña.

Problema: el servidor necesita el secreto TOTP para verificar códigos,
pero guardarlo en claro en la BD permitiría a quien tenga el dump
suplantar el segundo factor. No hay AES en stdlib y no se quiere gestión
de llaves maestras: la solución es LIGAR el secreto a la contraseña del
usuario (que solo existe en claro en memoria durante el request).

Construcción (cifrado de flujo con HMAC-SHA256 como PRF, estilo CTR):
    keystream_j = HMAC-SHA256(clave=contraseña_utf8,
                              msg=b"zayvero-mfa/v1" || sal || LE32(j))
    keystream   = keystream_0 || keystream_1 || ... (truncado a 20 bytes)
    cifrado     = secreto XOR keystream
    sha         = SHA-256(secreto)   (compromiso de integridad)

Propiedades de seguridad:
- Sin la contraseña EN CLARO el secreto es irrecuperable: con el dump
  completo (cifrado + sal + sha) el atacante debe romper primero el
  PBKDF2 de la contraseña (tenant/crypto.py). La sal es única por
  inscripción: no hay reutilización de keystream entre usuarios.
- Con SOLO la contraseña (p. ej. phishing) tampoco alcanza: falta el
  cifrado que vive en la BD. Doble factor de verdad: hacen falta los
  dos elementos.
- `desenvolver_secreto` verifica SHA-256(secreto) en tiempo constante:
  contraseña incorrecta, cifrado manipulado o contraseña cambiada sin
  re-envolver -> ErrorSeguridad (fail-closed). El cambio de contraseña
  sin re-envolver deja el MFA pidiendo re-inscripción, nunca abierto.
- La contraseña en claro NUNCA se persiste ni se loguea.

Códigos de recuperación: 10 por inscripción, un solo uso. Se guardan
SOLO como SHA-256 (se verifican por comparación: el hash basta, no se
necesita el valor para ninguna operación criptográfica). 16 caracteres
de un alfabeto de 32 símbolos sin ambigüedades (sin 0/O, 1/I/l) = 80
bits de entropía por código.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import struct

from .errores import ErrorSeguridad

ETIQUETA = b"zayvero-mfa/v1"
SECRETO_LEN = 20
SAL_LEN = 16

_ALFABETO_CODIGOS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 32 símbolos
_GRUPOS_CODIGO = 4
_LARGO_GRUPO = 4  # 16 caracteres = 80 bits


def _keystream(password: str, sal: bytes, n_bytes: int) -> bytes:
    clave = (password or "").encode("utf-8")
    if not clave:
        raise ErrorSeguridad("contraseña inválida")
    out = b""
    j = 0
    while len(out) < n_bytes:
        out += hmac.new(
            clave, ETIQUETA + sal + struct.pack("<I", j),
            hashlib.sha256).digest()
        j += 1
    return out[:n_bytes]


def _xor(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))


def envolver_secreto(secreto: bytes, password: str,
                     sal: bytes | None = None) -> dict:
    """Envuelve el secreto con la contraseña. Devuelve dict con hex.

    Claves: {"sal", "cifrado", "sha"}. Ningún valor permite recuperar el
    secreto sin la contraseña en claro.
    """
    if not secreto or len(secreto) != SECRETO_LEN:
        raise ErrorSeguridad("secreto inválido")
    sal = sal if sal is not None else secrets.token_bytes(SAL_LEN)
    ks = _keystream(password, sal, SECRETO_LEN)
    cifrado = _xor(bytes(secreto), ks)
    return {
        "sal": sal.hex(),
        "cifrado": cifrado.hex(),
        "sha": hashlib.sha256(bytes(secreto)).hexdigest(),
    }


def desenvolver_secreto(cifrado_hex: str, sal_hex: str, sha_hex: str,
                        password: str) -> bytes:
    """Recupera el secreto con la contraseña. Falla cerrado ante
    contraseña incorrecta, manipulación o formato inválido."""
    try:
        cifrado = bytes.fromhex(cifrado_hex or "")
        sal = bytes.fromhex(sal_hex or "")
        sha = (sha_hex or "").strip().lower()
        if len(cifrado) != SECRETO_LEN or len(sal) != SAL_LEN or len(sha) != 64:
            raise ErrorSeguridad("envoltorio inválido")
        ks = _keystream(password, sal, SECRETO_LEN)
        secreto = _xor(cifrado, ks)
    except ErrorSeguridad:
        raise
    except Exception:
        raise ErrorSeguridad("envoltorio inválido")
    if not hmac.compare_digest(hashlib.sha256(secreto).hexdigest(), sha):
        # Contraseña incorrecta, cifrado manipulado o contraseña cambiada
        # sin re-envolver: no se distingue hacia afuera (fail-closed).
        raise ErrorSeguridad("no se pudo verificar el segundo factor")
    return secreto


def rotar_envoltorio(cifrado_hex: str, sal_hex: str, sha_hex: str,
                     password_anterior: str, password_nueva: str) -> dict:
    """Re-envuelve el secreto con una contraseña nueva (cambio de clave).

    Requiere la contraseña ANTERIOR en claro: sin ella no hay forma de
    recuperar el secreto y el MFA queda pidiendo re-inscripción
    (documentado en docs/bloque-seguridad.md).
    """
    secreto = desenvolver_secreto(cifrado_hex, sal_hex, sha_hex,
                                  password_anterior)
    return envolver_secreto(secreto, password_nueva)


def generar_codigos_recuperacion(n: int = 10) -> list[str]:
    """10 códigos de un solo uso, formato XXXX-XXXX-XXXX-XXXX."""
    codigos = set()
    while len(codigos) < n:
        grupos = ["".join(secrets.choice(_ALFABETO_CODIGOS)
                          for _ in range(_LARGO_GRUPO))
                  for _ in range(_GRUPOS_CODIGO)]
        codigos.add("-".join(grupos))
    return sorted(codigos)


def normalizar_codigo(codigo: str) -> str:
    """Mayúsculas, sin guiones ni espacios: la comparación es insensible
    a cómo lo teclee el usuario."""
    return "".join((codigo or "").upper().split()).replace("-", "")


def hash_codigo(codigo: str) -> str:
    """SHA-256 hex del código normalizado. Es lo ÚNICO que se persiste."""
    norm = normalizar_codigo(codigo)
    if len(norm) != _GRUPOS_CODIGO * _LARGO_GRUPO:
        raise ErrorSeguridad("código de recuperación inválido")
    return hashlib.sha256(norm.encode("ascii")).hexdigest()
