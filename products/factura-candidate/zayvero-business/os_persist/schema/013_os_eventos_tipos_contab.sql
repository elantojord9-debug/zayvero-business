-- 013 — Extensión del CHECK de tipos de os_eventos con los tipos del
-- bloque Contabilidad.
--
-- El bloque Contabilidad (os_contab/rutas.py) audita tipos 'presupuesto',
-- 'conciliacion' y 'ncf' que están en TIPOS_EVENTO (Python,
-- os_persist/auditoria.py) pero no estaban en el CHECK de la tabla →
-- CheckViolation en PostgreSQL.
-- Se extiende aquí con el patrón DROP/ADD idempotente (igual que la 011
-- y la 012).
--
-- LISTA UNIÓN CANÓNICA: este CHECK es el último de la cadena en el orden
-- canónico de _orden.txt; debe contener la unión completa de tipos
-- (regla 011/012: si se toca el CHECK, preservar la unión completa o se
-- rompe la auditoría de otros bloques).
--
-- Idempotente: DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT.
-- Reversión (manual): re-aplicar el CHECK sin los 3 tipos de
-- contabilidad (solo si no quedan eventos de esos tipos).

-- Extensión ADITIVA del CHECK de tipos de os_eventos (2026-10-13):
-- el patrón anterior (DROP + ADD con lista fija) rompía al re-aplicarse
-- con datos de tipos más nuevos (CheckViolation "violated by some row")
-- y al aplicarse fuera de orden (perdía tipos). Ahora: lee los tipos del
-- CHECK vigente, agrega los nuevos solo si faltan y reconstruye
-- preservando la unión existente. Idempotente.
DO $$
DECLARE
    def_actual TEXT;
    tipos TEXT[];
    lista TEXT;
BEGIN
    SELECT pg_get_constraintdef(cc.oid) INTO def_actual
      FROM pg_constraint cc
     WHERE cc.conname = 'os_eventos_tipo_check'
       AND cc.connamespace = current_schema()::regnamespace;
    IF def_actual IS NULL THEN
        RETURN;
    END IF;
    SELECT array_agg(DISTINCT m[1]) INTO tipos
      FROM regexp_matches(def_actual, '''([^'']+)''', 'g') AS m;
    IF NOT ('presupuesto' = ANY (COALESCE(tipos, '{}'))) AND NOT ('conciliacion' = ANY (COALESCE(tipos, '{}'))) AND NOT ('ncf' = ANY (COALESCE(tipos, '{}'))) THEN
        SELECT string_agg(quote_literal(t), ', ' ORDER BY t)
          INTO lista
          FROM unnest(COALESCE(tipos, '{}') || ARRAY['presupuesto', 'conciliacion', 'ncf']) AS t;
        ALTER TABLE os_eventos DROP CONSTRAINT os_eventos_tipo_check;
        EXECUTE 'ALTER TABLE os_eventos ADD CONSTRAINT os_eventos_tipo_check '
             || 'CHECK (tipo IN (' || lista || '))';
    END IF;
END $$;

