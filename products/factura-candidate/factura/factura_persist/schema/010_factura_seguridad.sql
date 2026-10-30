-- 010_factura_seguridad.sql — cierre pre-staging P0 #1/#2/#3.
-- Dialecto: SQLite (el migrador traduce lo mínimo a PostgreSQL).
--
-- 1) sess_version en factura_usuarios: versión de sesión por usuario.
--    Se incrementa al cambiar la contraseña e invalida todas las
--    sesiones anteriores ("cerrar todas las sesiones").
-- 2) factura_intentos: registro durable (sobrevive reinicios) de intentos
--    para rate limiting / fuerza bruta (recuperación, login, superadmin).
-- 3) factura_sesiones_revocadas: revocación de sesiones del lado servidor
--    (logout). Se guarda el SHA-256 del valor de la cookie, nunca la cookie.
-- 4) Se invalidan los tokens de recuperación emitidos antes de este parche:
--    estaban almacenados EN CLARO; a partir de aquí se guardan como
--    HASH SHA-256 y nunca vuelven al HTML.

ALTER TABLE factura_usuarios
    ADD COLUMN sess_version INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS factura_intentos (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    tipo        TEXT NOT NULL,
    clave       TEXT NOT NULL,
    ip          TEXT NOT NULL DEFAULT '',
    creado_en   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intentos_tipo_clave
    ON factura_intentos(tipo, clave, creado_en);

CREATE TABLE IF NOT EXISTS factura_sesiones_revocadas (
    cookie_hash TEXT PRIMARY KEY,
    expira_en   TEXT NOT NULL
);

DELETE FROM factura_reset_tokens;
