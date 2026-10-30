-- BLOQUE 1 (2026-10-12) — Migración 004: auditoría empresarial.
-- Tabla os_eventos: un registro INMUTABLE por operación de negocio.
--
-- Diseño:
-- - Cada mutación exitosa (venta, compra, recepción, cobro, devolución,
--   aprobación, ajuste) inserta su evento EN LA MISMA TRANSACCIÓN PG de la
--   operación (ver os_persist/tx.py + os_persist/auditoria.py). Si la
--   operación falla (status >= 400 o excepción) hay ROLLBACK y el evento
--   NO queda registrado como exitoso: evento y estado siempre coinciden.
-- - Aislamiento multiempresa: toda lectura filtra por company_id; los
--   índices compuestos empiezan por company_id.
-- - La tabla es append-only por convención: ningún código hace UPDATE ni
--   DELETE sobre os_eventos (los eventos son evidencia; no se corrigen,
--   se compensan con una operación nueva).
--
-- Idempotente: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.
--
-- REVERSIÓN (manual, documentada; no hay down script separado):
--   1) DROP TABLE IF EXISTS os_eventos;
--   2) DELETE FROM os_migrations WHERE archivo = '004_os_eventos.sql';
-- Nota: revertir BORRA el historial de auditoría. En staging/producción
-- hacerlo solo con autorización explícita y respaldo previo.

CREATE TABLE IF NOT EXISTS os_eventos (
    evento_id  TEXT NOT NULL PRIMARY KEY,  -- uuid hex generado en Python
    company_id TEXT NOT NULL REFERENCES os_companies(company_id),
    tipo       TEXT NOT NULL CHECK (tipo IN (
                   'venta', 'compra', 'recepcion', 'cobro',
                   'devolucion', 'aprobacion', 'ajuste')),
    actor      TEXT NOT NULL DEFAULT '',   -- usuario que ejecutó (ctx.user_id)
    fecha      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ref_id     TEXT NOT NULL DEFAULT '',   -- id de la operación (venta_id, ...)
    detalle    JSONB NOT NULL DEFAULT '{}'::jsonb,  -- qué cambió: cantidades/importes clave
    resultado  TEXT NOT NULL DEFAULT 'exito'
               CHECK (resultado IN ('exito', 'fallo'))
    -- 'fallo' queda reservado para futuros eventos de intento rechazado
    -- registrados fuera de la transacción de la operación. Hoy, una
    -- operación fallida no deja ningún evento (rollback total).
);

-- Aislamiento y consulta por empresa (todas empiezan por company_id)
CREATE INDEX IF NOT EXISTS ix_os_eventos_empresa_fecha
    ON os_eventos (company_id, fecha);
CREATE INDEX IF NOT EXISTS ix_os_eventos_empresa_tipo
    ON os_eventos (company_id, tipo);
CREATE INDEX IF NOT EXISTS ix_os_eventos_empresa_ref
    ON os_eventos (company_id, ref_id);
