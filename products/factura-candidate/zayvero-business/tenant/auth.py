"""FASE 6A — Autenticación: login, logout, sesiones seguras.

- Contraseñas: PBKDF2-HMAC-SHA256 (ver crypto.py), nunca texto plano.
- Sesiones (sprint 3, PRIORIDAD 1): token opaco aleatorio de 256 bits; la
  BD guarda SOLO su verificador (ver tenant/session_tokens.py), nunca el
  token reutilizable. Cada request recomputa el lookup y compara el
  verificador en tiempo constante. Expiración y revocación se verifican
  en cada lookup (el store falla cerrado). El token NUNCA se guarda en
  logs ni en la BD.
- Login multi-empresa (sprint 4, issue #3 — ANTI-ENUMERACIÓN): el email es
  único POR EMPRESA, no global. El login NUNCA revela nombres/IDs de
  empresas antes de validar credenciales: la contraseña se verifica
  contra el hash de CADA cuenta con ese email ANTES de comunicar nada.
  Contrato completo en el docstring de login() y en
  docs/login-anti-enumeracion.md.
- La identificación del usuario autenticado siempre pasa por get_tenant_context().
- BLOQUE A SEGURIDAD (2026-10-13): get_tenant_context() rechaza sesiones
  `mfa_pending` (fail-closed: sin MFA completo no hay acceso a datos).
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
from . import session_tokens as st_mod

SESSION_TTL_HOURS = 8


class AuthError(Exception):
    """Error de autenticación. Mensajes genéricos: no revelan si el email existe."""


class AmbiguousLogin(AuthError):
    """La contraseña validó en más de una cuenta del mismo email.

    Se lanza SOLO DESPUÉS de validar la contraseña contra el hash de
    cada cuenta con ese email (sprint 4, issue #3 — anti-enumeración):
    el solicitante ya probó conocer una contraseña válida, así que
    revelar las empresas cuyos hashes validaron no filtra nada a un
    atacante que solo tenga el email.

    El llamante DEBE pedir al usuario que elija empresa (company_id) y
    reintentar el login con ese parámetro; el reintento vuelve a validar
    la contraseña contra la cuenta elegida. Nunca se auto-elige.
    `companies`: lista de {"company_id", "name"} con SOLO las empresas
    activas cuyos hashes de cuenta validaron la contraseña.
    """

    def __init__(self, companies: list[dict]):
        super().__init__("seleccione la empresa")
        self.companies = companies


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
    # Sprint 3: el email es único POR EMPRESA (no global). Solo se
    # rechaza si ya existe en ESTA empresa.
    email_en_empresa = any(
        u.email.strip().lower() == email
        for u in store.list_users_by_company(company_id))
    if email_en_empresa:
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


def _new_session(user: User, *, origin_session_ref: str = "",
                 ttl_hours: int = SESSION_TTL_HOURS) -> tuple[str, Session]:
    """Crea la Session con verificadores y devuelve (token_en_claro, Session).

    El token en claro solo vive en memoria efímera: el llamador lo pone en
    la cookie HttpOnly y lo olvida. La Session persistida lleva solo
    verificadores (ver tenant/session_tokens.py).
    """
    token = st_mod.new_token()
    lookup, verifier, salt = st_mod.make_verifier(token)
    now = _now()
    session = Session(
        session_id=st_mod.new_session_id(),
        user_id=user.user_id,
        company_id=user.company_id,
        created_at=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        expires_at=(now + timedelta(hours=ttl_hours)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"),
        revoked=False,
        token_sha=lookup,
        token_verifier=verifier,
        token_salt=salt,
        # SEG-03: cada sesión nace con su propio token CSRF.
        csrf_token=new_csrf_token(),
        origin_session_ref=origin_session_ref,
    )
    return token, session


def _finish_login(store: TenantStore, user: User, company) -> tuple[str, TenantContext]:
    """Crea la sesión y el contexto tras credenciales ya validadas."""
    token, session = _new_session(user)
    store.save_session(session)
    ctx = build_tenant_context(store, user, company_name=company.name)
    audit_mod.log_event(
        store, company_id=user.company_id, user_id=user.user_id,
        action=audit_mod.LOGIN_SUCCESS,
        metadata={"session": audit_mod.token_fingerprint(token)},
    )
    return token, ctx


def login(store: TenantStore, email: str, password: str,
          company_id: str | None = None) -> tuple[str, TenantContext]:
    """Autentica y crea una sesión. Devuelve (token, TenantContext).

    CONTRATO ANTI-ENUMERACIÓN (sprint 4, issue #3):
    Ningún nombre ni ID de empresa se revela antes de validar
    credenciales. Un atacante con solo el email no aprende nada:

    1. Login con empresa explícita (email + company_id + password):
       TODO fallo devuelve el genérico AuthError("credenciales
       inválidas"): email inexistente, empresa inexistente para ese
       email, contraseña incorrecta, usuario deshabilitado y empresa
       inactiva son INDISTINGUIBLES (mismo mensaje; el no-encontrado
       hace el mismo trabajo PBKDF2 que el encontrado).

    2. Login sin company_id: la contraseña se verifica contra el hash
       de CADA cuenta con ese email ANTES de comunicar nada. Sea
       `matched` = cuentas cuya contraseña validó + usuario activo +
       empresa activa:
       - 0 → AuthError genérico (el motivo interno —"unknown_user",
         "bad_password", "disabled", "company_inactive"— queda solo en
         la auditoría del servidor, nunca sale).
       - 1 → login directo en esa cuenta. La contraseña identifica la
         cuenta sin ambigüedad; no se "elige" empresa por defecto, se
         autentica la única cuenta válida.
       - >1 → AmbiguousLogin con SOLO las empresas cuyos hashes
         validaron. Es post-autenticación: el solicitante probó conocer
         la contraseña. El cliente muestra el selector y reintenta con
         company_id (el reintento vuelve a validar la contraseña).

    SEG-06: email inexistente se "verifica" contra un hash ficticio con
    el mismo esquema PBKDF2 (reduce la diferencia de tiempos; no se
    afirma inmunidad estadística). Residual documentado: un email con N
    cuentas consume N verificaciones PBKDF2; la diferencia es del orden
    de una verificación y no revela nombres/IDs.

    Mapeo HTTP (webapp/server.py, tenant/server.py):
    - éxito → 200 + cookie de sesión.
    - AuthError → 401 {"error": "credenciales incorrectas"} (genérico).
    - AmbiguousLogin → 409 {"error": "seleccione la empresa",
      "companies": [...]} (solo post-validación de contraseña).

    BLOQUE A SEGURIDAD (2026-10-13): tras el login exitoso, el llamador
    (os_seguridad.mfa.login_con_mfa) marca la sesión `mfa_pending` si el
    usuario tiene MFA; get_tenant_context() la rechaza hasta completar
    el segundo factor.
    """

    def _password_ok(plain: str, stored_hash: str) -> bool:
        return verify_password(plain or "", stored_hash)

    email_norm = (email or "").strip().lower()
    company_id = (company_id or "").strip() or None
    candidates = store.list_users_by_email(email_norm)

    # -- Ruta 1: empresa explícita -------------------------------------
    if company_id:
        user = next((u for u in candidates
                     if u.company_id == company_id), None)
        if user is None:
            # Ni el email ni la empresa se confirman: fallo genérico con
            # el mismo trabajo PBKDF2 (no revela por tiempo).
            _password_ok(password or "", _dummy_hash_value())
            _login_failed(store, company_id, "", email_norm,
                          "unknown_user_or_company")
        # Se verifica el hash ANTES de mirar el estado: "deshabilitado"
        # y "clave incorrecta" consumen el mismo trabajo.
        password_ok = _password_ok(password or "", user.password_hash)
        if user.status != "active" or not password_ok:
            reason = ("disabled" if user.status != "active"
                      else "bad_password")
            _login_failed(store, user.company_id, user.user_id, email_norm,
                          reason)
        company = store.get_company(user.company_id)
        if company is None or company.status != "active":
            _login_failed(store, user.company_id, user.user_id, email_norm,
                          "company_inactive")
        return _finish_login(store, user, company)

    # -- Ruta 2: sin empresa — validar la contraseña contra TODOS los --
    # -- candidatos ANTES de revelar nada.                           --
    verified: list[User] = []
    if not candidates:
        _password_ok(password or "", _dummy_hash_value())
    for u in candidates:
        if _password_ok(password or "", u.password_hash):
            verified.append(u)

    matched: list[tuple[User, object]] = []
    for u in verified:
        if u.status != "active":
            continue
        company = store.get_company(u.company_id)
        if company is not None and company.status == "active":
            matched.append((u, company))

    if not matched:
        # Motivo INTERNO para operar (auditoría del servidor); el
        # mensaje externo es siempre el genérico.
        if not candidates:
            reason = "unknown_user"
        elif not verified:
            reason = "bad_password"
        elif any(u.status != "active" for u in verified):
            reason = "disabled"
        else:
            reason = "company_inactive"
        cid = candidates[0].company_id if candidates else ""
        uid = candidates[0].user_id if candidates else ""
        _login_failed(store, cid, uid, email_norm, reason)

    if len(matched) > 1:
        # Post-autenticación: la contraseña validó en varias cuentas;
        # solo esas empresas se revelan (nunca por el email solo).
        empresas = [{"company_id": c.company_id, "name": c.name}
                    for _, c in matched]
        audit_mod.log_event(
            store, company_id="", user_id="",
            action=audit_mod.LOGIN_FAILED, result="failed",
            metadata={"email": email_norm,
                      "reason": "ambiguous_company_post_auth",
                      "empresas": len(empresas)},
        )
        raise AmbiguousLogin(empresas)

    user, company = matched[0]
    return _finish_login(store, user, company)


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
    # BLOQUE A SEGURIDAD (2026-10-13): fail-closed para MFA. Una sesión
    # `mfa_pending` (contraseña OK, falta el código TOTP) NO identifica
    # al usuario para acceso a datos. El segundo paso usa
    # os_seguridad.mfa.sesion_pendiente (vía carve-out HTTP), nunca esta
    # función.
    if getattr(session, "mfa_estado", "") == "pendiente":
        raise AuthError("mfa requerido")
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
    lectura) y expira en 1 hora. La sesión origen NO se revoca.

    Sprint 3: la sesión demo ya NO guarda el token de la sesión origen en
    claro; guarda origin_session_ref (id interno). Al salir, exit_demo
    ROTA el token de la sesión origen (el anterior queda inválido y se
    emite uno nuevo en la cookie). Los datos nunca se mezclan: la sesión
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
    origin_session = store.get_session(origin_token)
    if origin_session is None:
        raise AuthError("sesión inválida")

    token, session = _new_session(
        demo_user,
        origin_session_ref=origin_session.session_id,
        ttl_hours=DEMO_SESSION_TTL_HOURS)
    # demo_user pertenece a DEMO_COMPANY_ID (_demo_viewer filtra por ella).
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
    """Sale de la demo: revoca la sesión demo y ROTA el token de la sesión
    origen. Devuelve el NUEVO token de la sesión origen ("" si expiró, fue
    revocada, o su usuario/empresa ya no están activos: el cliente debe
    pedir login).

    Solo acepta tokens cuya sesión pertenezca a la empresa demo y tenga
    referencia a una sesión origen; nunca revoca ni toca sesiones ajenas.

    Por qué rotación (sprint 3): el servidor NUNCA conoce el token origen
    en claro (solo su verificador), así que no puede "devolver" el mismo
    token. Emitir uno nuevo para la misma sesión (mismo usuario, misma
    empresa, mismo vencimiento) es el equivalente seguro. Efecto lateral
    documentado: otras pestañas con el token anterior reciben 401 tras
    salir de la demo (su token fue rotado).
    """
    session = store.get_session(demo_token) if demo_token else None
    if (session is None or session.revoked
            or session.company_id != DEMO_COMPANY_ID
            or not session.origin_session_ref):
        raise AuthError("no hay sesión demo activa")
    # 1. Revocar la demo primero (si lo demás falla, no queda demo viva).
    session.revoked = True
    store.save_session(session)
    audit_mod.log_event(
        store, company_id=DEMO_COMPANY_ID, user_id=session.user_id,
        action=audit_mod.DEMO_EXIT,
        metadata={"session": audit_mod.token_fingerprint(demo_token)},
    )
    # 2. Resolver la sesión origen por su id interno (sin token).
    origin = store.get_session_by_id(session.origin_session_ref)
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
    user = store.get_user(origin.user_id)
    if user is None or user.status != "active":
        return ""
    origin_company = store.get_company(origin.company_id)
    if origin_company is None or origin_company.status != "active":
        return ""
    # Defensa en profundidad: la sesión no puede cambiar de empresa.
    if origin.company_id != user.company_id:
        return ""
    # 3. Rotación: el token anterior muere; se emite uno nuevo atado a la
    # misma sesión (mismo vencimiento original).
    new_token = st_mod.new_token()
    lookup, verifier, salt = st_mod.make_verifier(new_token)
    origin.token_sha = lookup
    origin.token_verifier = verifier
    origin.token_salt = salt
    store.save_session(origin)
    return new_token


def get_csrf_token(store: TenantStore, session_token: str) -> str:
    """Devuelve el token CSRF de una sesión válida, o "" si no aplica.

    SEG-03: el frontend lo obtiene tras el login (respuesta JSON) y con
    GET /api/me (recargas), y lo envía en X-CSRF-Token. Nunca va a logs.
    """
    session = store.get_session(session_token) if session_token else None
    if session is None or session.revoked:
        return ""
    return session.csrf_token or ""
