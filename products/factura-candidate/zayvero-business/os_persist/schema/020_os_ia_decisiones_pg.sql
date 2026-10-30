-- 020 — Persistencia PG del Bloque IA: propuestas (Autopilot) y Centro de
-- Decisiones (issue #6, P1c, 2026-10-13).
--
-- Problema: os_ia/autopilot.py y os_ia/decisiones.py guardaban en memoria
-- por company_id y perdían todo al reiniciar. Esta migración crea las
-- tablas duraderas; la lógica vive en os_ia/almacen.py (patrón
-- os_empleados/almacen.py: tx re-entrante vía os_persist.tx::tx_ambiente).
--
-- REGLA DE NEGOCIO (inviolable): el Autopilot SOLO propone. Ninguna
-- operación financiera o externa se ejecuta desde este bloque; la
-- aprobación humana pasa por el módulo aprobaciones/ (os_aprobaciones)
-- y la ejecución la registra un humano FUERA del sistema.
-- Estados de propuesta: propuesta -> aprobada | rechazada; aprobada ->
-- ejecutada. La transición a 'ejecutada' exige aprobacion_id en estado
-- 'aprobada' (fail-closed) y solo ocurre una vez (UPDATE condicional).
--
-- Idempotente: CREATE TABLE IF NOT EXISTS en todo; UNIQUE
-- (company_id, idempotencia) + ON CONFLICT DO NOTHING en el código.
-- Reversión (manual): DROP TABLE os_ia_propuestas_historial,
-- os_ia_propuestas, os_centro_decisiones_historial, os_centro_decisiones
-- (en ese orden) y re-aplicar el CHECK de os_eventos sin los 2 tipos
-- nuevos (solo si no quedan eventos de esos tipos).

-- ---- propuestas del Autopilot ----------------------------------------
CREATE TABLE IF NOT EXISTS os_ia_propuestas (
    company_id      TEXT NOT NULL,
    propuesta_id    TEXT NOT NULL,
    idempotencia    TEXT NOT NULL,
    tipo            TEXT NOT NULL
        CHECK (tipo IN ('reordenar', 'ajustar_precio',
                        'revisar_proveedor', 'revisar_producto')),
    titulo_es       TEXT NOT NULL DEFAULT '',
    titulo_en       TEXT NOT NULL DEFAULT '',
    descripcion_es  TEXT NOT NULL DEFAULT '',
    descripcion_en  TEXT NOT NULL DEFAULT '',
    payload         JSONB NOT NULL DEFAULT '{}',
    fuente          JSONB NOT NULL DEFAULT '{}',
    evidencia       JSONB NOT NULL DEFAULT '{}',
    impacto_estimado JSONB,
    estado          TEXT NOT NULL DEFAULT 'propuesta'
        CHECK (estado IN ('propuesta', 'aprobada', 'rechazada',
                          'ejecutada')),
    aprobacion_id   TEXT,
    creada_por      TEXT NOT NULL DEFAULT 'autopilot',
    decidida_por    TEXT NOT NULL DEFAULT '',
    decidida_at     TIMESTAMPTZ,
    motivo_decision TEXT NOT NULL DEFAULT '',
    creada_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizada_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, propuesta_id),
    UNIQUE (company_id, idempotencia)
);
CREATE INDEX IF NOT EXISTS os_ia_propuestas_estado_idx
    ON os_ia_propuestas (company_id, estado);
CREATE INDEX IF NOT EXISTS os_ia_propuestas_tipo_idx
    ON os_ia_propuestas (company_id, tipo);

CREATE TABLE IF NOT EXISTS os_ia_propuestas_historial (
    hist_id         TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    propuesta_id    TEXT NOT NULL,
    actor           TEXT NOT NULL DEFAULT '',
    estado_anterior TEXT NOT NULL DEFAULT '',
    estado_nuevo    TEXT NOT NULL DEFAULT '',
    nota            TEXT NOT NULL DEFAULT '',
    fecha           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS os_ia_propuestas_historial_idx
    ON os_ia_propuestas_historial (company_id, propuesta_id, fecha);

-- ---- centro de decisiones --------------------------------------------
CREATE TABLE IF NOT EXISTS os_centro_decisiones (
    company_id          TEXT NOT NULL,
    decision_id         TEXT NOT NULL,
    fuente              TEXT NOT NULL DEFAULT 'autopilot'
        CHECK (fuente IN ('inspector', 'shield', 'advisor', 'autopilot')),
    problema            TEXT NOT NULL DEFAULT '',
    causa_probable      TEXT NOT NULL DEFAULT '',
    evidencia           JSONB NOT NULL DEFAULT '{}',
    impacto_estimado    JSONB,
    accion_recomendada  TEXT NOT NULL DEFAULT '',
    responsable_sugerido TEXT NOT NULL DEFAULT 'gerencia',
    estado              TEXT NOT NULL DEFAULT 'pendiente'
        CHECK (estado IN ('pendiente', 'aprobada', 'rechazada',
                          'ejecutada')),
    aprobacion_id       TEXT,
    creada_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    actualizada_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, decision_id)
);
CREATE INDEX IF NOT EXISTS os_centro_decisiones_estado_idx
    ON os_centro_decisiones (company_id, estado);
