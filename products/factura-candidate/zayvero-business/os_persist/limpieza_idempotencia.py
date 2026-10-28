"""BLOQUE 6 (2026-10-12) — OPERACIONES: limpieza SEGURA de claves
idempotentes vencidas.

Borra SOLO filas de `os_idempotency` con `expires_at` estrictamente en
el pasado (`expires_at < now()`). Nunca toca claves activas.

Reglas de seguridad:
(a) Predicado estricto: `expires_at < now()` (no `<=`). Una clave cuyo
    vencimiento coincide con el instante de la pasada NO se borra.
(b) Borrado por lotes (defecto 1000 filas/lote) con COMMIT por lote:
    una pasada grande no retiene un lock largo ni una transacción
    gigante.
(c) `--dry-run`: cuenta cuántas filas se borrarían SIN borrar nada.
(d) Log de lo borrado: cuenta por empresa y por día de vencimiento.

Operación recomendada (ver docs/operaciones.md): invocación manual o
cron interno del servidor de la app. Este módulo NO crea cron jobs del
sistema; es CLI + función importable.

Relación con `os_persist/idempotencia.py::limpiar_expiradas`:
esa función (BLOQUE 2) sigue existiendo para uso puntual en código;
este módulo es la herramienta de OPERACIÓN (por lotes, dry-run, log).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

LOTE_DEFECTO = 1000


def _ahora_utc() -> datetime:
    return datetime.now(timezone.utc)


def contar_vencidas(conn, company_id: str | None = None) -> int:
    """Cuenta claves con expires_at estrictamente en el pasado."""
    cur = conn.cursor()
    if company_id is None:
        cur.execute("SELECT COUNT(*) FROM os_idempotency"
                    " WHERE expires_at < now()")
    else:
        cur.execute("SELECT COUNT(*) FROM os_idempotency"
                    " WHERE company_id=%s AND expires_at < now()",
                    (company_id,))
    return int(cur.fetchone()[0])


def limpiar(conn, lote: int = LOTE_DEFECTO, dry_run: bool = False,
            company_id: str | None = None,
            limite_total: int | None = None) -> dict:
    """Borra claves vencidas por lotes, con commit por lote.

    - Solo `expires_at < now()`: las activas (futuro) jamás se tocan.
    - En cada lote se seleccionan las PK (company_id, clave) a borrar,
      se agregan al log (empresa + día de vencimiento) y se borran
      por PK exacta. Commit por lote.
    - `dry_run=True`: solo cuenta, no borra, no hace commit.
    - `limite_total`: tope de filas a borrar en esta invocación
      (None = sin tope).

    Devuelve dict con:
      borradas: int, dry_run: bool, lotes: int,
      por_empresa: {company_id: n}, por_dia: {"YYYY-MM-DD": n},
      limite_total: int|None, lote: int
    """
    if lote < 1:
        raise ValueError("lote debe ser >= 1")
    if limite_total is not None and limite_total < 0:
        raise ValueError("limite_total debe ser >= 0")

    total = contar_vencidas(conn, company_id)
    resultado = {
        "borradas": 0,
        "dry_run": bool(dry_run),
        "lotes": 0,
        "por_empresa": {},
        "por_dia": {},
        "vencidas_detectadas": total,
        "limite_total": limite_total,
        "lote": lote,
    }
    if dry_run or total == 0:
        return resultado

    cur = conn.cursor()
    restantes = total if limite_total is None else min(total, limite_total)
    while restantes > 0:
        n_lote = min(lote, restantes)
        # 1) Seleccionar las PK exactas a borrar en este lote
        #    (ordenadas por vencimiento: las más viejas primero).
        if company_id is None:
            cur.execute(
                """SELECT company_id, clave, expires_at
                   FROM os_idempotency
                   WHERE expires_at < now()
                   ORDER BY expires_at ASC
                   LIMIT %s""", (n_lote,))
        else:
            cur.execute(
                """SELECT company_id, clave, expires_at
                   FROM os_idempotency
                   WHERE company_id=%s AND expires_at < now()
                   ORDER BY expires_at ASC
                   LIMIT %s""", (company_id, n_lote))
        filas = cur.fetchall()
        if not filas:
            break  # otra pasada concurrente las borró
        for cid, clave, expira in filas:
            resultado["por_empresa"][cid] = \
                resultado["por_empresa"].get(cid, 0) + 1
            dia = expira.astimezone(timezone.utc).strftime("%Y-%m-%d")
            resultado["por_dia"][dia] = resultado["por_dia"].get(dia, 0) + 1
        # 2) Borrar SOLO esas PK exactas (doble predicado de seguridad:
        #    PK + expires_at < now(); si entre el SELECT y el DELETE la
        #    fila cambió, el predicado la protege).
        pks = [(f[0], f[1]) for f in filas]
        cur.executemany(
            "DELETE FROM os_idempotency"
            " WHERE company_id=%s AND clave=%s AND expires_at < now()",
            pks)
        borradas_lote = cur.rowcount
        conn.commit()  # commit por lote
        resultado["borradas"] += borradas_lote
        resultado["lotes"] += 1
        restantes -= borradas_lote
        if borradas_lote < len(filas):
            # Algunas filas dejaron de estar vencidas entre el SELECT y
            # el DELETE (caso rarísimo): no reintentar en esta pasada.
            break
    return resultado


def _main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Limpieza segura de claves idempotentes vencidas "
                    "(os_idempotency). Solo borra expires_at < now().")
    ap.add_argument("--dry-run", action="store_true",
                    help="solo cuenta cuántas se borrarían, sin borrar")
    ap.add_argument("--lote", type=int, default=LOTE_DEFECTO,
                    help=f"filas por lote (defecto {LOTE_DEFECTO})")
    ap.add_argument("--empresa", default=None,
                    help="limitar a una company_id")
    ap.add_argument("--limite", type=int, default=None,
                    help="tope de filas a borrar en esta invocación")
    ap.add_argument("--json", action="store_true",
                    help="salida en JSON (una línea)")
    args = ap.parse_args(argv)

    from os_persist.pg import connect
    conn = connect()
    try:
        res = limpiar(conn, lote=args.lote, dry_run=args.dry_run,
                      company_id=args.empresa, limite_total=args.limite)
    finally:
        conn.close()
    res["cuando_utc"] = _ahora_utc().isoformat()
    if args.json:
        print(json.dumps(res, ensure_ascii=False))
    else:
        print(f"[limpieza-idempotencia] dry_run={res['dry_run']} "
              f"vencidas_detectadas={res['vencidas_detectadas']} "
              f"borradas={res['borradas']} lotes={res['lotes']} "
              f"lote={res['lote']} limite={res['limite_total']}")
        if res["por_empresa"]:
            top = sorted(res["por_empresa"].items(),
                         key=lambda kv: -kv[1])[:10]
            print("  por empresa (top 10): " +
                  ", ".join(f"{c}={n}" for c, n in top))
        if res["por_dia"]:
            print("  por día de vencimiento: " +
                  ", ".join(f"{d}={n}"
                            for d, n in sorted(res["por_dia"].items())))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
