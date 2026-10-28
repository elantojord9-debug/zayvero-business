-- 009_factura_fase2.sql — FASE 2: gastos/ingresos, proveedores, servicios,
-- stock mínimo por producto y vencimiento de CxC.
-- Dialecto portable SQLite/PostgreSQL (placeholders %s en el código).

-- Proveedores (enlazables a gastos)
CREATE TABLE IF NOT EXISTS factura_proveedores (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id  INTEGER NOT NULL REFERENCES factura_empresas(id),
    nombre      TEXT NOT NULL,
    rnc         TEXT NOT NULL DEFAULT '',
    telefono    TEXT DEFAULT '',
    email       TEXT DEFAULT '',
    direccion   TEXT DEFAULT '',
    balance     TEXT NOT NULL DEFAULT '0',
    activo      INTEGER NOT NULL DEFAULT 1,
    creado      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_proveedores_empresa
    ON factura_proveedores(empresa_id);

-- Gastos: egresos operativos no ligados a ventas (alquiler, nómina, etc.)
CREATE TABLE IF NOT EXISTS factura_gastos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id      INTEGER NOT NULL REFERENCES factura_empresas(id),
    fecha           TEXT NOT NULL,
    categoria       TEXT NOT NULL,
    descripcion     TEXT NOT NULL DEFAULT '',
    comprobante     TEXT NOT NULL DEFAULT '',
    monto           TEXT NOT NULL DEFAULT '0',
    metodo          TEXT NOT NULL DEFAULT 'efectivo',
    sucursal_id     INTEGER REFERENCES factura_sucursales(id),
    proveedor_id    INTEGER REFERENCES factura_proveedores(id),
    creado_por      INTEGER REFERENCES factura_usuarios(id),
    creada          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_gastos_empresa_fecha
    ON factura_gastos(empresa_id, fecha);

-- Ingresos no-venta (otros ingresos)
CREATE TABLE IF NOT EXISTS factura_ingresos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id      INTEGER NOT NULL REFERENCES factura_empresas(id),
    fecha           TEXT NOT NULL,
    categoria       TEXT NOT NULL,
    descripcion     TEXT NOT NULL DEFAULT '',
    comprobante     TEXT NOT NULL DEFAULT '',
    monto           TEXT NOT NULL DEFAULT '0',
    metodo          TEXT NOT NULL DEFAULT 'efectivo',
    sucursal_id     INTEGER REFERENCES factura_sucursales(id),
    creado_por      INTEGER REFERENCES factura_usuarios(id),
    creada          TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ingresos_empresa_fecha
    ON factura_ingresos(empresa_id, fecha);

-- Servicios: tipo producto/servicio (los servicios no mueven inventario) y
-- stock mínimo configurable por producto para las alertas.
ALTER TABLE factura_productos
    ADD COLUMN tipo TEXT NOT NULL DEFAULT 'producto';
ALTER TABLE factura_productos
    ADD COLUMN stock_minimo TEXT NOT NULL DEFAULT '5';

-- Vencimiento de CxC: fecha límite de pago ISO ('' = sin fecha límite).
ALTER TABLE factura_ventas
    ADD COLUMN vencimiento TEXT NOT NULL DEFAULT '';
