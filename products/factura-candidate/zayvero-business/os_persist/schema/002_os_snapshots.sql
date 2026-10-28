-- LOTE 9B — Migración 002: snapshots del Business OS (write-through).
-- Un registro por (empresa, módulo); upsert atómico en una transacción.

CREATE TABLE IF NOT EXISTS os_snapshots (
    company_id TEXT NOT NULL REFERENCES os_companies(company_id),
    modulo     TEXT NOT NULL,
    datos      JSONB NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, modulo)
);

CREATE TABLE IF NOT EXISTS os_flags (
    company_id TEXT NOT NULL REFERENCES os_companies(company_id),
    flag       TEXT NOT NULL,
    PRIMARY KEY (company_id, flag)
);
