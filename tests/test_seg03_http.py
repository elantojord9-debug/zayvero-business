"""SEG-03 — Pruebas HTTP NEGATIVAS reales contra el servidor local.

A diferencia de test_seg01_03.py (que prueba require_csrf() directo),
aquí se golpea el servidor HTTP real (WebappHandler en 127.0.0.1) con
sesiones y datos sintéticos, verificando no solo el 403 sino también
que NO hubo efecto colateral (sin archivo creado, sin modificación).

Casos:
  - POST /api/logout sin CSRF → 403 y la sesión SIGUE válida.
  - POST /api/datasets/upload multipart con CSRF inválido → 403 y
    ningún dataset creado.
  - PUT /api/company/config sin CSRF → 403 y configuración intacta.
  - POST /api/demo/enter con token incorrecto → 403.
  - POST /api/login con Origin no permitido → rechazado (403).
  - Equivalentes legítimos con token válido → funcionan.

Solo stdlib + unittest. Datos sintéticos.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "SegHttp123!"

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE_DIRS = ["datasets", "uploads", "processed", "profiles", "anomalies",
              "reviews", "mappings"]


def _cleanup_company_data(company_id: str):
    for stage in STAGE_DIRS:
        shutil.rmtree(os.path.join(PROJECT_ROOT, "data", stage, company_id),
                      ignore_errors=True)


def _csv_bytes(n=12):
    lines = ["factura,producto,cantidad,fecha_venta,precio,cliente"]
    for i in range(1, n + 1):
        lines.append(
            f"F{i:05d},PROD-{i % 5},10,2024-{(i % 12) + 1:02d}-15,25.50,CLI-{i % 8}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _multipart(filename, content: bytes, nombre=""):
    boundary = "----ZAYVEROSEG" + "y" * 16
    parts = []
    parts.append(f"--{boundary}\r\n"
                 f'Content-Disposition: form-data; name="nombre"\r\n\r\n'
                 f"{nombre}\r\n".encode())
    parts.append(f"--{boundary}\r\n"
                 f'Content-Disposition: form-data; name="file"; '
                 f'filename="{filename}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n".encode()
                 + content + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return (b"".join(parts),
            f"multipart/form-data; boundary={boundary}")


class RawClient:
    """Cliente HTTP crudo: NUNCA envía X-CSRF-Token salvo que se le pase
    explícitamente. Así se prueban los rechazos reales del servidor."""

    def __init__(self, port):
        self.port = port
        self.jar = http.cookiejar.CookieJar()

    def _req(self, method, path, body=None, raw_body=None, headers=None,
             csrf=None):
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        data = raw_body
        hdrs = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        if csrf is not None:
            hdrs["X-CSRF-Token"] = csrf
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            headers=hdrs, method=method)
        try:
            with opener.open(req) as res:
                raw = res.read().decode("utf-8")
                try:
                    payload = json.loads(raw) if raw else {}
                except Exception:
                    payload = {"_raw": raw}
                return res.status, payload
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                payload = json.loads(raw) if raw else {}
            except Exception:
                payload = {}
            return e.code, payload

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, body=None, csrf=None):
        return self._req("POST", path, body=body, csrf=csrf)

    def put(self, path, body=None, csrf=None):
        return self._req("PUT", path, body=body, csrf=csrf)

    def login(self, email, password, origin=None):
        headers = None
        if origin is not None:
            headers = {"Origin": origin}
        return self._req("POST", "/api/login",
                         body={"email": email, "password": password},
                         headers=headers)

    def upload(self, filename, content, nombre="", csrf=None):
        body, ctype = _multipart(filename, content, nombre)
        return self._req("POST", "/api/datasets/upload", raw_body=body,
                         headers={"Content-Type": ctype}, csrf=csrf)


class Seg03HttpNegativeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tenant import TenantStore, create_company
        from tenant.auth import create_user
        import webapp.server as server_mod

        cls.tmp = tempfile.mkdtemp(prefix="zb_seg03http_")
        store = TenantStore(cls.tmp)
        store.ensure_demo_company()
        cls.comp_a = create_company(store, "Empresa SegHTTP A")
        create_user(store, company_id=cls.comp_a.company_id,
                    email="ownera@seghttp.test", name="Owner A",
                    password=PASS, role_id="owner")
        # Lector de la empresa demo (enter_demo lo exige).
        create_user(store, company_id="demo-retail",
                    email="viewerdemo@seghttp.test", name="Demo Viewer",
                    password=PASS, role_id="viewer")
        cls.store = store
        server_mod.STORE = store
        server_mod._DATA_CACHE.clear()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                        server_mod.WebappHandler)
        cls.port = cls.httpd.server_address[1]
        t = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        t.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        _cleanup_company_data(cls.comp_a.company_id)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _login(self):
        c = RawClient(self.port)
        st, payload = c.login("ownera@seghttp.test", PASS)
        self.assertEqual(st, 200)
        self.assertIn("csrf_token", payload)
        return c, payload["csrf_token"]

    # ---- 1. logout sin CSRF ------------------------------------------------
    def test_logout_sin_csrf_403_y_sesion_sigue_valida(self):
        c, csrf = self._login()
        st, _ = c.post("/api/logout")  # sin token
        self.assertEqual(st, 403)
        # La sesión NO se revocó: sigue válida.
        st, payload = c.get("/api/me")
        self.assertEqual(st, 200)
        self.assertIn("csrf_token", payload)
        # Con el token legítimo sí cierra.
        st, _ = c.post("/api/logout", csrf=csrf)
        self.assertEqual(st, 200)
        st, _ = c.get("/api/me")
        self.assertEqual(st, 401)

    # ---- 2. upload multipart con CSRF inválido -----------------------------
    def test_upload_csrf_invalido_403_sin_archivo(self):
        c, _ = self._login()
        st, before = c.get("/api/datasets")
        self.assertEqual(st, 200)
        self.assertEqual(before["datasets"], [])
        st, _ = c.upload("ventas.csv", _csv_bytes(), "Ventas SegHTTP",
                         csrf="token-totalmente-invalido")
        self.assertEqual(st, 403)
        # Ningún dataset creado: el rechazo ocurre ANTES de procesar el
        # multipart (el archivo ni siquiera se parsea).
        st, after = c.get("/api/datasets")
        self.assertEqual(st, 200)
        self.assertEqual(after["datasets"], [])
        # Con el token legítimo la misma subida sí funciona.
        c2, csrf2 = self._login()
        st, res = c2.upload("ventas.csv", _csv_bytes(), "Ventas SegHTTP",
                            csrf=csrf2)
        self.assertEqual(st, 200)
        st, after2 = c2.get("/api/datasets")
        self.assertEqual(st, 200)
        self.assertEqual(len(after2["datasets"]), 1)

    # ---- 3. PUT config sin CSRF --------------------------------------------
    def test_put_config_sin_csrf_403_sin_modificar(self):
        c, _ = self._login()
        st, before = c.get("/api/company/config")
        self.assertEqual(st, 200)
        config_antes = json.dumps(before["config"], sort_keys=True)
        st, _ = c.put("/api/company/config",
                      {"country": "País Atacante", "language": "es"})
        self.assertEqual(st, 403)
        st, after = c.get("/api/company/config")
        self.assertEqual(st, 200)
        self.assertEqual(json.dumps(after["config"], sort_keys=True),
                         config_antes)
        # Con el token legítimo el cambio sí aplica.
        c2, csrf2 = self._login()
        st, _ = c2.put("/api/company/config",
                       {"country": "República Dominicana",
                        "language": "es", "currency": "DOP",
                        "timezone": "America/Santo_Domingo"},
                       csrf=csrf2)
        self.assertEqual(st, 200)
        st, changed = c2.get("/api/company/config")
        self.assertEqual(st, 200)
        self.assertEqual(changed["config"]["country"], "República Dominicana")

    # ---- 4. demo/enter con token incorrecto ---------------------------------
    def test_demo_enter_token_incorrecto_403(self):
        c, _ = self._login()
        st, _ = c.post("/api/demo/enter", {}, csrf="token-falso")
        self.assertEqual(st, 403)
        # Con el token legítimo sí entra a la demo.
        c2, csrf2 = self._login()
        st, payload = c2.post("/api/demo/enter", {}, csrf=csrf2)
        self.assertEqual(st, 200)
        self.assertIn("csrf_token", payload)

    # ---- 5. login con Origin no permitido -----------------------------------
    def test_login_origen_no_permitido_rechazado(self):
        c = RawClient(self.port)
        st, _ = c.login("ownera@seghttp.test", PASS,
                        origin="https://evil.com")
        self.assertEqual(st, 403)
        # Sin Origin (curl) el login legítimo funciona.
        c2 = RawClient(self.port)
        st, payload = c2.login("ownera@seghttp.test", PASS)
        self.assertEqual(st, 200)
        self.assertIn("csrf_token", payload)

    # ---- 6. advisor/ask sin CSRF --------------------------------------------
    def test_advisor_sin_csrf_403(self):
        c, csrf = self._login()
        st, _ = c.post("/api/advisor/ask", {"question": "hola"}, csrf=None)
        self.assertEqual(st, 403)


if __name__ == "__main__":
    unittest.main(verbosity=2)
