#!/usr/bin/env python3
"""BLOQUE 5 — Migración de datos existentes hacia el catálogo maestro.

Ruta de migración documentada en docs/catalogo.md, paso 1 ("cosecha"):

- Lee los `producto_id` / `almacen_id` que ya usan los motores en las
  tablas normalizadas (`os_movimientos`, líneas de `os_ventas`) para una
  empresa y crea en el catálogo:
    * almacenes para cada `almacen_id` visto,
    * productos con sku = producto_id y nombre = producto_id
      (metadata.origen = "migracion"; el nombre real se cura a mano).
- NO crea precios ni costos: los motores siguen con su derivación
  actual (fallback) hasta que alguien fije precio/costo en el catálogo.
- NO modifica ni borra nada existente: solo AGREGA filas al catálogo.
- Idempotente: lo que ya existe se omite.

Uso:
    .venv/bin/python catalogo/migrar_existentes.py --company EMPRESA

Requiere ZAYVERO_OS_DATABASE_URL (PostgreSQL local).
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalogo.api import Catalogo  # noqa: E402
from catalogo.modelo import ErrorCatalogo  # noqa: E402


def _cosechar(company_id: str) -> tuple[set[str], set[str]]:
    """Devuelve (productos, almacenes) vistos en tablas normalizadas."""
    from os_persist.pg import connect
    dsn = os.environ.get("ZAYVERO_OS_DATABASE_URL")
    if not dsn:
        raise ErrorCatalogo("defina ZAYVERO_OS_DATABASE_URL")
    productos, almacenes = set(), set()
    conn = connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute(
            "SELECT DISTINCT producto_id, almacen_id FROM os_movimientos"
            " WHERE company_id=%s", (company_id,))
        for pid, alm in cur.fetchall():
            productos.add(pid)
            almacenes.add(alm or "principal")
        cur.execute(
            "SELECT lineas FROM os_ventas WHERE company_id=%s", (company_id,))
        for (lineas,) in cur.fetchall():
            for lin in json.loads(lineas or "[]"):
                if lin.get("producto_id"):
                    productos.add(lin["producto_id"])
                almacenes.add(lin.get("almacen_id") or "principal")
    finally:
        conn.close()
    return productos, almacenes


def migrar(company_id: str) -> dict:
    productos, almacenes = _cosechar(company_id)
    cat = Catalogo(company_id)
    stats = {"almacenes_creados": 0, "productos_creados": 0,
             "omitidos": 0}
    for alm in sorted(almacenes):
        try:
            cat.crear_almacen(alm, f"Almacén {alm}",
                              direccion="")
            stats["almacenes_creados"] += 1
        except ErrorCatalogo as e:
            if "duplicado" in str(e):
                stats["omitidos"] += 1
            else:
                raise
    for pid in sorted(productos):
        try:
            cat.crear_producto(
                pid, pid, pid,  # sku=nombre=producto_id (curar a mano)
                metadata={"origen": "migracion",
                          "nombre_pendiente_curacion": True})
            stats["productos_creados"] += 1
        except ErrorCatalogo as e:
            if "duplicado" in str(e) or "SKU duplicado" in str(e):
                stats["omitidos"] += 1
            else:
                raise
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Cosecha productos/almacenes existentes al catálogo")
    ap.add_argument("--company", required=True)
    args = ap.parse_args()
    stats = migrar(args.company)
    print(f"empresa: {args.company}")
    print(f"  almacenes creados: {stats['almacenes_creados']}, "
          f"productos creados: {stats['productos_creados']}, "
          f"omitidos: {stats['omitidos']}")
    print("  NOTA: precios/costos NO se migraron; los motores siguen con "
          "su derivación actual hasta que se fijen en el catálogo.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
