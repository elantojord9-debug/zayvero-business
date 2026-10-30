"""FASE 6A — Modelos de datos del Multi-Tenant Foundation.

Entidades: Company, User, Role, Permission, AuditEvent, Session, TenantContext.

100% serializables a JSON. Sin secretos en las representaciones públicas.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Company:
    company_id: str
    name: str
    status: str = "active"  # active | disabled
    is_demo: bool = False
    created_at: str = ""
    updated_at: str = ""
    # FASE 7C — identidad y configuración internacional por empresa.
    # Campos opcionales con valor "" (sin configurar): empresas existentes
    # cargan sin cambios (compatibilidad total hacia atrás).
    country: str = ""
    industry: str = ""
    language: str = ""       # es | en (vacío = sin configurar)
    currency: str = ""       # código ISO 4217 (vacío = sin configurar)
    timezone: str = ""       # IANA (vacío = sin configurar)
    date_format: str = ""    # dd/mm/yyyy | mm/dd/yyyy | yyyy-mm-dd
    number_format: str = ""  # es | en

    def to_dict(self):
        return asdict(self)


@dataclass
class User:
    user_id: str
    company_id: str
    email: str
    name: str
    status: str = "active"  # active | disabled
    role_id: str = "viewer"
    # NUNCA se expone fuera del store. Formato: "pbkdf2_sha256$iteraciones$salt_hex$hash_hex"
    password_hash: str = ""
    created_at: str = ""
    updated_at: str = ""

    def to_dict(self, include_secret: bool = False):
        d = asdict(self)
        if not include_secret:
            d.pop("password_hash", None)
        return d


@dataclass
class Role:
    role_id: str
    name: str

    def to_dict(self):
        return asdict(self)


@dataclass
class Permission:
    permission_id: str
    name: str

    def to_dict(self):
        return asdict(self)


@dataclass
class Session:
    # Sprint 3 (PRIORIDAD 1): session_id es un id INTERNO aleatorio (PK).
    # El Bearer token opaco NUNCA se persiste: la BD guarda solo su
    # verificador (token_sha/token_verifier/token_salt, ver
    # tenant/session_tokens.py). El token real solo viaja en la cookie
    # HttpOnly del cliente y en memoria efímera del servidor.
    session_id: str
    user_id: str
    company_id: str
    created_at: str = ""
    expires_at: str = ""
    revoked: bool = False
    # SHA-256 hex(token): clave de búsqueda determinista e indexada.
    token_sha: str = ""
    # SHA-256 hex(salt || token): prueba de posesión (comparación en
    # tiempo constante).
    token_verifier: str = ""
    # Sal aleatoria por sesión (hex).
    token_salt: str = ""
    # SEG-03: token CSRF sincronizado con la sesión. Se genera al crear la
    # sesión, viaja al frontend (JSON, nunca en logs) y el frontend lo
    # devuelve en la cabecera X-CSRF-Token en operaciones con estado.
    # Evaluación de sensibilidad (sprint 3): se guarda en claro porque NO
    # es un Bearer token — solo sirve junto a la cookie de sesión — y el
    # cliente lo necesita en cada recarga vía /api/me. Ver
    # docs/tenant-postgres.md.
    csrf_token: str = ""
    # Demo: id INTERNO de la sesión origen (nunca su token). Al salir de
    # la demo se ROTA el token de la sesión origen. "" = sesión normal.
    origin_session_ref: str = ""
    # BLOQUE A SEGURIDAD (2026-10-13): estado del segundo factor.
    # "" = sin MFA (o MFA ya completado en sesiones viejas);
    # "pendiente" = login con contraseña OK, falta el código TOTP;
    # "ok" = MFA completado. get_tenant_context() rechaza "pendiente"
    # (fail-closed: sin MFA completo no hay acceso a datos). Las filas
    # viejas sin la clave cargan "" por el default (compatibilidad total).
    mfa_estado: str = ""

    def to_dict(self):
        return asdict(self)


@dataclass
class AuditEvent:
    audit_id: str
    company_id: str
    user_id: str
    action: str
    resource: str = ""
    timestamp: str = ""
    result: str = "ok"  # ok | denied | failed
    metadata: dict = field(default_factory=dict)

    def to_dict(self):
        return asdict(self)


@dataclass
class TenantContext:
    """Contexto de la sesión autenticada.

    El company_id SIEMPRE proviene de la sesión, nunca de un parámetro
    enviado por el usuario. Todo el código de negocio debe derivarlo de aquí.
    """

    user_id: str
    company_id: str
    role: str
    permissions: list = field(default_factory=list)
    email: str = ""
    user_name: str = ""
    company_name: str = ""

    def to_dict(self):
        return asdict(self)

    def has(self, permission: str) -> bool:
        return permission in self.permissions
