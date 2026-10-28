-- BLOQUE B (2026-10-13) — Migración 015: NOTIFICACIONES Y REPORTES
-- PROGRAMADOS (os_notif).
--
-- Idempotente: CREATE TABLE/INDEX IF NOT EXISTS + DROP/ADD CONSTRAINT
-- para el CHECK de tipos de os_eventos. Reversible: DROP TABLE
-- (manual; no se ejecuta automáticamente).
--
-- Decisiones:
-- - company_id en TODAS las tablas (aislamiento multiempresa estricto).
-- - os_notificaciones: centro de notificaciones in-app por empresa.
--   `huella` = sha256(regla|fecha|dato canónico): UNIQUE por empresa
--   para no re-notificar el mismo hallazgo en cada pasada (anti-spam).
--   El `payload` se guarda ya SANITIZADO (os_notif/sanitizar.py): nunca
--   guarda emails, teléfonos, cuentas, tokens ni contraseñas.
-- - os_notif_preferencias: suscripción por (usuario, tipo de alerta,
--   canal). Sin fila = defaults: suscrito a inapp, no suscrito a
--   canales externos.
-- - os_notif_canales: canales externos (email/WhatsApp/Telegram)
--   registrados con CONSENTIMIENTO explícito. Por defecto
--   DESHABILITADOS: consentimiento=FALSE, habilitado=FALSE. El
--   destino NO se guarda en claro: se guarda máscara legible
--   (destino_mask) + sha256 (destino_hash) para verificación futura.
-- - os_notif_programaciones: reportes programados (diaria/semanal/
--   mensual, hora, formato pdf/xlsx, módulo de reportes/, roles
--   destinatarios). NO envían nada por sí solos: generan el archivo
--   en la bandeja de salida como SIMULADO.
-- - os_notif_bandeja: bandeja de salida. REGLA DURA: estado 'simulado'
--   por defecto; el envío externo real requiere aprobación humana en
--   el módulo aprobaciones/ (tipo aprobacion_financiera, enlazada por
--   aprobacion_id) y aun aprobada el envío real queda RETENIDO
--   (implementación futura; os_notif NO envía mensajes reales).
-- - Se extiende el CHECK de tipos de os_eventos con 'notificacion'
--   (patrón 011/012/013: preservar la unión completa de tipos).
--   Fuente Python: os_persist/auditoria.py::TIPOS_EVENTO.

-- ------------------------------------------------------------------
-- Centro de notificaciones in-app por empresa.
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS os_notificaciones (
    company_id  TEXT NOT NULL REFERENCES os_companies(company_id),
    notif_id    TEXT NOT NULL,
    tipo        TEXT NOT NULL,          -- p. ej. shield.stock_critico |
                                       -- finanzas.presupuesto_sobreejecutado |
                                       -- decisiones.nueva
    prioridad   TEXT NOT NULL DEFAULT 'info',  -- info | alerta | critico
    titulo      TEXT NOT NULL DEFAULT '',
    resumen     TEXT NOT NULL DEFAULT '',
    fuente      TEXT NOT NULL DEFAULT '',  -- shield | finanzas | inventario |
                                           -- decisiones
    regla       TEXT NOT NULL DEFAULT '',
    huella      TEXT NOT NULL,          -- sha256 para dedup (anti-spam)
    payload     JSONB NOT NULL DEFAULT '{}',  -- evidencia SANITIZADA
    leida       BOOLEAN NOT NULL DEFAULT FALSE,
    creada_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    leida_at    TIMESTAMPTZ,
    PRIMARY KEY (company_id, notif_id),
    CONSTRAINT ck_notif_prioridad CHECK (prioridad IN (
        'info', 'alerta', 'critico'))
);
CREATE UNIQUE INDEX IF NOT EXISTS ix_notif_huella
    ON os_notificaciones (company_id, huella);
CREATE INDEX IF NOT EXISTS ix_notif_leida
    ON os_notificaciones (company_id, leida, creada_at DESC);
CREATE INDEX IF NOT EXISTS ix_notif_prioridad
    ON os_notificaciones (company_id, prioridad, creada_at DESC);

-- ------------------------------------------------------------------
-- Preferencias: suscripción a tipos de alerta por canal.
-- Sin fila = defaults: suscrito=TRUE solo en 'inapp'.
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS os_notif_preferencias (
    company_id   TEXT NOT NULL REFERENCES os_companies(company_id),
    user_id      TEXT NOT NULL DEFAULT '',
    tipo_alerta  TEXT NOT NULL,          -- shield.stock_critico | * (todas)
    canal        TEXT NOT NULL DEFAULT 'inapp',  -- inapp | email |
                                                -- whatsapp | telegram
    suscrito     BOOLEAN NOT NULL DEFAULT TRUE,
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, user_id, tipo_alerta, canal),
    CONSTRAINT ck_notifpref_canal CHECK (canal IN (
        'inapp', 'email', 'whatsapp', 'telegram'))
);

-- ------------------------------------------------------------------
-- Canales externos con consentimiento explícito.
-- Por defecto DESHABILITADOS (consentimiento=FALSE, habilitado=FALSE).
-- El destino se guarda enmascarado + hash, nunca en claro.
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS os_notif_canales (
    company_id      TEXT NOT NULL REFERENCES os_companies(company_id),
    canal_id        TEXT NOT NULL,
    user_id         TEXT NOT NULL DEFAULT '',
    canal           TEXT NOT NULL,      -- email | whatsapp | telegram
    destino_mask    TEXT NOT NULL DEFAULT '',  -- p. ej. u***@dominio.com
    destino_hash    TEXT NOT NULL DEFAULT '',  -- sha256 del destino real
    consentimiento  BOOLEAN NOT NULL DEFAULT FALSE,
    consentimiento_at TIMESTAMPTZ,
    consentimiento_por TEXT NOT NULL DEFAULT '',
    habilitado      BOOLEAN NOT NULL DEFAULT FALSE,
    creada_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, canal_id),
    CONSTRAINT ck_notifcan_canal CHECK (canal IN (
        'email', 'whatsapp', 'telegram'))
);
CREATE INDEX IF NOT EXISTS ix_notifcan_user
    ON os_notif_canales (company_id, user_id, canal);

