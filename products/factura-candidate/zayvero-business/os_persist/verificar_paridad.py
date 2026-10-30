"""BLOQUE 2 — INTEGRIDAD (2026-10-12) — Verificador de PARIDAD.

Compara los snapshots JSONB (fuente primaria de lectura) contra las
tablas normalizadas (os_movimientos, os_ordenes, os_recepciones,
os_ventas, os_cuentas, os_gastos, os_clientes, os_documentos,
os_saldos) y reporta cada diferencia con detalle:
empresa, módulo/tabla, clave, valor esperado (snapshot) vs real
(normalizado).

Reutiliza os_persist.normalizacion.verificar_integridad y añade la
comparación de saldos de tesorería (os_saldos vs bundle.tesoreria._caja),
que el verificador original no cubría.

Uso:
    ZAYVERO_OS_DATABASE_URL=... python -m os_persist.verificar_paridad [company_id]

Salida: 0 = paridad total, 1 = se encontraron diferencias,
        2 = error de uso/entorno.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _empresas(conn) -> list:
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT company_id FROM os_snapshots ORDER BY 1")
    return [r[0] for r in cur.fetchall()]


def _check_saldos(conn, company_id: str) -> list:
    """os_saldos vs bundle.tesoreria._caja. La convergencia la hace el
    write-through (upsert por company+moneda); aquí solo se verifica."""
    from webapp.os_api import OSBundle
    from os_persist.snapshots import cargar_snapshot

    diffs = []
    b = OSBundle.crear(company_id)
    if not cargar_snapshot(conn, company_id, b):
        return diffs
    cur = conn.cursor()
    cur.execute("SELECT moneda, saldo FROM os_saldos WHERE company_id=%s",
                (company_id,))
    norm = {r[0]: Decimal(str(r[1])) for r in cur.fetchall()}
    snap = {m: Decimal(str(s)) for m, s in b.tesoreria._caja.items()}
    for moneda in sorted(set(snap) | set(norm)):
        s = snap.get(moneda, Decimal("0"))
        n = norm.get(moneda, Decimal("0"))
        if s != n:
            diffs.append({"tabla": "os_saldos", "clave": moneda,
                          "snapshot": str(s), "normalizado": str(n)})
    return diffs


def verificar_paridad(conn, company_id: str | None = None) -> dict:
    """Paridad para una empresa o todas. Devuelve {company_id: [diffs]}.

    Solo incluye empresas con diferencias; {} = paridad total.
    """
    from os_persist.normalizacion import verificar_integridad

    cids = [company_id] if company_id else _empresas(conn)
    resultado = {}
    for cid in cids:
        try:
            diffs = verificar_integridad(conn, cid)
            diffs += _check_saldos(conn, cid)
        except Exception as e:  # snapshot ilegible: se reporta, no se cae
            diffs = [{"tabla": "os_snapshots", "clave": "ilegible",
                      "snapshot": f"{type(e).__name__}: {e}",
                      "normalizado": "—"}]
        for d in diffs:
            d["company_id"] = cid
        if diffs:
            resultado[cid] = diffs
    return resultado


def _imprimir(resultado: dict, total_empresas: int):
    total = sum(len(v) for v in resultado.values())
    for cid, diffs in resultado.items():
        for d in diffs:
            print(f"[{cid}] {d['tabla']} clave={d['clave']!r}")
            print(f"    esperado (snapshot)  : {d['snapshot']}")
            print(f"    real (normalizado)   : {d['normalizado']}")
    if total == 0:
        print(f"PARIDAD OK: {total_empresas} empresa(s) verificadas, "
              f"0 diferencias.")
    else:
        print(f"PARIDAD ROTA: {total} diferencia(s) en "
              f"{len(resultado)} empresa(s) de {total_empresas} "
              f"verificada(s).")


def main(argv: list[str]) -> int:
    dsn = os.environ.get("ZAYVERO_OS_DATABASE_URL")
    if not dsn:
        print("defina ZAYVERO_OS_DATABASE_URL", file=sys.stderr)
        return 2
    from os_persist.pg import connect, migrate
    conn = connect(dsn)
    try:
        migrate(conn)
        cid = argv[1] if len(argv) > 1 else None
        resultado = verificar_paridad(conn, cid)
        total = 1 if cid else len(_empresas(conn))
        _imprimir(resultado, total)
        return 1 if resultado else 0
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
