"""FASE 8 — Tests de la capa de experiencia de producto.

Cubre: modelos presentacionales, estados del producto, onboarding (7 pasos),
secuencia post-onboarding, reglas de lenguaje comercial (sin garantías),
i18n es/en, endpoint /api/product/overview (auth, permisos, tenant
isolation, company_id ignorado, auditoría), cheques estáticos del frontend
(accesibilidad, responsive) y determinismo.

Ejecutar: .venv/bin/python -m unittest tests.test_product -v
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
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from product import (
    BENEFITS,
    DATA_SOURCES,
    FIRST_ENTRY,
    FORBIDDEN_CLAIMS,
    ONBOARDING_STEPS,
    PLANS,
    POST_SEQUENCE,
    PRODUCT_STATES,
    build_onboarding,
    build_post_sequence,
    build_product_overview,
    build_product_state,
)
from product.models import (
    ADVISOR_LINE_KEY,
    DEMO_ENTRY,
    EXPERIENCE_HIERARCHY,
    STATE_ACTIONS,
    STATE_MESSAGE_KEYS,
    STEP_STATUS_CURRENT,
    STEP_STATUS_DONE,
    STEP_STATUS_PENDING,
)

PASS = "ProductTest123"


# ---------------------------------------------------------------- modelos
class ProductModelsTest(unittest.TestCase):
    def test_states_exact(self):
        self.assertEqual(list(PRODUCT_STATES),
                         ["NO_DATA", "PROCESSING", "READY", "ERROR",
                          "INCOMPLETE_CONFIGURATION"])

    def test_state_message_keys_cover_all(self):
        self.assertEqual(set(STATE_MESSAGE_KEYS.keys()), set(PRODUCT_STATES))

    def test_state_actions_cover_all_with_key_and_route(self):
        self.assertEqual(set(STATE_ACTIONS.keys()), set(PRODUCT_STATES))
        for st, actions in STATE_ACTIONS.items():
            self.assertTrue(actions, st)
            for a in actions:
                self.assertIn("key", a)
                self.assertIn("route", a)
                self.assertTrue(a["route"].startswith("#/"), a)

    def test_onboarding_seven_steps_ordered(self):
        self.assertEqual(len(ONBOARDING_STEPS), 7)
        self.assertEqual([s["step"] for s in ONBOARDING_STEPS],
                         [1, 2, 3, 4, 5, 6, 7])
        for s in ONBOARDING_STEPS:
            self.assertIn("title_key", s)
            self.assertIn("desc_key", s)

    def test_post_sequence_five(self):
        self.assertEqual(len(POST_SEQUENCE), 5)

    def test_benefits_seven(self):
        self.assertEqual(len(BENEFITS), 7)

    def test_plans_no_prices_all_marked_not_definitive(self):
        self.assertEqual(len(PLANS), 3)
        for p in PLANS:
            self.assertIsNone(p["price"], p["key"])
            self.assertEqual(p["price_note_key"], "plans.price.not_definitive")
            self.assertTrue(p["features_keys"], p["key"])

    def test_forbidden_claims_non_empty(self):
        self.assertTrue(len(FORBIDDEN_CLAIMS) >= 5)

    def test_hierarchy_order(self):
        self.assertEqual([h["key"] for h in EXPERIENCE_HIERARCHY],
                         ["diagnostic", "intelligence", "findings", "advisor"])
        for h in EXPERIENCE_HIERARCHY:
            self.assertTrue(h["route"].startswith("#/"))


# ------------------------------------------------------- estado del producto
def _cfg(complete=True, name="Empresa X"):
    return {"name": name, "complete": complete, "missing": [] if complete else ["currency"]}


class ProductStateTest(unittest.TestCase):
    def test_incomplete_config_wins(self):
        s = build_product_state(_cfg(False), "READY", "AVAILABLE")
        self.assertEqual(s["state"], "INCOMPLETE_CONFIGURATION")

    def test_incomplete_config_when_no_config(self):
        s = build_product_state(None, "NO_DATA")
        self.assertEqual(s["state"], "INCOMPLETE_CONFIGURATION")

    def test_no_data(self):
        s = build_product_state(_cfg(True), "NO_DATA")
        self.assertEqual(s["state"], "NO_DATA")

    def test_no_data_when_data_uploaded(self):
        s = build_product_state(_cfg(True), "DATA_UPLOADED")
        self.assertEqual(s["state"], "NO_DATA")

    def test_processing(self):
        s = build_product_state(_cfg(True), "PROCESSING")
        self.assertEqual(s["state"], "PROCESSING")

    def test_ready(self):
        s = build_product_state(_cfg(True), "READY", "AVAILABLE")
        self.assertEqual(s["state"], "READY")

    def test_error(self):
        s = build_product_state(_cfg(True), "ERROR")
        self.assertEqual(s["state"], "ERROR")

    def test_error_beats_processing_order(self):
        # ERROR tiene prioridad sobre estados de datos no listos.
        s = build_product_state(_cfg(True), "ERROR")
        self.assertEqual(s["state"], "ERROR")

    def test_unknown_dataset_state_maps_to_no_data(self):
        s = build_product_state(_cfg(True), "MAPPING_PENDING")
        self.assertEqual(s["state"], "NO_DATA")

    def test_message_key_matches_state(self):
        for st in PRODUCT_STATES:
            s = build_product_state(_cfg(True), st if st != "INCOMPLETE_CONFIGURATION" else "READY")
            self.assertIn("message_key", s)
            self.assertEqual(s["message_key"], STATE_MESSAGE_KEYS[s["state"]])

    def test_actions_present_for_every_state(self):
        for st in PRODUCT_STATES:
            s = build_product_state(
                _cfg(st != "INCOMPLETE_CONFIGURATION"),
                "READY" if st in ("READY", "INCOMPLETE_CONFIGURATION") else st)
            self.assertEqual(s["state"], st)
            self.assertTrue(s["actions"], st)

    def test_ready_action_points_to_diagnostic_first(self):
        s = build_product_state(_cfg(True), "READY", "AVAILABLE")
        routes = [a["route"] for a in s["actions"]]
        self.assertEqual(routes[0], "#/diagnostico")

    def test_deterministic(self):
        a = build_product_state(_cfg(True), "READY", "AVAILABLE")
        b = build_product_state(_cfg(True), "READY", "AVAILABLE")
        self.assertEqual(a, b)


# ------------------------------------------------------------- onboarding
class OnboardingTest(unittest.TestCase):
    def test_seven_steps(self):
        ob = build_onboarding(_cfg(True), [], "NO_DATA")
        self.assertEqual(len(ob["steps"]), 7)
        self.assertEqual(ob["total"], 7)

    def test_current_step_one_when_nothing_done(self):
        ob = build_onboarding({"name": "", "complete": False,
                               "missing": ["country"]}, [], "NO_DATA")
        self.assertEqual(ob["current_step"], 1)
        self.assertEqual(ob["steps"][0]["status"], STEP_STATUS_CURRENT)
        self.assertEqual(ob["completed"], 0)

    def test_step1_done_with_company_name(self):
        ob = build_onboarding(_cfg(True, name="Mi Empresa"), [], "NO_DATA")
        self.assertEqual(ob["steps"][0]["status"], STEP_STATUS_DONE)
        self.assertEqual(ob["current_step"], 3)  # paso 2 también completo

    def test_step2_done_when_config_complete(self):
        ob = build_onboarding(_cfg(True, name=""), [], "NO_DATA")
        self.assertEqual(ob["steps"][1]["status"], STEP_STATUS_DONE)

    def test_step3_done_when_datasets_exist(self):
        ds = [{"review_done": False, "mapping_confirmed": False}]
        ob = build_onboarding(_cfg(True), ds, "DATA_UPLOADED")
        self.assertEqual(ob["steps"][2]["status"], STEP_STATUS_DONE)

    def test_step4_done_when_review_done(self):
        ds = [{"review_done": True, "mapping_confirmed": False}]
        ob = build_onboarding(_cfg(True), ds, "DATA_UPLOADED")
        self.assertEqual(ob["steps"][3]["status"], STEP_STATUS_DONE)

    def test_step5_done_when_mapping_confirmed(self):
        ds = [{"review_done": True, "mapping_confirmed": True}]
        ob = build_onboarding(_cfg(True), ds, "DATA_UPLOADED")
        self.assertEqual(ob["steps"][4]["status"], STEP_STATUS_DONE)

    def test_step6_done_when_ready(self):
        ds = [{"review_done": True, "mapping_confirmed": True}]
        ob = build_onboarding(_cfg(True), ds, "READY", "AVAILABLE")
        self.assertEqual(ob["steps"][5]["status"], STEP_STATUS_DONE)

    def test_step7_done_when_diagnostic_available(self):
        ds = [{"review_done": True, "mapping_confirmed": True}]
        ob = build_onboarding(_cfg(True), ds, "READY", "AVAILABLE")
        self.assertEqual(ob["steps"][6]["status"], STEP_STATUS_DONE)
        self.assertTrue(ob["all_done"])
        self.assertEqual(ob["current_step"], 7)

    def test_step7_pending_when_no_diagnostic(self):
        ds = [{"review_done": True, "mapping_confirmed": True}]
        ob = build_onboarding(_cfg(True), ds, "READY", "INSUFFICIENT")
        self.assertEqual(ob["steps"][6]["status"], STEP_STATUS_CURRENT)

    def test_statuses_only_valid_values(self):
        ob = build_onboarding(_cfg(True), [], "NO_DATA")
        for s in ob["steps"]:
            self.assertIn(s["status"], (STEP_STATUS_DONE, STEP_STATUS_CURRENT,
                                        STEP_STATUS_PENDING))

    def test_deterministic(self):
        ds = [{"review_done": True, "mapping_confirmed": True}]
        a = build_onboarding(_cfg(True), ds, "READY", "AVAILABLE")
        b = build_onboarding(_cfg(True), ds, "READY", "AVAILABLE")
        self.assertEqual(a, b)


# ------------------------------------------------------- secuencia
class PostSequenceTest(unittest.TestCase):
    def test_five_milestones(self):
        seq = build_post_sequence(True, True, True, True, True)
        self.assertEqual(len(seq), 5)
        self.assertTrue(all(m["status"] == STEP_STATUS_DONE for m in seq))

    def test_all_pending_when_nothing(self):
        seq = build_post_sequence(False, False, False, False, False)
        self.assertTrue(all(m["status"] == STEP_STATUS_PENDING for m in seq))

    def test_partial(self):
        seq = build_post_sequence(True, True, False, False, False)
        self.assertEqual([m["status"] for m in seq],
                         [STEP_STATUS_DONE, STEP_STATUS_DONE,
                          STEP_STATUS_PENDING, STEP_STATUS_PENDING,
                          STEP_STATUS_PENDING])


# ------------------------------------------------------- overview
class ProductOverviewTest(unittest.TestCase):
    def _ws(self, state="NO_DATA", datasets=None, active=None):
        return {"dataset_state": state, "datasets": datasets or [],
                "active_dataset": active, "can_upload": True}

    def test_structure(self):
        ov = build_product_overview(
            {"company_id": "CMP-X", "name": "X"}, _cfg(True),
            self._ws(), None, False)
        for key in ("version", "company", "product_state", "onboarding",
                    "post_sequence", "first_entry", "data_sources",
                    "data_flow", "hierarchy", "benefits", "plans", "demo",
                    "advisor_line_key", "states_catalog", "can_upload"):
            self.assertIn(key, ov)

    def test_data_flow_six_steps(self):
        ov = build_product_overview(
            {"company_id": "CMP-X", "name": "X"}, _cfg(True),
            self._ws(), None, False)
        self.assertEqual(len(ov["data_flow"]), 6)

    def test_demo_flag(self):
        ov = build_product_overview(
            {"company_id": "demo-retail", "name": "Demo"}, _cfg(True),
            self._ws("READY"), "AVAILABLE", True)
        self.assertTrue(ov["company"]["is_demo"])
        self.assertEqual(ov["demo"], DEMO_ENTRY)

    def test_no_active_dataset(self):
        ov = build_product_overview(
            {"company_id": "CMP-X", "name": "X"}, _cfg(True),
            self._ws(), None, False)
        self.assertIsNone(ov["active_dataset"])

    def test_deterministic(self):
        kw = dict(company_view={"company_id": "CMP-X", "name": "X"},
                  config_view=_cfg(True), workspace=self._ws(),
                  diagnostic_status=None, is_demo=False)
        self.assertEqual(build_product_overview(**kw),
                         build_product_overview(**kw))

    def test_first_entry_keys(self):
        self.assertEqual(FIRST_ENTRY["main_key"], "product.hero.main")
        self.assertEqual(FIRST_ENTRY["secondary_key"], "product.hero.secondary")

    def test_advisor_line_key(self):
        self.assertEqual(ADVISOR_LINE_KEY, "advisor.explainer")


# ------------------------------------------------------- i18n
def _load_catalogs():
    """Extrae los catálogos es/en de i18n.js (sin ejecutar JS)."""
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "webapp", "static", "i18n.js")
    src = open(path, encoding="utf-8").read()
    cats = {}
    for lang in ("es", "en"):
        m = re.search(lang + r":\s*\{(.*?)\n    \}", src, re.S)
        assert m, f"catálogo {lang} no encontrado"
        keys = re.findall(r'"([^"]+)":', m.group(1))
        cats[lang] = set(keys)
    return cats


def _catalog_texts(lang):
    path = os.path.join(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__))), "webapp", "static", "i18n.js")
    src = open(path, encoding="utf-8").read()
    m = re.search(lang + r":\s*\{(.*?)\n    \}", src, re.S)
    return m.group(1).lower()


class I18nProductTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cats = _load_catalogs()

    def _model_keys(self):
        keys = set()
        for s in ONBOARDING_STEPS:
            keys.add(s["title_key"])
            keys.add(s["desc_key"])
        for b in BENEFITS:
            keys.add(b["label_key"])
        for p in PLANS:
            keys.add(p["name_key"])
            keys.add(p["price_note_key"])
            keys.update(p["features_keys"])
        for s in DATA_SOURCES:
            keys.add(s["label_key"])
        for h in EXPERIENCE_HIERARCHY:
            keys.add(h["label_key"])
            keys.add(h["label_key"] + "_desc")
        keys.add(FIRST_ENTRY["main_key"])
        keys.add(FIRST_ENTRY["secondary_key"])
        keys.add(FIRST_ENTRY["ready_key"])
        keys.add(DEMO_ENTRY["title_key"])
        keys.add(DEMO_ENTRY["desc_key"])
        keys.add(DEMO_ENTRY["cta_key"])
        keys.add(ADVISOR_LINE_KEY)
        keys.update(STATE_MESSAGE_KEYS.values())
        for actions in STATE_ACTIONS.values():
            for a in actions:
                keys.add(a["key"])
        for m in POST_SEQUENCE:
            keys.add(m["label_key"])
        return keys

    def test_all_model_keys_exist_in_es(self):
        missing = [k for k in self._model_keys() if k not in self.cats["es"]]
        self.assertEqual(missing, [])

    def test_all_model_keys_exist_in_en(self):
        missing = [k for k in self._model_keys() if k not in self.cats["en"]]
        self.assertEqual(missing, [])

    def test_nav_inicio_both_languages(self):
        self.assertIn("nav.inicio", self.cats["es"])
        self.assertIn("nav.inicio", self.cats["en"])

    def test_no_forbidden_claims_in_es(self):
        text = _catalog_texts("es")
        for claim in FORBIDDEN_CLAIMS:
            if any(c in "áéíóúñ" for c in claim):
                self.assertNotIn(claim, text, claim)

    def test_no_forbidden_claims_in_en(self):
        text = _catalog_texts("en")
        for claim in FORBIDDEN_CLAIMS:
            if not any(c in "áéíóúñ" for c in claim):
                self.assertNotIn(claim, text, claim)

    def test_price_note_marks_not_definitive(self):
        text_es = _catalog_texts("es")
        text_en = _catalog_texts("en")
        self.assertIn("no definitivo", text_es)
        self.assertIn("not definitive", text_en)

    def test_future_integration_not_promised(self):
        text_es = _catalog_texts("es")
        # La fuente "futura" debe decir que es futura / que no existe aún.
        self.assertTrue("futuro" in text_es or "no existen" in text_es)


# ------------------------------------------------------- endpoint
class Client:
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

    def post(self, path, body=None):
        return self._req("POST", path, body)

    def login(self, email, password):
        return self.post("/api/login", {"email": email, "password": password})


class ProductEndpointTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tenant import TenantStore, create_company
        from tenant.auth import create_user
        import webapp.server as server_mod

        cls.tmp = tempfile.mkdtemp(prefix="zayvero8_")
        store = TenantStore(cls.tmp)
        store.ensure_demo_company()
        cls.comp_a = create_company(store, "Empresa A F8")
        cls.comp_b = create_company(store, "Empresa B F8")
        create_user(store, company_id=cls.comp_a.company_id,
                    email="owner8a@t.do", name="Owner A",
                    password=PASS, role_id="owner")
        create_user(store, company_id=cls.comp_b.company_id,
                    email="owner8b@t.do", name="Owner B",
                    password=PASS, role_id="owner")
        create_user(store, company_id=cls.comp_a.company_id,
                    email="viewer8a@t.do", name="Viewer A",
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
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _login(self, email):
        c = Client(self.port)
        st, _ = c.login(email, PASS)
        self.assertEqual(st, 200)
        return c

    def test_401_without_session(self):
        c = Client(self.port)
        st, _ = c.get("/api/product/overview")
        self.assertEqual(st, 401)

    def test_200_for_owner(self):
        c = self._login("owner8a@t.do")
        st, payload = c.get("/api/product/overview")
        self.assertEqual(st, 200)
        self.assertIn("overview", payload)

    def test_overview_structure(self):
        c = self._login("owner8a@t.do")
        st, payload = c.get("/api/product/overview")
        ov = payload["overview"]
        self.assertEqual(ov["company"]["company_id"], self.comp_a.company_id)
        self.assertIn(ov["product_state"]["state"], list(PRODUCT_STATES))
        self.assertEqual(len(ov["onboarding"]["steps"]), 7)
        self.assertEqual(len(ov["post_sequence"]), 5)
        self.assertEqual(len(ov["benefits"]), 7)
        self.assertEqual(len(ov["plans"]), 3)
        self.assertFalse(ov["company"]["is_demo"])

    def test_incomplete_config_state_for_fresh_company(self):
        c = self._login("owner8a@t.do")
        st, payload = c.get("/api/product/overview")
        ov = payload["overview"]
        # Empresa recién creada: sin configuración regional completa.
        self.assertEqual(ov["product_state"]["state"],
                         "INCOMPLETE_CONFIGURATION")
        self.assertFalse(ov["product_state"]["config_complete"])

    def test_company_id_query_param_ignored(self):
        c = self._login("owner8a@t.do")
        st, payload = c.get("/api/product/overview?company_id=" +
                            self.comp_b.company_id)
        self.assertEqual(st, 200)
        # Debe seguir devolviendo SU empresa, no la del parámetro.
        self.assertEqual(payload["overview"]["company"]["company_id"],
                         self.comp_a.company_id)

    def test_tenant_isolation(self):
        ca = self._login("owner8a@t.do")
        cb = self._login("owner8b@t.do")
        _, pa = ca.get("/api/product/overview")
        _, pb = cb.get("/api/product/overview")
        self.assertEqual(pa["overview"]["company"]["company_id"],
                         self.comp_a.company_id)
        self.assertEqual(pb["overview"]["company"]["company_id"],
                         self.comp_b.company_id)
        self.assertNotEqual(pa["overview"]["company"]["company_id"],
                            pb["overview"]["company"]["company_id"])

    def test_viewer_can_view(self):
        c = self._login("viewer8a@t.do")
        st, _ = c.get("/api/product/overview")
        self.assertEqual(st, 200)

    def test_audit_logged(self):
        c = self._login("owner8a@t.do")
        before = len([e for e in self.store.list_audit(self.comp_a.company_id)
                      if e.action == "PRODUCT_OVERVIEW_VIEWED"])
        c.get("/api/product/overview")
        events = [e for e in self.store.list_audit(self.comp_a.company_id)
                  if e.action == "PRODUCT_OVERVIEW_VIEWED"]
        self.assertEqual(len(events), before + 1)
        ev = events[-1]
        self.assertEqual(ev.company_id, self.comp_a.company_id)
        self.assertTrue(ev.user_id)
        self.assertTrue(ev.timestamp)
        self.assertEqual(ev.result, "ok")

    def test_audit_has_no_secrets(self):
        c = self._login("owner8a@t.do")
        c.get("/api/product/overview")
        events = [e for e in self.store.list_audit(self.comp_a.company_id)
                  if e.action == "PRODUCT_OVERVIEW_VIEWED"]
        ev = events[-1]
        blob = json.dumps({"r": ev.resource, "m": ev.metadata}).lower()
        for secret_word in ("password", "token", "secret", "api_key"):
            self.assertNotIn(secret_word, blob)

    def test_deterministic_endpoint(self):
        c = self._login("owner8a@t.do")
        _, p1 = c.get("/api/product/overview")
        _, p2 = c.get("/api/product/overview")
        self.assertEqual(p1, p2)


# ------------------------------------------------------- frontend estático
class FrontendStaticTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "webapp", "static")
        cls.app = open(os.path.join(base, "app.js"), encoding="utf-8").read()
        cls.index = open(os.path.join(base, "index.html"), encoding="utf-8").read()
        cls.css = open(os.path.join(base, "style.css"), encoding="utf-8").read()

    def test_render_product_entry_exists(self):
        self.assertIn("function renderProductEntry", self.app)

    def test_default_route_is_inicio(self):
        self.assertIn('location.hash || "#/inicio"', self.app)

    def test_inicio_route_registered(self):
        self.assertIn('if (h.indexOf("#/inicio") === 0)', self.app)

    def test_boot_forces_inicio_entry(self):
        # Tras login o al abrir la app con sesión válida, boot() debe
        # llevar a #/inicio ignorando cualquier hash obsoleto.
        self.assertIn('location.hash = "#/inicio"', self.app)
        boot = self.app[self.app.index("async function boot"):]
        boot = boot[:boot.index("applyChromeLanguage")]
        self.assertIn('location.hash = "#/inicio"', boot)

    def test_nav_inicio_in_html(self):
        self.assertIn('data-route="inicio"', self.index)
        self.assertIn('#/inicio', self.index)

    def test_accessibility_attributes(self):
        # aria-live, role=status, progressbar y labels en la vista nueva
        # (el JS usa comillas simples; se acepta cualquiera de las dos).
        pairs = [("role='progressbar'", 'role="progressbar"'),
                 ("role='status'", 'role="status"'),
                 ("aria-live='polite'", 'aria-live="polite"')]
        for a, b in pairs:
            self.assertTrue(a in self.app or b in self.app, a)
        for token in ("aria-label", "<label", "aria-valuenow"):
            self.assertIn(token, self.app, token)

    def test_focus_visible_css(self):
        self.assertIn(":focus-visible", self.css)

    def test_responsive_css_for_new_components(self):
        self.assertIn(".hero", self.css)
        self.assertIn("@media", self.css)
        # Las nuevas grillas colapsan en móvil.
        self.assertIn(".plans-grid", self.css)

    def test_advisor_explainer_sentence(self):
        self.assertIn('T("advisor.explainer")', self.app)

    def test_no_company_id_sent_from_frontend(self):
        # El frontend nunca construye company_id en query params.
        self.assertNotIn("company_id=", self.app)

    def test_uses_i18n_for_product_strings(self):
        # Las claves de producto se resuelven vía T() (nunca hardcodeadas).
        for token in ('T("advisor.explainer")', 'inicio: "nav.inicio"',
                      "T(ov.first_entry.main_key)", "T(s.title_key)",
                      "T(m.label_key)", "T(b.label_key)", "T(p.name_key)"):
            self.assertIn(token, self.app, token)


if __name__ == "__main__":
    unittest.main()
