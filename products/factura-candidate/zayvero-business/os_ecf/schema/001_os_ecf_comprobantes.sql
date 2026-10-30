-- 001_os_ecf_comprobantes.sql — tabla principal de comprobantes e-CF.
-- Esquema PROPIO del módulo os_ecf (fase 1, local, sintético).
-- NO toca os_persist/schema/ ni os_migrations: el tracking es
-- os_ecf_migrations, aplicado por os_ecf/migrar.py.
-- AVISO: SIMULACIÓN — NO VÁLIDO ANTE DGII.

CREATE TABLE IF NOT EXISTS os_ecf_comprobantes (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    tipo            TEXT NOT NULL,              -- "31".."47" (ver TIPOS_ECF)
    e_ncf           TEXT NOT NULL UNIQUE,       -- E + tipo(2) + secuencia(10)
    encf_original   TEXT NOT NULL DEFAULT '',   -- notas 33/34 enlazan aquí
    emisor_rnc      TEXT,
    emisor_razon    TEXT,
    receptor_rnc    TEXT,
    receptor_razon  TEXT,
    moneda          TEXT NOT NULL DEFAULT 'DOP',
    estado          TEXT NOT NULL,              -- ver ESTADOS en modelos.py
    modo            TEXT NOT NULL DEFAULT 'disabled',
    subtotal        NUMERIC(14,2),
    descuento       NUMERIC(14,2),              -- descuento global
    itbis           NUMERIC(14,2),
    total           NUMERIC(14,2),
    datos           JSONB NOT NULL DEFAULT '{}', -- Comprobante.to_dict()
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- e_ncf ya queda indexado por la restricción UNIQUE; este compuesto
-- acelera las lecturas filtradas por empresa (aislamiento multiempresa).
CREATE INDEX IF NOT EXISTS idx_ecf_comp_company
    ON os_ecf_comprobantes (company_id);
CREATE INDEX IF NOT EXISTS idx_ecf_comp_company_encf
    ON os_ecf_comprobantes (company_id, e_ncf);
