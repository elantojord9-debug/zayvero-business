-- P1d (issue #6, 2026-10-13) — PERSISTENCIA REAL DE APROBACIONES:
-- idempotencia por clave de operación + ejecución única.
--
-- Idempotente: todo con IF NOT EXISTS. Reversible (DOWN documentado,
-- no se ejecuta automáticamente):
--   ALTER TABLE os_aprobaciones DROP COLUMN IF EXISTS clave_operacion;
--   ALTER TABLE os_aprobaciones DROP COLUMN IF EXISTS clave_hash;
--   ALTER TABLE os_aprobaciones DROP COLUMN IF EXISTS evidencia;
--   ALTER TABLE os_aprobaciones DROP COLUMN IF EXISTS intentos_ejecucion;
--   ALTER TABLE os_aprobaciones DROP COLUMN IF EXISTS fallo_ejecucion_at;
--   ALTER TABLE os_aprobaciones DROP COLUMN IF EXISTS
--     ultimo_error_ejecucion;
--   DROP INDEX IF EXISTS ux_apr_clave_operacion;
--   DROP INDEX IF EXISTS ix_apr_clave_hash;
-- (el DOWN pierde las claves de idempotencia y los marcadores de fallo;
--  los estados pendiente/aprobada/rechazada/ejecutada/cancelada se
--  conservan intactos).
--
-- Decisiones:
-- - company_id en la clave única (aislamiento multiempresa): la misma
--   clave de operación en dos empresas son operaciones distintas.
-- - `clave_operacion` NULL = sin idempotencia (comportamiento anterior:
--   cada solicitar() crea una solicitud nueva). El índice único es
--   parcial (WHERE NOT NULL) para no chocar con las filas históricas.
-- - `clave_hash` = sha256 canónico de (tipo, clave, payload):
--   misma clave + misma operación -> se repite la solicitud existente;
--   misma clave + operación distinta -> conflicto (409).
-- - `intentos_ejecucion` / `fallo_ejecucion_at` /
--   `ultimo_error_ejecucion`: permiten distinguir "aprobada sin
--   ejecutar porque la ejecución está en curso" (reintento -> 409, la
--   operación se ejecuta UNA sola vez) de "aprobada cuya ejecución
--   FALLÓ y quedó registrada" (reintento permitido). Sin estos
--   marcadores, dos aprobaciones concurrentes ejecutaban la operación
--   dos veces (hueco del diseño anterior).
-- - `evidencia`: JSONB libre para adjuntar evidencia de la decisión
--   (capturas, referencias externas). No lo usa ningún flujo todavía;
--   queda reservado para no romper el contrato después.
-- - Migración de estados existentes: la tabla os_aprobaciones YA vive
--   en PostgreSQL (migración 008); no hay estados en memoria/JSON que
--   migrar. Los ALTER son no destructivos: las filas existentes
--   conservan su estado y reciben los defaults de las columnas nuevas.
--
-- NOTA DE APLICACIÓN (2026-10-13): este archivo NO está registrado en
-- os_persist/schema/_orden.txt (manifiesto congelado por orden de la
-- coordinación: no editarlo). Se aplica de forma idempotente desde
-- aprobaciones/api.py::_asegurar_schema_021() (conexión propia en
-- autocommit, una vez por proceso) y desde
-- aplicar_migracion_021(conn) para pruebas. Cuando el manifiesto se
-- descongele, registrarla al final con su número.

-- Guardia de existencia (lección de la 014/020, 2026-10-13): en esquemas
-- de prueba o BDs donde la 008 no se aplicó, la tabla puede no existir;
-- sin tabla no se toca nada.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = 'os_aprobaciones'
          AND n.nspname = current_schema()
    ) THEN
        -- Clave de idempotencia de la operación (opcional).
        ALTER TABLE os_aprobaciones
            ADD COLUMN IF NOT EXISTS clave_operacion TEXT;
        ALTER TABLE os_aprobaciones
            ADD COLUMN IF NOT EXISTS clave_hash TEXT;
        -- Evidencia adjunta (JSONB libre, reservado).
        ALTER TABLE os_aprobaciones
            ADD COLUMN IF NOT EXISTS evidencia JSONB NOT NULL DEFAULT '{}';
        -- Marcadores de ejecución (ejecución única + reintento tras fallo).
        ALTER TABLE os_aprobaciones
            ADD COLUMN IF NOT EXISTS intentos_ejecucion INTEGER NOT NULL DEFAULT 0;
        ALTER TABLE os_aprobaciones
            ADD COLUMN IF NOT EXISTS fallo_ejecucion_at TIMESTAMPTZ;
        ALTER TABLE os_aprobaciones
            ADD COLUMN IF NOT EXISTS ultimo_error_ejecucion TEXT NOT NULL DEFAULT '';

        -- Una clave de operación identifica UNA solicitud por empresa.
        CREATE UNIQUE INDEX IF NOT EXISTS ux_apr_clave_operacion
            ON os_aprobaciones (company_id, clave_operacion)
            WHERE clave_operacion IS NOT NULL;

        CREATE INDEX IF NOT EXISTS ix_apr_clave_hash
            ON os_aprobaciones (company_id, clave_hash)
            WHERE clave_hash IS NOT NULL;
    END IF;
END $$;
