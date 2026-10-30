-- BLOQUE A SEGURIDAD (2026-10-13, roadmap P1-12 / issue #2) — Migración 014.
--
-- MFA/TOTP + códigos de recuperación + recuperación asistida + políticas
-- de retención.
--
-- Modelo de secretos (ver os_seguridad/envoltorio.py):
-- - El secreto TOTP (20 bytes aleatorios) NUNCA se guarda en claro.
--   Se persiste ENVUELTO con un keystream HMAC-SHA256 derivado de la
--   contraseña del usuario EN CLARO (solo vive en memoria durante el
--   request): cifrado = secreto XOR keystream(contraseña, sal).
--   Sin la contraseña no hay forma de recuperar el secreto aunque se
--   tenga el dump completo (primero habría que romper el PBKDF2 de la
--   contraseña); con solo la contraseña (phishing) tampoco, porque falta
--   el cifrado de la BD. Doble factor de verdad, sin AES (solo stdlib).
-- - secreto_sha = SHA-256 hex del secreto: compromiso de integridad.
--   Detecta manipulación del cifrado y cambio de contraseña sin
--   re-envolver (en ese caso el MFA pide re-inscripción, fail-closed).
-- - Los códigos de recuperación se guardan SOLO como SHA-256 (se verifican
--   por comparación, nunca se usan como clave: el hash basta).
--
-- NOTA DE APLICACIÓN: el coordinador agrega este archivo al final de
-- os_persist/schema/_orden.txt (NO se edita aquí por orden de trabajo).
-- Mientras tanto, os_seguridad/migracion.py::asegurar_esquema() lo aplica
-- directamente de forma idempotente (todo IF NOT EXISTS): aplicarlo dos
-- veces es seguro.
--
-- Idempotente: todo con IF NOT EXISTS.

-- Ampliación del CHECK de tipos de la migración 008 (aprobaciones):
-- el borrado de cuenta usa el tipo 'eliminacion_cuenta'. No se edita la
-- 008 (ya aplicada). Idempotente y compatible con tipos agregados por
-- otros bloques (p. ej. 'ia_propuesta'): reconstruye la lista a partir de
-- los tipos ya presentes en la tabla + 'eliminacion_cuenta'. Si el
-- constraint no existe (otro bloque lo retiró), no se toca nada.
DO $$
DECLARE
    def_actual TEXT;
    tipos TEXT[];
    lista TEXT;
BEGIN
    -- ADITIVO (2026-10-13): la versión anterior reconstruía la lista
    -- desde los tipos PRESENTES EN LA TABLA, lo que PERDÍA tipos válidos
    -- sin filas en ese momento (p. ej. 'aprobacion_financiera') cada vez
    -- que asegurar_esquema() re-ejecutaba la 014. Ahora: lee los tipos
    -- del CHECK vigente, agrega 'eliminacion_cuenta' solo si falta y
    -- preserva todo lo demás. Idempotente.
    -- Restringido al esquema actual: pg_constraint sin filtro de esquema
    -- veía constraints de otros esquemas (p. ej. public) y luego la
    -- consulta fallaba en esquemas de prueba.
    SELECT pg_get_constraintdef(c.oid) INTO def_actual
      FROM pg_constraint c
      JOIN pg_class t ON t.oid = c.conrelid
      JOIN pg_namespace n ON n.oid = t.relnamespace
     WHERE c.conname = 'ck_apr_tipo'
       AND t.relname = 'os_aprobaciones'
       AND n.nspname = current_schema();
    IF def_actual IS NULL THEN
        RETURN;
    END IF;
    SELECT array_agg(DISTINCT m[1]) INTO tipos
      FROM regexp_matches(def_actual, '''([^'']+)''', 'g') AS m;
    IF NOT ('eliminacion_cuenta' = ANY (COALESCE(tipos, '{}'))) THEN
        SELECT string_agg(quote_literal(t), ', ' ORDER BY t)
          INTO lista
          FROM unnest(COALESCE(tipos, '{}')
                      || ARRAY['eliminacion_cuenta']) AS t;
        ALTER TABLE os_aprobaciones DROP CONSTRAINT ck_apr_tipo;
        EXECUTE 'ALTER TABLE os_aprobaciones ADD CONSTRAINT ck_apr_tipo '
             || 'CHECK (tipo IN (' || lista || '))';
    END IF;
END $$;

-- Una fila por usuario con MFA inscrito. La inscripción solo EXIGE el
-- segundo factor cuando confirmado=TRUE (antes es como si no existiera:
-- evita el auto-bloqueo si el usuario no termina el enrolamiento).
CREATE TABLE IF NOT EXISTS os_mfa_secretos (
    usuario_id    TEXT PRIMARY KEY
                  REFERENCES os_usuarios(id) ON DELETE CASCADE,
    secreto_cif   TEXT NOT NULL,  -- secreto XOR keystream (hex), nunca en claro
    secreto_sha   TEXT NOT NULL,  -- SHA-256 hex del secreto (integridad)
    sal           TEXT NOT NULL,  -- sal hex del envoltorio (única por inscripción)
    confirmado    BOOLEAN NOT NULL DEFAULT FALSE,
    creado_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    confirmado_at TIMESTAMPTZ
);

-- Códigos de recuperación: 10 por inscripción, un solo uso, solo hashes.
-- Se regeneran con confirmación de contraseña (los anteriores se invalidan).
CREATE TABLE IF NOT EXISTS os_codigos_recuperacion (
    id         TEXT PRIMARY KEY,
    usuario_id TEXT NOT NULL REFERENCES os_usuarios(id) ON DELETE CASCADE,
    codigo_sha TEXT NOT NULL,  -- SHA-256 hex del código normalizado
    usado      BOOLEAN NOT NULL DEFAULT FALSE,
    usado_at   TIMESTAMPTZ,
    creado_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (usuario_id, codigo_sha)
);
CREATE INDEX IF NOT EXISTS ix_codigos_rec_usuario
    ON os_codigos_recuperacion (usuario_id);

-- Recuperación asistida por owner/admin: dos pasos (solicitar -> ejecutar)
-- con motivo y canal de verificación fuera del sistema. Todo auditado.
CREATE TABLE IF NOT EXISTS os_solicitudes_recuperacion (
    solicitud_id       TEXT PRIMARY KEY,
    empresa_id         TEXT NOT NULL REFERENCES os_empresas(id) ON DELETE CASCADE,
    usuario_objetivo   TEXT NOT NULL REFERENCES os_usuarios(id) ON DELETE CASCADE,
    solicitante        TEXT NOT NULL,   -- user_id de quien solicita
    estado             TEXT NOT NULL DEFAULT 'pendiente',  -- pendiente | ejecutada | cancelada
    motivo             TEXT NOT NULL DEFAULT '',
    canal_verificacion TEXT NOT NULL DEFAULT '',  -- dónde se verificó la identidad (fuera del sistema)
    creado_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    ejecutada_at       TIMESTAMPTZ,
    ejecutada_por      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_solrec_empresa
    ON os_solicitudes_recuperacion (empresa_id);

-- Anti-fuerza bruta del segundo factor: cubeta de fallos por usuario.
-- 5 fallos en 15 min -> bloqueo de 15 min (ver os_seguridad/mfa.py).
CREATE TABLE IF NOT EXISTS os_mfa_intentos (
    usuario_id      TEXT PRIMARY KEY REFERENCES os_usuarios(id) ON DELETE CASCADE,
    fallos          TIMESTAMPTZ[] NOT NULL DEFAULT '{}',
    bloqueado_hasta TIMESTAMPTZ
);

-- Políticas de retención por empresa y tipo de dato.
-- La matriz por defecto vive en os_seguridad/mfa.py::POLITICAS_DEFECTO y en
-- docs/politica-retencion.md; aquí solo van las personalizaciones.
CREATE TABLE IF NOT EXISTS os_politicas_retencion (
    empresa_id      TEXT NOT NULL REFERENCES os_empresas(id) ON DELETE CASCADE,
    tipo_dato       TEXT NOT NULL,
    plazo_dias      INTEGER NOT NULL CHECK (plazo_dias >= 0),
    accion          TEXT NOT NULL CHECK (accion IN ('eliminar','anonimizar','archivar')),
    base            TEXT NOT NULL DEFAULT 'ultima_actividad',
    actualizado_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_por TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (empresa_id, tipo_dato)
);
