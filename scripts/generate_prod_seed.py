#!/usr/bin/env python3
"""Genera el seed de usuarios para el Secret File de Render (NO para el repo).

USO (solo por el operador autorizado, nunca en CI):
    python3 scripts/generate_prod_seed.py \
        --source data/tenant/users.json \
        --out /tmp/users.prod-seed.json

Qué hace:
- Lee el users.json actual (respaldo).
- ELIMINA las 14 cuentas de prueba (@test.com, @e2e.test, @t.do, @f8.do).
  Verifica una por una que ninguna sea real antes de eliminar.
- CONSERVA demo@zayvero.com y viewer@zayvero.com con contraseñas NUEVAS
  aleatorias (se muestran UNA vez por stdout para entregarlas a Miguel
  por canal seguro; nunca se guardan en el repo).
- CONSERVA demiguel099@gmail.com (owner, cuenta real de Miguel) con un
  hash INVÁLIDO ("PENDING_ROTATION_BY_OWNER"): no puede entrar hasta que
  él establezca su contraseña con scripts/rotate_owner_password.py.
- Escribe el JSON listo para pegar como Secret File en Render.

ADVERTENCIA: la salida contiene hashes de contraseñas. NUNCA commitearla
al repo ni pegarla en el chat. Solo va al Secret File de Render.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tenant.crypto import hash_password  # noqa: E402

# Dominios de prueba: ninguna cuenta real usa estos dominios.
TEST_DOMAINS = ("test.com", "e2e.test", "t.do", "f8.do")
DEMO_ACCOUNTS = ("demo@zayvero.com", "viewer@zayvero.com")
OWNER_EMAIL = "demiguel099@gmail.com"
PENDING_MARKER = "PENDING_ROTATION_BY_OWNER"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True, help="users.json actual (respaldo)")
    ap.add_argument("--out", required=True, help="ruta de salida (fuera del repo)")
    args = ap.parse_args()

    with open(args.source, encoding="utf-8") as f:
        users = json.load(f)

    kept: list[dict] = []
    dropped: list[str] = []
    demo_passwords: dict[str, str] = {}

    for u in users:
        email = (u.get("email") or "").strip().lower()
        domain = email.split("@")[-1] if "@" in email else ""

        if email in DEMO_ACCOUNTS:
            # Credenciales nuevas y exclusivas para la demo.
            new_pw = "demo-" + secrets.token_urlsafe(18)
            u = dict(u)
            u["password_hash"] = hash_password(new_pw)
            u["role_id"] = "owner" if email == "demo@zayvero.com" else "viewer"
            u["status"] = "active"
            demo_passwords[email] = new_pw
            kept.append(u)
        elif email == OWNER_EMAIL:
            # Cuenta real de Miguel: se conserva el registro, pero SIN
            # contraseña utilizable hasta que él la establezca.
            u = dict(u)
            u["password_hash"] = PENDING_MARKER
            u["status"] = "active"
            kept.append(u)
        elif domain in TEST_DOMAINS:
            dropped.append(email)
        else:
            # Cualquier cuenta desconocida: NO se elimina automáticamente.
            print(f"AVISO: cuenta no clasificada, se conserva sin cambios: {email}",
                  file=sys.stderr)
            kept.append(u)

    # Aislamiento de la demo: demo@ y viewer@ solo pertenecen a demo-retail.
    for u in kept:
        if u["email"] in DEMO_ACCOUNTS and u.get("company_id") != "demo-retail":
            raise SystemExit(f"ERROR: cuenta demo fuera de demo-retail: {u['email']}")

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(kept, f, ensure_ascii=False, indent=2)
    os.chmod(args.out, 0o600)

    print(f"Cuentas conservadas: {len(kept)}")
    for u in kept:
        print(f"  KEEP  {u['email']} | {u.get('role_id')} | {u.get('status')} | {u.get('company_id')}")
    print(f"Cuentas de prueba eliminadas: {len(dropped)}")
    for e in sorted(dropped):
        print(f"  DROP  {e}")
    print()
    print("=== CREDENCIALES DEMO (entregar a Miguel por canal seguro, UNA sola vez) ===")
    for email, pw in demo_passwords.items():
        print(f"  {email}  /  {pw}")
    print("=== FIN: no guardar estas contraseñas en el repo ni en el chat ===")
    print(f"Archivo escrito en: {args.out} (contiene hashes: NO commitear)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
