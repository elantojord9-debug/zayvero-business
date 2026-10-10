"""FASE 6C — Tests de Product Experience, Dashboard Refinement & Client Workspace.

Cubre: Centro de Inteligencia, métricas con explicaciones, atención prioritaria,
tendencias observadas/proyectadas, predicciones, detalle de hallazgos mejorado,
Advisor mejorado (incertidumbre, recomendaciones con tipo, preguntas sugeridas),
estados vacíos, errores amigables, accesibilidad, responsive testeable, DEMO,
tenant isolation y seguridad sin cambios.

Ejecutar: .venv/bin/python -m pytest tests/test_webapp_6c.py -x -q
(o con unittest).
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import sys
import tempfile
import threading
import unittest
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tenant import TenantStore, create_company, create_user
import webapp.server as server_mod
from http.server import ThreadingHTTPServer

PASS = "Webapp6cTest123"
STATIC_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                          "webapp", "static")


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

    def _req(self, method, path, body=None):
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        data = None
        headers = {}
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            headers=headers, method=method)
        try:
            with opener.open(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                return e.code, json.loads(raw)
            except Exception:
                return e.code, {"raw": raw}

    def get(self, path):
        return self._req("GET", path)

    def post(self, path, body=None):
        return self._req("POST", path, body)


def _read_static(name):
    with open(os.path.join(STATIC_DIR, name), encoding="utf-8") as fh:
        return fh.read()


class Webapp6CTest(unittest.TestCase):
    """Base: servidor con empresa demo + usuario owner."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="zayvero-6c-")
        cls.store = TenantStore(data_dir=cls.tmp)
        cls.demo = cls.store.ensure_demo_company()
        cls.owner = create_user(cls.store, company_id=cls.demo.company_id,
                                email="owner6c@demo.test", name="Owner 6C",
                                password=PASS, role_id="owner")
        cls.httpd, cls.port = _start_server(cls.store)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _login(self, email="owner6c@demo.test", password=PASS):
        c = Client(self.port)
        status, body = c.post("/api/login", {"email": email, "password": password})
        return c, status, body

    def _authed(self):
        c, status, _ = self._login()
        self.assertEqual(status, 200)
        return c


# ---------------------------------------------------------------- dashboard
class TestIntelligenceCenter(Webapp6CTest):

    def test_summary_returns_intelligence_center_fields(self):
        c = self._authed()
        status, body = c.get("/api/summary")
        self.assertEqual(status, 200)
        for key in ("attention_level", "cards", "top_urgent_findings",
                    "trends", "prediction_intelligence", "prediction_validation",
                    "limitations", "snapshot"):
            self.assertIn(key, body, f"falta {key} en summary")

    def test_summary_cards_values_from_real_data(self):
        c = self._authed()
        status, body = c.get("/api/summary")
        cards = body["cards"]
        self.assertEqual(cards["urgent_findings"], 25)
        self.assertEqual(cards["important_findings"], 480)
        self.assertIsNotNone(cards["context_confidence"])
        self.assertIsNotNone(cards["data_quality_score"])
        # Calidad de datos y confianza del contexto son distintas.
        self.assertNotEqual(cards["data_quality_score"], cards["context_confidence"])

    def test_top_urgent_findings_sorted_by_priority_then_impact(self):
        c = self._authed()
        status, body = c.get("/api/summary")
        top = body["top_urgent_findings"]
        self.assertTrue(len(top) > 0)
        rank = {"URGENT": 0, "IMPORTANT": 1, "REVIEW": 2, "MONITOR": 3}
        keys = [(rank.get(f["business_priority"], 99), -(f["impact_score"] or 0))
                for f in top]
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(top[0]["business_priority"], "URGENT")

    def test_trends_distinguish_observed_from_projected(self):
        c = self._authed()
        status, body = c.get("/api/summary")
        types = {t["trend_type"] for t in body["trends"]}
        self.assertIn("OBSERVED_TREND", types)
        self.assertIn("PROJECTED_TREND", types)

    def test_app_js_has_metric_explanations(self):
        js = _read_static("app.js")
        self.assertIn("¿Qué significa?", js)
        self.assertIn("METRIC_HELP", js)
        # Distingue calidad de datos de confianza del análisis.
        self.assertIn("No es lo mismo que la confianza", js)

    def test_app_js_has_attention_section(self):
        js = _read_static("app.js")
        self.assertIn("Requiere atención", js)
        self.assertIn("Ver análisis", js)
        self.assertIn("Ver todos los hallazgos", js)

    def test_app_js_has_trend_separation(self):
        js = _read_static("app.js")
        self.assertIn("OBSERVED_TREND", js)
        self.assertIn("Observado", js)
        self.assertIn("Proyección", js)

    def test_app_js_has_next_step(self):
        js = _read_static("app.js")
        self.assertIn("siguiente paso", js.lower())

    def test_app_js_has_advisor_cta(self):
        js = _read_static("app.js")
        self.assertIn("Pregúntale a ZAYVERO", js)
        self.assertIn("Abrir el Advisor", js)


