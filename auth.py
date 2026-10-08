"""FASE 6A CLI — administración de empresas y usuarios.

Uso:
  python auth.py init-demo
      Crea la empresa demo (demo-retail) si no existe.

  python auth.py create-company "Nombre Empresa"
      Crea una empresa (devuelve company_id).

  python auth.py create-user --company <company_id> --email <email> \\
      --name "Nombre" --role owner|admin|analyst|viewer
      Crea un usuario (pide la contraseña de forma segura, sin eco).

  python auth.py login --email <email>
      Verifica credenciales (pide contraseña) y muestra el TenantContext.
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from tenant import (
    AuthError, TenantStore, create_company, create_user, get_tenant_context,
    login, utcnow_iso,
)


def cmd_init_demo(store):
    company = store.ensure_demo_company()
    print(json.dumps({"company_id": company.company_id, "name": company.name,
                      "is_demo": company.is_demo}, ensure_ascii=False))


def cmd_create_company(store, name):
    try:
        company = create_company(store, name)
    except AuthError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"company_id": company.company_id, "name": company.name},
                     ensure_ascii=False))


def cmd_create_user(store, args):
    password = getpass.getpass("Contraseña (mínimo 8 caracteres): ")
    password2 = getpass.getpass("Confirmar contraseña: ")
    if password != password2:
        print("ERROR: las contraseñas no coinciden", file=sys.stderr)
        sys.exit(1)
    try:
        user = create_user(store, company_id=args.company, email=args.email,
                           name=args.name, password=password, role_id=args.role)
    except AuthError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps({"user_id": user.user_id, "email": user.email,
                      "company_id": user.company_id, "role_id": user.role_id},
                     ensure_ascii=False))


def cmd_login(store, args):
    password = getpass.getpass("Contraseña: ")
    try:
        token, ctx = login(store, args.email, password)
    except AuthError:
        print("ERROR: credenciales inválidas", file=sys.stderr)
        sys.exit(1)
    # El token solo se muestra para pruebas locales; nunca va a logs.
    print(json.dumps({"session_token": token, "context": ctx.to_dict()},
                     ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="FASE 6A — administración de tenants")
    parser.add_argument("--data-dir", default=None,
                        help="directorio del tenant store")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-demo", help="crea la empresa demo")
    p = sub.add_parser("create-company", help="crea una empresa")
    p.add_argument("name")
    p = sub.add_parser("create-user", help="crea un usuario")
    p.add_argument("--company", required=True)
    p.add_argument("--email", required=True)
    p.add_argument("--name", required=True)
    p.add_argument("--role", default="viewer",
                   choices=["owner", "admin", "analyst", "viewer"])
    p = sub.add_parser("login", help="verifica credenciales")
    p.add_argument("--email", required=True)

    args = parser.parse_args()
    store = TenantStore(args.data_dir)

    if args.cmd == "init-demo":
        cmd_init_demo(store)
    elif args.cmd == "create-company":
        cmd_create_company(store, args.name)
    elif args.cmd == "create-user":
        cmd_create_user(store, args)
    elif args.cmd == "login":
        cmd_login(store, args)


if __name__ == "__main__":
    main()
