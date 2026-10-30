-- 017 — Tablas del Bloque E (garantías) + tipos de auditoría.
--
-- BLOQUE E (2026-10-13): términos de garantía por producto/lote,
-- tarjetas activadas desde ventas, y ciclo de reclamos con costo real.
-- En memoria el bloque vive en el bundle (os_garantias/); estas tablas
-- son el esquema duradero para el backend PG del OSBundle (issue #1).
--
-- NOTA DE REGISTRO: este archivo DEBE agregarse al final de
-- os_persist/schema/_orden.txt (lo hace el coordinador; el migrador
-- falla si un .sql no está en el manifiesto).
--
-- Idempotente: CREATE TABLE IF NOT EXISTS + DROP/ADD CONSTRAINT para
-- el CHECK de tipos (patrón 011/012/013).
-- Reversión (manual): DROP TABLE os_garantia_reclamos,
-- os_garantia_tarjetas, os_garantia_terminos (en ese orden) y re-aplicar
-- el CHECK de os_eventos sin los 2 tipos nuevos (solo si no quedan
-- eventos de esos tipos).

-- ---- términos --------------------------------------------------------
CREATE TABLE IF NOT EXISTS os_garantia_terminos (
    termino_id   TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL,
    producto_id  TEXT NOT NULL,
    lote_id      TEXT NOT NULL DEFAULT '',
    duracion_meses INTEGER NOT NULL CHECK (duracion_meses > 0),
    cobertura    TEXT NOT NULL DEFAULT 'fabricacion'
        CHECK (cobertura IN ('fabricacion', 'fabricacion_mano_obra',
                             'extendida', 'proveedor')),
    condiciones  TEXT NOT NULL DEFAULT '',
    inicio       TEXT NOT NULL DEFAULT 'fecha_venta'
        CHECK (inicio IN ('fecha_venta', 'fecha_entrega')),
    costo_maximo_por_reclamo NUMERIC(18, 2),
    proveedor_respalda BOOLEAN NOT NULL DEFAULT FALSE,
    creado       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_garantia_terminos_empresa
    ON os_garantia_terminos (company_id, producto_id);

-- ---- tarjetas (activaciones) ------------------------------------------
CREATE TABLE IF NOT EXISTS os_garantia_tarjetas (
    tarjeta_id   TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL,
    termino_id   TEXT NOT NULL
        REFERENCES os_garantia_terminos (termino_id),
    producto_id  TEXT NOT NULL,
    venta_id     TEXT NOT NULL,
    cliente_id   TEXT NOT NULL,
    serie_o_lote TEXT NOT NULL DEFAULT '',
    fecha_inicio TIMESTAMPTZ NOT NULL,
    fecha_vencimiento TIMESTAMPTZ NOT NULL,
    cantidad     NUMERIC(18, 3) NOT NULL DEFAULT 1
        CHECK (cantidad > 0),
    estado       TEXT NOT NULL DEFAULT 'activa'
        CHECK (estado IN ('activa', 'reclamada', 'reemplazada',
                          'anulada', 'expirada')),
    reclamo_ref  TEXT NOT NULL DEFAULT '',
    creado       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT ck_tarjeta_vencimiento
        CHECK (fecha_vencimiento > fecha_inicio)
);
CREATE INDEX IF NOT EXISTS ix_garantia_tarjetas_empresa
    ON os_garantia_tarjetas (company_id, estado, fecha_vencimiento);
CREATE INDEX IF NOT EXISTS ix_garantia_tarjetas_cliente
    ON os_garantia_tarjetas (company_id, cliente_id);
CREATE INDEX IF NOT EXISTS ix_garantia_tarjetas_producto
    ON os_garantia_tarjetas (company_id, producto_id);

-- ---- reclamos ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS os_garantia_reclamos (
    reclamo_id   TEXT PRIMARY KEY,
    company_id   TEXT NOT NULL,
    tarjeta_id   TEXT NOT NULL
        REFERENCES os_garantia_tarjetas (tarjeta_id),
    cliente_id   TEXT NOT NULL,
    producto_id  TEXT NOT NULL,
    venta_id     TEXT NOT NULL,
    serie_o_lote TEXT NOT NULL DEFAULT '',
    fecha_reporte TIMESTAMPTZ NOT NULL,
    sintomas     TEXT NOT NULL DEFAULT '',
    diagnostico  TEXT NOT NULL DEFAULT '',
    causa        TEXT NOT NULL DEFAULT ''
        CHECK (causa IN ('', 'fabricacion', 'mal_uso',
                        'desgaste_normal', 'no_determinada')),
    cubierto     BOOLEAN,
    resolucion   TEXT NOT NULL DEFAULT ''
        CHECK (resolucion IN ('', 'reparacion', 'reemplazo',
                              'devolucion', 'rechazado')),
    costo_mano_obra NUMERIC(18, 2) NOT NULL DEFAULT 0
        CHECK (costo_mano_obra >= 0),
    costo_repuestos NUMERIC(18, 2) NOT NULL DEFAULT 0
        CHECK (costo_repuestos >= 0),
    costo_envio  NUMERIC(18, 2) NOT NULL DEFAULT 0
        CHECK (costo_envio >= 0),
    moneda       TEXT NOT NULL DEFAULT 'DOP',
    estado       TEXT NOT NULL DEFAULT 'abierto'
        CHECK (estado IN ('abierto', 'diagnosticado', 'cerrado')),
    fecha_cierre TIMESTAMPTZ,
    gasto_emitido BOOLEAN NOT NULL DEFAULT FALSE,
    creado       TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_garantia_reclamos_empresa
    ON os_garantia_reclamos (company_id, estado, fecha_cierre);
CREATE INDEX IF NOT EXISTS ix_garantia_reclamos_producto
    ON os_garantia_reclamos (company_id, producto_id);

-- ---- tipos de auditoría -------------------------------------------------
-- LISTA UNIÓN CANÓNICA: este CHECK es el último de la cadena en el orden
-- canónico de _orden.txt; contiene la unión completa de tipos (regla
-- 011/012/013: si se toca el CHECK, preservar la unión completa o se
-- rompe la auditoría de otros bloques). Agrega 'garantia' y
-- 'reclamo_garantia' (tipos auditados por os_garantias/rutas.py).

-- Extensión ADITIVA del CHECK de tipos de os_eventos (2026-10-13):
-- el patrón anterior (DROP + ADD con lista fija) rompía al re-aplicarse
-- con datos de tipos más nuevos (CheckViolation "violated by some row")
-- y al aplicarse fuera de orden (perdía tipos). Ahora: lee los tipos del
-- CHECK vigente, agrega los nuevos solo si faltan y reconstruye
-- preservando la unión existente. Idempotente.
DO $$
DECLARE
    def_actual TEXT;
    tipos TEXT[];
    lista TEXT;
BEGIN
    SELECT pg_get_constraintdef(cc.oid) INTO def_actual
      FROM pg_constraint cc
     WHERE cc.conname = 'os_eventos_tipo_check'
       AND cc.connamespace = current_schema()::regnamespace;
    IF def_actual IS NULL THEN
        RETURN;
    END IF;
    SELECT array_agg(DISTINCT m[1]) INTO tipos
      FROM regexp_matches(def_actual, '''([^'']+)''', 'g') AS m;
    IF NOT ('garantia' = ANY (COALESCE(tipos, '{}'))) AND NOT ('reclamo_garantia' = ANY (COALESCE(tipos, '{}'))) THEN
        SELECT string_agg(quote_literal(t), ', ' ORDER BY t)
          INTO lista
          FROM unnest(COALESCE(tipos, '{}') || ARRAY['garantia', 'reclamo_garantia']) AS t;
        ALTER TABLE os_eventos DROP CONSTRAINT os_eventos_tipo_check;
        EXECUTE 'ALTER TABLE os_eventos ADD CONSTRAINT os_eventos_tipo_check '
             || 'CHECK (tipo IN (' || lista || '))';
    END IF;
END $$;

