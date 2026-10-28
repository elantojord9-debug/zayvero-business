"""Synthetic browser E2E. Run explicitly; never points at an external app.

FACTURA_BROWSER_PATH may point to an installed Chromium executable.
Otherwise Playwright's installed chromium is used.
"""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main():
    from playwright.sync_api import sync_playwright
    with tempfile.TemporaryDirectory(prefix="factura-browser-") as tmp:
        os.environ["FACTURA_SQLITE_PATH"] = str(Path(tmp) / "test.db")
        os.environ["FACTURA_ENV"] = "local"
        os.environ["FACTURA_SECRET"] = "synthetic-browser-tests-signing-key-only"
        os.environ.pop("FACTURA_DATABASE_URL", None)
        import config
        from factura_persist import db
        from factura_auth import registro
        from factura_core import inventario, caja, ventas
        db.migrate()
        registered = registro.registrar_empresa("Demo E2E", "123456789", "", "", "browser@test.local", "Test-Browser-Password-123!")
        eid = registered["empresa_id"]
        user = registered["usuario"]["id"]
        product = inventario.crear(eid, "E2E", "Producto de prueba", "100", stock=5)
        caja.abrir(eid, user, 0)
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        env = os.environ.copy()
        with open(Path(tmp) / "server.log", "w") as log:
            server = subprocess.Popen([sys.executable, "-m", "gunicorn", "-c", str(ROOT / "gunicorn.conf.py"), "--bind", "127.0.0.1:" + str(port), "wsgi:application"], cwd=ROOT, env=env, stdout=log, stderr=log)
            try:
                base = "http://127.0.0.1:" + str(port)
                for _ in range(100):
                    try:
                        urllib.request.urlopen(base + "/health", timeout=1)
                        break
                    except Exception:
                        if server.poll() is not None:
                            raise RuntimeError("local server failed")
                        time.sleep(.05)
                errors = []
                with sync_playwright() as pw:
                    browser = pw.chromium.launch(executable_path=os.environ.get("FACTURA_BROWSER_PATH"), headless=True, args=["--no-sandbox", "--disable-dev-shm-usage", "--use-gl=angle", "--use-angle=swiftshader"])
                    for viewport in ({"width": 1440, "height": 1000}, {"width": 390, "height": 844}):
                        context = browser.new_context(viewport=viewport)
                        page = context.new_page()
                        page.on("pageerror", lambda exc: errors.append(str(exc)))
                        page.goto(base + "/login")
                        page.locator("#email").fill("browser@test.local")
                        page.locator("#password").fill("Test-Browser-Password-123!")
                        page.get_by_role("button", name="Entrar", exact=True).click()
                        page.wait_for_url(lambda url: url.endswith("/panel") or url.endswith("/bienvenida"))
                        page.goto(base + "/pos")
                        page.locator("[data-add]").first.click()
                        page.locator("#cobrar").click()
                        page.locator("#pos-msg .alert.ok").wait_for()
                        assert "SIN VALOR FISCAL" in page.locator("body").inner_text()
                        page.emulate_media(media="print")
                        assert page.locator("#ticket-print .ticket").is_visible(), "printed ticket is hidden"
                        assert "SIN VALOR FISCAL" in page.locator("#ticket-print").inner_text()
                        page.emulate_media(media="screen")
                        page.locator("#ticket-print a").click()
                        page.emulate_media(media="print")
                        assert page.locator(".fiscal-notice").is_visible(), "printed fiscal notice is hidden"
                        page.emulate_media(media="screen")
                        for route in ("/facturas", "/inventario", "/cxc", "/reportes", "/devoluciones"):
                            response = page.goto(base + route)
                            assert response.status == 200, (route, response.status)
                        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 2"), "mobile horizontal overflow"
                        page.goto(base + "/planes")
                        html = page.content()
                        assert all(value not in html for value in ("790", "1,490", "2,490"))
                        context.close()
                    browser.close()
                assert not errors, errors
                assert len(ventas.listar(eid)) == 2
                assert str(inventario.obtener(eid, product["id"])["stock"]) == "3"
                print(json.dumps({"status": "passed", "viewports": ["1440x1000", "390x844"], "flows": ["login", "POS", "invoice", "inventory", "receivables", "reports", "returns", "price_gate"], "javascript_errors": errors, "database": "SQLite synthetic", "sales": 2}))
            finally:
                server.terminate()
                server.wait(timeout=10)


if __name__ == "__main__":
    main()
