"""FASE 6A — Roles y permisos.

Matriz central de permisos. ÚNICO lugar donde se definen qué permisos
tiene cada rol. La capa de autorización (authorization.py) la consume;
el resto del código NUNCA la duplica.
"""

from __future__ import annotations

# --- Permisos disponibles -----------------------------------------------
# company.admin  : administrar la empresa (datos, configuración)
# user.admin     : crear / actualizar / deshabilitar usuarios
# dashboard.read : ver el dashboard
# findings.read  : ver hallazgos de negocio
# predictions.read: ver predicciones e inteligencia de predicción
# advisor.use    : usar el AI Business Advisor
# audit.read     : ver el registro de auditoría de su empresa
# data.admin     : (FASE 7A) cargar archivos y lanzar el análisis de datos.
#                  Necesario porque ni company.admin (OWNER) ni la matriz
#                  existente cubrían la carga de datos para ADMIN sin darle
#                  administración total de la empresa. Mínimo cambio
#                  documentado: solo OWNER y ADMIN lo tienen.
# company.config   : (FASE 7C) administrar la configuración internacional de
#                  la empresa (país, idioma, moneda, zona horaria, formatos).
#                  Mínimo cambio documentado: solo OWNER y ADMIN lo tienen.

PERMISSIONS = {
    "company.admin": "Administrar empresa",
    "user.admin": "Administrar usuarios",
    "dashboard.read": "Ver dashboard",
    "findings.read": "Ver hallazgos",
    "predictions.read": "Ver predicciones",
    "advisor.use": "Usar AI Business Advisor",
    "audit.read": "Ver auditoría",
    "data.admin": "Cargar y analizar datos",
    "company.config": "Configurar empresa",
}

# --- Roles iniciales (no crear más en FASE 6A) ---------------------------

ROLES = {
    "owner": "OWNER",
    "admin": "ADMIN",
    "analyst": "ANALYST",
    "viewer": "VIEWER",
}

ROLE_PERMISSIONS = {
    "owner": [
        "company.admin",
        "user.admin",
        "dashboard.read",
        "findings.read",
        "predictions.read",
        "advisor.use",
        "audit.read",
        "data.admin",
        "company.config",
    ],
    "admin": [
        "user.admin",
        "dashboard.read",
        "findings.read",
        "predictions.read",
        "advisor.use",
        "audit.read",
        "data.admin",
        "company.config",
    ],
    "analyst": [
        "dashboard.read",
        "findings.read",
        "predictions.read",
        "advisor.use",
    ],
    "viewer": [
        "dashboard.read",
        "findings.read",
        "predictions.read",
    ],
}


def permissions_for(role_id: str) -> list:
    """Permisos del rol. Rol desconocido → lista vacía (denegar por defecto)."""
    return list(ROLE_PERMISSIONS.get(role_id, []))


def is_valid_role(role_id: str) -> bool:
    return role_id in ROLES
