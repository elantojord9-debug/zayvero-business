"""Autenticación: PBKDF2 vía tenant.crypto + cookie de sesión firmada (HMAC).

Reutiliza:
- tenant.crypto.hash_password / verify_password (PBKDF2-HMAC-SHA256)
- tenant.password_policy.validate_new_password (claves nuevas)

La sesión viaja en una cookie firmada con HMAC-SHA256 (secreto de
FACTURA_SECRET o generado y guardado en .factura_secret). Expira en 8h.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

import config
from factura_persist import db

# Motores reutilizados del Business OS (solo importación)
from tenant.crypto import hash_password, verify_password
from tenant.password_policy import validate_new_password


def _secret() -> bytes:
    env = os.environ.get(config.SECRET_ENV)
    if env:
        return env.encode("utf-8")
    if os.path.exists(config.SECRET_FILE):
        with open(config.SECRET_FILE, "rb") as fh:
            return fh.read().strip()
    valor = secrets.token_bytes(32)
    with open(config.SECRET_FILE, "wb") as fh:
        fh.write(valor)
    os.chmod(config.SECRET_FILE, 0o600)
    return valor


def _firmar(payload_b64: str) -> str:
    return hmac.new(_secret(), payload_b64.encode("ascii"),
                    hashlib.sha256).hexdigest()


def _sess_version(usuario_id: int) -> int:
    """Versión de sesión del usuario (0 si no existe la columna aún)."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        try:
            fila = db.fetchone(cur, "SELECT sess_version FROM factura_usuarios"
                                    " WHERE id=%s", (usuario_id,))
        except Exception:
            return 0
        return int(fila[0]) if fila and fila[0] is not None else 0
    finally:
        conn.close()


def crear_sesion(usuario: dict) -> str:
    """Cookie value: base64(payload).firma_hex.

    Incluye "sv" (versión de sesión): al cambiar la contraseña la versión
    se incrementa y las cookies viejas quedan invalidadas.

    Incluye "jti" (nonce criptográficamente seguro, único por sesión):
    sin él, dos logins dentro del mismo segundo producirían la misma
    cookie (mismo exp, misma firma) y el logout/revocación de una
    destruiría también la otra. Con el jti, cada sesión es distinguible.
    """
    try:
        sv = int(usuario.get("sess_version", 0))
    except Exception:
        sv = 0
    if not sv and usuario.get("id"):
        sv = _sess_version(usuario["id"])
    payload = {
        "u": usuario["id"],
        "e": usuario["empresa_id"],
        "r": usuario["rol"],
        "sv": sv,
        "jti": secrets.token_urlsafe(32),  # nonce único por sesión
        "exp": int(time.time()) + config.SESSION_HOURS * 3600,
    }
    crudo = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(crudo).decode("ascii")
    return f"{b64}.{_firmar(b64)}"


def version_sesion_ok(payload: dict) -> bool:
    """True si la versión de sesión de la cookie coincide con la vigente
    en BD. Las cookies emitidas antes del P0 no traen "sv": se tratan
    como versión 0 (valor por defecto de la migración 010)."""
    if not payload or not payload.get("u"):
        return False
    try:
        sv_cookie = int(payload.get("sv", 0))
    except Exception:
        return False
    return sv_cookie == _sess_version(payload["u"])


def leer_sesion(cookie_value: str | None) -> dict | None:
    """Valida firma y expiración. Devuelve el payload o None."""
    if not cookie_value or "." not in cookie_value:
        return None
    b64, firma = cookie_value.rsplit(".", 1)
    if not hmac.compare_digest(_firmar(b64), firma):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(b64.encode("ascii")))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time():
        return None
    return payload


def csrf_token(sesion: dict) -> str:
    """Token anti-CSRF derivado de la sesión (sin estado en servidor).

    El "jti" (nonce de sesión) forma parte de la derivación: dos sesiones
    creadas en el mismo segundo obtienen tokens CSRF distintos, y el
    token de una sesión no sirve para la otra. Sesiones legacy sin "jti"
    usan "" (siguen funcionando con el comportamiento anterior)."""
    base = (f"{sesion['u']}:{sesion['e']}:{sesion['exp']}:"
            f"{sesion.get('jti', '')}")
    return hmac.new(_secret(), base.encode("utf-8"),
                    hashlib.sha256).hexdigest()[:32]


def csrf_valido(sesion: dict, token: str | None) -> bool:
    return bool(token) and hmac.compare_digest(csrf_token(sesion),
                                               token or "")


def _fila_usuario_por_email(email: str) -> dict | None:
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(cur, "SELECT * FROM factura_usuarios WHERE email=%s",
                           (email.strip().lower(),))
        return dict(fila) if fila else None
    finally:
        conn.close()


def autenticar(email: str, password: str) -> dict | None:
    """Devuelve el usuario si las credenciales son válidas y está activo."""
    fila = _fila_usuario_por_email(email or "")
    if not fila or not fila.get("activo"):
        return None
    if not verify_password(password or "", fila["hash"]):
        return None
    return fila


