-- 2026-10-13 — Migración 009: tipo 'gasto' en os_eventos.
--
-- Contexto: el cableado RegistroGastos -> LibroFinanzas (integridad
-- financiera) registra cada gasto operativo como evento de auditoría
-- en la MISMA transacción de la operación (ver os_persist/tx.py +
-- os_persist/auditoria.py, ruta POST /api/v1/os/finanzas/gastos).
-- El CHECK de la migración 004 no incluía 'gasto': sin esta migración
-- el INSERT del evento fallaría en PostgreSQL.
--
-- Idempotente: el bloque DO solo actúa si la tabla existe (en esquemas
-- parciales es no-op); DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT con
-- el nombre canónico os_eventos_tipo_check (el que PG generó en la 004).
-- REGLA DE UNIÓN (ver 011_os_empleados.sql): la lista conserva TODOS los
-- tipos introducidos por migraciones anteriores en el orden del
-- manifiesto (los 'saas_*' de 009_os_saas.sql, que corre antes).
-- No toca datos existentes. Reversión: re-aplicar el CHECK sin 'gasto'
-- (solo si no quedan eventos tipo 'gasto'; si los hay, conservarlos y
-- no revertir: son evidencia append-only).

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM information_schema.tables
               WHERE table_schema = current_schema()
               AND table_name = 'os_eventos') THEN
        ALTER TABLE os_eventos
            DROP CONSTRAINT IF EXISTS os_eventos_tipo_check;
        ALTER TABLE os_eventos
            ADD CONSTRAINT os_eventos_tipo_check
            CHECK (tipo IN (
                'venta', 'compra', 'recepcion', 'cobro',
                'devolucion', 'aprobacion', 'ajuste',
                'saas_registro', 'saas_plan', 'saas_config',
                'saas_usuario', 'saas_onboarding',
                'gasto'));
    END IF;
END $$;