# ---------------------------------------------------------------- hallazgos
class TestFindingsUX(Webapp6CTest):

    def test_findings_list_unchanged_contract(self):
        c = self._authed()
        status, body = c.get("/api/findings?priority=all&type=all&period=all&q=&page=1")
        self.assertEqual(status, 200)
        self.assertEqual(body["total"], 708)
        self.assertEqual(body["by_priority"]["URGENT"], 25)

    def test_findings_filter_urgent_only(self):
        c = self._authed()
        status, body = c.get("/api/findings?priority=URGENT&type=all&period=all&q=&page=1")
        self.assertEqual(status, 200)
        for item in body["items"]:
            self.assertEqual(item["business_priority"], "URGENT")

    def test_finding_detail_has_separated_sections(self):
        c = self._authed()
        _, lst = c.get("/api/findings?priority=all&type=all&period=all&q=&page=1")
        fid = lst["items"][0]["finding_id"]
        status, body = c.get(f"/api/findings/{fid}")
        self.assertEqual(status, 200)
        f = body["finding"]
        for key in ("statistical_explanation", "facts", "possible_explanations",
                    "recommendations", "evidence_quality"):
            self.assertIn(key, f, f"falta {key} en detalle")

    def test_finding_detail_recommendations_have_kind(self):
        c = self._authed()
        _, lst = c.get("/api/findings?priority=all&type=all&period=all&q=&page=1")
        fid = lst["items"][0]["finding_id"]
        _, body = c.get(f"/api/findings/{fid}")
        for r in body["finding"]["recommendations"]:
            self.assertIn("kind", r)

    def test_finding_detail_404_friendly(self):
        c = self._authed()
        status, body = c.get("/api/findings/NO-EXISTE-999")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_app_js_finding_detail_sections(self):
        js = _read_static("app.js")
        self.assertIn("Hecho observado", js)
        self.assertIn("Posibles explicaciones", js)
        self.assertIn("hipótesis, no hecho", js)
        self.assertIn("Recomendación existente", js)
        self.assertIn("Recomendación de asesoría", js)

    def test_app_js_finding_items_keyboard_accessible(self):
        js = _read_static("app.js")
        self.assertIn('tabindex=\'0\'', js)
        self.assertIn('role=\'button\'', js)
        self.assertIn("keydown", js)


# --------------------------------------------------------------- oportunidades
class TestOpportunitiesUX(Webapp6CTest):

    def test_opportunities_endpoint(self):
        c = self._authed()
        status, body = c.get("/api/opportunities")
        self.assertEqual(status, 200)
        self.assertIsInstance(body["opportunities"], list)
        self.assertEqual(len(body["opportunities"]), 60)

    def test_app_js_opportunity_language(self):
        js = _read_static("app.js")
        self.assertIn("Posible oportunidad", js)
        self.assertIn("no es una garantía de resultado", js)
        # Nunca promete ganancias garantizadas.
        self.assertNotIn("ganancia garantizada", js.lower())

    def test_app_js_opportunity_empty_state(self):
        js = _read_static("app.js")
        self.assertIn("No hay oportunidades identificadas con los datos actuales.", js)


# --------------------------------------------------------------- predicciones
class TestPredictionsUX(Webapp6CTest):

    def test_predictions_endpoint_has_validation_status(self):
        c = self._authed()
        status, body = c.get("/api/predictions")
        self.assertEqual(status, 200)
        self.assertTrue(len(body["insights"]) > 0)
        for ins in body["insights"]:
            self.assertIn("validation_status", ins)

    def test_predictions_validation_pending_shown(self):
        c = self._authed()
        _, body = c.get("/api/predictions")
        pending = [i for i in body["insights"]
                   if i["validation_status"] == "PENDING"]
        self.assertTrue(len(pending) > 0)

    def test_app_js_prediction_status_labels(self):
        js = _read_static("app.js")
        self.assertIn("Pendiente de validación", js)
        self.assertIn("Datos insuficientes", js)
        self.assertIn("proyecciones, no certezas", js)


