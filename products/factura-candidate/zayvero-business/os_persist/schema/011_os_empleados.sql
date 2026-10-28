-- 2026-10-13 — Migración 011: bloque Empleados/Operaciones (ZAYVERO Business OS).
--
-- Contexto: módulos NUEVOS del bloque empleados/operaciones —
-- Employee Analytics, Task Monitor, SLA Monitor y Control de
-- sucursales (ver os_empleados/ y docs/bloque-empleados.md).
-- Todo con company_id (aislamiento multiempresa). Datos 100 %
-- sintéticos en local; sin efecto en staging/producción.
--
-- Tablas:
--   os_empleados            registro de personal (vínculo opcional a tenant vía usuario_id)
--   os_tareas               tareas con responsable/vencimiento/estado/prioridad
--   os_tareas_historial     historial append-only de cambios de estado
--   os_sla_config           objetivos de SLA por tipo de operación
--   os_sla_mediciones       mediciones de tiempos de respuesta/resolución
--   os_sucursales           sucursales/ubicaciones + almacenes asociados
--   os_empleado_sucursal    visibilidad: qué sucursales ve cada usuario
--   os_promesas_seguimiento promesas de pago con responsable (empleado_id)
--
-- Auditoría: las mutaciones del bloque se auditan en os_eventos con los
-- tipos NUEVOS 'tarea' y 'empleado' (misma transacción, ver
-- os_empleados/almacen.py). El CHECK de la 004 (+009, +010) no los
-- incluía: se extiende aquí con el patrón DROP/ADD idempotente.
--
-- Idempotente: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS
-- + DROP CONSTRAINT IF EXISTS. Reversión (manual): DROP TABLE de las 8
-- tablas en orden inverso de dependencias y re-aplicar el CHECK sin
-- 'tarea'/'empleado' (solo si no quedan eventos de esos tipos).

-- ---- Extensión del CHECK de tipos de evento -------------------------
-- LISTA UNIÓN CANÓNICA (2026-10-13, bloque SaaS): este CHECK es el ÚLTIMO
-- de la cadena en el orden canónico de _orden.txt, por lo que debe
-- conservar TODOS los tipos introducidos por migraciones anteriores
-- (incluidos los 'saas_*' de 009_os_saas.sql). REGLA: cualquier migración
-- futura que toque este CHECK debe preservar la unión completa; de lo
-- contrario rompe los INSERT de auditoría de otros bloques (fallan con
-- violación de CHECK en runtime).
ALTER TABLE os_eventos DROP CONSTRAINT IF EXISTS os_eventos_tipo_check;
ALTER TABLE os_eventos ADD CONSTRAINT os_eventos_tipo_check
    CHECK (tipo IN (
        'venta', 'compra', 'recepcion', 'cobro',
        'devolucion', 'aprobacion', 'ajuste', 'gasto',
        'importacion', 'tarea', 'empleado',
        'saas_registro', 'saas_plan', 'saas_config', 'saas_usuario',
        'saas_onboarding'));

-- ---- Registro de personal -------------------------------------------
CREATE TABLE IF NOT EXISTS os_empleados (
    empleado_id TEXT NOT NULL,
    company_id  TEXT NOT NULL REFERENCES os_companies(company_id),
    nombre      TEXT NOT NULL CHECK (nombre <> ''),
    cargo       TEXT NOT NULL DEFAULT '',
    sucursal_id TEXT NOT NULL DEFAULT '',
    usuario_id  TEXT NOT NULL DEFAULT '',  -- vínculo opcional con el login (ctx.user_id)
    email       TEXT NOT NULL DEFAULT '',
    activo      BOOLEAN NOT NULL DEFAULT TRUE,
    creado_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, empleado_id)
);
CREATE INDEX IF NOT EXISTS ix_os_empleados_empresa_usuario
    ON os_empleados (company_id, usuario_id);
CREATE INDEX IF NOT EXISTS ix_os_empleados_empresa_sucursal
    ON os_empleados (company_id, sucursal_id);

-- ---- Tareas ----------------------------------------------------------
CREATE TABLE IF NOT EXISTS os_tareas (
    tarea_id      TEXT NOT NULL,
    company_id    TEXT NOT NULL REFERENCES os_companies(company_id),
    titulo        TEXT NOT NULL CHECK (titulo <> ''),
    descripcion   TEXT NOT NULL DEFAULT '',
    responsable   TEXT NOT NULL,            -- empleado_id (FK lógica; el empleado puede desactivarse)
    sucursal_id   TEXT NOT NULL DEFAULT '',
    vencimiento   DATE NOT NULL,
    estado        TEXT NOT NULL DEFAULT 'pendiente'
                  CHECK (estado IN ('pendiente', 'en_curso', 'bloqueada', 'completada')),
    prioridad     TEXT NOT NULL DEFAULT 'media'
                  CHECK (prioridad IN ('baja', 'media', 'alta', 'critica')),
    creada_por    TEXT NOT NULL DEFAULT '',
    creada_en     TIMESTAMPTZ NOT NULL DEFAULT now(),
    completada_en TIMESTAMPTZ,
    PRIMARY KEY (company_id, tarea_id)
);
CREATE INDEX IF NOT EXISTS ix_os_tareas_empresa_resp
    ON os_tareas (company_id, responsable);
