-- 006_factura_auditoria.sql — FASE 5: auditoría duradera.
-- Registro inmutable (solo INSERT, sin UPDATE/DELETE en la app) de
-- acciones críticas: login, ventas, anular, pagos, e-CF, usuarios.
-- Vive en la misma BD transaccional (no en disco temporal).

CREATE TABLE IF NOT EXISTS factura_auditoria (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id  INTEGER,
    usuario_id  INTEGER,
    accion      TEXT NOT NULL,
    entidad     TEXT NOT NULL DEFAULT '',
    entidad_id  INTEGER,
    detalle     TEXT NOT NULL DEFAULT '{}',
    ip          TEXT NOT NULL DEFAULT '',
    creada_en   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_aud_empresa ON factura_auditoria(empresa_id, id);
CREATE INDEX IF NOT EXISTS idx_aud_accion ON factura_auditoria(accion, creada_en);
