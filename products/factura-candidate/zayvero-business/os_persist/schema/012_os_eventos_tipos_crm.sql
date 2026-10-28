-- 012 — Extensión del CHECK de tipos de os_eventos con los tipos del bloque CRM.
--
-- El bloque CRM (os_crm/http.py) audita tipos 'oportunidad', 'descuento',
-- 'recordatorio' y 'promesa_pago' que están en TIPOS_EVENTO (Python) pero
-- no estaban en el CHECK de la tabla → CheckViolation en PostgreSQL.
-- Se extiende aquí con el patrón DROP/ADD idempotente (igual que la 011).
--
-- LISTA UNIÓN CANÓNICA: este CHECK es el último de la cadena en el orden
-- canónico de _orden.txt; debe contener la unión completa de tipos.
--
-- Idempotente: DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT.
-- Reversión (manual): re-aplicar el CHECK sin los 4 tipos CRM
-- (solo si no quedan eventos de esos tipos).

ALTER TABLE os_eventos DROP CONSTRAINT IF EXISTS os_eventos_tipo_check;
ALTER TABLE os_eventos ADD CONSTRAINT os_eventos_tipo_check
    CHECK (tipo IN (
        'venta', 'compra', 'recepcion', 'cobro',
        'devolucion', 'aprobacion', 'ajuste', 'gasto',
        'importacion', 'tarea', 'empleado',
        'saas_registro', 'saas_plan', 'saas_config', 'saas_usuario',
        'saas_onboarding',
        'oportunidad', 'descuento', 'recordatorio', 'promesa_pago'));
