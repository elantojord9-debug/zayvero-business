CREATE TABLE IF NOT EXISTS factura_operaciones (
    empresa_id INTEGER NOT NULL REFERENCES factura_empresas(id),
    operacion TEXT NOT NULL,
    clave TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    respuesta TEXT NOT NULL,
    PRIMARY KEY (empresa_id, operacion, clave)
);
