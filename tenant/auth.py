"""FASE 6A — Autenticación: login, logout, sesiones seguras.

- Contraseñas: PBKDF2-HMAC-SHA256 (ver crypto.py), nunca texto plano.
- Sesiones: token opaco aleatorio (secrets), expiración configurable,
  revocación en logout. El token NUNCA se guarda en logs.
- La identificación del usuario autenticado siempre pasa por get_tenant_context().
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime, timedelta, timezone

from . import audit as audit_mod
from .models import Session, TenantContext, User
from .store import TenantStore, utcnow_iso, DEMO_COMPANY_ID
from .crypto import hash_password, verify_password
from . import roles as roles_mod
from .security import new_csrf_token
from . import password_policy as pwd_policy_mod

SESSION_TTL_HOURS = 8


class AuthError(Exception):
    """Error de autenticación. Mensajes genéricos: no revelan si el email existe."""


# SEG-06: hash ficticio para verificación "dummy". Cuando el email no
# existe, se verifica la contraseña contra este hash con el MISMO esquema
# PBKDF2, para no revelar por tiempo si la cuenta existe. Reduce la
# diferencia, no la elimina por completo (ver docstring de login).
_dummy_hash: str | None = None


def _dummy_hash_value() -> str:
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password(secrets.token_urlsafe(16))
    return _dummy_hash


def _login_failed(store: TenantStore, company_id: str, user_id: str,
                  email_norm: str, reason: str) -> None:
    """Registra el fallo con el motivo INTERNO y lanza error genérico.

    `reason` ("unknown_user" | "bad_password" | "disabled" |
    "company_inactive") queda solo en la auditoría del servidor: nunca
    se expone al navegador. El mensaje externo es siempre el mismo.
    """
    audit_mod.log_event(
        store, company_id=company_id, user_id=user_id,
        action=audit_mod.LOGIN_FAILED, result="failed",
        metadata={"email": email_norm, "reason": reason},
    )
    raise AuthError("credenciales inválidas")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def create_user(store: TenantStore, *, company_id: str, email: str, name: str,
                password: str, role_id: str = "viewer",
                actor_ctx: TenantContext | None = None) -> User:
    """Crea un usuario. Valida inputs; audita USER_CREATED."""
    email = (email or "").strip().lower()
    name = (name or "").strip()
    if not email or "@" not in email or len(email) > 254:
        raise AuthError("email inválido")
    if not name or len(name) > 120:
        raise AuthError("nombre inválido")
    # SEG-05: política centralizada para NUEVAS contraseñas (mínimo 12).
    # Las heredadas (8-11) no pasan por aquí: se siguen verificando.
    pwd_errors = pwd_policy_mod.validate_new_password(password)
    if pwd_errors:
        raise AuthError(pwd_errors[0])
    if not roles_mod.is_valid_role(role_id):
        raise AuthError("rol inválido")
    company = store.get_company(company_id)
    if company is None or company.status != "active":
        raise AuthError("empresa inválida")
    if store.get_user_by_email(email) is not None:
        raise AuthError("email ya registrado")

    user = User(
        user_id="USR-" + uuid.uuid4().hex[:12],
        company_id=company_id,
        email=email,
        name=name,
        status="active",
        role_id=role_id,
        password_hash=hash_password(password),
        created_at=utcnow_iso(),
        updated_at=utcnow_iso(),
    )
    store.save_user(user)
    audit_mod.log_event(
        store, company_id=company_id,
        user_id=actor_ctx.user_id if actor_ctx else user.user_id,
        action=audit_mod.USER_CREATED, resource=f"user:{user.user_id}",
        metadata={"email": email, "role_id": role_id},
    )
    return user


def update_user(store: TenantStore, user_id: str, *, name=None, role_id=None,
                status=None, actor_ctx: TenantContext | None = None) -> User:
    user = store.get_user(user_id)
    if user is None:
        raise AuthError("usuario no encontrado")
    changes = {}
    if name is not None:
        name = name.strip()
        if not name or len(name) > 120:
            raise AuthError("nombre inválido")
        user.name = name
        changes["name"] = True
    if role_id is not None:
        if not roles_mod.is_valid_role(role_id):
            raise AuthError("rol inválido")
        user.role_id = role_id
        changes["role_id"] = role_id
    if status is not None:
        if status not in ("active", "disabled"):
            raise AuthError("estado inválido")
        user.status = status
        changes["status"] = status
    user.updated_at = utcnow_iso()
    store.save_user(user)
    audit_mod.log_event(
        store, company_id=user.company_id,
        user_id=actor_ctx.user_id if actor_ctx else user.user_id,
        action=audit_mod.USER_DISABLED if status == "disabled" else audit_mod.USER_UPDATED,
        resource=f"user:{user.user_id}", metadata=changes,
    )
    return user


def login(store: TenantStore, email: str, password: str) -> tuple[str, TenantContext]:
    """Autentica y crea una sesión. Devuelve (token, TenantContext).

    SEG-06: el comportamiento EXTERNO es homogéneo para email
    inexistente, contraseña incorrecta y usuario deshabilitado: siempre
    AuthError("credenciales inválidas"). El motivo queda solo en la
    auditoría interna. Para no revelar por tiempo si el email existe,
    un email desconocido se "verifica" contra un hash ficticio con el
    mismo esquema PBKDF2 (reduce la diferencia de tiempos; no se
    afirma inmunidad estadística).
    """
    email_norm = (email or "").strip().lower()
    user = store.get_user_by_email(email_norm)
    if user is None:
        verify_password(password or "", _dummy_hash_value())
        _login_failed(store, "", "", email_norm, "unknown_user")
    # Se verifica el hash ANTES de mirar el estado: "deshabilitado" y
    # "clave incorrecta" consumen el mismo trabajo.
    password_ok = verify_password(password or "", user.password_hash)
    if user.status != "active" or not password_ok:
        reason = "disabled" if user.status != "active" else "bad_password"
        _login_failed(store, user.company_id, user.user_id, email_norm,
                      reason)

    company = store.get_company(user.company_id)
    if company is None or company.status != "active":
        _login_failed(store, user.company_id, user.user_id, email_norm,
                      "company_inactive")

    token = secrets.token_urlsafe(32)
    now = _now()
    session = Session(
        session_id=token,
        user_id=user.user_id,
        company_id=user.company_id,
        created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=(now + timedelta(hours=SESSION_TTL_HOURS)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        revoked=False,
        # SEG-03: cada sesión nace con su propio token CSRF.
        csrf_token=new_csrf_token(),
    )
    store.save_session(session)
    ctx = build_tenant_context(store, user, company_name=company.name)
    audit_mod.log_event(
        store, company_id=user.company_id, user_id=user.user_id,
        action=audit_mod.LOGIN_SUCCESS,
        metadata={"session": audit_mod.token_fingerprint(token)},
    )
    return token, ctx


def build_tenant_context(store: TenantStore, user: User, company_name: str = "") -> TenantContext:
    return TenantContext(
        user_id=user.user_id,
        company_id=user.company_id,
        role=user.role_id,
        permissions=roles_mod.permissions_for(user.role_id),
        email=user.email,
        user_name=user.name,
        company_name=company_name,
    )


def get_tenant_context(store: TenantStore, token: str) -> TenantContext:
    """Identifica al usuario autenticado desde el token de sesión.

    Rechaza: token ausente, sesión inexistente, revocada o expirada,
    usuario deshabilitado, empresa deshabilitada.
    """
    if not token:
        raise AuthError("sesión inválida")
    session = store.get_session(token)
    if session is None or session.revoked:
        raise AuthError("sesión inválida")
    try:
        exp = datetime.strptime(session.expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except Exception:
        raise AuthError("sesión inválida")
    if exp < _now():
        raise AuthError("sesión expirada")

    user = store.get_user(session.user_id)
    if user is None or user.status != "active":
        raise AuthError("sesión inválida")
    company = store.get_company(user.company_id)
    if company is None or company.status != "active":
        raise AuthError("sesión inválida")
    # Defensa en profundidad: la sesión no puede cambiar de empresa.
    if session.company_id != user.company_id:
        raise AuthError("sesión inválida")
    return build_tenant_context(store, user, company_name=company.name)


def logout(store: TenantStore, token: str) -> bool:
    """Revoca la sesión. Devuelve True si existía."""
    session = store.get_session(token) if token else None
    if session is None:
        return False
    session.revoked = True
    store.save_session(session)
    audit_mod.log_event(
        store, company_id=session.company_id, user_id=session.user_id,
        action=audit_mod.LOGOUT,
        metadata={"session": audit_mod.token_fingerprint(token)},
    )
    return True


DEMO_SESSION_TTL_HOURS = 1


def _demo_viewer(store: TenantStore):
    """Usuario lector de la empresa demo (solo lectura)."""
    for u in store.list_users_by_company(DEMO_COMPANY_ID):
        if u.status == "active" and u.role_id == "viewer":
            return u
    return None


def enter_demo(store: TenantStore, ctx: TenantContext,
               origin_token: str) -> tuple[str, TenantContext]:
    """Crea una sesión de demostración ligada a la empresa demo-retail.

    Requiere un contexto autenticado válido (el llamante ya inició sesión).
    La sesión demo usa el usuario lector de la demo (rol viewer: solo
    lectura) y expira en 1 hora. La sesión origen NO se revoca; su token
    queda guardado en el registro de la sesión demo (solo servidor) para
    poder volver sin pedir login. Los datos nunca se mezclan: la sesión
    es o de la empresa real o de la demo, nunca ambas.
    """
    if not origin_token:
        raise AuthError("sesión inválida")
    demo_company = store.get_company(DEMO_COMPANY_ID)
    if (demo_company is None or demo_company.status != "active"
            or not demo_company.is_demo):
        raise AuthError("demo no disponible")
    demo_user = _demo_viewer(store)
    if demo_user is None:
        raise AuthError("demo no disponible")

    token = secrets.token_urlsafe(32)
    now = _now()
    session = Session(
        session_id=token,
        user_id=demo_user.user_id,
        company_id=DEMO_COMPANY_ID,
        created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=(now + timedelta(hours=DEMO_SESSION_TTL_HOURS)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        revoked=False,
        origin_session_id=origin_token,
        # SEG-03: la sesión demo tiene su propio token CSRF (no reutiliza
        # el de la sesión origen).
        csrf_token=new_csrf_token(),
    )
    store.save_session(session)
    demo_ctx = build_tenant_context(store, demo_user,
                                    company_name=demo_company.name)
    audit_mod.log_event(
        store, company_id=DEMO_COMPANY_ID, user_id=ctx.user_id,
        action=audit_mod.DEMO_ENTER,
        metadata={
            "session": audit_mod.token_fingerprint(token),
            "origin_company_id": ctx.company_id,
            "origin_email": ctx.email,
        },
    )
    return token, demo_ctx


def exit_demo(store: TenantStore, demo_token: str) -> str:
    """Sale de la demo: revoca la sesión demo y devuelve el token de la
    sesión origen si sigue válida ("" si expiró o fue revocada).

    Solo acepta tokens cuya sesión pertenezca a la empresa demo; nunca
    revoca ni devuelve sesiones ajenas.
    """
    session = store.get_session(demo_token) if demo_token else None
    if (session is None or session.revoked
            or session.company_id != DEMO_COMPANY_ID
            or not session.origin_session_id):
        raise AuthError("no hay sesión demo activa")
    origin_token = session.origin_session_id
    session.revoked = True
    store.save_session(session)
    audit_mod.log_event(
        store, company_id=DEMO_COMPANY_ID, user_id=session.user_id,
        action=audit_mod.DEMO_EXIT,
        metadata={"session": audit_mod.token_fingerprint(demo_token)},
    )
    origin = store.get_session(origin_token)
    if origin is None or origin.revoked:
        return ""
    try:
        exp = datetime.strptime(
            origin.expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc)
    except Exception:
        return ""
    if exp < _now():
        return ""
    origin_company = store.get_company(origin.company_id)
    if origin_company is None or origin_company.status != "active":
        return ""
    return origin_token


def get_csrf_token(store: TenantStore, session_token: str) -> str:
    """Devuelve el token CSRF de una sesión válida, o "" si no aplica.

    SEG-03: el frontend lo obtiene tras el login (respuesta JSON) y con
    GET /api/me (recargas), y lo envía en X-CSRF-Token. Nunca va a logs.
    """
    session = store.get_session(session_token) if session_token else None
    if session is None or session.revoked:
        return ""
    return session.csrf_token or ""
