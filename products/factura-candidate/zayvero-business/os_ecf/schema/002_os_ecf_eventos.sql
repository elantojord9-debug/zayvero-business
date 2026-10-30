-- 002_os_ecf_eventos.sql — bitácora de eventos del comprobante e-CF.
-- Patrón: auditoría de eventos (ver os_contab/ncf_pg.py).
-- AVISO: SIMULACIÓN — NO VÁLIDO ANTE DGII.

CREATE TABLE IF NOT EXISTS os_ecf_eventos (
    id              BIGSERIAL PRIMARY KEY,
    comprobante_id  TEXT NOT NULL REFERENCES os_ecf_comprobantes(id),
    company_id      TEXT NOT NULL,
    de_estado       TEXT,                        -- NULL en la creación
    a_estado        TEXT NOT NULL,
    actor           TEXT NOT NULL DEFAULT '',
    detalle         JSONB DEFAULT '{}',
    hash            TEXT NOT NULL,               -- encadenamiento de eventos
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_ecf_ev_comprobante
    ON os_ecf_eventos (comprobante_id);
CREATE INDEX IF NOT EXISTS idx_ecf_ev_company
    ON os_ecf_eventos (company_id);
