"""BLOQUE 1 (sprint 2) — Migración única JSON → PostgreSQL del tenant store.

Lee companies.json / users.json / sessions.json del backend JSON y los
sube a os_empresas / os_usuarios / os_sesiones vía PostgresTenantStore
(upserts: la migración es IDEMPOTENTE, se puede re-ejecutar sin duplicar).

Resolución del archivo de usuarios: la misma prioridad que TenantStore
(ZAYVERO_USERS_FILE > /etc/secrets/users.json > <data_dir>/users.json)
y respeta ZAYVERO_REQUIRE_SECRET_FILE=1: con la variable activa y sin
Secret File, ABORTA en vez de migrar la copia local del repo.

La auditoría (audit.jsonl, append-only) NO se migra a PG: ambos backends
usan el mismo formato/archivo. Si el data_dir destino difiere del
origen, --copy-audit fusiona las líneas por audit_id sin duplicar.

Uso:
    .venv/bin/python -m tenant.migrate_json_to_pg [--data-dir DIR]
        [--target-data-dir DIR] [--dsn DSN] [--copy-audit] [--dry-run]

Todo local, datos sintéticos. No toca producción.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tenant.models import Company, Session, User  # noqa: E402
from tenant import session_tokens as _st  # noqa: E402
from tenant.store import (  # noqa: E402
    DEFAULT_DATA_DIR,
    REQUIRE_SECRET_FILE_ENV_VAR,
    USERS_FILE_ENV_VAR,
    RENDER_SECRETS_USERS_FILE,
    resolve_users_file,
    _strtobool,
)
from tenant.store_pg import PostgresTenantStore  # noqa: E402


def _load_json_list(path: str) -> list:
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def migrate(data_dir: str, dsn: str | None = None,
            target_data_dir: str | None = None,
            copy_audit: bool = False, dry_run: bool = False) -> dict:
    """Ejecuta la migración. Devuelve conteos por entidad."""
    users_file, origin = resolve_users_file(data_dir)
    if origin == "local" and _strtobool(
            os.environ.get(REQUIRE_SECRET_FILE_ENV_VAR, "")):
        # resolve_users_file ya habría abortado; defensa en profundidad.
        raise RuntimeError(
            f"{REQUIRE_SECRET_FILE_ENV_VAR}=1: no se migra la copia local")
    companies = _load_json_list(os.path.join(data_dir, "companies.json"))
    users = _load_json_list(users_file)
    sessions = _load_json_list(os.path.join(data_dir, "sessions.json"))

    counts = {"companies": 0, "users": 0, "sessions": 0,
              "users_file": users_file, "users_origin": origin,
              "audit_copied": 0}
    if dry_run:
        counts.update(companies=len(companies), users=len(users),
                      sessions=len(sessions))
        return counts

    pg = PostgresTenantStore(
        data_dir=target_data_dir or data_dir, dsn=dsn)
    for c in companies:
        pg.save_company(Company(**c))
        counts["companies"] += 1
    for u in users:
        pg.save_user(User(**u))
        counts["users"] += 1
    import dataclasses
    _SESSION_FIELDS = {f.name for f in dataclasses.fields(Session)}
    for s in sessions:
        # Sprint 3: las sesiones ya vienen con verificadores (formato
        # nuevo: session_id interno + token_sha/verifier/salt): se migran
        # tal cual. Las legacy (session_id == token en claro, sin
        # verificadores) se convierten; el token nunca queda en PG.
        if s.get("token_sha") and s.get("token_verifier") \
                and s.get("token_salt"):
            pg.save_session(Session(
                **{k: v for k, v in s.items() if k in _SESSION_FIELDS}))
            counts["sessions"] += 1
            continue
        for ns in _st.migrate_legacy_sessions([s]):
            pg.save_session(ns)
            counts["sessions"] += 1

    if copy_audit and (target_data_dir or data_dir) != data_dir:
        counts["audit_copied"] = _merge_audit(
            os.path.join(data_dir, "audit.jsonl"),
            os.path.join(target_data_dir or data_dir, "audit.jsonl"))
    return counts


def _merge_audit(src: str, dst: str) -> int:
    """Fusiona líneas de auditoría sin duplicar (por audit_id)."""
    if not os.path.exists(src):
        return 0
    seen = set()
    if os.path.exists(dst):
        with open(dst, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        seen.add(json.loads(line).get("audit_id"))
                    except Exception:
                        pass
    copied = 0
    with open(src, encoding="utf-8") as f:
        lines = [ln for ln in f if ln.strip()]
    with open(dst, "a", encoding="utf-8") as out:
        for ln in lines:
            try:
                aid = json.loads(ln).get("audit_id")
            except Exception:
                continue
            if aid not in seen:
                out.write(ln if ln.endswith("\n") else ln + "\n")
                seen.add(aid)
                copied += 1
    return copied


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Migra el tenant store JSON a PostgreSQL (idempotente).")
    ap.add_argument("--data-dir", default=DEFAULT_DATA_DIR,
                    help="dir con companies.json/users.json/sessions.json")
    ap.add_argument("--target-data-dir", default=None,
                    help="data_dir del store PG (default: el mismo)")
    ap.add_argument("--dsn", default=None,
                    help="DSN Postgres (default: ZAYVERO_OS_DATABASE_URL)")
    ap.add_argument("--copy-audit", action="store_true",
                    help="fusionar audit.jsonl si cambia el data_dir")
    ap.add_argument("--dry-run", action="store_true",
                    help="solo cuenta, no escribe")
    args = ap.parse_args(argv)

    counts = migrate(args.data_dir, dsn=args.dsn,
                     target_data_dir=args.target_data_dir,
                     copy_audit=args.copy_audit, dry_run=args.dry_run)
    print(f"origen users.json: {counts['users_file']} "
          f"(origen: {counts['users_origin']})")
    print(f"empresas: {counts['companies']}, usuarios: {counts['users']}, "
          f"sesiones: {counts['sessions']}"
          + (" (dry-run)" if args.dry_run else ""))
    if args.copy_audit:
        print(f"eventos de auditoría copiados: {counts['audit_copied']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