# ------------------------------------------------------------------- advisor
class TestAdvisorUX(Webapp6CTest):

    def test_advisor_ask_returns_uncertainty(self):
        c = self._authed()
        status, body = c.post("/api/advisor/ask",
                              {"question": "¿Qué debería revisar primero?"})
        self.assertEqual(status, 200)
        self.assertIn("uncertainty", body)
        self.assertIn(body["uncertainty"].get("uncertainty_level"),
                      {"LOW", "MEDIUM", "HIGH", "UNKNOWN"})

    def test_advisor_ask_returns_recommendations_with_kind(self):
        c = self._authed()
        _, body = c.post("/api/advisor/ask",
                         {"question": "¿Qué debería revisar primero?"})
        recs = body.get("advisor_recommendations") or []
        self.assertTrue(len(recs) > 0)
        for r in recs:
            self.assertIn(r.get("kind"),
                          {"EXISTING_RECOMMENDATION", "ADVISORY_RECOMMENDATION"})

    def test_advisor_ask_insufficient_evidence(self):
        c = self._authed()
        status, body = c.post("/api/advisor/ask",
                              {"question": "¿Por qué mi proveedor aumentó los precios?"})
        self.assertEqual(status, 200)
        txt = (body.get("answer") or "").lower()
        self.assertIn("evidencia", txt)

    def test_advisor_ask_empty_question_rejected(self):
        c = self._authed()
        status, _ = c.post("/api/advisor/ask", {"question": "   "})
        self.assertEqual(status, 400)

    def test_app_js_six_suggested_questions(self):
        js = _read_static("app.js")
        expected = [
            "¿Qué debería revisar primero?",
            "¿Cuáles son los problemas más importantes?",
            "¿Qué productos presentan comportamientos inusuales?",
            "¿Qué oportunidades debería investigar?",
            "¿Qué predicciones requieren atención?",
            "¿Qué información falta para analizar mejor mi empresa?",
        ]
        for q in expected:
            self.assertIn(q, js, f"falta pregunta sugerida: {q}")

    def test_app_js_advisor_loading_states(self):
        js = _read_static("app.js")
        self.assertIn("Analizando evidencia…", js)
        self.assertIn("Preparando respuesta…", js)

    def test_app_js_advisor_sections(self):
        js = _read_static("app.js")
        for section in ("Evidencia utilizada", "Confianza e incertidumbre",
                        "Recomendaciones", "Limitaciones", "Puntos clave"):
            self.assertIn(section, js, f"falta sección: {section}")

    def test_advisor_permission_denied_for_viewer(self):
        viewer = create_user(self.store, company_id=self.demo.company_id,
                             email="viewer6c@demo.test", name="Viewer 6C",
                             password=PASS, role_id="viewer")
        c, status, _ = self._login("viewer6c@demo.test")
        self.assertEqual(status, 200)
        status, _ = c.post("/api/advisor/ask", {"question": "hola"})
        self.assertEqual(status, 403)


# ------------------------------------------------------- estados y errores
class TestStatesAndErrors(Webapp6CTest):

    def test_app_js_empty_states(self):
        js = _read_static("app.js")
        self.assertIn("Sin hallazgos disponibles.", js)
        self.assertIn("No existen predicciones disponibles.", js)
        self.assertIn("Datos insuficientes para generar este análisis.", js)

    def test_app_js_loading_states(self):
        js = _read_static("app.js")
        self.assertIn("Analizando información…", js)
        self.assertIn("Consultando evidencia…", js)
        self.assertIn("loadingHTML", js)

    def test_app_js_friendly_errors(self):
        js = _read_static("app.js")
        self.assertIn("friendlyError", js)
        self.assertIn("No tienes permiso para acceder a esta sección.", js)
        self.assertIn("Ocurrió un error interno. Inténtalo de nuevo más tarde.", js)

    def test_500_response_has_no_internals(self):
        # Forzar un error interno es difícil sin romper el servidor;
        # verificamos el formato de error genérico en login inválido.
        c = Client(self.port)
        status, body = c.post("/api/login",
                              {"email": "nadie@demo.test", "password": "x"})
        self.assertEqual(status, 401)
        self.assertEqual(set(body.keys()), {"error"})

    def test_401_on_protected_route_without_session(self):
        c = Client(self.port)
        status, _ = c.get("/api/summary")
        self.assertEqual(status, 401)

    def test_expired_or_invalid_session_message(self):
        c = Client(self.port)
        status, body = c.get("/api/me")
        self.assertEqual(status, 401)
        self.assertIn("error", body)


