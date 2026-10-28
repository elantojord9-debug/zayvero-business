#!/usr/bin/env python3
"""P0 #4 (cierre pre-staging): preflight cloud, migraciones separadas del
servidor y rol PostgreSQL sin permisos DDL.

Uso:
    python -m pytest tests/test_db_p0.py -v   # con el venv del proyecto activado

Los tests que necesitan PostgreSQL se omiten (skip) si no hay servidor
local disponible. Los que crean base/rol de prueba usan nombres propios
(`factura_p0_test`, `factura_p0_ro`) y los borran al terminar; nunca tocan
`factura_app`.
"""
from __future__ import annotations

import os
import re
import secrets
import subprocess
import sys

import pytest

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import config  # noqa: E402
from factura_persist import db  # noqa: E402

# Intérprete actual: los subprocesos usan el mismo venv que corre las pruebas.
PY = sys.executable
DSN_FILE = os.path.expanduser("~/.pg_dsn_factura_app")
# Directorio del socket local de PostgreSQL (solo tests con PG local).
_PG_SOCKET_DIR = os.environ.get("FACTURA_PG_SOCKET_DIR", "/var/run/postgresql")
_ENV_KEYS = ("FACTURA_ENV", "FACTURA_DATABASE_URL", "ZAYVERO_OS_DATABASE_URL",
             "FACTURA_SQLITE_PATH", "FACTURA_ALLOW_INSECURE_DSN")


def _dsn_admin() -> str | None:
    if os.environ.get("FACTURA_TEST_ALLOW_DDL") != "1":
        return None
    return os.environ.get("FACTURA_TEST_DATABASE_URL") or None



def _hay_pg() -> bool:
    d = _dsn_admin()
    if not d:
        return False
    try:
        import psycopg
        c = psycopg.connect(d, connect_timeout=3)
        c.close()
        return True
    except Exception:
        return False


PG_DISPONIBLE = _hay_pg()
requiere_pg = pytest.mark.skipif(not PG_DISPONIBLE,
                                 reason="sin PostgreSQL local disponible")


@pytest.fixture
def env_limpio(tmp_path):
    """Aísla variables de entorno, ruta SQLite y caché de backend."""
    guardadas = {k: os.environ.get(k) for k in _ENV_KEYS}
    sqlite_orig = config.SQLITE_PATH
    config.SQLITE_PATH = str(tmp_path / "p0.db")
    db.reset_backend_cache()
    yield
    for k, v in guardadas.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    config.SQLITE_PATH = sqlite_orig
    db.reset_backend_cache()


# --- preflight ---------------------------------------------------------------

def test_preflight_rechaza_sqlite_en_cloud(env_limpio):
    os.environ.pop("FACTURA_DATABASE_URL", None)
    os.environ.pop("ZAYVERO_OS_DATABASE_URL", None)
    os.environ["FACTURA_ENV"] = "cloud"
    db.reset_backend_cache()
    assert db.backend() == "sqlite"
    with pytest.raises(db.PreflightError) as ei:
        db.preflight_cloud()
    assert "SQLite" in str(ei.value)


def test_preflight_rechaza_dsn_sin_tls_en_cloud(env_limpio):
    os.environ["FACTURA_ENV"] = "staging"
    os.environ["FACTURA_DATABASE_URL"] = \
        "postgresql://u:p@127.0.0.1:5432/factura_p0_test"
    db.reset_backend_cache()
    assert db.backend() == "pg"
    with pytest.raises(db.PreflightError) as ei:
        db.preflight_cloud()
    assert "sslmode" in str(ei.value)


def test_sslmode_se_extrae_del_dsn(env_limpio):
    assert db._sslmode_dsn(
        "postgresql://u:p@h:5432/b?sslmode=require") == "require"
    assert db._sslmode_dsn(
        "postgresql://u:p@h:5432/b?sslmode=verify-full") == "verify-full"
    assert db._sslmode_dsn("postgresql://u:p@h:5432/b") is None
    assert db._sslmode_dsn(
        "host=h user=u dbname=b sslmode=require") == "require"


# --- P0 (2026-10-14): FACTURA_ALLOW_INSECURE_DSN solo vale en local --------

def test_override_tls_ignorado_en_cloud(env_limpio):
    """(a) cloud + DSN sin TLS + override=1: preflight LANZA
    PreflightError y el mensaje explica que la excepción solo funciona
    en desarrollo local."""
    os.environ["FACTURA_ENV"] = "staging"
    os.environ["FACTURA_DATABASE_URL"] = \
        "postgresql://u:p@127.0.0.1:5432/factura_p0_test"
    os.environ["FACTURA_ALLOW_INSECURE_DSN"] = "1"
    db.reset_backend_cache()
    assert db.backend() == "pg"
    with pytest.raises(db.PreflightError) as ei:
        db.preflight_cloud()
    mensaje = str(ei.value)
    assert "sslmode" in mensaje
    assert "solo funciona en desarrollo local" in mensaje


