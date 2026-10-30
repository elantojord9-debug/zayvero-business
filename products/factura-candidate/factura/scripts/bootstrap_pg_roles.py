"""Provision NEW runtime/backup roles in a dedicated migrated database.

Owner credentials belong only in this administrative invocation. Nothing
prints passwords or DSNs. Existing roles are refused instead of rotated.
"""
import os
import re
import psycopg
from psycopg import sql


def provision_roles(connection, app_role, app_password, backup_role, backup_password, schema="public"):
    for name in (app_role, backup_role, schema):
        if not re.fullmatch(r"[a-z_][a-z0-9_]{2,62}", name):
            raise ValueError("identificador de rol/esquema inválido")
    if app_role == backup_role or min(len(app_password), len(backup_password)) < 24:
        raise ValueError("roles distintos y contraseñas de al menos 24 caracteres requeridos")
    with connection.transaction():
        for role in (app_role, backup_role):
            if connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                raise ValueError("rol existente: no se rota ni modifica automáticamente")
        dbname = connection.execute("SELECT current_database()").fetchone()[0]
        tables = [row[0] for row in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s AND tablename LIKE 'factura_%%'", (schema,)).fetchall()]
        if "factura_migrations" not in tables:
            raise ValueError("aplique migraciones antes del bootstrap")
        for role, password in ((app_role, app_password), (backup_role, backup_password)):
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}").format(sql.Identifier(role), sql.Literal(password)))
            connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(dbname), sql.Identifier(role)))
            connection.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(schema), sql.Identifier(role)))
        connection.execute(sql.SQL("REVOKE CREATE ON SCHEMA {} FROM PUBLIC").format(sql.Identifier(schema)))
        for table in tables:
            ref = sql.Identifier(schema, table)
            connection.execute(sql.SQL("GRANT SELECT ON {} TO {}").format(ref, sql.Identifier(backup_role)))
            perms = "SELECT" if table == "factura_migrations" else "SELECT, INSERT" if table in ("factura_auditoria", "factura_operaciones", "factura_reembolsos") else "SELECT, INSERT, UPDATE, DELETE"
            connection.execute(sql.SQL("GRANT " + perms + " ON {} TO {}").format(ref, sql.Identifier(app_role)))
        sequences = connection.execute("SELECT sequencename FROM pg_sequences WHERE schemaname=%s AND sequencename LIKE 'factura_%%'", (schema,)).fetchall()
        for (sequence,) in sequences:
            ref = sql.Identifier(schema, sequence)
            connection.execute(sql.SQL("GRANT USAGE, SELECT ON SEQUENCE {} TO {}").format(ref, sql.Identifier(app_role)))
            connection.execute(sql.SQL("GRANT SELECT ON SEQUENCE {} TO {}").format(ref, sql.Identifier(backup_role)))
    return {"runtime_role": app_role, "backup_role": backup_role, "tables": len(tables)}


def main():
    dsn = os.environ["FACTURA_ADMIN_DATABASE_URL"]
    kwargs = {}
    if os.environ.get("FACTURA_ENV") in ("staging", "production"):
        kwargs["sslmode"] = "require"
    with psycopg.connect(dsn, **kwargs) as conn:
        result = provision_roles(conn, os.environ["FACTURA_APP_ROLE"], os.environ["FACTURA_APP_PASSWORD"],
                                 os.environ["FACTURA_BACKUP_ROLE"], os.environ["FACTURA_BACKUP_PASSWORD"])
    import json
    print(json.dumps(result))


if __name__ == "__main__":
    main()
