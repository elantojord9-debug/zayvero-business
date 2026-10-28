-- REFORZAMIENTO — Migración 003: bloqueo transaccional por empresa.
-- Una fila por empresa; SELECT FOR UPDATE la bloquea durante la
-- transacción de mutación, serializando escritores entre procesos.

CREATE TABLE IF NOT EXISTS os_company_lock (
    company_id TEXT PRIMARY KEY REFERENCES os_companies(company_id),
    version    BIGINT NOT NULL DEFAULT 0,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
