CREATE TABLE IF NOT EXISTS factura_sandbox (
    empresa_id INTEGER NOT NULL REFERENCES factura_empresas(id),
    venta_id INTEGER NOT NULL REFERENCES factura_ventas(id),
    tipo TEXT NOT NULL,
    secuencia INTEGER NOT NULL,
    ncf TEXT NOT NULL UNIQUE,
    payload TEXT NOT NULL,
    estado TEXT NOT NULL,
    invoice_id INTEGER,
    respuesta TEXT NOT NULL DEFAULT '{}',
    mensaje TEXT NOT NULL DEFAULT '',
    qr TEXT NOT NULL DEFAULT '',
    xml TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (empresa_id, venta_id)
);
