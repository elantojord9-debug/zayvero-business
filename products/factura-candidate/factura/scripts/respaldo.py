#!/usr/bin/env python3
"""Respaldo y recuperación de TALENTO (FASE 5 + FASE 6).

- SQLite: la API de backup de sqlite3 (copia consistente en caliente).
- PostgreSQL: pg_dump en formato custom (-Fc) contra el DSN de entorno.
- Destino: `backups/` dentro del proyecto (durable, NUNCA /tmp).
- Cada respaldo genera un manifiesto JSON con SHA-256 para verificar
  integridad antes de restaurar.
- `verificar(archivo)`: SQLite restaura a temporal y corre
  integrity_check; PG valida SHA-256 + lista del archivo custom.
- `restaurar(archivo) --confirmo [--db nombre]`: SQLite sobrescribe
  factura.db (con respaldo previo); PG restaura con pg_restore sobre la
  base del DSN o sobre --db (la base destino debe existir; se crea vacía
  aparte si hace falta).

Uso:
    respaldo.py crear [etiqueta]   -> crea respaldo, imprime manifiesto
    respaldo.py verificar <archivo> -> verifica integridad del respaldo
    respaldo.py restaurar <archivo> --confirmo [--db <bd>]
                                    -> restaura (SQLite: factura.db;
                                       PG: base del DSN o --db)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from urllib.parse import urlparse, urlunparse

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import config  # noqa: E402
from factura_persist import db  # noqa: E402

RESPALDOS_DIR = os.environ.get("FACTURA_BACKUP_DIR", os.path.join(BASE, "backups"))


def _sha256(ruta: str) -> str:
    h = hashlib.sha256()
    with open(ruta, "rb") as fh:
        for bloque in iter(lambda: fh.read(1 << 20), b""):
            h.update(bloque)
    return h.hexdigest()


def _dsn_db(dsn: str, nueva_db: str | None = None) -> str:
    """Devuelve el DSN apuntando a otra base (para restaurar en --db)."""
    if not nueva_db:
        return dsn
    p = urlparse(dsn)
    return urlunparse(p._replace(path="/" + nueva_db))


def _pg_dump(dsn: str, destino: str) -> None:
    env = dict(os.environ, PGDATABASE=dsn)
    r = subprocess.run(["pg_dump", "-Fc", "-f", destino],
                       env=env, capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError("pg_dump falló; revisar conexión/permisos sin registrar DSN")


def _pg_listado_tablas(dump: str) -> list[str]:
    r = subprocess.run(["pg_restore", "-l", dump],
                       capture_output=True, text=True, timeout=180)
    if r.returncode != 0:
        raise RuntimeError(f"pg_restore -l falló: {r.stderr.strip()[:300]}")
    tablas = []
    for linea in r.stdout.splitlines():
        partes = linea.split()
        # formato: <id>; <oid> <oid> TABLE <schema> <nombre> <owner>
        if len(partes) >= 6 and partes[3] == "TABLE" \
                and partes[4] not in ("DATA", "pg_catalog", "information_schema"):
            tablas.append(f"{partes[4]}.{partes[5]}")
    return tablas


def crear(etiqueta: str = "manual") -> dict:
    import re
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,48}", etiqueta):
        raise ValueError("etiqueta de respaldo inválida")
    os.makedirs(RESPALDOS_DIR, exist_ok=True)
    os.chmod(RESPALDOS_DIR, 0o700)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    if db.backend() == "pg":
        nombre = f"factura-{ts}-{etiqueta}.dump"
        destino = os.path.join(RESPALDOS_DIR, nombre)
        _pg_dump(db.dsn_configurado(), destino)
        os.chmod(destino, 0o600)
        manifiesto = {
            "archivo": nombre,
            "sha256": _sha256(destino),
            "bytes": os.path.getsize(destino),
            "backend": "pg",
            "formato": "pg_dump-custom",
            "tablas": sorted(_pg_listado_tablas(destino)),
            "etiqueta": etiqueta,
            "creado_en": datetime.now(timezone.utc).isoformat(),
        }
        with open(destino + ".json", "w", encoding="utf-8") as fh:
            json.dump(manifiesto, fh, ensure_ascii=False, indent=2)
        return manifiesto
    nombre = f"factura-{ts}-{etiqueta}.db"
    destino = os.path.join(RESPALDOS_DIR, nombre)
    origen = sqlite3.connect(config.SQLITE_PATH)
    try:
        dest = sqlite3.connect(destino)
        try:
            origen.backup(dest)
        finally:
            dest.close()
    finally:
        origen.close()
    os.chmod(destino, 0o600)
    manifiesto = {
        "archivo": nombre,
        "sha256": _sha256(destino),
        "bytes": os.path.getsize(destino),
        "backend": db.backend(),
        "etiqueta": etiqueta,
        "creado_en": datetime.now(timezone.utc).isoformat(),
    }
    with open(destino + ".json", "w", encoding="utf-8") as fh:
        json.dump(manifiesto, fh, ensure_ascii=False, indent=2)
    return manifiesto


def verificar(archivo: str) -> dict:
    ruta = archivo if os.path.isabs(archivo) else os.path.join(
        RESPALDOS_DIR, archivo)
    man_path = ruta + ".json"
    if not os.path.exists(ruta) or not os.path.exists(man_path):
        raise ValueError("respaldo o manifiesto no encontrado")
    with open(man_path, encoding="utf-8") as fh:
        man = json.load(fh)
    sha_real = _sha256(ruta)
    if sha_real != man.get("sha256"):
        raise ValueError("SHA-256 NO coincide: respaldo corrupto o alterado")
    if man.get("backend") == "pg" or ruta.endswith(".dump"):
        # Verificación PG: el archivo custom se lista sin servidor.
        tablas = _pg_listado_tablas(ruta)
        n_factura = sum(1 for t in tablas if ".factura_" in t)
        return {"sha_ok": True, "formato": "pg_dump-custom",
                "tablas_totales": len(tablas), "tablas_factura": n_factura,
                "verificacion": "OK" if n_factura > 0 else "FALLO"}
    # Restaurar a temporal y verificar integridad (nunca /tmp crítico:
    # es solo verificación, el original no se toca).
    tmp = tempfile.mkdtemp(prefix="factura-verify-")
    copia = os.path.join(tmp, "check.db")
    shutil.copy2(ruta, copia)
    try:
        conn = sqlite3.connect(copia)
        try:
            integ = conn.execute("PRAGMA integrity_check").fetchone()[0]
            tablas = conn.execute(
                "SELECT COUNT(*) FROM sqlite_master"
                " WHERE type='table' AND name LIKE 'factura_%'").fetchone()[0]
            migs = conn.execute(
                "SELECT COUNT(*) FROM factura_migrations").fetchone()[0]
        finally:
            conn.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    return {"sha_ok": True, "integrity_check": integ, "tablas_factura": tablas,
            "migraciones": migs,
            "verificacion": "OK" if integ == "ok" else "FALLO"}


def restaurar(archivo: str, db_destino: str | None = None) -> dict:
    v = verificar(archivo)
    if v["verificacion"] != "OK":
        raise ValueError("el respaldo no pasa la verificación")
    ruta = archivo if os.path.isabs(archivo) else os.path.join(
        RESPALDOS_DIR, archivo)
    if ruta.endswith(".dump"):
        # Restauración PG con pg_restore. La base destino debe existir
        # (vacía para pruebas de recuperación, o la productiva con
        # --confirmo). NUNCA se restaura sobre la productiva sin --confirmo.
        if not db_destino:
            raise ValueError("restore exige base destino separada explícita")
        source_db = urlparse(db.dsn_configurado()).path.lstrip("/")
        if db_destino == source_db:
            raise ValueError("no se restaura sobre la base operativa")
        dsn = _dsn_db(db.dsn_configurado(), db_destino)
        r = subprocess.run(
            ["pg_restore", "--single-transaction", "--exit-on-error", "--no-owner", "--no-privileges", "-d", "", ruta],
            env=dict(os.environ, PGDATABASE=dsn), capture_output=True, text=True)
        if r.returncode != 0:
            raise RuntimeError(
                "pg_restore falló; destino debe estar vacío; operación revertida")
        return {"restaurado": os.path.basename(ruta),
                "base_destino": db_destino or "(base del DSN)",
                "verificacion": v}
    # Respaldo de seguridad del estado actual antes de sobrescribir.
    previo = crear("pre-restore")
    source = sqlite3.connect(ruta)
    destination = sqlite3.connect(config.SQLITE_PATH)
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    return {"restaurado": os.path.basename(ruta),
            "respaldo_previo": previo["archivo"], "verificacion": v}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or argv[1] not in ("crear", "verificar", "restaurar"):
        print(__doc__)
        return 2
    if argv[1] == "crear":
        man = crear(argv[2] if len(argv) > 2 else "manual")
        print(json.dumps(man, indent=2, ensure_ascii=False))
        return 0
    if argv[1] == "verificar":
        if len(argv) < 3:
            print("indique el archivo de respaldo")
            return 2
        print(json.dumps(verificar(argv[2]), indent=2, ensure_ascii=False))
        return 0
    if argv[1] == "restaurar":
        if len(argv) < 3 or "--confirmo" not in argv:
            print("uso: respaldo.py restaurar <archivo> --confirmo [--db <bd>]")
            return 2
        destino = None
        if "--db" in argv:
            i = argv.index("--db")
            if i + 1 >= len(argv):
                print("falta el nombre de la base tras --db")
                return 2
            destino = argv[i + 1]
        print(json.dumps(restaurar(argv[2], destino), indent=2,
                         ensure_ascii=False))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
