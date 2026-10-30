"""Sprint 3 (PRIORIDAD 1) — Verificadores de sesión.

La base de datos NUNCA guarda Bearer tokens en claro. Por cada sesión se
persiste únicamente:

  token_sha      = SHA-256 hex(token)            (clave de búsqueda indexada)
  token_verifier = SHA-256 hex(salt || token)    (prueba de posesión)
  token_salt     = sal aleatoria por sesión (hex)

El token real solo existe en dos lugares: la cookie HttpOnly del cliente
y la memoria efímera del servidor durante el request. En cada request el
servidor recomputa token_sha para localizar la fila y compara el
verificador con hmac.compare_digest (tiempo constante).

Por qué SHA-256(salt || token) y no HMAC con secreto del servidor:
  - El token tiene 256 bits de entropía (secrets.token_urlsafe(32)): la
    preimagen de SHA-256 es inviable por fuerza bruta aunque el atacante
    tenga el dump completo (incluida la sal).
  - Sin secreto de servidor no hay nada que rotar/gestionar en el Secret
    File, y un reinicio revalida sesiones solo con la BD (requisito del
    sprint). Con HMAC, perder/rotar el secreto invalidaría todas las
    sesiones o exigiría versionado de secretos.
  - La sal por sesión separa la clave de búsqueda (token_sha, sin sal,
    necesaria para el lookup indexado O(1)) de la prueba de posesión, y
    blinda contra ataques por lote si la entropía del token bajara en
    algún cambio futuro.
  - La comparación del verificador es en tiempo constante
    (hmac.compare_digest) como higiene ante timing side-channels.

Solo stdlib (hashlib/hmac/secrets), igual que tenant/crypto.py.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets

TOKEN_BYTES = 32   # 256 bits de entropía por token
SALT_BYTES = 16    # 128 bits de sal por sesión


def new_token() -> str:
    """Token opaco de sesión: 256 bits, solo viaja en cookie HttpOnly."""
    return secrets.token_urlsafe(TOKEN_BYTES)


def new_session_id() -> str:
    """Id interno aleatorio de la fila de sesión (PK). NO es un secreto."""
    return "SES-" + secrets.token_hex(16)


def lookup_key(token: str) -> str:
    """SHA-256 hex(token): clave de búsqueda determinista e indexada."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def make_verifier(token: str) -> tuple[str, str, str]:
    """Crea (token_sha, token_verifier, token_salt) para un token nuevo."""
    if not token or not isinstance(token, str):
        raise ValueError("token inválido")
    salt = secrets.token_hex(SALT_BYTES)
    verifier = hashlib.sha256(
        bytes.fromhex(salt) + token.encode("utf-8")).hexdigest()
    return lookup_key(token), verifier, salt


def verify_token(token: str, verifier: str, salt: str) -> bool:
    """¿El token presentado corresponde al verificador guardado?

    Comparación en tiempo constante. Cualquier formato inválido -> False
    (falla cerrado).
    """
    try:
        if not token or not verifier or not salt:
            return False
        expected = hashlib.sha256(
            bytes.fromhex(salt) + token.encode("utf-8")).hexdigest()
    except Exception:
        return False
    return hmac.compare_digest(expected, verifier)


def sanitize_datos(datos: dict) -> dict:
    """Quita de `datos` todo secreto en claro (tokens de sesión/origen).

    Solo elimina "origin_session_id" (legacy: token de la sesión origen en
    claro). El "session_id" NUEVO es un id interno aleatorio, no un
    secreto, y se conserva (el loader lo exige como invariante).
    El csrf_token se conserva: ver evaluación en docs/tenant-postgres.md
    (no es un Bearer token y viaja al cliente por diseño).
    """
    d = dict(datos or {})
    d.pop("origin_session_id", None)  # legacy: token de la sesión origen
    return d


def legacy_session_to_new(d: dict) -> tuple["Session", str | None]:
    """Convierte un dict de sesión LEGACY (token en claro) al modelo nuevo.

    Devuelve (Session con verificadores, origin_token_en_claro_o_None).
    El llamador resuelve el token de origen a id interno con el mapa
    token -> nuevo id (ver migrate_legacy_sessions). Los campos sensibles
    en claro NUNCA quedan en la Session resultante.
    """
    from .models import Session

    token = (d.get("session_id") or "").strip()
    if not token:
        raise ValueError("sesión legacy sin token")
    lookup, verifier, salt = make_verifier(token)
    origin_token = (d.get("origin_session_id") or "").strip() or None
    datos = sanitize_datos(d)
    # El session_id legacy ERA el token en claro: se reemplaza por el id
    # interno nuevo (no sensible).
    new_id = new_session_id()
    datos["session_id"] = new_id
    # Paridad de campos no sensibles con el formato anterior.
    datos.setdefault("user_id", d.get("user_id", ""))
    datos.setdefault("company_id", d.get("company_id", ""))
    datos.setdefault("created_at", d.get("created_at", ""))
    datos.setdefault("expires_at", d.get("expires_at", ""))
    datos.setdefault("revoked", bool(d.get("revoked", False)))
    datos.setdefault("csrf_token", d.get("csrf_token", ""))
    return (
        Session(
            session_id=new_id,
            user_id=d.get("user_id", ""),
            company_id=d.get("company_id", ""),
            created_at=d.get("created_at", ""),
            expires_at=d.get("expires_at", ""),
            revoked=bool(d.get("revoked", False)),
            token_sha=lookup,
            token_verifier=verifier,
            token_salt=salt,
            csrf_token=d.get("csrf_token", ""),
            origin_session_ref="",  # lo resuelve migrate_legacy_sessions
        ),
        origin_token,
    )


def migrate_legacy_sessions(dicts: list[dict]) -> list["Session"]:
    """Convierte una lista de dicts legacy a Sessions con verificadores.

    Resuelve origin_session_id (token en claro) -> origin_session_ref
    (id interno) dentro del lote. Idempotente por construcción: cada
    llamada genera sales nuevas, pero el upsert por token_sha en el store
    evita duplicados si se re-ejecuta.
    """
    nuevas: list = []
    pendientes: list[tuple] = []  # (Session, origin_token)
    token_a_id: dict[str, str] = {}
    for d in dicts:
        try:
            ses, origin_token = legacy_session_to_new(d)
        except ValueError:
            continue  # fila legacy corrupta: se omite (falla cerrado)
        nuevas.append(ses)
        pendientes.append((ses, origin_token))
        t = (d.get("session_id") or "").strip()
        if t:
            token_a_id[t] = ses.session_id
    for ses, origin_token in pendientes:
        if origin_token and origin_token in token_a_id:
            ses.origin_session_ref = token_a_id[origin_token]
        # Si el origen no está en el lote, queda "" y exit_demo falla
        # cerrado ("no hay sesión demo activa").
    return nuevas
