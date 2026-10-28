#!/usr/bin/env python3
"""BLOQUE 5 — Siembra sintética del catálogo maestro (demo/pruebas).

Datos 100 % FICTICIOS: empresa, categorías, almacenes, productos,
precios y costos inventados para probar y demostrar el catálogo sin
tocar datos reales.

Uso:
    .venv/bin/python catalogo/seed_demo.py [--company demo-catalogo]

Idempotente: lo que ya existe se omite (no duplica).
Requiere ZAYVERO_OS_DATABASE_URL (PostgreSQL local).
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalogo.api import Catalogo  # noqa: E402
from catalogo.modelo import (  # noqa: E402
    ErrorCatalogo, SKUduplicado, ValidacionCatalogo,
)

CATEGORIAS = [
    ("bebidas", "Bebidas", None),
    ("abarrotes", "Abarrotes", None),
    ("limpieza", "Limpieza", None),
    ("cafe", "Café y té", "bebidas"),
]

ALMACENES = [
    ("principal", "Tienda principal", "Av. Ficticia 123, Santo Domingo"),
    ("deposito", "Depósito", "Calle Imaginaria 45, Santo Domingo"),
]

# (producto_id, sku, nombre, categoria_id, unidad, precio, costo)
PRODUCTOS = [
    ("prod-cafe-500", "BEB-CAFE-500G", "Café molido premium 500 g",
     "cafe", "und", "385.00", "240.00"),
    ("prod-te-verde", "BEB-TEV-20U", "Té verde 20 sobres",
     "cafe", "caja", "150.00", "88.50"),
    ("prod-arroz-10", "ABA-ARR-10LB", "Arroz selecto 10 lb",
     "abarrotes", "saco", "620.00", "470.00"),
    ("prod-aceite-1l", "ABA-ACE-1L", "Aceite vegetal 1 L",
     "abarrotes", "und", "295.00", "210.75"),
    ("prod-azucar-5", "ABA-AZU-5LB", "Azúcar crema 5 lb",
     "abarrotes", "saco", "340.00", "255.00"),
    ("prod-detergente", "LIM-DET-2KG", "Detergente multiusos 2 kg",
     "limpieza", "und", "410.00", "289.90"),
    ("prod-jabon", "LIM-JAB-3U", "Jabón de cuaba 3 uds",
     "limpieza", "paq", "120.00", "72.00"),
    # Sin precio inicial a propósito: prueba el fallback de los motores.
    ("prod-muestra", "MUE-001", "Producto muestra sin precio",
     "abarrotes", "und", None, "50.00"),
]


def sembrar(company_id: str, moneda: str = "DOP") -> dict:
    cat = Catalogo(company_id)
    stats = {"categorias": 0, "almacenes": 0, "productos": 0,
             "omitidos": 0, "advertencias": []}
    for cid, nombre, padre in CATEGORIAS:
        try:
            cat.crear_categoria(cid, nombre, padre)
            stats["categorias"] += 1
        except (ValidacionCatalogo, ErrorCatalogo) as e:
            if "duplicada" in str(e):
                stats["omitidos"] += 1
            else:
                raise
    for aid, nombre, direc in ALMACENES:
        try:
            cat.crear_almacen(aid, nombre, direc)
            stats["almacenes"] += 1
        except (ValidacionCatalogo, ErrorCatalogo) as e:
            if "duplicado" in str(e):
                stats["omitidos"] += 1
            else:
                raise
    for pid, sku, nombre, cat_id, unidad, precio, costo in PRODUCTOS:
        try:
            r = cat.crear_producto(
                pid, sku, nombre, categoria_id=cat_id, unidad=unidad,
                metadata={"origen": "seed_demo", "ficticio": True},
                precio=precio, costo=costo, moneda=moneda)
            stats["productos"] += 1
            stats["advertencias"].extend(r.get("advertencias", []))
        except (SKUduplicado, ValidacionCatalogo):
            stats["omitidos"] += 1
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Siembra demo del catálogo")
    ap.add_argument("--company", default="demo-catalogo")
    ap.add_argument("--moneda", default="DOP")
    args = ap.parse_args()
    stats = sembrar(args.company, args.moneda)
    print(f"empresa: {args.company}")
    print(f"  categorías: {stats['categorias']}, almacenes: "
          f"{stats['almacenes']}, productos: {stats['productos']}, "
          f"omitidos (ya existían): {stats['omitidos']}")
    for a in stats["advertencias"]:
        print(f"  ! {a}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
