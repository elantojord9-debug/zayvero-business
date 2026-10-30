-- BLOQUE 1 (sprint 2) — Migración 006: backend PostgreSQL del tenant store.
--
-- Problema que resuelve: tenant/store.py guardaba empresas/usuarios/
-- sesiones en JSON con threading.RLock, que solo protege hilos del MISMO
-- proceso. Con 2+ procesos había read-modify-write sin lock: sesiones
-- perdidas y corrupción posible.
--
-- Diseño:
-- - os_empresas / os_usuarios / os_sesiones espejan los JSON
--   (companies.json, users.json, sessions.json). La columna `datos`
--   (JSONB) guarda el dict completo del modelo (Company/User/Session),
--   igual que el JSON: paridad total de campos, incluidos los
--   opcionales (intl, origin_session_id, csrf_token, metadata).
-- - Las columnas estructuradas (nombre, email, rol, hash, expira_at,
--   token_sha) son conveniencias de consulta/índice; se derivan de
--   `datos` en cada escritura.
-- - Escrituras por empresa: SELECT ... FOR UPDATE sobre su fila de
--   os_company_lock (migración 003) + bump de `version` como barrera,
--   igual que os_persist/tx.py. Serializa escritores entre procesos.
-- - Sesiones: upsert atómico por id (el token opaco); `token_sha`
--   (SHA-256 hex) es la clave de búsqueda endurecida con índice único.
-- - Email único por empresa SOLO cuando no es vacío (índice parcial):
--   las filas "stub" auto-creadas para satisfacer la FK de sesiones
--   (p.ej. tests que guardan sesiones de usuarios sintéticos) usan
--   email='' y no colisionan entre sí.
--
-- Idempotente: todo con IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS os_empresas (
    id         TEXT PRIMARY KEY,
    nombre     TEXT NOT NULL DEFAULT '',
    datos      JSONB NOT NULL DEFAULT '{}',
    version    BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS os_usuarios (
    id         TEXT PRIMARY KEY,
    empresa_id TEXT NOT NULL REFERENCES os_empresas(id) ON DELETE CASCADE,
    email      TEXT NOT NULL DEFAULT '',
    hash       TEXT NOT NULL DEFAULT '',
    rol        TEXT NOT NULL DEFAULT 'viewer',
    datos      JSONB NOT NULL DEFAULT '{}',
    version    BIGINT NOT NULL DEFAULT 0,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Único por empresa solo para emails reales (no vacíos): los stubs
-- (email='') quedan fuera y pueden coexistir.
CREATE UNIQUE INDEX IF NOT EXISTS ux_os_usuarios_empresa_email
    ON os_usuarios (empresa_id, email) WHERE email <> '';
CREATE INDEX IF NOT EXISTS ix_os_usuarios_empresa
    ON os_usuarios (empresa_id);
CREATE INDEX IF NOT EXISTS ix_os_usuarios_email_lower
    ON os_usuarios (lower(email)) WHERE email <> '';

CREATE TABLE IF NOT EXISTS os_sesiones (
    id         TEXT PRIMARY KEY,  -- el token opaco de sesión (session_id)
    usuario_id TEXT NOT NULL REFERENCES os_usuarios(id) ON DELETE CASCADE,
    empresa_id TEXT NOT NULL REFERENCES os_empresas(id) ON DELETE CASCADE,
    token_sha  TEXT NOT NULL,     -- SHA-256 hex del token (búsqueda endurecida)
    expira_at  TIMESTAMPTZ,       -- NULL si el formato no es parseable
    datos      JSONB NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_os_sesiones_token_sha
    ON os_sesiones (token_sha);
CREATE INDEX IF NOT EXISTS ix_os_sesiones_usuario
    ON os_sesiones (usuario_id);
CREATE INDEX IF NOT EXISTS ix_os_sesiones_empresa
    ON os_sesiones (empresa_id);
CREATE INDEX IF NOT EXISTS ix_os_sesiones_expira
    ON os_sesiones (expira_at);
