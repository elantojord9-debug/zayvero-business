-- 005_factura_ecf_emisor.sql — FASE 3 (issue #11): unicidad e-NCF por
-- emisor fiscal normalizado, no global ni solo por empresa.
--
-- Contexto: UNIQUE(e_ncf) global es incorrecto; UNIQUE(empresa_id, e_ncf)
-- tampoco basta: dos cuentas/empresas podrían compartir el mismo RNC
-- emisor y generar el mismo e-NCF. La unicidad canónica es
-- UNIQUE(emisor_rnc_norm, e_ncf), con secuencias atómicas por
-- (RNC emisor, tipo, espacio).
--
-- 1. Columna con el RNC del emisor normalizado (solo dígitos, vía
--    os_ecf.modelos.validar_rnc en el servicio).
ALTER TABLE factura_ecf ADD COLUMN emisor_rnc_norm TEXT NOT NULL DEFAULT '';

-- 2. Backfill desde factura_empresas (quita guiones, espacios y puntos).
UPDATE factura_ecf SET emisor_rnc_norm = (
    SELECT REPLACE(REPLACE(REPLACE(e.rnc, '-', ''), ' ', ''), '.', '')
    FROM factura_empresas e WHERE e.id = factura_ecf.empresa_id
) WHERE emisor_rnc_norm = '';

-- 3. Unicidad canónica por emisor fiscal + e-NCF.
-- NOTA: se conserva el UNIQUE(empresa_id, e_ncf) original de 002 como
-- redundante pero inofensivo (empresa_id determina funcionalmente un
-- solo RNC, así que el nuevo índice lo subsume). No se reconstruye la
-- tabla para mantener la migración portable SQLite/PostgreSQL.
CREATE UNIQUE INDEX IF NOT EXISTS uq_ecf_emisor_encf
    ON factura_ecf(emisor_rnc_norm, e_ncf);
CREATE INDEX IF NOT EXISTS idx_ecf_emisor ON factura_ecf(emisor_rnc_norm);

-- 4. Secuencias canónicas por (emisor fiscal, tipo, espacio).
-- espacio: 'mock' (simulación) vs 'real' (futura vía DGII/proveedor).
-- Namespaces separados: el mock JAMÁS consume rango real.
CREATE TABLE IF NOT EXISTS factura_ecf_secuencias (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    emisor_rnc_norm  TEXT NOT NULL,
    tipo            TEXT NOT NULL,
    espacio         TEXT NOT NULL DEFAULT 'mock',
    ultimo_numero   INTEGER NOT NULL DEFAULT 0,
    actualizado_en  TEXT NOT NULL DEFAULT '',
    UNIQUE (emisor_rnc_norm, tipo, espacio)
);
CREATE INDEX IF NOT EXISTS idx_ecf_seq_emisor
    ON factura_ecf_secuencias(emisor_rnc_norm, tipo, espacio);

-- 5. Backfill: migrar los contadores ECF-* existentes (clave por empresa)
-- al esquema por emisor fiscal. Si varias empresas comparten un RNC, se
-- conserva el MAX para no reutilizar números ya emitidos.
-- ROLLBACK: basta con ignorar esta tabla y volver a usar
-- factura_secuencias (las filas originales no se tocan).
INSERT INTO factura_ecf_secuencias
    (emisor_rnc_norm, tipo, espacio, ultimo_numero, actualizado_en)
SELECT REPLACE(REPLACE(REPLACE(e.rnc, '-', ''), ' ', ''), '.', ''),
       SUBSTR(s.tipo_doc, 5), 'mock', MAX(s.ultimo_numero), ''
FROM factura_secuencias s
JOIN factura_empresas e ON e.id = s.empresa_id
WHERE s.tipo_doc LIKE 'ECF-%'
GROUP BY 1, 2;
