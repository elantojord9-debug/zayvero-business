-- 002_factura_ecf.sql — comprobantes e-CF (SIMULACIÓN, sin validez DGII).
-- Se reutilizan los MOTORES de os_ecf (modelos, flujo, validación, XML,
-- MockProvider); la persistencia vive aquí porque el almacén PG de os_ecf
-- requiere PostgreSQL y este despliegue usa SQLite por defecto.

CREATE TABLE IF NOT EXISTS factura_ecf (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id  INTEGER NOT NULL REFERENCES factura_empresas(id),
    e_ncf       TEXT NOT NULL,
    tipo        TEXT NOT NULL,
    estado      TEXT NOT NULL DEFAULT 'borrador',
    modo        TEXT NOT NULL DEFAULT 'disabled',
    xml         TEXT NOT NULL DEFAULT '',
    datos       TEXT NOT NULL DEFAULT '{}',
    historial   TEXT NOT NULL DEFAULT '[]',
    creado_en   TEXT NOT NULL,
    actualizado_en TEXT NOT NULL,
    UNIQUE (empresa_id, e_ncf)
);
CREATE INDEX IF NOT EXISTS idx_ecf_empresa ON factura_ecf(empresa_id);
CREATE INDEX IF NOT EXISTS idx_ecf_estado ON factura_ecf(empresa_id, estado);

CREATE TABLE IF NOT EXISTS factura_ecf_eventos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    comprobante_id  INTEGER NOT NULL REFERENCES factura_ecf(id),
    empresa_id      INTEGER NOT NULL REFERENCES factura_empresas(id),
    seq             INTEGER NOT NULL,
    de_estado       TEXT,
    a_estado        TEXT NOT NULL,
    actor           TEXT NOT NULL DEFAULT '',
    detalle         TEXT NOT NULL DEFAULT '{}',
    hash            TEXT NOT NULL,
    timestamp       TEXT NOT NULL,
    creado_en       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ecf_ev_comp ON factura_ecf_eventos(comprobante_id);