@requiere_pg
def test_override_tls_no_bloquea_dsn_con_tls_en_cloud(env_limpio,
                                                      pg_segura):
    """(b) cloud + DSN con sslmode=require + override=1: el preflight pasa
    completo (el override no estorba a un DSN que sí exige TLS)."""
    os.environ["FACTURA_ENV"] = "cloud"
    os.environ["FACTURA_DATABASE_URL"] = \
        _tls_dsn(pg_segura["admin"])
    os.environ["FACTURA_ALLOW_INSECURE_DSN"] = "1"
    db.reset_backend_cache()
    db.preflight_cloud()  # no debe lanzar


@requiere_pg
def test_local_con_override_y_dsn_sin_tls_pasa(env_limpio, pg_segura):
    """(c) local + DSN sin TLS + override=1: la vía local no exige TLS y
    el chequeo de serve pasa con la base migrada."""
    os.environ.pop("FACTURA_ENV", None)
    os.environ["FACTURA_DATABASE_URL"] = pg_segura["admin"]  # sin sslmode
    os.environ["FACTURA_ALLOW_INSECURE_DSN"] = "1"
    db.reset_backend_cache()
    assert not db.es_modo_cloud()
    db.chequeo_serve()  # no debe lanzar


@requiere_pg
def test_local_sin_override_tampoco_exige_tls(env_limpio, pg_segura):
    """(d) local + DSN sin TLS sin override: comportamiento local actual —
    en local no hay exigencia de TLS, el chequeo pasa igual."""
    os.environ.pop("FACTURA_ENV", None)
    os.environ.pop("FACTURA_ALLOW_INSECURE_DSN", None)
    os.environ["FACTURA_DATABASE_URL"] = pg_segura["admin"]  # sin sslmode
    db.reset_backend_cache()
    assert not db.es_modo_cloud()
    db.chequeo_serve()  # no debe lanzar


def test_serve_falla_sin_migraciones_con_mensaje_util(env_limpio, tmp_path):
    """`app.py --preflight` sobre una base sin migrar: rc!=0 y dice --migrate.

    Ejerce el mismo `chequeo_serve()` que usa el modo serve (que ya no
    migra solo al arrancar).
    """
    fresco = str(tmp_path / "fresco.db")
    env = {k: v for k, v in os.environ.items()
           if k not in ("FACTURA_DATABASE_URL", "ZAYVERO_OS_DATABASE_URL",
                        "FACTURA_ENV")}
    env["FACTURA_SQLITE_PATH"] = fresco
    p = subprocess.run([PY, os.path.join(BASE, "app.py"), "--preflight"],
                       capture_output=True, text=True, timeout=180,
                       env=env, cwd=BASE)
    salida = p.stdout + p.stderr
    assert p.returncode == 2, salida
    assert "--migrate" in salida
    assert "001_factura_base.sql" in salida


# --- rol sin DDL (PostgreSQL) -------------------------------------------------

@pytest.fixture(scope="module")
def pg_segura():
    """Isolated DB + DML-only role using the explicit QA admin DSN.

    No runuser, local Unix socket, production filename or host-specific role.
    """
    import psycopg
    from psycopg import sql
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    dsn_admin = _dsn_admin()
    assert dsn_admin, "se necesita DSN explícito de QA"
    suffix = str(os.getpid()) + "_" + secrets.token_hex(3)
    dbname = "factura_p0_test_" + suffix
    rol = "factura_p0_ro_" + suffix
    ro_pass = secrets.token_urlsafe(24)
    parsed = urlsplit(dsn_admin)
    dsn_db = urlunsplit(parsed._replace(path="/" + dbname))

    def statement(query, target=dsn_admin):
        with psycopg.connect(target, autocommit=True) as connection:
            connection.execute(query)

    statement(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(dbname)))
    statement(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(rol), sql.Literal(ro_pass)))
    try:
        os.environ["FACTURA_DATABASE_URL"] = dsn_db
        db.reset_backend_cache()
        hechas = db.migrate()
        assert len(hechas) == len(db._orden()), hechas
        assert db.migraciones_faltantes() == []
        statement(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname), sql.Identifier(rol)))
        for query in ("GRANT USAGE ON SCHEMA public TO {}",
                      "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}",
                      "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}"):
            statement(sql.SQL(query).format(sql.Identifier(rol)), dsn_db)
        host = parsed.hostname or "127.0.0.1"
        port = parsed.port or 5432
        from urllib.parse import quote
        dsn_ro = urlunsplit((parsed.scheme, quote(rol) + ":" + quote(ro_pass) + "@" + host + ":" + str(port), "/" + dbname, parsed.query, ""))
        yield {"admin": dsn_db, "ro": dsn_ro, "dbname": dbname, "rol": rol}
    finally:
        db.reset_backend_cache()
        os.environ.pop("FACTURA_DATABASE_URL", None)
        statement(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))
        statement(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(rol)))
        db.reset_backend_cache()


