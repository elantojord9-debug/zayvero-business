-- 018 — Persistencia PostgreSQL del bloque Contabilidad (P1a, issue #6).
--
-- Contexto: `os_contab/presupuestos.py` (RegistroPresupuestos) y
-- `os_contab/conciliacion.py` (RegistroConciliacion) guardaban en MEMORIA
-- del proceso (dicts ligados al bundle) y perdían todo al reiniciar
-- (limitación documentada en docs/bloque-contabilidad.md). Esta migración
-- les da tablas reales con company_id (aislamiento multiempresa),
-- idempotencia por clave natural y timestamps.
--
-- Tablas:
--   os_presupuestos    presupuestos por (categoria, periodo); clave natural
--                      UNIQUE (company_id, categoria, periodo)
--   os_extractos       líneas de extracto SIMULADO cargadas por CSV;
--                      clave natural UNIQUE (company_id, extracto_id,
--                      nro_linea): re-cargar el mismo extracto no duplica
--   os_conciliaciones  corridas de cotejo (historial); idempotencia por
--                      UNIQUE (company_id, extracto_id, entrada_hash):
--                      reintentar el mismo cotejo devuelve la corrida
--                      guardada sin duplicar filas
--   os_contab_seqs     contador por empresa para los linea_id "L<n>"
--                      (compatibles con el formato en memoria)
--
-- Auditoría: reutiliza los tipos 'presupuesto' y 'conciliacion' de
-- os_eventos (migración 013 + TIPOS_EVENTO); no se tocan CHECKs aquí.
--
-- Idempotente: CREATE TABLE/INDEX IF NOT EXISTS en todo el DDL.
-- Reversión (manual, solo si no quedan datos del bloque):
--   DROP TABLE os_conciliaciones, os_extractos, os_presupuestos,
--            os_contab_seqs;
-- (sin dependencias externas salvo os_companies).

-- ---- Presupuestos ----------------------------------------------------
CREATE TABLE IF NOT EXISTS os_presupuestos (
    presupuesto_id TEXT NOT NULL,
    company_id     TEXT NOT NULL REFERENCES os_companies(company_id),
    categoria      TEXT NOT NULL CHECK (categoria <> ''),
    periodo        TEXT NOT NULL
                   CHECK (periodo ~ '^\d{4}-(0[1-9]|1[0-2])$'),
    limite         NUMERIC(14, 2) NOT NULL CHECK (limite > 0),
    moneda         TEXT NOT NULL CHECK (moneda ~ '^[A-Z]{3}$'),
    creado_en      TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, presupuesto_id)
);
-- Clave natural: un solo presupuesto por (categoria, periodo) y empresa
-- (regla de negocio de RegistroPresupuestos.crear).
CREATE UNIQUE INDEX IF NOT EXISTS ux_os_presupuestos_clave_natural
    ON os_presupuestos (company_id, categoria, periodo);
CREATE INDEX IF NOT EXISTS ix_os_presupuestos_empresa_periodo
    ON os_presupuestos (company_id, periodo);

-- ---- Extractos (líneas simuladas) -------------------------------------
CREATE TABLE IF NOT EXISTS os_extractos (
    linea_id      TEXT NOT NULL,
    company_id    TEXT NOT NULL REFERENCES os_companies(company_id),
    extracto_id   TEXT NOT NULL,            -- lote de carga (CSV)
    nro_linea     INTEGER NOT NULL CHECK (nro_linea >= 1),
    fecha         DATE NOT NULL,
    descripcion   TEXT NOT NULL DEFAULT '',
    monto         NUMERIC(14, 2) NOT NULL,  -- + entrada, − salida
    moneda        TEXT NOT NULL CHECK (moneda ~ '^[A-Z]{3}$'),
    referencia    TEXT NOT NULL DEFAULT '',
    estado        TEXT NOT NULL DEFAULT 'pendiente'
                  CHECK (estado IN ('conciliado', 'pendiente',
                                    'discrepancia')),
    contraparte_id TEXT NOT NULL DEFAULT '',  -- mov_id del libro
    marca_manual  JSONB NOT NULL DEFAULT '{}'::jsonb,
    creado_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizado_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, linea_id)
);
-- Clave natural: re-cargar el mismo extracto no duplica líneas.
CREATE UNIQUE INDEX IF NOT EXISTS ux_os_extractos_clave_natural
    ON os_extractos (company_id, extracto_id, nro_linea);
CREATE INDEX IF NOT EXISTS ix_os_extractos_empresa_extracto
    ON os_extractos (company_id, extracto_id, nro_linea);
CREATE INDEX IF NOT EXISTS ix_os_extractos_empresa_estado
    ON os_extractos (company_id, estado);

-- ---- Conciliaciones (corridas de cotejo) ------------------------------
CREATE TABLE IF NOT EXISTS os_conciliaciones (
    conciliacion_id TEXT NOT NULL,
    company_id      TEXT NOT NULL REFERENCES os_companies(company_id),
    extracto_id     TEXT NOT NULL DEFAULT '',  -- alcance ('' = todo)
    entrada_hash    TEXT NOT NULL DEFAULT '',  -- sha256 de las entradas
    creada_en       TIMESTAMPTZ NOT NULL DEFAULT now(),
    lineas_total    INTEGER NOT NULL DEFAULT 0 CHECK (lineas_total >= 0),
    resumen         JSONB NOT NULL DEFAULT '{}'::jsonb,
    resultado       JSONB NOT NULL DEFAULT '{}'::jsonb,
    simulado        BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (company_id, conciliacion_id)
);
-- Idempotencia: el mismo cotejo sobre las mismas entradas no duplica.
CREATE UNIQUE INDEX IF NOT EXISTS ux_os_conciliaciones_idem
    ON os_conciliaciones (company_id, extracto_id, entrada_hash);

-- ---- Contador de líneas por empresa ------------------------------------
CREATE TABLE IF NOT EXISTS os_contab_seqs (
    company_id    TEXT NOT NULL PRIMARY KEY
                  REFERENCES os_companies(company_id),
    proxima_linea BIGINT NOT NULL DEFAULT 1 CHECK (proxima_linea >= 1)
);
