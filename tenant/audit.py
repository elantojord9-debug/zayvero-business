"""FASE 6A — Auditoría de eventos de seguridad.

Registra quién / qué / cuándo / empresa / resultado.
NUNCA registra contraseñas, tokens, API keys ni secretos.
"""

from __future__ import annotations

import uuid

from .models import AuditEvent
from .store import TenantStore, utcnow_iso

# Eventos soportados
LOGIN_SUCCESS = "LOGIN_SUCCESS"
LOGIN_FAILED = "LOGIN_FAILED"
LOGOUT = "LOGOUT"
USER_CREATED = "USER_CREATED"
USER_UPDATED = "USER_UPDATED"
USER_DISABLED = "USER_DISABLED"
PERMISSION_DENIED = "PERMISSION_DENIED"
DATA_ACCESS = "DATA_ACCESS"
# FASE 7B — diagnóstico ejecutivo
DIAGNOSTIC_GENERATED = "DIAGNOSTIC_GENERATED"
DIAGNOSTIC_VIEWED = "DIAGNOSTIC_VIEWED"
# FASE 7A — ciclo de vida de datasets empresariales
DATASET_CREATED = "DATASET_CREATED"
DATASET_UPLOAD_STARTED = "DATASET_UPLOAD_STARTED"
DATASET_UPLOAD_COMPLETED = "DATASET_UPLOAD_COMPLETED"
DATASET_PROCESSING_STARTED = "DATASET_PROCESSING_STARTED"
DATASET_PROCESSING_COMPLETED = "DATASET_PROCESSING_COMPLETED"
DATASET_PROCESSING_FAILED = "DATASET_PROCESSING_FAILED"
DATASET_ACTIVATED = "DATASET_ACTIVATED"
# FASE 7C — configuración internacional de la empresa
COMPANY_CONFIG_UPDATED = "COMPANY_CONFIG_UPDATED"
# FASE 8 — experiencia de producto (primera entrada / onboarding)
PRODUCT_OVERVIEW_VIEWED = "PRODUCT_OVERVIEW_VIEWED"
DEMO_ENTER = "DEMO_ENTER"
DEMO_EXIT = "DEMO_EXIT"


def _scrub_metadata(metadata: dict | None) -> dict:
    """Elimina cualquier rastro de secretos antes de persistir."""
    meta = dict(metadata or {})
    for key in ("password", "password_hash", "token", "session_id", "api_key",
                "secret", "credentials"):
        meta.pop(key, None)
    return meta


def log_event(store: TenantStore, *, company_id: str, user_id: str,
               action: str, resource: str = "", result: str = "ok",
               metadata: dict | None = None) -> AuditEvent:
    event = AuditEvent(
        audit_id="AUD-" + uuid.uuid4().hex[:12],
        company_id=company_id or "",
        user_id=user_id or "",
        action=action,
        resource=resource,
        timestamp=utcnow_iso(),
        result=result,
        metadata=_scrub_metadata(metadata),
    )
    store.append_audit(event)
    return event


def token_fingerprint(token: str) -> str:
    """Huella corta para logs: NUNCA el token completo."""
    if not token:
        return ""
    return "tok…" + token[-6:]
