"""FASE 6A — Autorización central + protección IDOR.

CAPA CENTRAL: todo el código de negocio pasa por aquí. No se duplican
reglas de permisos en otros módulos.

Flujo: Authentication → TenantContext → Authorization → Business Service → Business Data.

Protección IDOR: el company_id NUNCA se acepta desde un parámetro del
usuario. Si un endpoint recibe un company_id explícito, se compara contra
el TenantContext y cualquier discrepancia se rechaza con PERMISSION_DENIED
(aunque el usuario conozca el company_id de otra empresa).
"""

from __future__ import annotations

from . import audit as audit_mod
from .models import TenantContext
from .store import TenantStore


class PermissionDenied(Exception):
    """Acceso denegado. Mensaje genérico: no revela datos de otros tenants."""


def require_permission(store: TenantStore, ctx: TenantContext, permission: str,
                       resource: str = "") -> None:
    """Permite o rechaza. Denegado → PermissionDenied + AuditEvent PERMISSION_DENIED."""
    if ctx.has(permission):
        return
    audit_mod.log_event(
        store, company_id=ctx.company_id, user_id=ctx.user_id,
        action=audit_mod.PERMISSION_DENIED, resource=resource or permission,
        result="denied", metadata={"missing_permission": permission},
    )
    raise PermissionDenied("acceso denegado")


def scoped_company_id(store: TenantStore, ctx: TenantContext,
                      requested_company_id: str | None,
                      resource: str = "") -> str:
    """Resuelve el company_id de la operación.

    - Si no se solicita ninguno → el de la sesión (caso normal).
    - Si se solicita uno distinto al de la sesión → IDOR → denegado + auditoría.

    El código de negocio SIEMPRE usa el valor devuelto, nunca el parámetro crudo.
    """
    if not requested_company_id or requested_company_id == ctx.company_id:
        return ctx.company_id
    audit_mod.log_event(
        store, company_id=ctx.company_id, user_id=ctx.user_id,
        action=audit_mod.PERMISSION_DENIED,
        resource=resource or f"company:{requested_company_id}",
        result="denied",
        metadata={"reason": "idor_company_mismatch",
                  "requested": requested_company_id},
    )
    raise PermissionDenied("acceso denegado")


def require_company_admin(store: TenantStore, ctx: TenantContext) -> None:
    require_permission(store, ctx, "company.admin")


def require_user_admin(store: TenantStore, ctx: TenantContext) -> None:
    require_permission(store, ctx, "user.admin")


def check_data_access(store: TenantStore, ctx: TenantContext, dataset_id: str) -> str:
    """Verifica que el dataset pertenezca a la empresa del contexto.

    Devuelve el company_id verificado. Lanza PermissionDenied si el dataset
    pertenece a otra empresa (o es desconocido).
    """
    from .store import dataset_owner
    owner = dataset_owner(dataset_id)
    if owner is None or owner != ctx.company_id:
        audit_mod.log_event(
            store, company_id=ctx.company_id, user_id=ctx.user_id,
            action=audit_mod.PERMISSION_DENIED,
            resource=f"dataset:{dataset_id}", result="denied",
            metadata={"reason": "dataset_tenant_mismatch"},
        )
        raise PermissionDenied("acceso denegado")
    audit_mod.log_event(
        store, company_id=ctx.company_id, user_id=ctx.user_id,
        action=audit_mod.DATA_ACCESS, resource=f"dataset:{dataset_id}",
    )
    return owner
