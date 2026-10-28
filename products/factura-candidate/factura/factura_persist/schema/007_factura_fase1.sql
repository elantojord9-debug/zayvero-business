-- 007_factura_fase1.sql — FASE 1 + FASE 4 (producto y SaaS).
-- Empresa: estado (activa/suspendida), trial_hasta (ISO fecha o '' =
-- sin límite por prueba), onboarding_completo, ultima_sucursal (NULL ok).
-- Tablas nuevas: sucursales, caja (turnos) + movimientos, devoluciones,
-- ajustes de inventario, tickets de soporte, solicitudes de demo,
-- suscripciones (estructura futura, sin pasarela), tokens de recuperación.
-- Dialecto portable SQLite/PostgreSQL (placeholders %s en el código).

ALTER TABLE factura_empresas ADD COLUMN estado TEXT NOT NULL DEFAULT 'activa';
ALTER TABLE factura_empresas ADD COLUMN trial_hasta TEXT NOT NULL DEFAULT '';
ALTER TABLE factura_empresas ADD COLUMN onboarding_completo INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS factura_sucursales (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id  INTEGER NOT NULL REFERENCES factura_empresas(id),
    nombre      TEXT NOT NULL,
    direccion   TEXT DEFAULT '',
    telefono    TEXT DEFAULT '',
    activa      INTEGER NOT NULL DEFAULT 1,
    creada      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_sucursales_empresa ON factura_sucursales(empresa_id);

-- Caja: un turno abierto por empresa (estado='abierta'). El POS exige
-- turno abierto. Al cerrar se registra monto_final y diferencia.
CREATE TABLE IF NOT EXISTS factura_caja (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id      INTEGER NOT NULL REFERENCES factura_empresas(id),
    sucursal_id     INTEGER REFERENCES factura_sucursales(id),
    estado          TEXT NOT NULL DEFAULT 'abierta',
    abierta_por     INTEGER REFERENCES factura_usuarios(id),
    monto_inicial   TEXT NOT NULL DEFAULT '0',
    monto_final     TEXT NOT NULL DEFAULT '0',
    diferencia      TEXT NOT NULL DEFAULT '0',
    abierta_en      TEXT NOT NULL,
    cerrada_en      TEXT NOT NULL DEFAULT '',
    cerrada_por     INTEGER REFERENCES factura_usuarios(id)
);
CREATE INDEX IF NOT EXISTS idx_caja_empresa ON factura_caja(empresa_id, estado);

CREATE TABLE IF NOT EXISTS factura_caja_movimientos (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    caja_id         INTEGER NOT NULL REFERENCES factura_caja(id),
    empresa_id      INTEGER NOT NULL REFERENCES factura_empresas(id),
    tipo            TEXT NOT NULL,
    monto           TEXT NOT NULL,
    motivo          TEXT NOT NULL DEFAULT '',
    creado_por      INTEGER REFERENCES factura_usuarios(id),
    creado_en       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_cajamov_caja ON factura_caja_movimientos(caja_id);

-- Devoluciones: NOTA sobre una venta (no edita la venta original).
-- items: JSON [{producto_id, nombre, cantidad, precio}] con cantidades
-- devueltas; el stock se devuelve al inventario.
CREATE TABLE IF NOT EXISTS factura_devoluciones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id      INTEGER NOT NULL REFERENCES factura_empresas(id),
    venta_id        INTEGER NOT NULL REFERENCES factura_ventas(id),
    numero          TEXT NOT NULL,
    items           TEXT NOT NULL DEFAULT '[]',
    total           TEXT NOT NULL DEFAULT '0',
    motivo          TEXT NOT NULL DEFAULT '',
    creada_por      INTEGER REFERENCES factura_usuarios(id),
    creada_en       TEXT NOT NULL,
    UNIQUE (empresa_id, numero)
);
CREATE INDEX IF NOT EXISTS idx_devol_empresa ON factura_devoluciones(empresa_id);
CREATE INDEX IF NOT EXISTS idx_devol_venta ON factura_devoluciones(venta_id);

-- Ajustes manuales de inventario (siempre con motivo, siempre auditados).
CREATE TABLE IF NOT EXISTS factura_ajustes_inventario (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id      INTEGER NOT NULL REFERENCES factura_empresas(id),
    producto_id     INTEGER NOT NULL REFERENCES factura_productos(id),
    stock_antes     TEXT NOT NULL,
    stock_despues   TEXT NOT NULL,
    motivo          TEXT NOT NULL,
    creado_por      INTEGER REFERENCES factura_usuarios(id),
    creado_en       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ajustes_producto
    ON factura_ajustes_inventario(producto_id);

-- Tickets de soporte por empresa.
CREATE TABLE IF NOT EXISTS factura_tickets (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id      INTEGER NOT NULL REFERENCES factura_empresas(id),
    usuario_id      INTEGER REFERENCES factura_usuarios(id),
    asunto          TEXT NOT NULL,
    mensaje         TEXT NOT NULL,
    estado          TEXT NOT NULL DEFAULT 'abierto',
    creado_en       TEXT NOT NULL,
    actualizado_en  TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_tickets_empresa ON factura_tickets(empresa_id, estado);

-- Solicitudes de demo desde la landing pública.
CREATE TABLE IF NOT EXISTS factura_solicitudes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre          TEXT NOT NULL,
    empresa         TEXT NOT NULL DEFAULT '',
    email           TEXT NOT NULL,
    telefono        TEXT NOT NULL DEFAULT '',
    mensaje         TEXT NOT NULL DEFAULT '',
    estado          TEXT NOT NULL DEFAULT 'nueva',
    creada_en       TEXT NOT NULL
);

-- Suscripciones: estructura preparada para pagos futuros.
-- SIN pasarela de pago: estado siempre 'pendiente_proveedor' hasta que
-- se integre un proveedor (Stripe/PayPal/Tilopay). No procesa cobros.
CREATE TABLE IF NOT EXISTS factura_suscripciones (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    empresa_id      INTEGER NOT NULL REFERENCES factura_empresas(id),
    plan            TEXT NOT NULL,
    estado          TEXT NOT NULL DEFAULT 'pendiente_proveedor',
    inicia_en       TEXT NOT NULL DEFAULT '',
    termina_en      TEXT NOT NULL DEFAULT '',
    proveedor       TEXT NOT NULL DEFAULT '',
    referencia_ext  TEXT NOT NULL DEFAULT '',
    notas           TEXT NOT NULL DEFAULT 'pendiente proveedor de pagos',
    creado_en       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_suscr_empresa ON factura_suscripciones(empresa_id);

-- Tokens de recuperación de acceso (un solo uso, 60 min de vigencia).
-- En modo demo el token se muestra en pantalla (en producción viajaría
-- por email; este prototipo NO envía correos).
CREATE TABLE IF NOT EXISTS factura_reset_tokens (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    usuario_id      INTEGER NOT NULL REFERENCES factura_usuarios(id),
    token           TEXT NOT NULL UNIQUE,
    expira_en       TEXT NOT NULL,
    usado_en        TEXT NOT NULL DEFAULT '',
    creado_en       TEXT NOT NULL
);
