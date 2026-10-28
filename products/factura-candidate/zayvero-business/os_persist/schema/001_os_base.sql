-- LOTE 9A — Migración 001: base de persistencia del Business OS.
-- Idempotente: CREATE TABLE IF NOT EXISTS + índices idempotentes.
-- Toda tabla lleva company_id (aislamiento) y created_at.

CREATE TABLE IF NOT EXISTS os_companies (
    company_id   TEXT PRIMARY KEY,
    nombre       TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Inventario: movimientos como fuente de verdad (el stock se deriva)
CREATE TABLE IF NOT EXISTS os_movimientos (
    movimiento_id TEXT NOT NULL,
    company_id    TEXT NOT NULL REFERENCES os_companies(company_id),
    producto_id   TEXT NOT NULL,
    almacen_id    TEXT NOT NULL DEFAULT 'principal',
    tipo          TEXT NOT NULL,              -- ENTRADA|SALIDA|AJUSTE|DEVOLUCION|TRANSFERENCIA
    cantidad      NUMERIC(20,4) NOT NULL,     -- signo según tipo
    fecha         TIMESTAMPTZ NOT NULL,
    referencia    TEXT NOT NULL DEFAULT '',  -- recepcion_id, venta_id...
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, movimiento_id)
);
CREATE INDEX IF NOT EXISTS ix_os_mov_prod
    ON os_movimientos (company_id, producto_id, almacen_id, fecha);

-- Compras: órdenes y recepciones
CREATE TABLE IF NOT EXISTS os_ordenes (
    compra_id     TEXT NOT NULL,
    company_id    TEXT NOT NULL REFERENCES os_companies(company_id),
    proveedor_id  TEXT NOT NULL,
    moneda        CHAR(3) NOT NULL,
    estado        TEXT NOT NULL,              -- BORRADOR|ENVIADA|PARCIAL|RECIBIDA|CERRADA|CANCELADA
    lineas        JSONB NOT NULL,             -- [{producto_id,cantidad,precio_unitario}]
    fecha_emision TIMESTAMPTZ,
    fecha_entrega_esperada TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, compra_id)
);
CREATE TABLE IF NOT EXISTS os_recepciones (
    recepcion_id TEXT NOT NULL,
    company_id   TEXT NOT NULL REFERENCES os_companies(company_id),
    compra_id    TEXT NOT NULL,
    almacen_id   TEXT NOT NULL DEFAULT 'principal',
    lineas       JSONB NOT NULL,              -- [{producto_id,cantidad}]
    fecha        TIMESTAMPTZ NOT NULL,
    factura_ref  TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, recepcion_id)
);

-- Ventas (CRM): la venta afecta inventario/ingresos/CxC una sola vez
CREATE TABLE IF NOT EXISTS os_ventas (
    venta_id    TEXT NOT NULL,
    company_id  TEXT NOT NULL REFERENCES os_companies(company_id),
    cliente_id  TEXT NOT NULL,
    moneda      CHAR(3) NOT NULL,
    fecha       TIMESTAMPTZ NOT NULL,
    lineas      JSONB NOT NULL,               -- [{producto_id,cantidad,precio_unitario,almacen_id,costo_unitario}]
    total       NUMERIC(20,2) NOT NULL,
    estado      TEXT NOT NULL,                -- PAGADA|PENDIENTE_COBRO|CANCELADA
    a_credito   BOOLEAN NOT NULL DEFAULT FALSE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, venta_id)
);

-- Finanzas: gastos y cuentas CxC/CxP
CREATE TABLE IF NOT EXISTS os_gastos (
    gasto_id     TEXT NOT NULL,
    company_id   TEXT NOT NULL REFERENCES os_companies(company_id),
    categoria    TEXT NOT NULL,
    monto        NUMERIC(20,2) NOT NULL,
    moneda       CHAR(3) NOT NULL,
    fecha        TIMESTAMPTZ NOT NULL,
    proveedor_id TEXT NOT NULL DEFAULT '',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, gasto_id)
);
CREATE TABLE IF NOT EXISTS os_cuentas (
    cuenta_id   TEXT NOT NULL,
    company_id  TEXT NOT NULL REFERENCES os_companies(company_id),
    tipo        TEXT NOT NULL,                -- cxc|cxp
    tercero_id  TEXT NOT NULL,                -- cliente_id o proveedor_id
    monto       NUMERIC(20,2) NOT NULL,
    moneda      CHAR(3) NOT NULL,
    pagado      NUMERIC(20,2) NOT NULL DEFAULT 0,
    vencimiento DATE NOT NULL,
    ref_id      TEXT NOT NULL DEFAULT '',    -- venta_id, compra_id...
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, cuenta_id)
);
CREATE INDEX IF NOT EXISTS ix_os_cuentas_venc
    ON os_cuentas (company_id, tipo, vencimiento);

-- Tesorería: saldos por moneda (un registro por company+moneda)
CREATE TABLE IF NOT EXISTS os_saldos (
    company_id TEXT NOT NULL REFERENCES os_companies(company_id),
    moneda     CHAR(3) NOT NULL,
    saldo      NUMERIC(20,2) NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, moneda)
);

-- CRM: clientes
CREATE TABLE IF NOT EXISTS os_clientes (
    cliente_id  TEXT NOT NULL,
    company_id  TEXT NOT NULL REFERENCES os_companies(company_id),
    nombre      TEXT NOT NULL,
    email       TEXT NOT NULL DEFAULT '',
    telefono    TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, cliente_id)
);

-- Documentos: registro de facturas extraídas (idempotente por documento_id)
CREATE TABLE IF NOT EXISTS os_documentos (
    documento_id   TEXT NOT NULL,
    company_id     TEXT NOT NULL REFERENCES os_companies(company_id),
    numero         TEXT NOT NULL DEFAULT '',
    proveedor      TEXT NOT NULL DEFAULT '',
    fecha          DATE,
    moneda         CHAR(3) NOT NULL DEFAULT 'DOP',
    total          NUMERIC(20,2) NOT NULL DEFAULT 0,
    formato        TEXT NOT NULL DEFAULT '',
    extraccion     JSONB NOT NULL,            -- factura estructurada completa
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, documento_id)
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_os_doc_proveedor_numero
    ON os_documentos (company_id, proveedor, numero);
