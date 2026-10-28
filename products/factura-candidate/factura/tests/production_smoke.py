"""Isolated production-image QA: TLS PG, least privilege and container restart.

Only accepts the explicit QA DSN and FACTURA_TEST_ALLOW_DDL=1. Creates and
drops its own database and role. Never targets an existing app database.
"""
import concurrent.futures
import hashlib
import json
import os
import secrets
import socket
import subprocess
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import psycopg
from psycopg import sql


def main():
    if os.environ.get("FACTURA_TEST_ALLOW_DDL") != "1":
        raise RuntimeError("solo QA explícita")
    admin = os.environ["FACTURA_TEST_DATABASE_URL"]
    parsed = urlsplit(admin)
    suffix = secrets.token_hex(6)
    dbname = "image_qa_" + suffix
    role = "image_runtime_" + suffix
    password = secrets.token_urlsafe(32)
    query = dict(parse_qsl(parsed.query))
    query["sslmode"] = "require"
    migration_dsn = urlunsplit(parsed._replace(path="/" + dbname, query=urlencode(query)))
    runtime_dsn = urlunsplit((parsed.scheme, quote(role) + ":" + quote(password) + "@" + parsed.hostname + ":" + str(parsed.port or 5432), "/" + dbname, urlencode(query), ""))
    container = "factura-qa-" + suffix
    image = os.environ.get("FACTURA_QA_IMAGE", "factura-production-qa")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, FACTURA_ENV="production", FACTURA_HTTPS="1",
               FACTURA_SECRET=secrets.token_urlsafe(48),
               FACTURA_PUBLIC_ORIGIN="https://synthetic.invalid",
               FACTURA_SUPERADMIN_EMAIL="qa@synthetic.invalid",
               FACTURA_SUPERADMIN_PASS=secrets.token_urlsafe(32))
    variables = ["FACTURA_ENV", "FACTURA_HTTPS", "FACTURA_SECRET", "FACTURA_PUBLIC_ORIGIN",
                 "FACTURA_SUPERADMIN_EMAIL", "FACTURA_SUPERADMIN_PASS", "FACTURA_DATABASE_URL"]

    def docker(*args, dsn=runtime_dsn):
        return subprocess.run(["docker", *args], env=dict(env, FACTURA_DATABASE_URL=dsn),
                              check=True, capture_output=True, text=True, timeout=180)

    def run_image(*command, dsn=runtime_dsn):
        args = ["run", "--rm", "--network", "host", "--read-only", "--tmpfs", "/tmp"]
        for key in variables:
            args += ["-e", key]
        return docker(*args, image, *command, dsn=dsn)

    def execute(query, dsn=admin):
        with psycopg.connect(dsn, autocommit=True) as conn:
            return conn.execute(query)

    def fingerprint():
        result = {}
        with psycopg.connect(runtime_dsn) as conn:
            tables = conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename").fetchall()
            for (table,) in tables:
                rows = conn.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table))).fetchall()
                ordered = sorted(json.dumps(row, default=str) for row in rows)
                result[table] = hashlib.sha256(json.dumps(ordered).encode()).hexdigest()
        return result

    base = "http://127.0.0.1:" + str(port)

    def ready():
        for _ in range(150):
            try:
                with urllib.request.urlopen(base + "/health", timeout=2) as response:
                    if json.loads(response.read())["ok"]:
                        return
            except Exception:
                time.sleep(.2)
        raise RuntimeError("production container not ready")

    execute(sql.SQL("CREATE DATABASE {} TEMPLATE template0").format(sql.Identifier(dbname)))
    execute(sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
    try:
        run_image("python", "app.py", "--migrate", dsn=migration_dsn)
        execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname), sql.Identifier(role)))
        for statement in ("REVOKE CREATE ON SCHEMA public FROM PUBLIC",
                          "GRANT USAGE ON SCHEMA public TO {}",
                          "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}",
                          "GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}",
                          "REVOKE UPDATE, DELETE ON factura_auditoria, factura_operaciones, factura_reembolsos FROM {}",
                          "REVOKE INSERT, UPDATE, DELETE ON factura_migrations FROM {}"):
            execute(sql.SQL(statement).format(sql.Identifier(role)), migration_dsn)
        run_image("python", "-c", "from factura_auth.registro import registrar_empresa; from factura_core import inventario,ventas; r=registrar_empresa('Synthetic QA','123456789','','','owner@qa.invalid','Synthetic-Password-123!'); e=r['empresa_id']; p=inventario.crear(e,'QA','Synthetic',100,stock=10); ventas.crear_venta(e,'factura',None,[{'producto_id':p['id'],'nombre':'Synthetic','cantidad':2,'precio':'100'}])")
        before = fingerprint()
        with psycopg.connect(runtime_dsn) as conn:
            assert conn.execute("SELECT ssl FROM pg_stat_ssl WHERE pid=pg_backend_pid()").fetchone()[0]
        try:
            with psycopg.connect(runtime_dsn) as conn:
                conn.execute("CREATE TABLE forbidden_ddl(id int)")
        except psycopg.errors.InsufficientPrivilege:
            pass
        else:
            raise AssertionError("runtime can perform DDL")
        args = ["run", "-d", "--name", container, "--network", "host", "--read-only", "--tmpfs", "/tmp"]
        for key in variables:
            args += ["-e", key]
        docker(*args, image, "python", "-m", "gunicorn", "-c", "gunicorn.conf.py", "--bind", "127.0.0.1:" + str(port), "wsgi:application")
        ready()

        def request(_):
            start = time.monotonic()
            with urllib.request.urlopen(base + "/health", timeout=5) as response:
                assert response.status == 200
                assert json.loads(response.read())["ok"]
            return time.monotonic() - start

        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            times = sorted(pool.map(request, range(50)))
        assert times[47] < 5
        docker("kill", container)
        docker("start", container)
        ready()
        assert before == fingerprint(), "business data changed/lost after image restart"
        print(json.dumps({"production_image": "passed", "runtime_ddl": "denied", "database_tls": True,
                          "container_restart": "passed", "tables_reconciled": len(before),
                          "health_requests": 50, "concurrency": 8, "health_p95_ms": round(times[47] * 1000, 2)}))
    except Exception:
        try:
            print(docker("logs", "--tail", "40", container).stdout)
        except Exception:
            pass
        raise
    finally:
        subprocess.run(["docker", "rm", "-f", container], capture_output=True)
        execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(dbname)))
        execute(sql.SQL("DROP ROLE IF EXISTS {}").format(sql.Identifier(role)))


if __name__ == "__main__":
    main()
