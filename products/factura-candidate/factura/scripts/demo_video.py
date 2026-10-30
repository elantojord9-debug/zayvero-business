#!/usr/bin/env python3
"""FASE 6 — Demo comercial de TALENTO.

Recorre el flujo completo con la empresa ficticia "Tienda La Esquina"
usando el software REAL (Playwright + Chromium headless) y captura
pantalla de cada paso. Las capturas sirven para el video demostrativo.

Uso:
    demo_video.py [--port 8801] [--salida demo/capturas]

Pasos: login → panel → POS → nueva factura → detalle factura →
clientes → inventario → CxC → e-CF → reportes → config.
"""
from __future__ import annotations

import argparse
import os
import sys
import time

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

EMAIL_DEMO = "admin@demo.do"
CLAVE_DEMO = "Demo1234!"

PASOS = [
    ("01-landing", "/", None),
    ("02-login", "/login", None),
    ("03-panel", "/panel", None),
    ("04-pos", "/pos", None),
    ("05-caja", "/caja", None),
    ("06-factura-nueva", "/facturas/nueva", None),
    ("07-facturas", "/facturas", None),
    ("08-devoluciones", "/devoluciones", None),
    ("09-clientes", "/clientes", None),
    ("10-inventario", "/inventario", None),
    ("11-sucursales", "/sucursales", None),
    ("12-cxc", "/cxc", None),
    ("13-ecf", "/ecf", None),
    ("14-reportes", "/reportes", None),
    ("15-auditoria", "/auditoria", None),
    ("16-config", "/config", None),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8801)
    ap.add_argument("--salida", default=os.path.join(BASE, "demo", "capturas"))
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    os.makedirs(args.salida, exist_ok=True)
    base_url = f"http://127.0.0.1:{args.port}"
    hechas = []
    with sync_playwright() as pw:
        navegador = pw.chromium.launch(headless=True, args=["--no-sandbox"])
        pagina = navegador.new_page(viewport={"width": 1366, "height": 800})
        for nombre, ruta, _ in PASOS:
            pagina.goto(base_url + ruta, wait_until="networkidle")
            if ruta == "/login":
                # Captura el login ANTES de autenticar
                dest = os.path.join(args.salida, f"{nombre}.png")
                pagina.screenshot(path=dest)
                hechas.append(dest)
                # Autenticar para los pasos siguientes
                pagina.fill('input[name="email"]', EMAIL_DEMO)
                pagina.fill('input[name="password"]', CLAVE_DEMO)
                pagina.click('button[type="submit"]')
                pagina.wait_for_url(
                    lambda url: url.rstrip("/") in (
                        base_url, base_url + "/panel",
                        base_url + "/bienvenida"),
                    timeout=10000)
                time.sleep(0.5)
                continue
            time.sleep(0.4)
            dest = os.path.join(args.salida, f"{nombre}.png")
            pagina.screenshot(path=dest)
            hechas.append(dest)
            print(f"  {nombre}: {ruta} OK")
        navegador.close()
    print(f"\n{len(hechas)} capturas en {args.salida}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