def crear_usuario(empresa_id: int, email: str, password: str,
                  rol: str = "cajero") -> dict:
    """Crea un usuario aplicando la política de contraseñas reutilizada."""
    from datetime import datetime, timezone
    problemas = validate_new_password(password)
    if problemas:
        raise ValueError("; ".join(problemas))
    if rol not in config.ROLES:
        raise ValueError(f"rol inválido: {rol!r}")
    email_n = (email or "").strip().lower()
    if "@" not in email_n:
        raise ValueError("email inválido")
    conn = db.connect()
    try:
        cur = conn.cursor()
        existe = db.fetchone(cur, "SELECT id FROM factura_usuarios WHERE email=%s",
                             (email_n,))
        if existe:
            raise ValueError("ese email ya está registrado")
        uid = db.insert_id(
            cur, "INSERT INTO factura_usuarios "
                 "(empresa_id, email, hash, rol, activo, creado) "
                 "VALUES (%s,%s,%s,%s,1,%s)",
            (empresa_id, email_n, hash_password(password), rol,
             datetime.now(timezone.utc).isoformat()))
        with db.tx(conn):
            pass
        return {"id": uid, "empresa_id": empresa_id, "email": email_n,
                "rol": rol}
    finally:
        conn.close()


def cambiar_rol(empresa_id: int, usuario_id: int, rol: str) -> dict:
    """Cambia el rol de un usuario de la empresa (solo admin, en server)."""
    if rol not in config.ROLES:
        raise ValueError(f"rol inválido: {rol!r}")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "UPDATE factura_usuarios SET rol=%s"
                            " WHERE id=%s AND empresa_id=%s",
                       (rol, usuario_id, empresa_id))
            fila = db.fetchone(cur, "SELECT id, email, rol, activo"
                                    " FROM factura_usuarios WHERE id=%s",
                               (usuario_id,))
        if not fila:
            raise ValueError("usuario no encontrado")
        return dict(fila)
    finally:
        conn.close()


def activar_desactivar(empresa_id: int, usuario_id: int,
                       activo: bool) -> dict:
    """Activa o desactiva un usuario (no se puede desactivar a sí mismo:
    el servidor lo valida)."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "UPDATE factura_usuarios SET activo=%s"
                            " WHERE id=%s AND empresa_id=%s",
                       (1 if activo else 0, usuario_id, empresa_id))
            fila = db.fetchone(cur, "SELECT id, email, rol, activo"
                                    " FROM factura_usuarios WHERE id=%s",
                               (usuario_id,))
        if not fila:
            raise ValueError("usuario no encontrado")
        return dict(fila)
    finally:
        conn.close()


def empresa_de_usuario(usuario: dict) -> dict | None:
    """Empresa del usuario (para validar estado en el login)."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(cur, "SELECT * FROM factura_empresas WHERE id=%s",
                           (usuario.get("empresa_id"),))
        return dict(fila) if fila else None
    finally:
        conn.close()


def cambiar_password(usuario_id: int, nueva: str) -> None:
    """Cambia la contraseña e incrementa sess_version: todas las sesiones
    vigentes del usuario quedan invalidadas ("cerrar todas las sesiones")."""
    problemas = validate_new_password(nueva)
    if problemas:
        raise ValueError("; ".join(problemas))
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "UPDATE factura_usuarios SET hash=%s,"
                            " sess_version=sess_version+1 WHERE id=%s",
                       (hash_password(nueva), usuario_id))
    finally:
        conn.close()


# --- CSRF pre-sesión (rutas públicas POST: login, registro, recuperar, -------
# --- restablecer, solicitar-demo, superadmin/login) ---------------------------
# Sin sesión aún no hay token derivado; se usa doble envío firmado: el
# servidor emite una cookie firmada con un token aleatorio y el formulario
# lo reenvía en campo oculto. En el POST se verifica que coincidan y que la
# firma sea válida.

def csrf_anon_nuevo() -> tuple[str, str]:
    """Devuelve (token_para_formulario, valor_cookie_firmada)."""
    token = secrets.token_urlsafe(24)
    firma = hmac.new(_secret(), ("anon:" + token).encode("ascii"),
                     hashlib.sha256).hexdigest()
    return token, f"{token}.{firma}"


def csrf_anon_valido(cookie_value: str | None,
                     token: str | None) -> bool:
    """True si el token del formulario coincide con el de la cookie firmada."""
    if not cookie_value or not token or "." not in cookie_value:
        return False
    t_cookie, firma = cookie_value.rsplit(".", 1)
    esperado = hmac.new(_secret(), ("anon:" + t_cookie).encode("ascii"),
                        hashlib.sha256).hexdigest()
    if not hmac.compare_digest(esperado, firma):
        return False
    return hmac.compare_digest(t_cookie, token)