-- ------------------------------------------------------------------
-- Reportes programados.
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS os_notif_programaciones (
    company_id       TEXT NOT NULL REFERENCES os_companies(company_id),
    prog_id          TEXT NOT NULL,
    modulo           TEXT NOT NULL,      -- inventario | finanzas | compras |
                                        -- cobranzas | shield
    formato          TEXT NOT NULL DEFAULT 'pdf',  -- pdf | xlsx
    frecuencia       TEXT NOT NULL DEFAULT 'diaria',  -- diaria | semanal |
                                                      -- mensual
    hora             TEXT NOT NULL DEFAULT '07:00',   -- HH:MM (hora RD)
    roles_destino    TEXT[] NOT NULL DEFAULT '{}',
    activa           BOOLEAN NOT NULL DEFAULT TRUE,
    ultima_ejecucion TIMESTAMPTZ,
    proxima_ejecucion TIMESTAMPTZ,
    creada_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, prog_id),
    CONSTRAINT ck_notifprog_modulo CHECK (modulo IN (
        'inventario', 'finanzas', 'compras', 'cobranzas', 'shield')),
    CONSTRAINT ck_notifprog_formato CHECK (formato IN ('pdf', 'xlsx')),
    CONSTRAINT ck_notifprog_frecuencia CHECK (frecuencia IN (
        'diaria', 'semanal', 'mensual'))
);

-- ------------------------------------------------------------------
-- Bandeja de salida. REGLA DURA: todo nace 'simulado'. El envío
-- externo real requiere aprobación en aprobaciones/ (aprobacion_id)
-- y la implementación futura del despacho (hoy NO envía nada real).
-- ------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS os_notif_bandeja (
    company_id    TEXT NOT NULL REFERENCES os_companies(company_id),
    salida_id     TEXT NOT NULL,
    tipo          TEXT NOT NULL DEFAULT 'reporte_programado',
                  -- reporte_programado | alerta_externa
    ref_id        TEXT NOT NULL DEFAULT '',  -- prog_id | notif_id origen
    formato       TEXT NOT NULL DEFAULT 'pdf',  -- pdf | xlsx
    nombre_archivo TEXT NOT NULL DEFAULT '',
    contenido     BYTEA,              -- bytes del reporte generado
    contenido_sha256 TEXT NOT NULL DEFAULT '',
    destinatario_rol TEXT NOT NULL DEFAULT '',
    canal         TEXT NOT NULL DEFAULT 'inapp',
    estado        TEXT NOT NULL DEFAULT 'simulado',
                  -- simulado | pendiente_aprobacion | aprobado |
                  -- rechazado | enviado_real | cancelado
    simulado      BOOLEAN NOT NULL DEFAULT TRUE,
    aprobacion_id TEXT,               -- solicitud en aprobaciones/
    creada_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    aprobada_at   TIMESTAMPTZ,
    enviada_at    TIMESTAMPTZ,
    nota          TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (company_id, salida_id),
    CONSTRAINT ck_notifban_estado CHECK (estado IN (
        'simulado', 'pendiente_aprobacion', 'aprobado',
        'rechazado', 'enviado_real', 'cancelado'))
);
CREATE INDEX IF NOT EXISTS ix_notifban_estado
    ON os_notif_bandeja (company_id, estado, creada_at DESC);

-- ------------------------------------------------------------------
-- Extensión del CHECK de tipos de os_eventos con 'notificacion'
-- (patrón 011/012/013: unión completa preservada).
-- ------------------------------------------------------------------
-- Extensión ADITIVA del CHECK de tipos de os_eventos (2026-10-13):
-- el patrón anterior (DROP + ADD con lista fija) rompía al re-aplicarse
-- con datos de tipos más nuevos (CheckViolation "violated by some row")
-- y al aplicarse fuera de orden (perdía tipos). Ahora: lee los tipos del
-- CHECK vigente, agrega los nuevos solo si faltan y reconstruye
-- preservando la unión existente. Idempotente.
DO $$
DECLARE
    def_actual TEXT;
    tipos TEXT[];
    lista TEXT;
BEGIN
    SELECT pg_get_constraintdef(cc.oid) INTO def_actual
      FROM pg_constraint cc
     WHERE cc.conname = 'os_eventos_tipo_check'
       AND cc.connamespace = current_schema()::regnamespace;
    IF def_actual IS NULL THEN
        RETURN;
    END IF;
    SELECT array_agg(DISTINCT m[1]) INTO tipos
      FROM regexp_matches(def_actual, '''([^'']+)''', 'g') AS m;
    IF NOT ('notificacion' = ANY (COALESCE(tipos, '{}'))) THEN
        SELECT string_agg(quote_literal(t), ', ' ORDER BY t)
          INTO lista
          FROM unnest(COALESCE(tipos, '{}') || ARRAY['notificacion']) AS t;
        ALTER TABLE os_eventos DROP CONSTRAINT os_eventos_tipo_check;
        EXECUTE 'ALTER TABLE os_eventos ADD CONSTRAINT os_eventos_tipo_check '
             || 'CHECK (tipo IN (' || lista || '))';
    END IF;
END $$;

