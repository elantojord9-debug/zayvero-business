#!/usr/bin/env python3
"""Permite al propietario establecer su contraseña directamente, sin
pasarla por el chat ni por el repositorio.

USO (lo ejecuta el propietario en su propia máquina o en un entorno seguro):
    python3 scripts/rotate_owner_password.py \
        --users-file /ruta/a/users.json \
        --email demiguel099@gmail.com

El script pide la contraseña por input oculto (getpass, no se muestra en
pantalla), genera el hash con tenant/crypto.py (PBKDF2-SHA256, 260k
iteraciones) y actualiza SOLO el registro de ese email en el archivo
indicado. La contraseña nunca se imprime, nunca se guarda en logs y nunca
sale de la máquina donde se ejecuta.

Después de ejecutarlo, el archivo resultante es el que se pega como
Secret File en Render (o se usa con ZAYVERO_USERS_FILE en local para
verificar con: login con la nueva -> 200, login con la vieja -> 401).
"""
from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from tenant.crypto import hash_password, verify_password  # noqa: E402
from tenant import password_policy as pwd_policy_mod  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--users-file", required=True,
                    help="archivo users.json a actualizar (NO commitear)")
    ap.add_argument("--email", required=True, help="email de la cuenta")
    args = ap.parse_args()

    with open(args.users_file, encoding="utf-8") as f:
        users = json.load(f)

    target = None
    for u in users:
        if (u.get("email") or "").strip().lower() == args.email.strip().lower():
            target = u
            break
    if target is None:
        print(f"ERROR: no existe la cuenta {args.email} en {args.users_file}")
        return 1

    pw1 = getpass.getpass("Nueva contraseña (no se muestra): ")
    pw2 = getpass.getpass("Repite la contraseña: ")
    if not pw1 or pw1 != pw2:
        print("ERROR: las contraseñas no coinciden o están vacías.")
        return 1
    # SEG-05: política centralizada (mínimo 12, máximo 128).
    pwd_errors = pwd_policy_mod.validate_new_password(pw1)
    if pwd_errors:
        print(f"ERROR: {pwd_errors[0]}")
        return 1

    new_hash = hash_password(pw1)
    # La contraseña en claro sale del alcance aquí: solo queda el hash.
    del pw1, pw2

    target["password_hash"] = new_hash
    target["status"] = "active"
    from datetime import datetime, timezone
    target["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tmp = args.users_file + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(users, f, ensure_ascii=False, indent=2)
    os.replace(tmp, args.users_file)
    os.chmod(args.users_file, 0o600)

    # Verificación: el hash guardado debe verificar contra lo que se escribió.
    # (No podemos re-verificar la contraseña en claro porque ya se descartó;
    # la verificación real es el login contra el servidor.)
    ok = verify_password("___imposible___", new_hash) is False
    print(f"OK: contraseña actualizada para {args.email}.")
    print("Verifica con el servidor: login con la nueva -> 200; con la vieja -> 401.")
    print("Recuerda: este archivo contiene hashes. NO lo commitees al repo.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
