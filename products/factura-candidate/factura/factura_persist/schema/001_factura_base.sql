-- 001_factura_base.sql — esquema base de ZAYVERO FACTURA.
-- Dialecto: SQLite (el migrador traduce lo mínimo a PostgreSQL).
-- Convenciones: montos TEXT (Decimal exacto en Python), booleanos
-- INTEGER 0/1, fechas TEXT ISO-8601, JSON en TEXT.

CREATE TABLE IF NOT EXISTS factura_empresas (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre          TEXT NOT NULL,
    rnc             TEXT NOT NULL,
    telefono        TEXT DEFAULT '',
    direccion       TEXT DEFAULT '',
    plan            TEXT NOT NULL DEFAULT 'basico',
    itbis_tasa      TEXT NOT NULL DEFAULT '18',
    e_cf_mode       TEXT NOT NULL DEFAULT 'disabled',
    creada          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS factura_usuarios (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id  INTEGER NOT NULL REFERENCES factura_empresas(id),
    email       TEXT NOT NULL UNIQUE,
    hash        TEXT NOT NULL,
    rol         TEXT NOT NULL DEFAULT 'cajero',
    activo      INTEGER NOT NULL DEFAULT 1,
    creado      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_usuarios_empresa ON factura_usuarios(empresa_id);

CREATE TABLE IF NOT EXISTS factura_productos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id  INTEGER NOT NULL REFERENCES factura_empresas(id),
    sku         TEXT NOT NULL DEFAULT '',
    nombre      TEXT NOT NULL,
    precio      TEXT NOT NULL DEFAULT '0',
    costo       TEXT NOT NULL DEFAULT '0',
    itbis_exento INTEGER NOT NULL DEFAULT 0,
    stock       TEXT NOT NULL DEFAULT '0',
    activo      INTEGER NOT NULL DEFAULT 1,
    creado      TEXT NOT NULL,
    UNIQUE (empresa_id, sku)
);
CREATE INDEX IF NOT EXISTS idx_productos_empresa ON factura_productos(empresa_id);

CREATE TABLE IF NOT EXISTS factura_clientes (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id  INTEGER NOT NULL REFERENCES factura_empresas(id),
    nombre      TEXT NOT NULL,
    rnc         TEXT NOT NULL DEFAULT '',
    telefono    TEXT DEFAULT '',
    direccion   TEXT DEFAULT '',
    creado      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_clientes_empresa ON factura_clientes(empresa_id);

CREATE TABLE IF NOT EXISTS factura_cotizaciones (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id  INTEGER NOT NULL REFERENCES factura_empresas(id),
    numero      TEXT NOT NULL,
    cliente_id  INTEGER REFERENCES factura_clientes(id),
    items       TEXT NOT NULL DEFAULT '[]',
    subtotal    TEXT NOT NULL DEFAULT '0',
    itbis       TEXT NOT NULL DEFAULT '0',
    total       TEXT NOT NULL DEFAULT '0',
    estado      TEXT NOT NULL DEFAULT 'vigente',
    creada      TEXT NOT NULL,
    creada_por  INTEGER REFERENCES factura_usuarios(id),
    UNIQUE (empresa_id, numero)
);
CREATE INDEX IF NOT EXISTS idx_cotiz_empresa ON factura_cotizaciones(empresa_id);

CREATE TABLE IF NOT EXISTS factura_ventas (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id  INTEGER NOT NULL REFERENCES factura_empresas(id),
    numero      TEXT NOT NULL,
    tipo        TEXT NOT NULL DEFAULT 'factura',
    cliente_id  INTEGER REFERENCES factura_clientes(id),
    items       TEXT NOT NULL DEFAULT '[]',
    subtotal    TEXT NOT NULL DEFAULT '0',
    itbis       TEXT NOT NULL DEFAULT '0',
    descuento   TEXT NOT NULL DEFAULT '0',
    total       TEXT NOT NULL DEFAULT '0',
    estado      TEXT NOT NULL DEFAULT 'pendiente',
    creada      TEXT NOT NULL,
    creada_por  INTEGER REFERENCES factura_usuarios(id),
    UNIQUE (empresa_id, numero)
);
CREATE INDEX IF NOT EXISTS idx_ventas_empresa ON factura_ventas(empresa_id);
CREATE INDEX IF NOT EXISTS idx_ventas_estado ON factura_ventas(empresa_id, estado);

CREATE TABLE IF NOT EXISTS factura_pagos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id  INTEGER NOT NULL REFERENCES factura_empresas(id),
    venta_id    INTEGER NOT NULL REFERENCES factura_ventas(id),
    monto       TEXT NOT NULL,
    metodo      TEXT NOT NULL DEFAULT 'efectivo',
    creada      TEXT NOT NULL,
    creado_por  INTEGER REFERENCES factura_usuarios(id)
);
CREATE INDEX IF NOT EXISTS idx_pagos_venta ON factura_pagos(venta_id);

CREATE TABLE IF NOT EXISTS factura_secuencias (
    tipo_doc        TEXT NOT NULL,
    empresa_id      INTEGER NOT NULL REFERENCES factura_empresas(id),
    ultimo_numero   INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (tipo_doc, empresa_id)
);
