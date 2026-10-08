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
    session_id: str  # token opaco (NO se loguea completo)
    user_id: str
    company_id: str
    created_at: str = ""
    expires_at: str = ""
    revoked: bool = False
    # Sesión de demostración: token de la sesión origen (del usuario que
    # entró a la demo) para poder volver sin pedir login. Solo la usa el
    # servidor; nunca viaja al frontend. "" = sesión normal.
    origin_session_id: str = ""

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
