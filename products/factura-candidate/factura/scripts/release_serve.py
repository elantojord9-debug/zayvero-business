"""Apply authorized release migrations, then drop the owner credential before serving."""
import os
import subprocess
import sys

import psycopg
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict

runtime = os.environ.get("FACTURA_DATABASE_URL")
admin = os.environ.pop("FACTURA_MIGRATION_DATABASE_URL", "")
if admin:
    if not runtime:
        raise SystemExit("Runtime database configuration required")
    runtime_info = conninfo_to_dict(runtime)
    admin_info = conninfo_to_dict(admin)
    if runtime_info.get("dbname") != admin_info.get("dbname"):
        raise SystemExit("Migration and runtime databases differ")
    if runtime_info.get("user") == admin_info.get("user"):
        raise SystemExit("Use separate migration and runtime roles")
    migration_env = dict(os.environ, FACTURA_DATABASE_URL=admin)
    result = subprocess.run([sys.executable, "app.py", "--migrate"], env=migration_env,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        print("Release migrations failed; application startup stopped", flush=True)
        raise SystemExit(result.returncode)
    with psycopg.connect(admin) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_advisory_xact_lock(1900119101)")
            for table in ("factura_operaciones", "factura_reembolsos", "factura_sandbox"):
                cursor.execute(sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE {} TO {}")
                               .format(sql.Identifier(table), sql.Identifier(runtime_info["user"])))
            cursor.execute(sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO {}")
                           .format(sql.Identifier(runtime_info["user"])))
    print("Release migrations applied; runtime role retained", flush=True)
    admin = None
    migration_env = None
os.execvpe(sys.executable, [sys.executable, "-m", "gunicorn", "-c",
                          "gunicorn.conf.py", "wsgi:application"], os.environ)
