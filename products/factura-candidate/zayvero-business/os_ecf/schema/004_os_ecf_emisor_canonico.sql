-- 004_os_ecf_emisor_canonico.sql — emisor fiscal canónico por RNC normalizado.
--
-- DECISIÓN EJECUTIVA (issue #11): la unicidad del e-NCF NO es global ni por
-- company_id: es por EMISOR FISCAL (RNC normalizado) + e-NCF.
--   - Dos RNC distintos PUEDEN usar el mismo número e-NCF.
--   - Dos empresas/sucursales con el MISMO RNC comparten secuencia: jamás
--     duplican.
-- Además separa el namespace SIMULADO del futuro FISCAL real (columna origen):
-- el mock jamás consume rango fiscal.
-- Regla cronológica: fecha_emision no puede retroceder por emisor+tipo.
-- AVISO: SIMULACIÓN — NO VÁLIDO ANTE DGII.

-- 1. Comprobantes: RNC normalizado + fecha de emisión.
ALTER TABLE os_ecf_comprobantes
    ADD COLUMN IF NOT EXISTS emisor_rnc_norm TEXT;
UPDATE os_ecf_comprobantes
    SET emisor_rnc_norm = regexp_replace(emisor_rnc, '\D', '', 'g')
    WHERE emisor_rnc_norm IS NULL AND emisor_rnc IS NOT NULL;
ALTER TABLE os_ecf_comprobantes
    ADD COLUMN IF NOT EXISTS fecha_emision TIMESTAMPTZ;
UPDATE os_ecf_comprobantes
    SET fecha_emision = creado_en
    WHERE fecha_emision IS NULL;

-- 2. Unicidad por emisor fiscal (reemplaza el UNIQUE global de e_ncf).
ALTER TABLE os_ecf_comprobantes
    DROP CONSTRAINT IF EXISTS os_ecf_comprobantes_e_ncf_key;
ALTER TABLE os_ecf_comprobantes
    ADD CONSTRAINT uq_ecf_emisor_encf
    UNIQUE (emisor_rnc_norm, e_ncf);
CREATE INDEX IF NOT EXISTS idx_ecf_comp_emisor_fecha
    ON os_ecf_comprobantes (emisor_rnc_norm, tipo, fecha_emision);

-- 3. Secuencias: clave canónica (rnc_norm, tipo, origen).
ALTER TABLE os_ecf_secuencias
    ADD COLUMN IF NOT EXISTS rnc_norm TEXT;
ALTER TABLE os_ecf_secuencias
    ADD COLUMN IF NOT EXISTS origen TEXT NOT NULL DEFAULT 'simulado';
UPDATE os_ecf_secuencias
    SET rnc_norm = regexp_replace(rnc_emisor, '\D', '', 'g')
    WHERE rnc_norm IS NULL;
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'ck_ecf_sec_origen') THEN
        ALTER TABLE os_ecf_secuencias
            ADD CONSTRAINT ck_ecf_sec_origen
            CHECK (origen IN ('simulado', 'fiscal'));
    END IF;
END $$;
-- Consolidar: si varias empresas compartían RNC, conservar el máximo
-- (los números ya asignados no se reutilizan jamás).
DELETE FROM os_ecf_secuencias a USING os_ecf_secuencias b
    WHERE a.rnc_norm = b.rnc_norm
      AND a.tipo = b.tipo
      AND a.origen = b.origen
      AND a.ultimo < b.ultimo;
-- (Si quedaran filas empatadas en ultimo con distinto company_id, conservar
-- una sola.)
DELETE FROM os_ecf_secuencias a USING os_ecf_secuencias b
    WHERE a.rnc_norm = b.rnc_norm
      AND a.tipo = b.tipo
      AND a.origen = b.origen
      AND a.ultimo = b.ultimo
      AND a.company_id < b.company_id;
ALTER TABLE os_ecf_secuencias
    DROP CONSTRAINT IF EXISTS os_ecf_secuencias_pkey;
ALTER TABLE os_ecf_secuencias
    ADD CONSTRAINT pk_ecf_secuencias PRIMARY KEY (rnc_norm, tipo, origen);

-- 4. Reservas idempotentes: reintentos con la misma clave no consumen número.
CREATE TABLE IF NOT EXISTS os_ecf_reservas (
    clave           TEXT PRIMARY KEY,   -- clave de idempotencia del llamador
    rnc_norm        TEXT NOT NULL,
    tipo            TEXT NOT NULL,
    e_ncf           TEXT NOT NULL,
    creado_en       TIMESTAMPTZ NOT NULL DEFAULT now()
);
