-- 005_os_ecf_seguridad_cierre.sql — cierre P0: aislamiento + numeración.
--
-- ORDEN EJECUTIVA DE SEGURIDAD (2026-10-13), puntos P0 #1–9:
--   #3 Emisores autorizados por empresa (os_ecf_emisores_autorizados).
--      En PRODUCCIÓN la autorización la otorga el onboarding con
--      verificación del RNC del contribuyente; NUNCA auto-registro
--      abierto. Ver docs/ecf-emisores-autorizados.md.
--   #5 Clave de idempotencia vinculada: os_ecf_reservas guarda el
--      contexto (rnc_norm, tipo, origen, operacion) que creó cada clave;
--      reutilizar una clave con contexto distinto es ErrorECF.
--   #7 Sin números huérfanos: guardar() reserva siempre con
--      clave_idempotencia = operacion = 'comprobante:<id>'.
--
-- AVISO: SIMULACIÓN — NO VÁLIDO ANTE DGII.

-- 1. Emisores autorizados por empresa -------------------------------------
CREATE TABLE IF NOT EXISTS os_ecf_emisores_autorizados (
    company_id TEXT NOT NULL,
    rnc_norm   TEXT NOT NULL,
    creado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, rnc_norm)
);
-- Backfill: todo (company_id, rnc_norm) con comprobantes históricos queda
-- autorizado (datos sintéticos; en producción el onboarding revalida).
INSERT INTO os_ecf_emisores_autorizados (company_id, rnc_norm)
SELECT DISTINCT company_id, emisor_rnc_norm
  FROM os_ecf_comprobantes
 WHERE emisor_rnc_norm IS NOT NULL
ON CONFLICT DO NOTHING;

-- 2. Clave de idempotencia vinculada a su contexto ------------------------
-- (rnc_norm y tipo ya existían desde 004; IF NOT EXISTS los respeta.)
ALTER TABLE os_ecf_reservas
    ADD COLUMN IF NOT EXISTS rnc_norm  TEXT,
    ADD COLUMN IF NOT EXISTS tipo      TEXT,
    ADD COLUMN IF NOT EXISTS origen    TEXT NOT NULL DEFAULT 'simulado',
    ADD COLUMN IF NOT EXISTS operacion TEXT;
-- NOTA: las filas anteriores a 005 quedan con operacion NULL. El código
-- las trata como operacion='manual' (legado documentado, no crítico:
-- las claves son UUIDs no adivinables y ningún flujo productivo las
-- reintenta con otra operación).
