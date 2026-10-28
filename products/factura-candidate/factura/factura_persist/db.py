"""Capa de conexión dual SQLite / PostgreSQL para TALENTO.

Backend por defecto: SQLite (archivo local `factura.db`). Si se define
FACTURA_DATABASE_URL (o ZAYVERO_OS_DATABASE_URL) se usa psycopg contra
PostgreSQL con la misma estructura de tablas. La infraestructura de la
FASE 6 opera el app en producción local sobre PostgreSQL
(base `factura_app`, rol dedicado; ver docs/PG-OPERACION.md).

Reglas de compatibilidad aplicadas en todo el código:
- SQL escrito con placeholders `%s` (estilo psycopg); `adapt()` los
  convierte a `?` para sqlite3.
- Montos y cantidades monetarias como TEXT; se convierten a Decimal en
  Python (exactitud idéntica en ambos motores).
- Booleanos como INTEGER 0/1.
- Fechas como TEXT ISO-8601 (orden lexicográfico = cronológico).
- JSON (items, historial, datos) como TEXT.
- `last_id(cur, sql, params)`: usa `RETURNING id` en PG y
  `cursor.lastrowid` en SQLite.
- `locked(conn)`: transacción con bloqueo de escritura — en PG se usa
  `SELECT ... FOR UPDATE` en el SQL correspondiente; en SQLite se abre
  la transacción con `BEGIN IMMEDIATE` para serializar escritores.
"""
from __future__ import annotations

import os
import re
import sqlite3
import threading
from contextlib import contextmanager
from decimal import Decimal

import config

_BACKEND = threading.local()


def dsn_configurado() -> str | None:
    return os.environ.get("FACTURA_DATABASE_URL") or os.environ.get(
        "ZAYVERO_OS_DATABASE_URL")


def backend() -> str:
    """'pg' si hay DSN, 'sqlite' en caso contrario."""
    b = getattr(_BACKEND, "nombre", None)
    if b is None:
        b = "pg" if dsn_configurado() else "sqlite"
        _BACKEND.nombre = b
    return b


def reset_backend_cache() -> None:
    _BACKEND.nombre = None


class Fila:
    """Fila de resultado compatible con sqlite3.Row.

    En SQLite, `connect()` fija `row_factory = sqlite3.Row`, y todo el
    código accede con `fila[0]`, `fila["columna"]` y `dict(fila)`. En
    PostgreSQL, psycopg devuelve tuplas por defecto, lo que rompía esos
    accesos; esta clase replica la interfaz de sqlite3.Row para que el
    mismo código funcione en ambos motores sin cambios.
    """
    __slots__ = ("_cols", "_vals")

    def __init__(self, cols, vals):
        self._cols = tuple(cols)
        self._vals = tuple(vals)

    def __getitem__(self, k):
        if isinstance(k, slice):
            return self._vals[k]
        if isinstance(k, int):
            return self._vals[k]
        return self._vals[self._cols.index(k)]

    def __iter__(self):
        # Como sqlite3.Row: iterar produce los VALORES (permite
        # `for a, b in filas` y desempaquetado). dict(fila) sigue
        # funcionando por el protocolo de mapping (keys + __getitem__).
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def keys(self):
        return list(self._cols)

    def __contains__(self, k):
        return k in self._cols

    def __repr__(self):
        return f"Fila({dict(zip(self._cols, self._vals))!r})"


def _fabrica_filas(cursor):
    """row_factory de psycopg: envuelve cada tupla en Fila."""
    cols = [d[0] for d in cursor.description] if cursor.description else []

    def _hacer(vals):
        return Fila(cols, vals)

    return _hacer