CREATE INDEX IF NOT EXISTS ix_os_tareas_empresa_estado
    ON os_tareas (company_id, estado);
CREATE INDEX IF NOT EXISTS ix_os_tareas_empresa_venc
    ON os_tareas (company_id, vencimiento);

-- ---- Historial de tareas (append-only) -------------------------------
CREATE TABLE IF NOT EXISTS os_tareas_historial (
    hist_id         TEXT NOT NULL PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES os_companies(company_id),
    tarea_id        TEXT NOT NULL,
    fecha           TIMESTAMPTZ NOT NULL DEFAULT now(),
    actor           TEXT NOT NULL DEFAULT '',
    estado_anterior TEXT NOT NULL DEFAULT '',
    estado_nuevo    TEXT NOT NULL DEFAULT '',
    nota            TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS ix_os_tareas_hist_empresa_tarea
    ON os_tareas_historial (company_id, tarea_id, fecha);

-- ---- Configuración de SLA --------------------------------------------
CREATE TABLE IF NOT EXISTS os_sla_config (
    company_id      TEXT NOT NULL REFERENCES os_companies(company_id),
    tipo_operacion  TEXT NOT NULL CHECK (tipo_operacion <> ''),
    objetivo_horas  NUMERIC(10, 2) NOT NULL CHECK (objetivo_horas > 0),
    activo          BOOLEAN NOT NULL DEFAULT TRUE,
    actualizado_por TEXT NOT NULL DEFAULT '',
    actualizado_en  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, tipo_operacion)
);

-- ---- Mediciones de SLA -----------------------------------------------
CREATE TABLE IF NOT EXISTS os_sla_mediciones (
    medicion_id     TEXT NOT NULL,
    company_id      TEXT NOT NULL REFERENCES os_companies(company_id),
    tipo_operacion  TEXT NOT NULL,
    ref_id          TEXT NOT NULL DEFAULT '',   -- id de la operación medida (pedido, reclamo...)
    descripcion     TEXT NOT NULL DEFAULT '',
    responsable     TEXT NOT NULL DEFAULT '',   -- empleado_id (opcional)
    inicio          TIMESTAMPTZ NOT NULL DEFAULT now(),
    fin             TIMESTAMPTZ,
    duracion_horas  NUMERIC(10, 2),              -- se calcula al cerrar
    cumplio         BOOLEAN,                     -- se evalúa al cerrar
    creada_por      TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (company_id, medicion_id)
);
CREATE INDEX IF NOT EXISTS ix_os_sla_med_empresa_tipo
    ON os_sla_mediciones (company_id, tipo_operacion);
CREATE INDEX IF NOT EXISTS ix_os_sla_med_abiertas
    ON os_sla_mediciones (company_id) WHERE fin IS NULL;

-- ---- Sucursales -------------------------------------------------------
CREATE TABLE IF NOT EXISTS os_sucursales (
    sucursal_id  TEXT NOT NULL,
    company_id   TEXT NOT NULL REFERENCES os_companies(company_id),
    nombre       TEXT NOT NULL CHECK (nombre <> ''),
    ubicacion    TEXT NOT NULL DEFAULT '',
    almacenes    JSONB NOT NULL DEFAULT '[]'::jsonb,  -- almacenes de inventario asociados
    activa       BOOLEAN NOT NULL DEFAULT TRUE,
    creada_en    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, sucursal_id)
);

-- ---- Visibilidad por sucursal (roles por ubicación) -------------------
CREATE TABLE IF NOT EXISTS os_empleado_sucursal (
    company_id  TEXT NOT NULL REFERENCES os_companies(company_id),
    usuario_id  TEXT NOT NULL,   -- ctx.user_id del usuario
    sucursal_id TEXT NOT NULL,
    asignada_en TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (company_id, usuario_id, sucursal_id)
);
CREATE INDEX IF NOT EXISTS ix_os_emp_suc_empresa_usuario
    ON os_empleado_sucursal (company_id, usuario_id);

-- ---- Promesas de pago con responsable ----------------------------------
CREATE TABLE IF NOT EXISTS os_promesas_seguimiento (
    promesa_id      TEXT NOT NULL,
    company_id      TEXT NOT NULL REFERENCES os_companies(company_id),
    cuenta_id       TEXT NOT NULL,      -- CxC de tesorería
    empleado_id     TEXT NOT NULL,      -- empleado que la registró
    fecha_prometida DATE NOT NULL,
    monto           NUMERIC(14, 2) NOT NULL CHECK (monto > 0),
    moneda          TEXT NOT NULL DEFAULT 'DOP',
    registrada_en   TIMESTAMPTZ NOT NULL DEFAULT now(),
    cumplida        BOOLEAN,            -- NULL = pendiente de evaluar
    cumplida_en     TIMESTAMPTZ,
    PRIMARY KEY (company_id, promesa_id)
);
CREATE INDEX IF NOT EXISTS ix_os_promesas_empresa_empleado
    ON os_promesas_seguimiento (company_id, empleado_id);
CREATE INDEX IF NOT EXISTS ix_os_promesas_empresa_cuenta
    ON os_promesas_seguimiento (company_id, cuenta_id);
