"""FASE 6A — Multi-Tenant Foundation + Authentication.

API pública del paquete:

  from tenant import (
      TenantStore, TenantContext,
      create_company, create_user, login, logout, get_tenant_context,
      require_permission, scoped_company_id, check_data_access,
      PermissionDenied, AuthError, DEMO_COMPANY_ID,
  )
"""

from __future__ import annotations

from .models import (
    AuditEvent, Company, Permission, Role, Session, TenantContext, User,
)
from .roles import PERMISSIONS, ROLES, ROLE_PERMISSIONS, is_valid_role, permissions_for
from .store import TenantStore, dataset_owner, utcnow_iso, DEMO_COMPANY_ID, DEMO_DATASET_ID
from .crypto import hash_password, verify_password
from .auth import (
    AuthError, create_user, update_user, login, logout,
    get_tenant_context, build_tenant_context,
)
from .authorization import (
    PermissionDenied, require_permission, scoped_company_id,
    require_company_admin, require_user_admin, check_data_access,
)
from . import audit as audit


def create_company(store: TenantStore, name: str, is_demo: bool = False) -> Company:
    """Crea una empresa. Valida inputs."""
    import uuid
    name = (name or "").strip()
    if not name or len(name) > 160:
        raise AuthError("nombre de empresa inválido")
    company = Company(
        company_id="CMP-" + uuid.uuid4().hex[:12],
        name=name,
        status="active",
        is_demo=is_demo,
        created_at=utcnow_iso(),
        updated_at=utcnow_iso(),
    )
    store.save_company(company)
    return company


__all__ = [
    "TenantStore", "TenantContext", "Company", "User", "Role", "Permission",
    "Session", "AuditEvent",
    "PERMISSIONS", "ROLES", "ROLE_PERMISSIONS", "is_valid_role", "permissions_for",
    "dataset_owner", "utcnow_iso", "DEMO_COMPANY_ID", "DEMO_DATASET_ID",
    "hash_password", "verify_password",
    "AuthError", "create_user", "update_user", "login", "logout",
    "get_tenant_context", "build_tenant_context",
    "PermissionDenied", "require_permission", "scoped_company_id",
    "require_company_admin", "require_user_admin", "check_data_access",
    "create_company", "audit",
]
