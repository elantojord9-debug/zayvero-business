-- BLOQUE 2 (2026-10-12) — Migración 005: idempotencia de operaciones.
-- Tabla os_idempotency: una fila por (empresa, Idempotency-Key).
--
-- Diseño:
-- - El cliente envía el header `Idempotency-Key` en endpoints
--   financieros/operativos (ventas, cobros, compras, recepciones y
--   devoluciones; ver webapp/os_routes.py::_SUBS_IDEMPOTENTES).
-- - La clave se RECLAMA dentro de la misma transacción de la operación
--   (ver os_persist/tx.py + os_persist/idempotencia.py): misma clave +
--   mismo body -> se devuelve la respuesta almacenada sin re-ejecutar
--   (sin doble cobro, sin doble descuento de inventario); misma clave +
--   body distinto -> 422 (conflicto de clave); operación fallida ->
--   la clave queda 'fallido' y el cliente puede reintentar.
-- - Aislamiento multiempresa: la PK es (company_id, clave); una empresa
--   no puede reusar ni leer claves de otra.
-- - `estado`: 'en_curso' (reclamada, operación en vuelo), 'completado'
--   (respuesta almacenada, lista para repetir), 'fallido' (el intento
--   falló; la clave queda libre para reintento).
-- - `request_hash`: sha256 del body canónico; detecta "misma clave,
--   distinto body" -> 422.
-- - TTL: expires_at = created_at + TTL (env
--   ZAYVERO_OS_IDEMPOTENCY_TTL_HOURS, defecto 24h). Las vencidas se
--   borran con limpiar_expiradas() (os_persist/idempotencia.py); una
--   clave vencida se puede reclamar de nuevo como si fuera nueva.
-- - Recuperación de caídas: si un worker muere dejando una fila
--   'en_curso', reclamar() la considera abandonada cuando supera el
--   umbral de antigüedad (env ZAYVERO_OS_IDEMPOTENCY_STALE_SECS,
--   defecto 300s) y la reclama de nuevo. Sin esto, una caída
--   bloquearía la clave para siempre.
--
-- Idempotente: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.
--
-- REVERSIÓN (manual, documentada; no hay down script separado):
--   1) DROP TABLE IF EXISTS os_idempotency;
--   2) DELETE FROM os_migrations WHERE archivo = '005_os_idempotency.sql';
-- Nota: revertir BORRA las claves en vuelo/almacenadas. En
-- staging/producción hacerlo solo con autorización explícita.

CREATE TABLE IF NOT EXISTS os_idempotency (
    company_id      TEXT NOT NULL REFERENCES os_companies(company_id),
    clave           TEXT NOT NULL,  -- la Idempotency-Key del cliente
    endpoint        TEXT NOT NULL,  -- sub-ruta, p.ej. 'ventas'
    request_hash    TEXT NOT NULL,  -- sha256 del body canónico
    response_status INT NOT NULL DEFAULT 0,  -- 0 = aún sin respuesta
    response_body   JSONB NOT NULL DEFAULT '{}'::jsonb,
    estado          TEXT NOT NULL DEFAULT 'en_curso'
                    CHECK (estado IN ('en_curso', 'completado', 'fallido')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (company_id, clave)
);

-- Limpieza de vencidas (WHERE expires_at <= now())
CREATE INDEX IF NOT EXISTS ix_os_idempotency_expira
    ON os_idempotency (expires_at);
