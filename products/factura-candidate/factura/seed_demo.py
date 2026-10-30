#!/usr/bin/env python3
"""Datos de demostración de TALENTO — 100% FICTICIOS.

Crea:
- Empresa "Tienda La Esquina" (RNC sintético 999888777, dirección inventada)
- Usuario demo: admin@demo.do / Demo1234!
  (clave corta intencional para la demo; cámbiela en Configuración)
- 12 productos de colmado dominicano con precios en RD$
- 5 clientes ficticios (uno con RNC 999999999: en el simulador e-CF ese
  RNC fuerza la respuesta "observado" — es un trigger sintético del
  MockProvider, documentado aquí a propósito)
- 3 ventas de ejemplo, 1 cotización, 1 CxC pendiente

Uso:
    python seed_demo.py [--reset]   # con el venv del proyecto activado
"""
from __future__ import annotations

import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

from datetime import datetime, timezone  # noqa: E402

import config  # noqa: E402
from factura_persist import db  # noqa: E402
from factura_auth.auth import hash_password  # noqa: E402
from factura_core import clientes, inventario, ventas, pagos  # noqa: E402

PRODUCTOS = [
    # (sku, nombre, precio, costo, exento, stock)
    ("ARZ-001", "Arroz Premium 1 lb", 68.00, 52.00, True, 120),
    ("HAB-002", "Habichuelas rojas 1 lb", 95.00, 70.00, True, 80),
    ("CAF-003", "Café molido 1 lb", 385.00, 290.00, True, 45),
    ("ACE-004", "Aceite vegetal 1 gal", 895.00, 700.00, True, 30),
    ("AZU-005", "Azúcar 1 lb", 62.00, 45.00, True, 150),
    ("SAL-006", "Sal 1 lb", 28.00, 18.00, True, 200),
    ("LEC-007", "Leche evaporada 315g", 115.00, 85.00, True, 60),
    ("PAN-008", "Pan de agua (unidad)", 15.00, 8.00, True, 300),
    ("HUE-009", "Huevos (docena)", 240.00, 180.00, True, 40),
    ("JAB-010", "Jabón de lavar (barra)", 55.00, 38.00, False, 90),
    ("DET-011", "Detergente 1 kg", 245.00, 180.00, False, 50),
    ("REF-012", "Refresco 2 L", 135.00, 95.00, False, 72),
]

CLIENTES = [
    ("María Fernández", "40212345678", "809-555-0101", "Calle Duarte #12, Santo Domingo"),
    ("Colmado El Progreso", "130456789", "809-555-0102", "Av. Independencia #45"),
    ("Juan Pérez", "", "809-555-0103", "Calle 8 #23, Los Mina"),
    ("Clínica Demo (prueba observado)", "999999999", "809-555-0104", "Dirección ficticia"),
    ("Ana Martínez", "40298765432", "809-555-0105", "Calle Central #7"),
]


def main() -> None:
    reset = "--reset" in sys.argv
    if reset and os.path.exists(config.SQLITE_PATH) and db.backend() == "sqlite":
        os.remove(config.SQLITE_PATH)
        print("factura.db eliminado (reset)")
    aplicadas = db.migrate()
    print("migraciones:", aplicadas or "al día", "| backend:", db.backend())

    conn = db.connect()
    try:
        cur = conn.cursor()
        existe = db.fetchone(cur, "SELECT id FROM factura_empresas WHERE rnc=%s",
                             ("999888777",))
        if existe and not reset:
            print("la demo ya existe (use --reset para recrear)")
            return
        with db.tx(conn):
            eid = db.insert_id(
                cur, "INSERT INTO factura_empresas (nombre, rnc, telefono,"
                     " direccion, plan, itbis_tasa, e_cf_mode, creada)"
                     " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                ("Tienda La Esquina", "999888777", "809-555-0000",
                 "Calle Ficticia #123, Santo Domingo (dirección inventada)",
                 "negocio", "18", "mock",
                 datetime.now(timezone.utc).isoformat()))
            db.execute(
                cur, "INSERT INTO factura_usuarios (empresa_id, email, hash,"
                     " rol, activo, creado) VALUES (%s,%s,%s,'admin',1,%s)",
                (eid, "admin@demo.do", hash_password("Demo1234!"),
                 datetime.now(timezone.utc).isoformat()))
    finally:
        conn.close()
    print(f"empresa demo id={eid}; usuario admin@demo.do / Demo1234!")

    for sku, nombre, precio, costo, exento, stock in PRODUCTOS:
        inventario.crear(eid, sku, nombre, precio, costo,
                         itbis_exento=exento, stock=stock)
    print(f"{len(PRODUCTOS)} productos creados")

    cids = []
    for nombre, rnc, tel, dire in CLIENTES:
        c = clientes.crear(eid, nombre, rnc, tel, dire)
        cids.append(c["id"])
    print(f"{len(cids)} clientes creados")

    prods = {p["sku"]: p for p in inventario.listar(eid)}

    def item(sku, cant):
        p = prods[sku]
        return {"producto_id": p["id"], "nombre": p["nombre"],
                "cantidad": cant, "precio": p["precio"],
                "itbis_exento": bool(p["itbis_exento"])}

    # Venta 1: de contado (POS)
    v1 = ventas.crear_venta(
        eid, "pos", cids[0],
        [item("ARZ-001", 2), item("HAB-002", 1), item("CAF-003", 1)],
        creada_por=1, metodo_pago="efectivo")
    # Venta 2: a crédito (queda pendiente -> CxC)
    v2 = ventas.crear_venta(
        eid, "factura", cids[1],
        [item("ACE-004", 2), item("AZU-005", 5), item("DET-011", 2)],
        creada_por=1, estado="pendiente")
    # Venta 3: de contado con tarjeta (incluye gravados con ITBIS)
    v3 = ventas.crear_venta(
        eid, "factura", cids[4],
        [item("LEC-007", 3), item("JAB-010", 4), item("REF-012", 2)],
        creada_por=1, metodo_pago="tarjeta")
    print(f"ventas: {v1['numero']} ({v1['estado']}), {v2['numero']} "
          f"({v2['estado']}), {v3['numero']} ({v3['estado']})")

    # Abono parcial a la venta a crédito (queda balance pendiente)
    pagos.registrar_pago(eid, v2["id"], 500, "efectivo", creado_por=1)
    print(f"abono RD$ 500 a {v2['numero']} (balance restante: "
          f"{pagos.balance(eid, v2['id'])})")

    # Cotización de ejemplo
    c1 = ventas.crear_cotizacion(
        eid, cids[2], [item("PAN-008", 20), item("HUE-009", 2)], creada_por=1)
    print(f"cotización: {c1['numero']} ({c1['estado']})")

    print("\nDemo lista. Arranque con: "
          "python app.py --port 8801   # con el venv del proyecto activado")


if __name__ == "__main__":
    main()
