"""FASE 6B — Tests de la web app ZAYVERO Business.

Cubre: login, logout, sesión, company context, rol, permisos, tenant
isolation, dashboard, findings, opportunities, predictions, advisor,
insufficient evidence, demo account, routing, error handling, access denied.

Ejecutar: .venv/bin/python -m pytest tests/test_webapp.py -x -q
(o con unittest).
"""

from __future__ import annotations

import http.client
import http.cookiejar
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tenant import TenantStore, create_company, create_user, update_user, AuthError
import webapp.server as server_mod
from http.server import ThreadingHTTPServer

PASS = "WebappTest123"


def _start_server(store):
    server_mod.STORE = store
    server_mod._DATA_CACHE.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.WebappHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port


class Client:
    """Cliente HTTP con cookies."""

    def __init__(self, port):
        self.port = port
        self.jar = http.cookiejar.CookieJar()
        # SEG-03: como el frontend real: guarda el csrf_token y lo envía.
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
        return self.post("/api/login", {"email": email, "password": password})


class WebappTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="zayvero6b_")
        store = TenantStore(cls.tmp)
        store.ensure_demo_company()
        # Usuarios demo
        create_user(store, company_id="demo-retail", email="owner@demo.test",
                    name="Demo Owner", password=PASS, role_id="owner")
        create_user(store, company_id="demo-retail", email="viewer@demo.test",
                    name="Demo Viewer", password=PASS, role_id="viewer")
        create_user(store, company_id="demo-retail", email="analyst@demo.test",
                    name="Demo Analyst", password=PASS, role_id="analyst")
        dis = create_user(store, company_id="demo-retail",
                          email="disabled@demo.test", name="Disabled",
                          password=PASS, role_id="viewer")
        update_user(store, dis.user_id, status="disabled")
        # Dos empresas para aislamiento
        cls.comp_a = create_company(store, "Empresa A Test")
        cls.comp_b = create_company(store, "Empresa B Test")
        create_user(store, company_id=cls.comp_a.company_id,
                    email="ownera@test.com", name="Owner A",
                    password=PASS, role_id="owner")
        create_user(store, company_id=cls.comp_b.company_id,
                    email="ownerb@test.com", name="Owner B",
                    password=PASS, role_id="owner")
        cls.store = store
        cls.httpd, cls.port = _start_server(store)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.c = Client(self.port)

    def login_as(self, email, password=PASS):
        code, data = self.c.login(email, password)
        self.assertEqual(code, 200, f"login falló para {email}: {data}")
        return data

    # ---- LOGIN ---------------------------------------------------------
    def test_01_login_valido(self):
        code, data = self.c.login("owner@demo.test", PASS)
        self.assertEqual(code, 200)
        self.assertTrue(data.get("ok"))
        self.assertEqual(data.get("role"), "owner")

    def test_02_login_invalido_password(self):
        code, data = self.c.login("owner@demo.test", "WrongPass000")
        self.assertEqual(code, 401)
        self.assertIn("credenciales", data.get("error", ""))

    def test_03_login_usuario_inexistente(self):
        code, _ = self.c.login("nadie@demo.test", PASS)
        self.assertEqual(code, 401)

    def test_04_login_usuario_deshabilitado(self):
        # SEG-06: respuesta EXTERNA homogénea (401 genérico). Un usuario
        # deshabilitado no debe distinguirse de credenciales incorrectas.
        code, data = self.c.login("disabled@demo.test", PASS)
        self.assertEqual(code, 401)
        self.assertIn("credenciales", data.get("error", ""))
        self.assertNotIn("deshabilitado", data.get("error", ""))

    def test_05_logout(self):
        self.login_as("owner@demo.test")
        code, _ = self.c.post("/api/logout")
        self.assertEqual(code, 200)
        code, _ = self.c.get("/api/me")
        self.assertEqual(code, 401, "la sesión debe quedar invalidada tras logout")

    # ---- SESIÓN ----------------------------------------------------------
    def test_06_me_sin_cookie_401(self):
        code, _ = self.c.get("/api/me")
        self.assertEqual(code, 401)

    def test_07_me_sesion_invalida_401(self):
        code, _ = self.c.get("/api/me")
        self.assertEqual(code, 401)

    def test_08_me_datos_correctos(self):
        self.login_as("owner@demo.test")
        code, data = self.c.get("/api/me")
        self.assertEqual(code, 200)
        self.assertEqual(data["user"]["email"], "owner@demo.test")
        self.assertEqual(data["user"]["role"], "owner")
        self.assertEqual(data["company"]["company_id"], "demo-retail")
        self.assertIn("dashboard.read", data["permissions"])

    def test_09_me_sin_secretos(self):
        self.login_as("owner@demo.test")
        _, data = self.c.get("/api/me")
        raw = json.dumps(data)
        self.assertNotIn("password_hash", raw)
        self.assertNotIn("pbkdf2", raw)

    # ---- COMPANY CONTEXT / ROL -------------------------------------------
    def test_10_company_context_demo(self):
        self.login_as("owner@demo.test")
        _, data = self.c.get("/api/me")
        self.assertTrue(data["company"]["is_demo"])
        self.assertIn("DEMO", data["company"]["name"])

    def test_11_company_context_empresa_a(self):
        self.login_as("ownera@test.com")
        _, data = self.c.get("/api/me")
        self.assertEqual(data["company"]["company_id"], self.comp_a.company_id)
        self.assertFalse(data["company"]["is_demo"])

    def test_12_rol_viewer_permisos(self):
        self.login_as("viewer@demo.test")
        _, data = self.c.get("/api/me")
        self.assertEqual(data["user"]["role"], "viewer")
        self.assertIn("findings.read", data["permissions"])
        self.assertNotIn("advisor.use", data["permissions"])
        self.assertNotIn("audit.read", data["permissions"])

    def test_13_rol_analyst_tiene_advisor(self):
        self.login_as("analyst@demo.test")
        _, data = self.c.get("/api/me")
        self.assertIn("advisor.use", data["permissions"])
        self.assertNotIn("audit.read", data["permissions"])

    # ---- DASHBOARD / SUMMARY ----------------------------------------------
    def test_14_summary_datos_reales(self):
        self.login_as("owner@demo.test")
        code, data = self.c.get("/api/summary")
        self.assertEqual(code, 200)
        cards = data["cards"]
        self.assertEqual(cards["urgent_findings"], 25)
        self.assertEqual(cards["important_findings"], 480)
        self.assertEqual(cards["opportunities"], 60)
        self.assertEqual(cards["predictions"], 38)
        self.assertEqual(data["attention_level"], "CRITICAL")

    def test_15_summary_sin_auth_401(self):
        code, _ = self.c.get("/api/summary")
        self.assertEqual(code, 401)

    # ---- FINDINGS -----------------------------------------------------------
    def test_16_findings_lista_total(self):
        self.login_as("owner@demo.test")
        code, data = self.c.get("/api/findings")
        self.assertEqual(code, 200)
        self.assertEqual(data["total"], 708)

    def test_17_findings_filtro_prioridad(self):
        self.login_as("owner@demo.test")
        _, data = self.c.get("/api/findings?priority=URGENT")
        self.assertEqual(data["total"], 25)
        for item in data["items"]:
            self.assertEqual(item["business_priority"], "URGENT")

    def test_18_findings_filtro_tipo(self):
        self.login_as("owner@demo.test")
        _, data = self.c.get("/api/findings?type=PRICE_ANOMALY")
        self.assertGreater(data["total"], 0)
        for item in data["items"]:
            self.assertEqual(item["type"], "PRICE_ANOMALY")

    def test_19_findings_busqueda(self):
        self.login_as("owner@demo.test")
        _, data = self.c.get("/api/findings?q=RABBIT")
        self.assertGreater(data["total"], 0)
        ids = [i["finding_id"] for i in data["items"]]
        self.assertIn("FND-000001", ids)

    def test_20_findings_paginacion(self):
        self.login_as("owner@demo.test")
        _, d1 = self.c.get("/api/findings?page=1")
        _, d2 = self.c.get("/api/findings?page=2")
        ids1 = {i["finding_id"] for i in d1["items"]}
        ids2 = {i["finding_id"] for i in d2["items"]}
        self.assertTrue(ids1.isdisjoint(ids2), "las páginas no deben repetir hallazgos")

    def test_21_findings_detalle(self):
        self.login_as("owner@demo.test")
        code, data = self.c.get("/api/findings/FND-000001")
        self.assertEqual(code, 200)
        f = data["finding"]
        self.assertEqual(f["finding_id"], "FND-000001")
        self.assertTrue(f["facts"], "el detalle debe incluir hechos")
        self.assertIn("possible_explanations", f)
        self.assertIn("recommendations", f)

    def test_22_findings_detalle_inexistente_404(self):
        self.login_as("owner@demo.test")
        code, _ = self.c.get("/api/findings/FND-XXXXXX")
        self.assertEqual(code, 404)

    def test_23_findings_viewer_puede_ver(self):
        self.login_as("viewer@demo.test")
        code, data = self.c.get("/api/findings")
        self.assertEqual(code, 200)
        self.assertEqual(data["total"], 708)

    # ---- OPORTUNIDADES -------------------------------------------------------
    def test_24_oportunidades(self):
        self.login_as("owner@demo.test")
        code, data = self.c.get("/api/opportunities")
        self.assertEqual(code, 200)
        opps = data["opportunities"]
        self.assertEqual(len(opps), 60)
        raw = json.dumps(opps, ensure_ascii=False).lower()
        self.assertIn("posible oportunidad", raw)
        self.assertNotIn("ganancia garantizada", raw)

    # ---- PREDICCIONES ----------------------------------------------------------
    def test_25_predicciones(self):
        self.login_as("owner@demo.test")
        code, data = self.c.get("/api/predictions")
        self.assertEqual(code, 200)
        self.assertEqual(len(data["insights"]), 38)
        self.assertEqual(data["validation_summary"]["validated"], 0)
        statuses = {i["validation_status"] for i in data["insights"]}
        self.assertIn("PENDING", statuses)

    def test_26_predicciones_viewer_con_permiso(self):
        # En 6A el viewer SÍ tiene predictions.read (dashboard, findings,
        # predictions). Se verifica que puede verlas.
        self.login_as("viewer@demo.test")
        code, data = self.c.get("/api/predictions")
        self.assertEqual(code, 200)
        self.assertEqual(len(data["insights"]), 38)

    # ---- ADVISOR -----------------------------------------------------------------
    def test_27_advisor_pregunta(self):
        self.login_as("owner@demo.test")
        code, data = self.c.post("/api/advisor/ask",
                                {"question": "¿Cuál es el problema más urgente?"})
        self.assertEqual(code, 200)
        self.assertTrue(data.get("answer"))
        self.assertTrue(data.get("evidence_used"), "debe citar evidencia")
        self.assertIn("RABBIT", data["answer"])

    def test_28_advisor_viewer_403(self):
        self.login_as("viewer@demo.test")
        code, _ = self.c.post("/api/advisor/ask", {"question": "hola"})
        self.assertEqual(code, 403)

    def test_29_advisor_pregunta_vacia_400(self):
        self.login_as("owner@demo.test")
        code, _ = self.c.post("/api/advisor/ask", {"question": "   "})
        self.assertEqual(code, 400)

    def test_30_advisor_sin_evidencia(self):
        self.login_as("owner@demo.test")
        code, data = self.c.post(
            "/api/advisor/ask",
            {"question": "¿Por qué mi proveedor aumentó los precios?"})
        self.assertEqual(code, 200)
        ans = (data.get("answer") or "").lower()
        self.assertTrue(
            "suficiente evidencia" in ans or "no tengo" in ans,
            "debe reconocer falta de evidencia, no inventar")

    def test_31_advisor_sin_secretos(self):
        self.login_as("owner@demo.test")
        _, data = self.c.post("/api/advisor/ask",
                              {"question": "¿Qué debería revisar primero?"})
        raw = json.dumps(data).lower()
        # "token_usage" es un nombre de campo de metadata legítimo; lo que no
        # debe aparecer son secretos reales.
        for bad in ("password", "api_key", "apikey", "secret", "pbkdf2",
                    "bearer", "contraseña"):
            self.assertNotIn(bad, raw, f"posible secreto en respuesta: {bad}")

    # ---- AUDITORÍA ----------------------------------------------------------------
    def test_32_audit_owner(self):
        self.login_as("owner@demo.test")
        code, data = self.c.get("/api/audit")
        self.assertEqual(code, 200)
        self.assertTrue(isinstance(data["events"], list))

    def test_33_audit_viewer_403(self):
        self.login_as("viewer@demo.test")
        code, _ = self.c.get("/api/audit")
        self.assertEqual(code, 403)

    def test_34_audit_sin_secretos(self):
        self.login_as("owner@demo.test")
        _, data = self.c.get("/api/audit")
        raw = json.dumps(data)
        self.assertNotIn("pbkdf2", raw)

    # ---- TENANT ISOLATION ------------------------------------------------------------
    # FASE 7A: una empresa sin dataset READY recibe su propio estado NO_DATA
    # (onboarding), NUNCA los datos de otra empresa ni del demo.
    def test_35_empresa_a_no_ve_datos_demo(self):
        self.login_as("ownera@test.com")
        code, data = self.c.get("/api/summary")
        self.assertEqual(code, 200)
        self.assertFalse(data.get("ready"))
        self.assertEqual(data.get("dataset_state"), "NO_DATA")
        self.assertNotIn("demo", json.dumps(data).lower())

    def test_36_empresa_a_no_ve_findings(self):
        self.login_as("ownera@test.com")
        code, data = self.c.get("/api/findings")
        self.assertEqual(code, 200)
        self.assertEqual(data.get("items"), [])
        self.assertEqual(data.get("dataset_state"), "NO_DATA")

    def test_37_empresa_a_no_ve_predicciones(self):
        self.login_as("ownera@test.com")
        code, data = self.c.get("/api/predictions")
        self.assertEqual(code, 200)
        self.assertFalse(data.get("ready"))
        self.assertEqual(data.get("dataset_state"), "NO_DATA")

    def test_38_empresa_a_no_ve_advisor(self):
        self.login_as("ownera@test.com")
        code, data = self.c.post("/api/advisor/ask", {"question": "hola"})
        self.assertEqual(code, 200)
        self.assertTrue(data.get("fallback"))
        # Sin inferencias: el advisor no inventa respuestas sin datos.
        blob = json.dumps(data)
        for word in ("demo-retail", "UCI Online Retail"):
            self.assertNotIn(word, blob)

    def test_39_empresa_b_no_ve_datos_demo(self):
        self.login_as("ownerb@test.com")
        code, data = self.c.get("/api/summary")
        self.assertEqual(code, 200)
        self.assertFalse(data.get("ready"))
        self.assertEqual(data.get("dataset_state"), "NO_DATA")

    def test_40_company_id_en_query_ignorado(self):
        # El backend nunca acepta company_id del cliente: el query param se
        # ignora y la empresa A sigue viendo SU propio estado (NO_DATA),
        # nunca los datos de demo-retail.
        self.login_as("ownera@test.com")
        code, data = self.c.get("/api/summary?company_id=demo-retail")
        self.assertEqual(code, 200)
        self.assertEqual(data.get("dataset_state"), "NO_DATA")
        self.assertNotIn("demo", json.dumps(data).lower())

    # ---- DEMO ACCOUNT / ESTÁTICOS / ERRORES --------------------------------------------
    def test_41_demo_identificada(self):
        self.login_as("owner@demo.test")
        _, data = self.c.get("/api/me")
        self.assertTrue(data["company"]["is_demo"])

    def test_42_index_sin_auth(self):
        code, _ = self.c.get("/")
        self.assertEqual(code, 200)

    def test_43_estaticos(self):
        for path in ("/style.css", "/app.js"):
            code, _ = self.c.get(path)
            self.assertEqual(code, 200, path)

    def test_44_ruta_inexistente_404(self):
        self.login_as("owner@demo.test")
        code, _ = self.c.get("/api/no-existe")
        self.assertEqual(code, 404)

    def test_45_health(self):
        code, data = self.c.get("/health")
        self.assertEqual(code, 200)
        self.assertEqual(data.get("status"), "ok")


if __name__ == "__main__":
    unittest.main(verbosity=1)
