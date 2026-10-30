-- BLOQUE (sprint 3, PRIORIDAD 1) — Migración 008: sesiones con verificador.
--
-- Problema que resuelve: la migración 006 guardaba el Bearer token de
-- sesión EN CLARO en os_sesiones (PK = token; `datos` incluía
-- session_id/origin_session_id/csrf_token en claro). Un dump de la BD
-- permitía recuperar Bearer tokens activos y suplantar sesiones.
--
-- Diseño nuevo (ver docs/tenant-postgres.md § "Sesiones con verificador"):
-- - La BD guarda SOLO verificadores, nunca el token reutilizable:
--     token_sha      = SHA-256 hex(token)          (clave de búsqueda indexada)
--     token_verifier = SHA-256 hex(salt || token)  (prueba de posesión)
--     token_salt     = sal aleatoria por sesión (hex)
--   El token real solo existe en la cookie del cliente y en memoria
--   efímera del servidor. Cada request: se recomputa token_sha para
--   localizar la fila y se compara el verificador con hmac.compare_digest
--   (tiempo constante).
-- - PK = id interno aleatorio ("SES-" + 32 hex), NO el token.
-- - `datos` JSONB queda sanitizado: sin tokens en claro. El csrf_token se
--   mantiene (evaluación de sensibilidad en docs/tenant-postgres.md: no es
--   un Bearer token — solo sirve junto a la cookie de sesión — y viaja al
--   cliente por diseño).
-- - `revocada` y `expira_at` como columnas estructuradas: el lookup las
--   verifica en CADA lectura (no solo en la limpieza periódica).
-- - Demo: la sesión demo ya no guarda el token de la sesión origen
--   (origin_session_id en claro); guarda origin_session_ref = id interno
--   de la sesión origen. Al salir de la demo se ROTA el token de la
--   sesión origen (ver tenant/auth.py::exit_demo).
--
-- Migración de datos: la hace el código Python (tenant/session_tokens.py::
-- migrate_legacy_sessions), porque el esquema del verificador vive en un
-- solo lugar (hashlib) y no depende de la extensión pgcrypto. El store
-- (PostgresTenantStore.__init__) la ejecuta de forma idempotente tras
-- aplicar esta migración: convierte cada fila legacy (token en claro en
-- `datos.session_id` o en el id) a verificadores, resuelve
-- origin_session_id -> origin_session_ref, y ELIMINA la tabla legacy
-- os_sesiones (un dump futuro no debe contenerla). Como la BD ya contuvo
-- tokens en claro, en producción se exigiría re-login forzoso tras este
-- cambio; en el entorno local demo se migra por hashing y queda
-- constancia aquí y en docs/tenant-postgres.md.
--
-- Idempotente: todo con IF NOT EXISTS.

CREATE TABLE IF NOT EXISTS os_sesiones_v2 (
    id                 TEXT PRIMARY KEY,  -- id interno aleatorio, NO el token
    usuario_id         TEXT NOT NULL REFERENCES os_usuarios(id) ON DELETE CASCADE,
    empresa_id         TEXT NOT NULL REFERENCES os_empresas(id) ON DELETE CASCADE,
    token_sha          TEXT NOT NULL,     -- SHA-256 hex(token): búsqueda indexada
    token_verifier     TEXT NOT NULL,     -- SHA-256 hex(salt || token)
    token_salt         TEXT NOT NULL,     -- sal hex aleatoria por sesión
    csrf_token         TEXT NOT NULL DEFAULT '',
    expira_at          TIMESTAMPTZ,       -- NULL = formato no parseable (falla cerrado)
    revocada           BOOLEAN NOT NULL DEFAULT FALSE,
    origin_session_ref TEXT NOT NULL DEFAULT '',  -- id interno (demo); "" = no demo
    datos              JSONB NOT NULL DEFAULT '{}',  -- metadatos NO sensibles
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_os_sesiones_v2_token_sha
    ON os_sesiones_v2 (token_sha);
CREATE INDEX IF NOT EXISTS ix_os_sesiones_v2_usuario
    ON os_sesiones_v2 (usuario_id);
CREATE INDEX IF NOT EXISTS ix_os_sesiones_v2_empresa
    ON os_sesiones_v2 (empresa_id);
CREATE INDEX IF NOT EXISTS ix_os_sesiones_v2_expira
    ON os_sesiones_v2 (expira_at);
