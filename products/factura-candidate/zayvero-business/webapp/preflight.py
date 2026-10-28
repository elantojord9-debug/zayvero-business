"""PRE-STAGING P0 — Preflight de arranque del servicio.

En staging/producción el servicio DEBE negarse a arrancar si la configuración
de persistencia no es PostgreSQL completa. En desarrollo local no se exige
nada (JSON/memoria siguen permitidos para pruebas).

Detección de entorno (no omitible por accidente en Render):
  - ZAYVERO_ENV=staging|production  -> ese entorno (explícito, manda).
  - RENDER=true (la fija la propia plataforma Render) -> staging.
  - Ninguna de las dos -> local (sin controles).

El preflight corre ANTES de abrir el puerto: run_webapp.py lo ejecuta y sale
con código 1 si hay errores.
"""

from __future__ import annotations

import os

# Reutiliza las constantes del store para no duplicar nombres de variables.
TENANT_BACKEND_VAR = "ZAYVERO_TENANT_BACKEND"
OS_BACKEND_VAR = "ZAYVERO_OS_BACKEND"
DSN_VAR = "ZAYVERO_OS_DATABASE_URL"
REQUIRE_SECRET_VAR = "ZAYVERO_REQUIRE_SECRET_FILE"
USERS_FILE_VAR = "ZAYVERO_USERS_FILE"
RENDER_SECRET_PATH = "/etc/secrets/users.json"


def detectar_entorno() -> str:
    """'production' | 'staging' | 'local'."""
    zenv = os.environ.get("ZAYVERO_ENV", "").strip().lower()
    if zenv in ("production", "prod"):
        return "production"
    if zenv in ("staging", "stage"):
        return "staging"
    # Render fija RENDER=true en sus servicios; no depende de que el
    # operador recuerde poner ZAYVERO_ENV.
    if os.environ.get("RENDER", "").strip().lower() in ("1", "true", "yes", "on"):
        return "staging"
    return "local"


def _migraciones_esperadas() -> list[str]:
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    orden_path = os.path.join(base, "os_persist", "schema", "_orden.txt")
    out = []
    with open(orden_path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
    return out


def _pg_conecta(dsn: str):
    import psycopg
    return psycopg.connect(dsn, connect_timeout=5)


def auto_migrate_permitido() -> bool:
    """¿Puede el runtime ejecutar migraciones DDL?

    CIERRE DE SEGURIDAD (issue #8): el servidor web NUNCA ejecuta migraciones
    en staging/producción — ni siquiera con ZAYVERO_OS_AUTO_MIGRATE=1.
    Las migraciones las aplica un rol DDL acotado en predeploy y el preflight
    verifica que estén aplicadas antes de abrir el puerto.

    Solo desarrollo local permite auto-migrate (override =1 redundante ahí;
    =0 lo prohíbe explícitamente también en local).
    """
    if detectar_entorno() != "local":
        return False
    override = os.environ.get("ZAYVERO_OS_AUTO_MIGRATE", "").strip().lower()
    return override not in ("0", "false", "no", "off")


def verificar_migraciones(dsn: str) -> list[str]:
    """Comprueba que las 23 migraciones del manifiesto estén aplicadas.

    Devuelve [] si todo está aplicado, o la lista de faltantes.
    Lanza excepción si no se puede verificar (p. ej. tabla inexistente).
    """
    esperadas = _migraciones_esperadas()
    conn = _pg_conecta(dsn)
    try:
        cur = conn.cursor()
        cur.execute("SELECT archivo FROM os_migrations")
        aplicadas = {r[0] for r in cur.fetchall()}
    finally:
        conn.close()
    return [m for m in esperadas if m not in aplicadas]


def _ruta_secret_file() -> str | None:
    """Ruta efectiva del archivo de usuarios (misma prioridad que tenant/store.py)."""
    env_path = os.environ.get(USERS_FILE_VAR, "").strip()
    if env_path:
        return env_path
    if os.path.exists(RENDER_SECRET_PATH):
        return RENDER_SECRET_PATH
    return None


def preflight() -> list[str]:
    """Ejecuta los controles. Devuelve la lista de errores ([] = arrancar).

    En entorno local siempre devuelve [] (desarrollo sin restricciones).
    """
    if detectar_entorno() == "local":
        return []

    errores: list[str] = []

    # 1. Ambos backends deben ser postgres.
    if os.environ.get(TENANT_BACKEND_VAR, "json").strip().lower() != "postgres":
        errores.append(
            f"{TENANT_BACKEND_VAR} debe ser 'postgres' en staging/producción "
            f"(actual: {os.environ.get(TENANT_BACKEND_VAR, 'json')!r})")
    if os.environ.get(OS_BACKEND_VAR, "memoria").strip().lower() != "postgres":
        errores.append(
            f"{OS_BACKEND_VAR} debe ser 'postgres' en staging/producción "
            f"(actual: {os.environ.get(OS_BACKEND_VAR, 'memoria')!r})")

    # 2. DSN presente y PostgreSQL alcanzable.
    dsn = os.environ.get(DSN_VAR, "").strip()
    pg_ok = False
    if not dsn:
        errores.append(f"falta {DSN_VAR}")
    else:
        try:
            import psycopg
            conn = psycopg.connect(dsn, connect_timeout=5)
            try:
                conn.execute("SELECT 1")
            finally:
                conn.close()
            pg_ok = True
        except Exception as exc:  # noqa: BLE001 - el mensaje va al operador
            errores.append(f"PostgreSQL no disponible ({DSN_VAR}): {exc}")

    # 3. Secret File obligatorio.
    if os.environ.get(REQUIRE_SECRET_VAR, "").strip().lower() not in (
            "1", "true", "yes", "on"):
        errores.append(
            f"{REQUIRE_SECRET_VAR}=1 es obligatorio en staging/producción")
    else:
        ruta = _ruta_secret_file()
        if not ruta or not os.path.isfile(ruta):
            errores.append(
                f"Secret File de usuarios no encontrado "
                f"({USERS_FILE_VAR} o {RENDER_SECRET_PATH})")

    # 4. Migraciones: las 23 del manifiesto deben estar aplicadas.
    if pg_ok:
        try:
            faltantes = verificar_migraciones(dsn)
            if faltantes:
                errores.append(
                    "migraciones sin aplicar en PostgreSQL: "
                    + ", ".join(faltantes))
        except Exception as exc:  # noqa: BLE001
            errores.append(f"no se pudo verificar migraciones: {exc}")

    return errores