def connect():
    from .uow import borrowed_connection
    borrowed = borrowed_connection()
    if borrowed is not None:
        return borrowed
    """Abre una conexión nueva (una por operación; hilos seguros)."""
    if backend() == "pg":
        import psycopg
        return psycopg.connect(dsn_configurado(), row_factory=_fabrica_filas)
    conn = sqlite3.connect(config.SQLITE_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def adapt(sql: str) -> str:
    """Convierte placeholders %s -> ? cuando el backend es SQLite."""
    if backend() == "sqlite":
        return sql.replace("%s", "?")
    return sql


def execute(cur, sql: str, params=()):
    return cur.execute(adapt(sql), params)


def fetchone(cur, sql: str, params=()):
    execute(cur, sql, params)
    return cur.fetchone()


def fetchall(cur, sql: str, params=()):
    execute(cur, sql, params)
    return cur.fetchall()


def insert_id(cur, sql: str, params=()) -> int:
    """INSERT que devuelve el id generado (RETURNING en PG, lastrowid en SQLite)."""
    if backend() == "pg":
        execute(cur, sql + " RETURNING id", params)
        return cur.fetchone()[0]
    execute(cur, sql, params)
    return cur.lastrowid


def dec(valor) -> Decimal:
    """Decimal exacto desde TEXT/numérico/None."""
    if valor is None or valor == "":
        return Decimal("0")
    if isinstance(valor, Decimal):
        return valor
    return Decimal(str(valor))


def dec_str(valor) -> str:
    return str(dec(valor))


@contextmanager
def tx(conn):
    """Transacción todo-o-nada."""
    from .uow import is_borrowed
    if is_borrowed(conn):
        yield conn
        return
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


@contextmanager
def locked(conn):
    """Transacción con bloqueo de escritura para secciones críticas
    (secuencias). En SQLite usa BEGIN IMMEDIATE para serializar; en PG
    el llamador debe usar SELECT ... FOR UPDATE dentro del bloque."""
    from .uow import is_borrowed
    if is_borrowed(conn):
        yield conn
        return
    if backend() == "sqlite":
        conn.isolation_level = None
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        finally:
            conn.isolation_level = ""
    else:
        with tx(conn):
            yield conn


# --- migraciones ------------------------------------------------------------

_SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema")
_MANIFEST = "_orden.txt"
_CONTROL = "factura_migrations"


def _traducir_pg(sql: str) -> str:
    """Traducción mínima del dialecto SQLite al de PostgreSQL."""
    sql = sql.replace("INTEGER PRIMARY KEY AUTOINCREMENT",
                      "BIGSERIAL PRIMARY KEY")
    return sql


def _dividir_sentencias_pg(sql: str) -> list[str]:
    """Divide un script SQL en sentencias sin partir dentro de:
    comentarios de línea (--), comentarios de bloque (/* */),
    literales de cadena ('...'), identificadores entrecomillados ("...") y
    cadenas con quoting de dólar ($$...$$, $tag$...$tag$).

    El splitter ingenuo `sql.split(";")` rompe archivos cuyos comentarios
    contienen ';' (pasó con 002_factura_ecf.sql).
    """
    sentencias: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    estado = None  # None | 'linea' | 'bloque' | 'cadena' | 'ident' | ('dolar', tag)

    def vaciar():
        s = "".join(buf).strip()
        if s:
            sentencias.append(s)
        buf.clear()

    while i < n:
        c = sql[i]
        dos = sql[i:i + 2]

        if estado == 'linea':
            buf.append(c)
            if c == "\n":
                estado = None
            i += 1
            continue
        if estado == 'bloque':
            buf.append(c)
            if dos == "*/":
                buf.append(sql[i + 1])
                i += 2
                estado = None
            else:
                i += 1
            continue
        if estado == 'cadena':
            buf.append(c)
            if c == "'":
                if sql[i + 1:i + 2] == "'":  # escape ''
                    buf.append("'")
                    i += 2
                else:
                    estado = None
                    i += 1
            else:
                i += 1
            continue
        if estado == 'ident':
            buf.append(c)
            if c == '"':
                if sql[i + 1:i + 2] == '"':
                    buf.append('"')
                    i += 2
                else:
                    estado = None
                    i += 1
            else:
                i += 1
            continue
        if isinstance(estado, tuple) and estado[0] == 'dolar':
            tag = estado[1]
            if sql.startswith(tag, i):
                buf.append(tag)
                i += len(tag)
                estado = None
            else:
                buf.append(c)
                i += 1
            continue

        # estado None: código normal
        if dos == "--":
            estado = 'linea'
            buf.append(c)
            i += 1
            continue
        if dos == "/*":
            estado = 'bloque'
            buf.append("/*")
            i += 2
            continue
        if c == "'":
            estado = 'cadena'
            buf.append(c)
            i += 1
            continue
        if c == '"':
            estado = 'ident'
            buf.append(c)
            i += 1
            continue
        if c == "$":
            # posible inicio de $tag$...$tag$
            j = i + 1
            while j < n and (sql[j].isalnum() or sql[j] == "_"):
                j += 1
            if j < n and sql[j] == "$":
                tag = sql[i:j + 1]
                estado = ('dolar', tag)
                buf.append(tag)
                i = j + 1
                continue
            buf.append(c)
            i += 1
            continue
        if c == ";":
            vaciar()
            i += 1
            continue
        buf.append(c)
        i += 1

    vaciar()
    return sentencias


def _orden() -> list[str]:
    ruta = os.path.join(_SCHEMA_DIR, _MANIFEST)
    with open(ruta, encoding="utf-8") as fh:
        orden = [l.strip() for l in fh if l.strip()
                 and not l.strip().startswith("#")]
    archivos = {f for f in os.listdir(_SCHEMA_DIR) if f.endswith(".sql")}
    faltan = sorted(archivos - set(orden))
    if faltan:
        raise RuntimeError(f"migraciones sin registrar en {_MANIFEST}: {faltan}")
    sobran = [f for f in orden if f not in archivos]
    if sobran:
        raise RuntimeError(f"{_MANIFEST} lista archivos inexistentes: {sobran}")
    return orden


def _traducir_error_migracion(exc: Exception) -> Exception:
    """Convierte un fallo de permisos en un error claro y accionable.

    Sin esto, `migrate()` con un rol sin DDL muere con el traceback crudo
    de psycopg (error 42501). En staging/producción la app corre con un
    rol que solo tiene CONNECT + USAGE + DML (ver docs/ROL-APP-SIN-DDL.md)
    y las migraciones se aplican con el rol dueño de la base.
    """
    nombre = type(exc).__name__.lower()
    es_permiso_pg = ("insufficientprivilege" in nombre
                     or "insufficient_privilege" in nombre
                     or "42501" in str(exc))
    if es_permiso_pg:
        return PermissionError(
            "migración BLOQUEADA por permisos: el rol actual de PostgreSQL "
            "no tiene permisos DDL (CREATE/ALTER/DROP) sobre la base. "
            "Las migraciones deben aplicarse con el rol dueño de la base "
            "(p. ej. con el DSN del rol administrador: "
            "'FACTURA_DATABASE_URL=<dsn-admin> python app.py --migrate'). "
            "El rol de la app en operación solo necesita CONNECT + USAGE + "
            "DML (ver docs/ROL-APP-SIN-DDL.md). Detalle original: "
            f"{type(exc).__name__}: {exc}")
    msg = str(exc).lower()
    if "readonly" in msg or "attempt to write a readonly" in msg:
        return PermissionError(
            "migración BLOQUEADA: la base SQLite es de solo lectura "
            f"({config.SQLITE_PATH}). Revisa permisos del archivo. "
            f"Detalle original: {exc}")
    return exc


def _migrar_en(conn) -> list[str]:
    cur = conn.cursor()
    execute(cur, f"CREATE TABLE IF NOT EXISTS {_CONTROL} ("
                 "archivo TEXT PRIMARY KEY, aplicada_en TEXT)")
    conn.commit()
    aplicadas = {r[0] for r in fetchall(
        cur, f"SELECT archivo FROM {_CONTROL}")}
    hechas: list[str] = []
    for nombre in _orden():
        if nombre in aplicadas:
            continue
        with open(os.path.join(_SCHEMA_DIR, nombre),
                  encoding="utf-8") as fh:
            sql = fh.read()
        if backend() == "pg":
            sql = _traducir_pg(sql)
        # Las migraciones pueden traer varias sentencias; en sqlite3
        # se ejecutan con executescript, en PG sentencia por sentencia
        # con un splitter consciente de comentarios y literales.
        if backend() == "sqlite":
            cur.executescript(sql)
        else:
            for stmt in _dividir_sentencias_pg(sql):
                cur.execute(stmt)
        from datetime import datetime, timezone
        ahora = datetime.now(timezone.utc).isoformat()
        execute(cur, f"INSERT INTO {_CONTROL} (archivo, aplicada_en)"
                     " VALUES (%s, %s)", (nombre, ahora))
        conn.commit()
        hechas.append(nombre)
    return hechas


def migrate() -> list[str]:
    """Aplica migraciones pendientes (idempotente). Devuelve las aplicadas.

    Los fallos de permisos (rol sin DDL) se traducen a PermissionError
    con mensaje accionable en vez del traceback crudo del driver.
    """
    conn = connect()
    try:
        try:
            return _migrar_en(conn)
        except Exception as exc:
            raise _traducir_error_migracion(exc) from exc
    finally:
        conn.close()


# --- preflight de arranque --------------------------------------------------

class PreflightError(RuntimeError):
    """El servidor no puede arrancar: el mensaje explica qué falta y cómo
    arreglarlo."""


NIVELES_CLOUD = ("cloud", "staging", "production")


def env_actual() -> str:
    """Nombre del entorno: FACTURA_ENV (local por defecto)."""
    return os.environ.get("FACTURA_ENV", "local").strip().lower() or "local"


def es_modo_cloud() -> bool:
    """True si FACTURA_ENV=cloud|staging|production."""
    return env_actual() in NIVELES_CLOUD


_TLS_SEGUROS = ("require", "verify-ca", "verify-full")


def _sslmode_dsn(dsn: str | None) -> str | None:
    """Extrae el sslmode del DSN (formato URL o clave=valor)."""
    if not dsn:
        return None
    dsn = dsn.strip()
    try:
        from urllib.parse import urlparse, parse_qs
        if "://" in dsn:
            qs = parse_qs(urlparse(dsn).query)
            if "sslmode" in qs and qs["sslmode"]:
                return qs["sslmode"][0].strip().lower()
        m = re.search(r"(?:^|\s)sslmode\s*=\s*([^\s'\"]+)", dsn)
        if m:
            return m.group(1).strip().lower()
    except Exception:
        return None
    return None


def migraciones_faltantes() -> list[str]:
    """Migraciones de _orden.txt aún no registradas en factura_migrations.

    Solo lectura: no crea ni modifica nada (segura con el rol sin DDL).
    Si la tabla de control no existe, se consideran pendientes todas.
    """
    esperadas = _orden()
    conn = connect()
    try:
        cur = conn.cursor()
        if backend() == "pg":
            fila = fetchone(
                cur,
                "SELECT 1 FROM pg_class c JOIN pg_namespace n "
                "ON n.oid = c.relnamespace "
                "WHERE c.relname = %s "
                "AND n.nspname NOT IN ('pg_catalog', 'information_schema')",
                (_CONTROL,))
            existe = fila is not None
        else:
            fila = fetchone(
                cur,
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=%s",
                (_CONTROL,))
            existe = fila is not None
        if not existe:
            return list(esperadas)
        aplicadas = {r[0] for r in fetchall(cur, f"SELECT archivo FROM {_CONTROL}")}
        return [n for n in esperadas if n not in aplicadas]
    finally:
        conn.close()


def preflight_cloud() -> None:
    """Verificación obligatoria al arrancar en cloud/staging/production.

    Exige: (a) backend PostgreSQL, (b) DSN con TLS
    (sslmode=require o superior), (c) todas las migraciones del _orden.txt
    aplicadas. Si algo falla, lanza PreflightError con el mensaje exacto
    de qué falta y cómo corregirlo; el servidor NO arranca.

    P0 (2026-10-14): la excepción FACTURA_ALLOW_INSECURE_DSN existe solo
    para desarrollo local; en este preflight se ignora deliberadamente
    aunque esté activada: un DSN sin TLS se rechaza siempre en
    cloud/staging/production.
    """
    errores: list[str] = []
    solo_pg = backend() == "pg"
    if not solo_pg:
        errores.append(
            "backend SQLite no permitido en modo cloud "
            f"(env={env_actual()}): la app está configurada con SQLite "
            "(archivo local), pero en cloud/staging/production es "
            "obligatorio PostgreSQL. Define FACTURA_DATABASE_URL con el "
            "DSN del rol de la app "
            "(postgresql://usuario:clave@host:puerto/base?sslmode=require) "
            "y vuelve a intentar.")
    else:
        sslmode = _sslmode_dsn(dsn_configurado())
        if sslmode not in _TLS_SEGUROS:
            # P0 (2026-10-14): en modo cloud/staging/production el DSN
            # sin TLS se rechaza SIEMPRE. La excepción
            # FACTURA_ALLOW_INSECURE_DSN existe solo para desarrollo
            # local y aquí se ignora deliberadamente aunque esté
            # activada (antes bastaba activarla para saltarse el TLS
            # también en cloud).
            override = os.environ.get(
                "FACTURA_ALLOW_INSECURE_DSN", "").strip().lower() in (
                    "1", "true", "yes", "si")
            msg = (
                "el DSN de PostgreSQL no exige TLS "
                f"(sslmode={sslmode!r}; se requiere 'require' o "
                "'verify-full'). En modo cloud el tráfico hacia la base "
                "debe ir cifrado. Añade 'sslmode=require' (o "
                "'verify-full') al DSN.")
            if override:
                msg += (
                    " FACTURA_ALLOW_INSECURE_DSN está activado, pero esa "
                    "excepción solo funciona en desarrollo local: en modo "
                    "cloud/staging/production el DSN sin TLS se rechaza "
                    "aunque el override esté activado.")
            errores.append(msg)
    if solo_pg:
        try:
            faltan = migraciones_faltantes()
        except Exception as exc:
            faltan = []
            errores.append(
                "no se pudo conectar a la base de datos para verificar "
                f"migraciones: {type(exc).__name__}: {exc}. Revisa el DSN, "
                "la red y que el rol tenga CONNECT sobre la base.")
        if faltan:
            errores.append(
                "migraciones pendientes (no aplicadas): "
                + ", ".join(faltan) + ". La base de datos no está al día. "
                "Aplícalas primero con el rol que tiene permisos DDL: "
                "'FACTURA_DATABASE_URL=<dsn-del-rol-dueño> "
                "python app.py --migrate' y vuelve a servir.")
    if errores:
        raise PreflightError("preflight cloud FALLÓ:\n- " + "\n- ".join(errores))


def chequeo_serve() -> None:
    """Verificación previa a servir (sin migrar nada).

    En modo cloud exige el preflight completo; en local exige que las
    migraciones estén al día. Lanza PreflightError con instrucciones
    accionables si algo falta.
    """
    if es_modo_cloud():
        preflight_cloud()
        return
    faltan = migraciones_faltantes()
    if faltan:
        raise PreflightError(
            "la base de datos no está al día: migraciones pendientes: "
            + ", ".join(faltan) + ". La app ya no migra sola al arrancar: "
            "corre primero 'python app.py --migrate' (aplica las "
            "pendientes y sale) y luego sirve de nuevo.")