CREATE INDEX IF NOT EXISTS os_centro_decisiones_fuente_idx
    ON os_centro_decisiones (company_id, fuente);

CREATE TABLE IF NOT EXISTS os_centro_decisiones_historial (
    hist_id         TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL,
    decision_id     TEXT NOT NULL,
    actor           TEXT NOT NULL DEFAULT '',
    estado_anterior TEXT NOT NULL DEFAULT '',
    estado_nuevo    TEXT NOT NULL DEFAULT '',
    nota            TEXT NOT NULL DEFAULT '',
    fecha           TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS os_centro_decisiones_historial_idx
    ON os_centro_decisiones_historial (company_id, decision_id, fecha);

-- ---- ck_apr_tipo: incluir 'ia_propuesta' ----------------------------------
-- Ampliación del CHECK de tipos de os_aprobaciones (patrón de la 014):
-- las solicitudes del Autopilot usan el tipo 'ia_propuesta'. Unión
-- superconjunto (incluye 'eliminacion_cuenta' de la 014 aunque aún no
-- esté aplicada): esta migración es la última en el orden de
-- aplicación, así que su CHECK es el que queda vigente.
-- COORDINACIÓN: 014/015/017 deben quedar ANTES que 020 en _orden.txt
-- (orden numérico); si se aplicaran después, su DROP/ADD quitaría
-- 'ia_propuesta' del CHECK.
DO $$
DECLARE
    hay_tabla BOOLEAN;
    hay_ck BOOLEAN;
BEGIN
    -- Defensa en dos capas (lección de la 014): pg_constraint sin filtro
    -- de esquema veía constraints de otros esquemas; y en esquemas de
    -- prueba la tabla os_aprobaciones puede no existir. Sin tabla, no se
    -- toca nada.
    SELECT EXISTS (
        SELECT 1 FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE c.relname = 'os_aprobaciones'
          AND n.nspname = current_schema()
    ) INTO hay_tabla;
    IF hay_tabla THEN
        SELECT EXISTS (
            SELECT 1 FROM pg_constraint c
            JOIN pg_class t ON t.oid = c.conrelid
            JOIN pg_namespace n ON n.oid = t.relnamespace
            WHERE c.conname = 'ck_apr_tipo'
              AND t.relname = 'os_aprobaciones'
              AND n.nspname = current_schema()
        ) INTO hay_ck;
        IF hay_ck THEN
            ALTER TABLE os_aprobaciones DROP CONSTRAINT ck_apr_tipo;
        END IF;
        ALTER TABLE os_aprobaciones ADD CONSTRAINT ck_apr_tipo CHECK (tipo IN (
            'cambio_precio', 'devolucion_venta', 'devolucion_compra',
            'aprobacion_financiera', 'eliminacion_cuenta', 'ia_propuesta'));
    END IF;
END $$;

-- ---- tipos de evento de auditoría --------------------------------------
-- Extiende el CHECK con los tipos del bloque IA (patrón 011/012/013).
-- LISTA UNIÓN CANÓNICA: debe contener la unión completa de tipos de
-- todas las migraciones (001..017 + 020), porque esta es la última en
-- el orden de aplicación y el DROP/ADD reemplaza el CHECK anterior.
ALTER TABLE os_eventos DROP CONSTRAINT IF EXISTS os_eventos_tipo_check;
ALTER TABLE os_eventos ADD CONSTRAINT os_eventos_tipo_check
    CHECK (tipo IN (
        'venta', 'compra', 'recepcion', 'cobro',
        'devolucion', 'aprobacion', 'ajuste', 'gasto',
        'importacion', 'tarea', 'empleado',
        'saas_registro', 'saas_plan', 'saas_config', 'saas_usuario',
        'saas_onboarding',
        'oportunidad', 'descuento', 'recordatorio', 'promesa_pago',
        'presupuesto', 'conciliacion', 'ncf',
        'sector', 'notificacion', 'garantia', 'reclamo_garantia',
        'ia_propuesta', 'ia_decision'));
