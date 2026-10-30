"""BLOQUE 5 — Backfill idempotente: snapshots -> tablas normalizadas.

NO es una migración automática: se ejecuta a mano cuando se decide
rellenar las tablas normalizadas desde los snapshots existentes.

- Reutiliza el MISMO `sincronizar_write_through` del write-through,
  con `antes` vacío (todo se considera nuevo).
- Idempotente: `ON CONFLICT DO NOTHING` + upserts convergentes ->
  correrlo dos veces no duplica ni cambia nada.
- Commit por empresa: una empresa con datos malos no bloquea a las
  demás (se reporta y se sigue).
- Revertir: `os_persist.normalizacion.revertir(conn, company_id)`.

Uso:
    python -m os_persist.backfill_normalizacion [company_id]
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def backfill(conn, company_id: str | None = None) -> dict:
    """Rellena las normalizadas desde snapshots. Devuelve resumen."""
    from os_persist.pg import migrate
    from webapp.os_api import OSBundle
    from os_persist.snapshots import cargar_snapshot
    from os_persist.normalizacion import (sincronizar_write_through,
                                          claves_vacias)

    migrate(conn)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT company_id FROM os_snapshots")
    cids = [r[0] for r in cur.fetchall()]
    if company_id is not None:
        cids = [c for c in cids if c == company_id]

    resumen = {}
    for cid in cids:
        b = OSBundle.crear(cid)
        try:
            if not cargar_snapshot(conn, cid, b):
                resumen[cid] = {"omitida": "sin snapshot"}
                continue
            counts = sincronizar_write_through(cur, cid, b, claves_vacias())
            conn.commit()
            resumen[cid] = {"ok": True, **counts}
        except Exception as e:  # noqa: BLE001 - una empresa no bloquea
            conn.rollback()
            resumen[cid] = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    return resumen


def main(argv: list[str]) -> int:
    dsn = os.environ.get("ZAYVERO_OS_DATABASE_URL")
    if not dsn:
        print("defina ZAYVERO_OS_DATABASE_URL", file=sys.stderr)
        return 2
    from os_persist.pg import connect
    conn = connect(dsn)
    try:
        cid = argv[1] if len(argv) > 1 else None
        resumen = backfill(conn, cid)
        for empresa, r in resumen.items():
            print(f"{empresa}: {r}")
        fallos = sum(1 for r in resumen.values() if r.get("ok") is False)
        return 1 if fallos else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
