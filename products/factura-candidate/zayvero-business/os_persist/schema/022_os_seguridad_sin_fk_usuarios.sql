-- BLOQUE A (2026-10-13) — Migración 022: soltar las FKs de la 014 hacia
-- os_usuarios / os_empresas.
--
-- CONTEXTO: la migración 014 creó os_mfa_secretos, os_codigos_recuperacion,
-- os_solicitudes_recuperacion y os_mfa_intentos con FOREIGN KEYs hacia
-- os_usuarios(id) / os_empresas(id). Esas tablas solo contienen usuarios
-- cuando el backend de tenant es PostgreSQL (tenant/store_pg.py); con el
-- backend JSON (valor por defecto, el que usa el despliegue actual y los
-- tests), los usuarios viven en archivos y las FKs hacían fallar CUALQUIER
-- enrolamiento MFA con ForeignKeyViolation.
--
-- DECISIÓN: la existencia del usuario se valida en la capa de aplicación
-- (os_seguridad/mfa.py::_usuario_activo, que consulta el tenant store
-- real sea JSON o PG). Las FKs se sueltan; la integridad referencial la
-- garantiza la aplicación. No se pierde ningún dato (solo constraints).
--
-- Idempotente: DROP CONSTRAINT IF EXISTS. Reversible: re-agregar las FKs
-- (solo válido si el backend de tenant es PG).
DO $$
DECLARE
    rec RECORD;
BEGIN
    FOR rec IN
        SELECT conname FROM pg_constraint
        WHERE connamespace = current_schema()::regnamespace
          AND contype = 'f'
          AND conname IN (
              'os_mfa_secretos_usuario_id_fkey',
              'os_codigos_recuperacion_usuario_id_fkey',
              'os_solicitudes_recuperacion_empresa_id_fkey',
              'os_solicitudes_recuperacion_usuario_objetivo_fkey',
              'os_mfa_intentos_usuario_id_fkey')
    LOOP
        EXECUTE format(
            'ALTER TABLE %I DROP CONSTRAINT %I',
            (SELECT c.relname FROM pg_constraint k
             JOIN pg_class c ON c.oid = k.conrelid
             WHERE k.conname = rec.conname
               AND k.connamespace = current_schema()::regnamespace),
            rec.conname);
    END LOOP;
END $$;
