-- BLOQUE PLATAFORMA SAAS (2026-10-13) — Migración 009: registro SaaS,
-- planes/límites, métricas de uso, onboarding, invitaciones de usuarios.
--
-- Diseño:
-- - Tablas saas_* (plataforma multiempresa): aislamiento por company_id /
--   email_norm en cada acceso. Todo el SQL de este módulo filtra por la
--   clave de la empresa/cuenta propietaria (anti-IDOR).
-- - Auditoría: los eventos SaaS se escriben en os_eventos (tabla compartida)
--   para que la sección de auditoría del panel los muestre sin cambios.
--   Para eso se AMPLÍA el CHECK os_eventos_tipo_check con los tipos
--   'saas_*' (aditivo; los 7 tipos de negocio quedan intactos).
-- - Idempotente: CREATE TABLE/INDEX IF NOT EXISTS + DO para el constraint.
-- - Reversión (manual): DROP TABLE saas_metricas_uso, saas_onboarding,
--   saas_invitaciones, saas_empresas, saas_cuentas; re-crear el CHECK
--   original de 004; DELETE FROM os_migrations WHERE archivo='009_os_saas.sql'.
-- - SIN FACTURACIÓN REAL: los precios de los planes son de referencia para
--   preparación local; no hay pasarela de pago ni cargos.

-- Cuentas SaaS: una cuenta = un email (normalizado). El plan vive en la
-- cuenta y aplica a TODAS sus empresas (una suscripción, N empresas).
CREATE TABLE IF NOT EXISTS saas_cuentas (
    email_norm TEXT PRIMARY KEY,   -- email en minúsculas, sin espacios
    plan_id    TEXT NOT NULL DEFAULT 'starter',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Empresas registradas por el flujo SaaS.
CREATE TABLE IF NOT EXISTS saas_empresas (
    company_id          TEXT PRIMARY KEY,
    nombre              TEXT NOT NULL,
    nombre_normalizado  TEXT NOT NULL,  -- único global (anti-duplicados)
    identificador_fiscal TEXT NOT NULL, -- normalizado (mayúsculas, sin
                                       -- espacios/guiones); SIN verificación
                                       -- fiscal real (ver docs/bloque-saas.md)
    moneda_base         CHAR(3) NOT NULL DEFAULT 'USD',
    plan_id             TEXT NOT NULL DEFAULT 'starter', -- plan heredado de
                                       -- la cuenta al crearla
    sucursales          JSONB NOT NULL DEFAULT '[]'::jsonb,
    estado              TEXT NOT NULL DEFAULT 'activa'
                        CHECK (estado IN ('activa', 'suspendida')),
    creado_por_email    TEXT NOT NULL DEFAULT '',
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_saas_empresas_fiscal
    ON saas_empresas (identificador_fiscal);
CREATE UNIQUE INDEX IF NOT EXISTS ux_saas_empresas_nombre_norm
    ON saas_empresas (nombre_normalizado);
CREATE INDEX IF NOT EXISTS ix_saas_empresas_creador
    ON saas_empresas (creado_por_email);

-- Invitaciones de usuarios: el token crudo se entrega UNA vez al
-- invitador (él lo reenvía por su canal); en BD solo vive el SHA-256.
-- NUNCA se envían correos desde este módulo.
CREATE TABLE IF NOT EXISTS saas_invitaciones (
    invitacion_id TEXT PRIMARY KEY,
    token_sha     TEXT NOT NULL,   -- SHA-256 hex del token (búsqueda)
    company_id    TEXT NOT NULL,
    email_norm    TEXT NOT NULL,
    rol           TEXT NOT NULL DEFAULT 'viewer',
    creado_por    TEXT NOT NULL DEFAULT '',
    expira_at     TIMESTAMPTZ NOT NULL,
    usado_at      TIMESTAMPTZ,     -- NULL = pendiente
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_saas_invitaciones_token
    ON saas_invitaciones (token_sha);
CREATE INDEX IF NOT EXISTS ix_saas_invitaciones_empresa
    ON saas_invitaciones (company_id);

-- Métricas de uso por empresa y mes (base para futura facturación).
CREATE TABLE IF NOT EXISTS saas_metricas_uso (
    company_id         TEXT NOT NULL,
    periodo            TEXT NOT NULL,   -- 'YYYY-MM'
    ejecuciones_ia     BIGINT NOT NULL DEFAULT 0,
    llamadas_api       BIGINT NOT NULL DEFAULT 0,
    almacenamiento_bytes BIGINT NOT NULL DEFAULT 0,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, periodo)
);

-- Onboarding: estado por paso de la checklist de puesta en marcha.
CREATE TABLE IF NOT EXISTS saas_onboarding (
    company_id TEXT PRIMARY KEY,
    pasos      JSONB NOT NULL DEFAULT '{}'::jsonb,  -- {paso: {estado, ...}}
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Ampliar os_eventos con los tipos SaaS (los 7 de negocio intactos).
DO $$
BEGIN
    ALTER TABLE os_eventos DROP CONSTRAINT IF EXISTS os_eventos_tipo_check;
EXCEPTION
    WHEN undefined_object THEN NULL;
END $$;
ALTER TABLE os_eventos
    ADD CONSTRAINT os_eventos_tipo_check CHECK (tipo IN (
        'venta', 'compra', 'recepcion', 'cobro',
        'devolucion', 'aprobacion', 'ajuste',
        'saas_registro', 'saas_plan', 'saas_config', 'saas_usuario',
        'saas_onboarding'));
