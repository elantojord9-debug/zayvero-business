-- 2026-10-13 — Migración 010: tipo 'importacion' en os_eventos.
--
-- Contexto: el importador CSV/Excel→OS (os_import/, bloque Documentos/
-- Integraciones 2026-10-13) registra cada importación aplicada como
-- evento de auditoría en la MISMA transacción de la operación
-- (ver os_persist/tx.py + os_persist/auditoria.py).
-- El CHECK de la migración 004 (extendido por la 009) no incluía
-- 'importacion': sin esta migración el INSERT del evento fallaría
-- en PostgreSQL.
--
-- Idempotente: DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT (nombre
-- canónico os_eventos_tipo_check, el que PG generó en la 004).
-- REGLA DE UNIÓN (ver 011_os_empleados.sql): la lista conserva TODOS los
-- tipos introducidos por migraciones anteriores en el orden del
-- manifiesto (los 'saas_*' de 009_os_saas.sql y 'gasto' de
-- 009_os_eventos_gasto.sql). No toca datos existentes. Reversión:
-- re-aplicar el CHECK sin 'importacion' (solo si no quedan eventos tipo
-- 'importacion'; si los hay, conservarlos y no revertir: son evidencia
-- append-only).

ALTER TABLE os_eventos DROP CONSTRAINT IF EXISTS os_eventos_tipo_check;
ALTER TABLE os_eventos ADD CONSTRAINT os_eventos_tipo_check
    CHECK (tipo IN (
        'venta', 'compra', 'recepcion', 'cobro',
        'devolucion', 'aprobacion', 'ajuste',
        'saas_registro', 'saas_plan', 'saas_config',
        'saas_usuario', 'saas_onboarding',
        'gasto', 'importacion'));
