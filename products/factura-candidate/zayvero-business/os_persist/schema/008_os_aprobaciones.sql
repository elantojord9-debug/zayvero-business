-- BLOQUE 6 (sprint 3, 2026-10-12) — Migración 008: APROBACIONES HUMANAS,
-- CONFIG DE UMBRALES DEL CATÁLOGO E IDEMPOTENCIA DE PRECIOS/COSTOS.
--
-- Idempotente: CREATE TABLE/INDEX IF NOT EXISTS. Reversible: DROP TABLE
-- (documentado en docs/aprobaciones.md; no se ejecuta automáticamente).
--
-- Decisiones:
-- - company_id en TODAS las tablas (aislamiento multiempresa).
-- - os_aprobaciones NO usa os_company_lock: las decisiones toman
--   SELECT ... FOR UPDATE sobre la FILA de la solicitud (serializa
--   decisiones concurrentes sobre la misma solicitud sin bloquear a
--   toda la empresa). Esto permite decidir aprobaciones DENTRO de
--   mutar_os (que ya tiene os_company_lock) sin deadlock: nunca se
--   toman los dos locks en la misma transacción en orden inverso.
-- - La ejecución de la operación aprobada la hace la capa HTTP
--   (aprobaciones/http.py, webapp/os_routes.py), no esta migración.
-- - os_catalogo_config: umbrales por empresa. Los DEFAULTS son de
--   DEMO (marcados): margen objetivo 40 %, piso en costo, cambio de
--   precio > 25 % requiere aprobación, devolución >= RD$5,000
--   requiere aprobación. Sin conversión de moneda (simplificación de
--   demo documentada): el umbral se compara directo en la moneda de
--   la operación.
-- - os_catalogo_idempotencia: claves Idempotency-Key para
--   POST catalogo/precios y catalogo/costos (misma semántica que
--   os_persist/idempotencia.py: misma clave + mismo body -> se
--   repite la respuesta; misma clave + body distinto -> 422).

-- Solicitudes de aprobación humana.
CREATE TABLE IF NOT EXISTS os_aprobaciones (
    company_id     TEXT NOT NULL REFERENCES os_companies(company_id),
    aprobacion_id  TEXT NOT NULL,
    tipo           TEXT NOT NULL,   -- cambio_precio | devolucion_venta |
                                   -- devolucion_compra | aprobacion_financiera
    estado         TEXT NOT NULL DEFAULT 'pendiente_aprobacion',
                                   -- pendiente_aprobacion | aprobada |
                                   -- rechazada | ejecutada | cancelada
    solicitante    TEXT NOT NULL DEFAULT '',   -- user_id que la pidió
    payload        JSONB NOT NULL DEFAULT '{}', -- datos para ejecutar
    monto          NUMERIC(20,2),               -- monto de referencia
    moneda         CHAR(3),                     -- moneda del monto
    motivo_solicitud TEXT NOT NULL DEFAULT '',
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    decidida_por   TEXT,
    decidida_at    TIMESTAMPTZ,
    motivo_decision TEXT NOT NULL DEFAULT '',
    ejecutada_at   TIMESTAMPTZ,
    PRIMARY KEY (company_id, aprobacion_id),
    CONSTRAINT ck_apr_tipo CHECK (tipo IN (
        'cambio_precio', 'devolucion_venta', 'devolucion_compra',
        'aprobacion_financiera')),
    CONSTRAINT ck_apr_estado CHECK (estado IN (
        'pendiente_aprobacion', 'aprobada', 'rechazada', 'ejecutada',
        'cancelada'))
);
CREATE INDEX IF NOT EXISTS ix_apr_estado
    ON os_aprobaciones (company_id, estado);

-- Umbrales y defaults por empresa (valores DEMO; la empresa los puede
-- personalizar vía POST /api/v1/os/catalogo/config).
CREATE TABLE IF NOT EXISTS os_catalogo_config (
    company_id            TEXT NOT NULL PRIMARY KEY
                          REFERENCES os_companies(company_id),
    margen_objetivo       NUMERIC(8,4) NOT NULL DEFAULT 0.40,
    margen_minimo         NUMERIC(8,4) NOT NULL DEFAULT 0.00,
    umbral_cambio_precio  NUMERIC(8,4) NOT NULL DEFAULT 0.25,
    umbral_devolucion_monto NUMERIC(20,2) NOT NULL DEFAULT 5000,
    moneda_defecto        CHAR(3) NOT NULL DEFAULT 'DOP',
    personalizada         BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Idempotencia de fijar_precio / fijar_costo del catálogo.
CREATE TABLE IF NOT EXISTS os_catalogo_idempotencia (
    company_id  TEXT NOT NULL REFERENCES os_companies(company_id),
    clave       TEXT NOT NULL,
    endpoint    TEXT NOT NULL,   -- catalogo/precios | catalogo/costos
    req_hash    TEXT NOT NULL,   -- sha256 del body canónico
    estado      TEXT NOT NULL DEFAULT 'en_curso',
                                   -- en_curso | completado | fallido
    respuesta   JSONB,             -- respuesta almacenada (completado)
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, clave),
    CONSTRAINT ck_cat_idem_estado CHECK (estado IN (
        'en_curso', 'completado', 'fallido'))
);