def _tls_dsn(dsn):
    from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
    parsed = urlsplit(dsn)
    query = dict(parse_qsl(parsed.query))
    query["sslmode"] = "require"
    return urlunsplit(parsed._replace(query=urlencode(query)))


@requiere_pg
def test_preflight_cloud_ok_con_tls_y_migraciones(env_limpio, pg_segura):
    os.environ["FACTURA_ENV"] = "cloud"
    os.environ["FACTURA_DATABASE_URL"] = \
        _tls_dsn(pg_segura["admin"])
    db.reset_backend_cache()
    db.preflight_cloud()  # no debe lanzar


@requiere_pg
def test_rol_sin_ddl_opera_recorrido_sintetico(env_limpio, pg_segura):
    """Login, crear factura, cobrar y leer reportes con el rol sin DDL."""
    os.environ["FACTURA_DATABASE_URL"] = pg_segura["ro"]
    db.reset_backend_cache()
    assert db.backend() == "pg"
    assert db.migraciones_faltantes() == []

    from factura_auth import auth
    from factura_core import clientes, ventas, pagos, reportes

    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            eid = db.insert_id(
                cur,
                "INSERT INTO factura_empresas (nombre, rnc, telefono, "
                "direccion, plan, itbis_tasa, e_cf_mode, creada) "
                "VALUES (%s,%s,'','','basico','18','mock',%s)",
                ("Empresa P0", "999000333", "2026-10-14T00:00:00+00:00"))
    finally:
        conn.close()

    u = auth.crear_usuario(eid, "p0test@prueba.do", "ClaveP0-Prueba1234",
                           rol="admin")
    ses = auth.autenticar("p0test@prueba.do", "ClaveP0-Prueba1234")
    assert ses is not None and ses["email"] == "p0test@prueba.do"

    c = clientes.crear(eid, "Cliente P0", "999000444")
    v = ventas.crear_venta(
        eid, "factura", c["id"],
        [{"nombre": "Servicio P0", "precio": "2500", "cantidad": 1}],
        creada_por=u["id"], metodo_pago="efectivo")
    assert v["estado"] == "pagada" and v["numero"].startswith("F-")

    v2 = ventas.crear_venta(
        eid, "factura", c["id"],
        [{"nombre": "Servicio P0b", "precio": "1500", "cantidad": 2}],
        creada_por=u["id"])
    pago = pagos.registrar_pago(eid, v2["id"], "3500",
                                metodo="transferencia", creado_por=u["id"])
    assert pago is not None
    assert ventas.obtener(eid, v2["id"])["estado"] == "pendiente"
    assert reportes.resumen_general(eid) is not None
    assert reportes.resumen_itbis(eid) is not None


@requiere_pg
def test_rol_sin_ddl_no_puede_migrar(env_limpio, pg_segura):
    """Migrar con el rol restringido falla con PermissionError claro."""
    os.environ["FACTURA_DATABASE_URL"] = pg_segura["admin"]
    db.reset_backend_cache()
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "DELETE FROM factura_migrations WHERE archivo=%s",
                       ("010_factura_seguridad.sql",))
    finally:
        conn.close()
    assert db.migraciones_faltantes() == ["010_factura_seguridad.sql"]

    os.environ["FACTURA_DATABASE_URL"] = pg_segura["ro"]
    db.reset_backend_cache()
    with pytest.raises(PermissionError) as ei:
        db.migrate()
    assert "BLOQUEADA por permisos" in str(ei.value)
    assert "DDL" in str(ei.value)

    # Dejar la base limpia para otros usos: re-registrar 010 (el cambio de
    # esquema ya está aplicado físicamente).
    os.environ["FACTURA_DATABASE_URL"] = pg_segura["admin"]
    db.reset_backend_cache()
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(
                cur,
                "INSERT INTO factura_migrations (archivo, aplicada_en) "
                "VALUES (%s, %s)",
                ("010_factura_seguridad.sql", "2026-10-14T00:00:00+00:00"))
    finally:
        conn.close()
    assert db.migraciones_faltantes() == []
