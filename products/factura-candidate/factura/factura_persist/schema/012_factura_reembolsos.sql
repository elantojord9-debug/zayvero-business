ALTER TABLE factura_devoluciones ADD COLUMN itbis TEXT NOT NULL DEFAULT '0';
ALTER TABLE factura_devoluciones ADD COLUMN financiera INTEGER NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS factura_reembolsos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id INTEGER NOT NULL REFERENCES factura_empresas(id),
    venta_id INTEGER NOT NULL REFERENCES factura_ventas(id),
    devolucion_id INTEGER NOT NULL REFERENCES factura_devoluciones(id),
    monto TEXT NOT NULL,
    metodo TEXT NOT NULL,
    creado_por INTEGER REFERENCES factura_usuarios(id),
    creada TEXT NOT NULL
);
