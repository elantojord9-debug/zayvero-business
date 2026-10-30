-- 004_factura_idempotencia.sql — FASE 2: idempotencia transaccional.
-- Clave de idempotencia por empresa en ventas y pagos: un reintento
-- (doble clic, recarga del navegador, reintento de red) con la misma
-- clave devuelve el documento existente en vez de crear un duplicado.
-- Índice parcial: la unicidad solo se exige cuando la clave no es vacía
-- (sintaxis compatible con SQLite y PostgreSQL).

ALTER TABLE factura_ventas ADD COLUMN idempotencia TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_ventas_idempotencia
    ON factura_ventas(empresa_id, idempotencia)
    WHERE idempotencia <> '';

ALTER TABLE factura_pagos ADD COLUMN idempotencia TEXT NOT NULL DEFAULT '';
CREATE UNIQUE INDEX IF NOT EXISTS uq_pagos_idempotencia
    ON factura_pagos(empresa_id, idempotencia)
    WHERE idempotencia <> '';
