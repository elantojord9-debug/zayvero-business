#!/usr/bin/env python3
"""ZAYVERO FACTURA — prototipo SaaS de facturación para RD.

Uso (con el venv del proyecto activado):
    python app.py --migrate   # aplica migraciones y sale
    python app.py --preflight # verifica requisitos y sale
    python app.py --port 8801 # sirve (sin migrar)

Flujo obligatorio: MIGRAR PRIMERO, SERVIR DESPUÉS.
La app ya no migra automáticamente al arrancar: en modo serve exige que
las migraciones estén al día (y en modo cloud exige además PostgreSQL
con TLS). Si falta algo, no arranca y el mensaje dice cómo corregirlo.

- SQLite por defecto (factura.db; ruta cambiable con FACTURA_SQLITE_PATH).
  Con FACTURA_DATABASE_URL definido usa PG.
- FACTURA_ENV=cloud|staging|production activa el preflight cloud.
- Datos 100% ficticios. Sin cobros reales. e-CF en simulación (sin DGII).
"""
from __future__ import annotations

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import config  # noqa: E402
from factura_persist import db  # noqa: E402
from factura_web.server import Handler  # noqa: E402
from http.server import ThreadingHTTPServer  # noqa: E402


def cmd_migrate() -> int:
    aplicadas = db.migrate()
    if aplicadas:
        for a in aplicadas:
            print(f"migración aplicada: {a}")
    else:
        print("sin migraciones pendientes (base al día)")
    print(f"backend: {db.backend()}")
    if db.backend() == "sqlite":
        print(f"sqlite: {config.SQLITE_PATH}")
    else:
        print("postgres: DSN de entorno")
    return 0


def cmd_preflight() -> int:
    try:
        db.chequeo_serve()
        config.validar_superadmin_arranque()
        config.validar_origen_publico_arranque()
        config.validar_seguridad_lanzamiento()
    except (db.PreflightError, RuntimeError) as exc:
        print(f"PREFLIGHT FALLÓ:\n{exc}", file=sys.stderr)
        return 2
    print(f"preflight OK — env={db.env_actual()}, backend={db.backend()}, "
          "migraciones al día")
    return 0


def cmd_serve(host: str, port: int) -> int:
    # La app NO migra sola: exige migraciones al día (o preflight cloud).
    try:
        db.chequeo_serve()
    except db.PreflightError as exc:
        print(f"NO SE PUEDE SERVIR:\n{exc}", file=sys.stderr)
        return 2
    # P0 #2: en modo cloud niega el arranque con credenciales de
    # superadmin por defecto o débiles.
    # P0: en modo cloud niega el arranque sin FACTURA_PUBLIC_ORIGIN
    # válido (el enlace de recuperación nunca usa el encabezado Host).
    try:
        config.validar_superadmin_arranque()
        config.validar_origen_publico_arranque()
        config.validar_seguridad_lanzamiento()
    except RuntimeError as exc:
        print(f"NO SE PUEDE SERVIR:\n{exc}", file=sys.stderr)
        return 2
    print(f"backend: {db.backend()}")
    if db.backend() == "sqlite":
        print(f"sqlite: {config.SQLITE_PATH}")
    else:
        print("postgres: DSN de entorno")

    srv = ThreadingHTTPServer((host, port), Handler)
    print(f"ZAYVERO FACTURA en http://{host}:{port}/ (Ctrl+C para salir)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\ndetenido")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="ZAYVERO FACTURA (prototipo)")
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--host", default="127.0.0.1")
    modo = ap.add_mutually_exclusive_group()
    modo.add_argument("--migrate", action="store_true",
                      help="aplica las migraciones pendientes y sale (no sirve)")
    modo.add_argument("--preflight", action="store_true",
                      help="verifica los requisitos de arranque y sale (no sirve)")
    args = ap.parse_args()

    if args.migrate:
        return cmd_migrate()
    if args.preflight:
        return cmd_preflight()
    return cmd_serve(args.host, args.port)


if __name__ == "__main__":
    sys.exit(main())
