"""Tests del flujo «Explorar demo» (corrección del bug demo-go).

Cubre: /api/demo/enter requiere autenticación (401 sin sesión), crea una
sesión demo ligada a demo-retail (rol viewer, TTL 1h) sin revocar la sesión
origen, aislamiento de tenants en modo demo (?company_id= ignorado),
/api/demo/exit revoca la demo y restaura la sesión origen, auditoría
DEMO_ENTER/DEMO_EXIT, y cheques estáticos del frontend (el botón ya no
llama a /api/logout).

Ejecutar: .venv/bin/python -m unittest tests.test_demo -v
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request
from datetime import datetime, timezone
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = "ProductTest123"
DEMO_COMPANY = "demo-retail"


class Client:
    def __init__(self, port):
        self.port = port
        self.jar = http.cookiejar.CookieJar()
        # SEG-03: el cliente de pruebas se comporta como el frontend real:
        # guarda el csrf_token del login y lo envía en X-CSRF-Token.
        self.csrf_token = ""

    def _req(self, method, path, body=None):
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if method in ("POST", "PUT", "PATCH", "DELETE") and self.csrf_token:
            headers["X-CSRF-Token"] = self.csrf_token
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            headers=headers, method=method)
        try:
            with opener.open(req) as res:
                raw = res.read().decode("utf-8")
                try:
                    payload = json.loads(raw) if raw else {}
                except Exception:
                    payload = {"_raw": raw}
                # SEG-03: demo enter/exit rotan la sesión y su token CSRF.
                if isinstance(payload, dict) and payload.get("csrf_token"):
                    self.csrf_token = payload["csrf_token"]
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

    def post(self, path, body=None):
        return self._req("POST", path, body)

    def login(self, email, password):
        st, payload = self.post("/api/login",
                                {"email": email, "password": password})
        return st, payload

    def cookie_token(self):
        for c in self.jar:
            if c.name == "zayvero_session":
                return c.value
        return ""


class DemoFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tenant import TenantStore, create_company
        from tenant.auth import create_user
        import webapp.server as server_mod

        cls.tmp = tempfile.mkdtemp(prefix="zayverodemo_")
        store = TenantStore(cls.tmp)
        store.ensure_demo_company()
        # Usuario lector de la demo (el que usa la sesión demo).
        create_user(store, company_id=DEMO_COMPANY, email="viewerdemo@t.do",
                    name="Demo Viewer", password=PASS, role_id="viewer")
        cls.comp_a = create_company(store, "Empresa A Demo")
        cls.comp_b = create_company(store, "Empresa B Demo")
        create_user(store, company_id=cls.comp_a.company_id,
                    email="ownerademo@t.do", name="Owner A",
                    password=PASS, role_id="owner")
        create_user(store, company_id=cls.comp_b.company_id,
                    email="ownerbdemo@t.do", name="Owner B",
                    password=PASS, role_id="owner")
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
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _login(self, email):
        c = Client(self.port)
        st, _ = c.login(email, PASS)
        self.assertEqual(st, 200)
        return c

    # ---------------------------------------------------------- /demo/enter
    def test_enter_requires_auth(self):
        c = Client(self.port)
        st, payload = c.post("/api/demo/enter")
        self.assertEqual(st, 401)
        self.assertIn("error", payload)

    def test_enter_creates_demo_session(self):
        c = self._login("ownerademo@t.do")
        origin_token = c.cookie_token()
        self.assertTrue(origin_token)
        st, payload = c.post("/api/demo/enter")
        self.assertEqual(st, 200)
        self.assertTrue(payload.get("ok"))
        demo_token = c.cookie_token()
        self.assertTrue(demo_token)
        self.assertNotEqual(demo_token, origin_token)
        # La sesión demo quedó ligada a demo-retail con TTL corto.
        sess = self.store.get_session(demo_token)
        self.assertIsNotNone(sess)
        self.assertEqual(sess.company_id, DEMO_COMPANY)
        self.assertEqual(sess.origin_session_id, origin_token)
        self.assertFalse(sess.revoked)
        exp = datetime.strptime(sess.expires_at, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
        now = datetime.now(timezone.utc)
        self.assertTrue(0 < (exp - now).total_seconds() <= 3600 + 60)
        # El usuario de la sesión demo es lector (solo lectura).
        demo_user = self.store.get_user(sess.user_id)
        self.assertEqual(demo_user.role_id, "viewer")
        # La sesión origen sigue válida (no se revocó).
        origin_sess = self.store.get_session(origin_token)
        self.assertIsNotNone(origin_sess)
        self.assertFalse(origin_sess.revoked)

    def test_enter_serves_demo_overview(self):
        c = self._login("ownerademo@t.do")
        c.post("/api/demo/enter")
        st, payload = c.get("/api/product/overview")
        self.assertEqual(st, 200)
        ov = payload["overview"]
        self.assertEqual(ov["company"]["company_id"], DEMO_COMPANY)
        self.assertTrue(ov["company"]["is_demo"])

    def test_demo_isolation_query_param_ignored(self):
        c = self._login("ownerademo@t.do")
        c.post("/api/demo/enter")
        st, payload = c.get("/api/product/overview?company_id=" +
                            self.comp_a.company_id)
        self.assertEqual(st, 200)
        self.assertEqual(payload["overview"]["company"]["company_id"],
                         DEMO_COMPANY)

    def test_demo_cannot_reach_other_company(self):
        ca = self._login("ownerademo@t.do")
        ca.post("/api/demo/enter")
        # La sesión demo no ve el overview de la empresa A ni con parámetro.
        st, payload = ca.get("/api/product/overview?company_id=" +
                             self.comp_a.company_id)
        self.assertEqual(payload["overview"]["company"]["company_id"],
                         DEMO_COMPANY)

    def test_audit_demo_enter(self):
        c = self._login("ownerademo@t.do")
        before = len([e for e in self.store.list_audit(DEMO_COMPANY)
                      if e.action == "DEMO_ENTER"])
        c.post("/api/demo/enter")
        events = [e for e in self.store.list_audit(DEMO_COMPANY)
                  if e.action == "DEMO_ENTER"]
        self.assertEqual(len(events), before + 1)
        ev = events[-1]
        self.assertEqual(ev.company_id, DEMO_COMPANY)
        self.assertEqual(ev.result, "ok")
        self.assertEqual(ev.metadata.get("origin_company_id"),
                         self.comp_a.company_id)
        self.assertEqual(ev.metadata.get("origin_email"), "ownerademo@t.do")
        blob = json.dumps(ev.metadata).lower()
        for secret_word in ("password", "secret", "api_key"):
            self.assertNotIn(secret_word, blob)

    # ----------------------------------------------------------- /demo/exit
    def test_exit_requires_auth(self):
        c = Client(self.port)
        st, _ = c.post("/api/demo/exit")
        self.assertEqual(st, 401)

    def test_exit_without_demo_session_fails(self):
        c = self._login("ownerademo@t.do")
        st, payload = c.post("/api/demo/exit")
        self.assertEqual(st, 400)
        self.assertIn("error", payload)

    def test_exit_restores_origin(self):
        c = self._login("ownerademo@t.do")
        origin_token = c.cookie_token()
        c.post("/api/demo/enter")
        demo_token = c.cookie_token()
        st, payload = c.post("/api/demo/exit")
        self.assertEqual(st, 200)
        self.assertTrue(payload.get("ok"))
        self.assertFalse(payload.get("login_required", False))
        # La cookie volvió a la sesión origen.
        self.assertEqual(c.cookie_token(), origin_token)
        # La sesión demo quedó revocada; la origen sigue válida.
        self.assertTrue(self.store.get_session(demo_token).revoked)
        self.assertFalse(self.store.get_session(origin_token).revoked)
        # El overview vuelve a mostrar la empresa real.
        st, payload = c.get("/api/product/overview")
        self.assertEqual(payload["overview"]["company"]["company_id"],
                         self.comp_a.company_id)
        self.assertFalse(payload["overview"]["company"]["is_demo"])

    def test_audit_demo_exit(self):
        c = self._login("ownerademo@t.do")
        c.post("/api/demo/enter")
        before = len([e for e in self.store.list_audit(DEMO_COMPANY)
                      if e.action == "DEMO_EXIT"])
        c.post("/api/demo/exit")
        events = [e for e in self.store.list_audit(DEMO_COMPANY)
                  if e.action == "DEMO_EXIT"]
        self.assertEqual(len(events), before + 1)
        self.assertEqual(events[-1].result, "ok")

    def test_exit_with_expired_origin_asks_login(self):
        from tenant.auth import logout
        c = self._login("ownerademo@t.do")
        origin_token = c.cookie_token()
        c.post("/api/demo/enter")
        # La sesión origen expira (se revoca) mientras se está en la demo.
        logout(self.store, origin_token)
        st, payload = c.post("/api/demo/exit")
        self.assertEqual(st, 200)
        self.assertTrue(payload.get("login_required"))

    # ------------------------------------------------------- frontend estático
    def test_frontend_demo_button_no_longer_logs_out(self):
        src = open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "webapp", "static", "app.js"), encoding="utf-8").read()
        # El handler de demo-go ya no llama a /api/logout.
        idx = src.find('getElementById("demo-go")')
        self.assertGreater(idx, 0)
        handler = src[idx:idx + 1200]
        self.assertNotIn("/api/logout", handler)
        self.assertIn("/api/demo/enter", handler)
        self.assertIn('sessionStorage.setItem("zb_demo"', handler)
        # Botón de volver existe y llama a /api/demo/exit.
        idx2 = src.find('getElementById("demo-back")')
        self.assertGreater(idx2, 0)
        self.assertIn("/api/demo/exit", src[idx2:idx2 + 1200])
        self.assertIn('sessionStorage.removeItem("zb_demo"', src)

    def test_frontend_i18n_demo_keys(self):
        src = open(os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "webapp", "static", "i18n.js"), encoding="utf-8").read()
        for key in ("demo.return", "demo.enter.failed", "demo.exit.failed",
                    "demo.exit.session_expired"):
            self.assertIn('"%s"' % key, src)


if __name__ == "__main__":
    unittest.main()