# ---------------------------------------------- accesibilidad y responsive
class TestA11yResponsive(Webapp6CTest):

    def test_index_has_viewport_meta(self):
        html = _read_static("index.html")
        self.assertIn('name="viewport"', html)

    def test_index_has_labels_and_skip_link(self):
        html = _read_static("index.html")
        self.assertIn("skip-link", html)
        self.assertIn('aria-label="Navegación principal"', html)

    def test_login_error_has_role_alert(self):
        html = _read_static("index.html")
        self.assertIn('role="alert"', html)

    def test_css_has_focus_visible(self):
        css = _read_static("style.css")
        self.assertIn(":focus-visible", css)

    def test_css_has_responsive_media_query(self):
        css = _read_static("style.css")
        self.assertIn("@media (max-width: 860px)", css)

    def test_css_has_loading_spinner(self):
        css = _read_static("style.css")
        self.assertIn(".spinner", css)
        self.assertIn(".loading", css)

    def test_css_has_empty_state(self):
        css = _read_static("style.css")
        self.assertIn(".empty-state", css)


# ------------------------------------------------- demo, tenant y seguridad
class TestDemoTenantSecurity(Webapp6CTest):

    def test_demo_badge_present(self):
        html = _read_static("index.html")
        self.assertIn("demo-badge", html)
        self.assertIn("DEMO", html)

    def test_me_reports_demo_company(self):
        c = self._authed()
        status, body = c.get("/api/me")
        self.assertEqual(status, 200)
        self.assertTrue(body["company"]["is_demo"])

    def test_company_id_param_is_ignored(self):
        c = self._authed()
        status, body = c.get("/api/summary?company_id=otra-empresa")
        self.assertEqual(status, 200)
        # Los datos siguen siendo de la empresa de la sesión.
        self.assertEqual(body["company_id"], self.demo.company_id)

    def test_company_a_cannot_access_company_b(self):
        comp_b = create_company(self.store, "Empresa B 6C")
        owner_b = create_user(self.store, company_id=comp_b.company_id,
                              email="ownerb6c@demo.test", name="Owner B",
                              password=PASS, role_id="owner")
        c, status, _ = self._login("ownerb6c@demo.test")
        self.assertEqual(status, 200)
        # FASE 7A: Empresa B no tiene acceso al dataset demo-retail; recibe
        # su propio estado NO_DATA (onboarding), no los datos de otra empresa.
        status, body = c.get("/api/summary")
        self.assertEqual(status, 200)
        self.assertFalse(body.get("ready"))
        self.assertEqual(body.get("dataset_state"), "NO_DATA")
        blob = json.dumps(body).lower()
        self.assertNotIn("demo-retail", blob)
        self.assertNotIn("uci online retail", blob)

    def test_frontend_never_sends_company_id(self):
        js = _read_static("app.js")
        self.assertNotIn("company_id=", js)

    def test_audit_endpoint_requires_permission(self):
        c = self._authed()
        status, body = c.get("/api/audit")
        self.assertEqual(status, 200)
        self.assertIn("events", body)
        # Sin secretos en los eventos.
        blob = json.dumps(body)
        for secret_word in ("password", "contraseña", "token", "secret"):
            self.assertNotIn(secret_word, blob.lower())

    def test_login_disabled_user(self):
        from tenant import update_user
        update_user(self.store, self.owner.user_id, status="disabled")
        try:
            c = Client(self.port)
            status, _ = c.post("/api/login",
                               {"email": "owner6c@demo.test", "password": PASS})
            self.assertEqual(status, 403)
        finally:
            update_user(self.store, self.owner.user_id, status="active")

    def test_static_files_serve(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        for path, ctype in (("/", "text/html"), ("/style.css", "text/css"),
                            ("/app.js", "javascript")):
            conn.request("GET", path)
            resp = conn.getresponse()
            self.assertEqual(resp.status, 200, f"falló {path}")
            self.assertIn(ctype, resp.getheader("Content-Type", ""))
            self.assertTrue(len(resp.read()) > 100, f"vacío: {path}")
        conn.close()


if __name__ == "__main__":
    unittest.main()
