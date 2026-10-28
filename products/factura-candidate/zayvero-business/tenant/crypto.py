"""FASE 6A — Criptografía de contraseñas.

Solo stdlib: PBKDF2-HMAC-SHA256 con sal aleatoria por usuario.
NUNCA se almacenan contraseñas en texto plano.
"""

from __future__ import annotations

import hashlib
import hmac
import os

ALGORITHM = "pbkdf2_sha256"
ITERATIONS = 260_000
SALT_BYTES = 16


def hash_password(password: str) -> str:
    """Genera 'pbkdf2_sha256$iteraciones$salt_hex$hash_hex'."""
    if not password or not isinstance(password, str):
        raise ValueError("password inválido")
    salt = os.urandom(SALT_BYTES)
    dk = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, ITERATIONS)
    return f"{ALGORITHM}${ITERATIONS}${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Verifica con comparación en tiempo constante. Formato inválido → False."""
    try:
        algo, iters, salt_hex, hash_hex = stored.split("$", 3)
        if algo != ALGORITHM:
            return False
        dk = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), int(iters)
        )
        return hmac.compare_digest(dk.hex(), hash_hex)
    except Exception:
        return False
