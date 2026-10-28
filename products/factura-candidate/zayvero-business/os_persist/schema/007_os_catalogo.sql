-- BLOQUE 5 (sprint 2, 2026-10-12) — Migración 007: CATÁLOGO MAESTRO.
--
-- Una sola fuente de verdad para productos, categorías, costos, precios
-- y almacenes, usable por Inventario, Simulator y Price Advisor SIN
-- duplicar datos. Los motores existentes SIGUEN leyendo sus snapshots
-- como fuente primaria; el catálogo solo los ENRIQUECE cuando tiene
-- datos (fallback automático si está vacío). Ver docs/catalogo.md.
--
-- Idempotente: CREATE TABLE/INDEX IF NOT EXISTS. Las restricciones van
-- inline en el CREATE TABLE para que la re-ejecución sea un no-op.
-- Reversible: el DOWN está documentado y probado en docs/catalogo.md
-- (no se ejecuta automáticamente).
--
-- Decisiones:
-- - company_id en TODAS las tablas (aislamiento multiempresa).
-- - SKU único por empresa: UNIQUE (company_id, sku).
-- - Precios y costos con VIGENCIA (vigente_desde/vigente_hasta): el
--   historial se conserva; "vigente" = vigente_hasta IS NULL. Un índice
--   único parcial garantiza UN solo vigente por (empresa, producto,
--   moneda). La app cierra la fila anterior en la misma transacción.
-- - Precios/costos son por producto+moneda (NO por almacén): el catálogo
--   es maestro comercial; el costo por almacén sigue viviendo en
--   os_finanzas/CostoInventario.
-- - Los productos se DESACTIVAN (activo=FALSE), no se borran: los
--   historiales de precio/costo referencian el producto.
-- - `version` en entidades maestras para bloqueo optimista en updates.

-- Categorías (árbol simple vía padre_id; la FK autorreferencial va
-- inline para que la re-ejecución sea no-op).
CREATE TABLE IF NOT EXISTS os_catalogo_categorias (
    company_id   TEXT NOT NULL REFERENCES os_companies(company_id),
    categoria_id TEXT NOT NULL,
    nombre       TEXT NOT NULL,
    padre_id     TEXT,
    activa       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    version      BIGINT NOT NULL DEFAULT 1,
    PRIMARY KEY (company_id, categoria_id),
    CONSTRAINT fk_cat_padre FOREIGN KEY (company_id, padre_id)
        REFERENCES os_catalogo_categorias (company_id, categoria_id)
);

-- Almacenes registrados (los movimientos usan almacen_id como texto
-- libre; el catálogo es el registro maestro de cuáles existen).
CREATE TABLE IF NOT EXISTS os_almacenes (
    company_id   TEXT NOT NULL REFERENCES os_companies(company_id),
    almacen_id   TEXT NOT NULL,
    nombre       TEXT NOT NULL,
    direccion    TEXT NOT NULL DEFAULT '',
    activo       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    version      BIGINT NOT NULL DEFAULT 1,
    PRIMARY KEY (company_id, almacen_id)
);

-- Productos maestros.
CREATE TABLE IF NOT EXISTS os_catalogo_productos (
    company_id   TEXT NOT NULL REFERENCES os_companies(company_id),
    producto_id  TEXT NOT NULL,
    sku          TEXT NOT NULL,
    nombre       TEXT NOT NULL,
    descripcion  TEXT NOT NULL DEFAULT '',
    categoria_id TEXT,
    unidad       TEXT NOT NULL DEFAULT 'und',
    activo       BOOLEAN NOT NULL DEFAULT TRUE,
    metadata     JSONB NOT NULL DEFAULT '{}',
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    version      BIGINT NOT NULL DEFAULT 1,
    PRIMARY KEY (company_id, producto_id),
    CONSTRAINT ux_cat_sku_empresa UNIQUE (company_id, sku),
    CONSTRAINT fk_cat_producto_categoria FOREIGN KEY
        (company_id, categoria_id)
        REFERENCES os_catalogo_categorias (company_id, categoria_id)
);
CREATE INDEX IF NOT EXISTS ix_cat_prod_categoria
    ON os_catalogo_productos (company_id, categoria_id);
CREATE INDEX IF NOT EXISTS ix_cat_prod_activo
    ON os_catalogo_productos (company_id, activo);

-- Precios de venta con vigencia (historial completo).
CREATE TABLE IF NOT EXISTS os_catalogo_precios (
    id             BIGSERIAL PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES os_companies(company_id),
    producto_id    TEXT NOT NULL,
    moneda         CHAR(3) NOT NULL,
    precio         NUMERIC(20,2) NOT NULL,
    vigente_desde  TIMESTAMPTZ NOT NULL DEFAULT now(),
    vigente_hasta  TIMESTAMPTZ,              -- NULL = vigente
    motivo         TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_cat_precio_producto FOREIGN KEY
        (company_id, producto_id)
        REFERENCES os_catalogo_productos (company_id, producto_id),
    CONSTRAINT ck_cat_precio_no_negativo CHECK (precio >= 0),
    CONSTRAINT ux_cat_precio_desde UNIQUE
        (company_id, producto_id, moneda, vigente_desde)
);
-- Un solo precio vigente por (empresa, producto, moneda).
CREATE UNIQUE INDEX IF NOT EXISTS ux_cat_precio_un_vigente
    ON os_catalogo_precios (company_id, producto_id, moneda)
    WHERE vigente_hasta IS NULL;
CREATE INDEX IF NOT EXISTS ix_cat_precio_hist
    ON os_catalogo_precios (company_id, producto_id, moneda, vigente_desde);

-- Costos de referencia con vigencia (historial completo).
-- `fuente`: manual | compra | importacion | migracion.
CREATE TABLE IF NOT EXISTS os_catalogo_costos (
    id             BIGSERIAL PRIMARY KEY,
    company_id     TEXT NOT NULL REFERENCES os_companies(company_id),
    producto_id    TEXT NOT NULL,
    moneda         CHAR(3) NOT NULL,
    costo          NUMERIC(20,4) NOT NULL,
    fuente         TEXT NOT NULL DEFAULT 'manual',
    vigente_desde  TIMESTAMPTZ NOT NULL DEFAULT now(),
    vigente_hasta  TIMESTAMPTZ,              -- NULL = vigente
    motivo         TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT fk_cat_costo_producto FOREIGN KEY
        (company_id, producto_id)
        REFERENCES os_catalogo_productos (company_id, producto_id),
    CONSTRAINT ck_cat_costo_no_negativo CHECK (costo >= 0),
    CONSTRAINT ux_cat_costo_desde UNIQUE
        (company_id, producto_id, moneda, vigente_desde)
);
-- Un solo costo vigente por (empresa, producto, moneda).
CREATE UNIQUE INDEX IF NOT EXISTS ux_cat_costo_un_vigente
    ON os_catalogo_costos (company_id, producto_id, moneda)
    WHERE vigente_hasta IS NULL;
CREATE INDEX IF NOT EXISTS ix_cat_costo_hist
    ON os_catalogo_costos (company_id, producto_id, moneda, vigente_desde);
