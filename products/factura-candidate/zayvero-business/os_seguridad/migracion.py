"""BLOQUE A SEGURIDAD — Aplicación idempotente de la migración 014.

El coordinador agregará `014_os_seguridad.sql` al final de
os_persist/schema/_orden.txt (NO se edita aquí por orden de trabajo).
Mientras tanto —y para los tests— `asegurar_esquema(dsn)` ejecuta el
archivo directamente: todo el SQL es IF NOT EXISTS, así que aplicarlo
dos veces (aquí y luego vía el migrador) es seguro.
"""

from __future__ import annotations

import os

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "os_persist", "schema")
ARCHIVO = "014_os_seguridad.sql"


def asegurar_esquema(dsn: str) -> None:
    """Crea las tablas del bloque si faltan. Idempotente."""
    from os_persist.pg import connect
    ruta = os.path.join(SCHEMA_DIR, ARCHIVO)
    with open(ruta, encoding="utf-8") as fh:
        sql = fh.read()
    conn = connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.commit()
    finally:
        conn.close()
