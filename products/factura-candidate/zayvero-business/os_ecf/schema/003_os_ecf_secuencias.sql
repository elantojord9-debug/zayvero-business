-- 003_os_ecf_secuencias.sql — secuencias de e-NCF por empresa/emisor/tipo.
-- El consumo usa SELECT ... FOR UPDATE (ver AlmacenECF.reservar_e_ncf):
-- los emisores concurrentes se serializan y cada número se asigna una
-- sola vez, sin duplicados.
-- AVISO: SIMULACIÓN — NO VÁLIDO ANTE DGII.

CREATE TABLE IF NOT EXISTS os_ecf_secuencias (
    company_id  TEXT NOT NULL,
    rnc_emisor  TEXT NOT NULL,
    tipo        TEXT NOT NULL,
    ultimo      BIGINT NOT NULL DEFAULT 0,   -- último secuencial asignado
    PRIMARY KEY (company_id, rnc_emisor, tipo)
);
