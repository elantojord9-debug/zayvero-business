-- 019 — Persistencia real de comprobantes NCF (issue #6, P1b).
--
-- ADVERTENCIA FISCAL: estas tablas ORGANIZAN números de comprobante para
-- control interno. NO es un sistema certificado por la DGII: el panel y
-- las rutas marcan cada salida como "NO CERTIFICADO DGII" / "SINTÉTICO".
--
-- Diseño de concurrencia:
-- - `os_ncf_secuencias`: una fila por (company_id, tipo). La emisión hace
--   SELECT ... FOR UPDATE sobre esa fila dentro de la transacción de
--   escritura (tx_ncf toma además el lock por empresa de os_company_lock,
--   mismo patrón que os_empleados/almacen.py::tx_empleados). Los emisores
--   concurrentes se serializan en el lock de fila: imposible duplicar un
--   número. Sin huecos en commits exitosos (un rollback dejaría el hueco,
--   documentado en docs/p1b-persistencia-ncf.md).
-- - `os_ncf_emitidos`: rastro auditable de cada NCF. La anulación NO borra
--   la fila (estado='anulado'): el número queda reservado y trazable.
-- - Idempotencia: `clave_idempotencia` con UNIQUE (company_id,
--   clave_idempotencia). Los NULL no colisionan (semántica estándar de
--   UNIQUE en PostgreSQL), así que emitir sin clave nunca choca.
--
-- Idempotente: CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS.
-- NO toca os_eventos (el tipo 'ncf' ya está en el CHECK desde la 013).
-- Reversión (manual): DROP TABLE os_ncf_emitidos, os_ncf_secuencias.
-- NO se agrega a _orden.txt (lo hace el coordinador de integración).

CREATE TABLE IF NOT EXISTS os_ncf_secuencias (
    company_id  TEXT NOT NULL,
    tipo        TEXT NOT NULL,
    siguiente   BIGINT NOT NULL DEFAULT 1,
    hasta       BIGINT NOT NULL,
    descripcion TEXT NOT NULL DEFAULT '',
    creada_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizada_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, tipo),
    CONSTRAINT ck_ncf_sec_siguiente CHECK (siguiente >= 1),
    CONSTRAINT ck_ncf_sec_hasta CHECK (hasta >= 1)
);

CREATE TABLE IF NOT EXISTS os_ncf_emitidos (
    company_id          TEXT NOT NULL,
    numero              TEXT NOT NULL,
    tipo                TEXT NOT NULL,
    secuencial          BIGINT NOT NULL,
    concepto            TEXT NOT NULL DEFAULT '',
    estado              TEXT NOT NULL DEFAULT 'emitido',
    clave_idempotencia  TEXT,
    emitido_por         TEXT NOT NULL DEFAULT '',
    emitido_en          TIMESTAMPTZ NOT NULL DEFAULT now(),
    anulado_por         TEXT,
    anulado_en          TIMESTAMPTZ,
    motivo_anulacion    TEXT,
    PRIMARY KEY (company_id, numero),
    CONSTRAINT ck_ncf_emit_estado
        CHECK (estado IN ('emitido', 'anulado')),
    CONSTRAINT uq_ncf_idem UNIQUE (company_id, clave_idempotencia)
);

CREATE INDEX IF NOT EXISTS ix_ncf_emitidos_tipo
    ON os_ncf_emitidos (company_id, tipo, secuencial);
CREATE INDEX IF NOT EXISTS ix_ncf_emitidos_estado
    ON os_ncf_emitidos (company_id, estado);
