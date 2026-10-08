"""FASE 7C — Tests de preparación global de ZAYVERO Business.

Cubre: configuración por empresa, idioma, moneda, país, timezone,
formato de fecha, formato numérico, empresa sin configuración, DEMO,
tenant isolation, permisos, auditoría, compatibilidad con empresas
existentes, frontend, company_id por URL ignorado, determinismo,
regresiones.

Ejecutar: .venv/bin/python -m unittest tests.test_global -v
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
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from intl import config as cfg_mod
from intl import formatting as fmt
from intl import i18n
from intl.models import DEMO_CONFIG
from tenant import TenantStore, create_company, create_user
from tenant import audit as audit_mod
from tenant import roles as roles_mod
import webapp.server as server_mod

PASS = "GlobalTest123"


# ================= configuración =================

class LocaleConfigTest(unittest.TestCase):
    def test_normalize_full_valid(self):
        data = {"name": "Mi Empresa", "country": "República Dominicana",
                "industry": "Retail", "language": "es", "currency": "dop",
                "timezone": "America/Santo_Domingo",
                "date_format": "dd/mm/yyyy", "number_format": "es"}
        config, errors = cfg_mod.normalize_config(data)
        self.assertEqual(errors, [])
        self.assertEqual(config["currency"], "DOP")
        self.assertEqual(config["language"], "es")

    def test_normalize_empty_defaults(self):
        config, errors = cfg_mod.normalize_config({})
        self.assertEqual(errors, [])
        for f in ("country", "language", "currency", "timezone",
                  "date_format", "number_format"):
            self.assertEqual(config[f], "")

    def test_invalid_language_rejected(self):
        config, errors = cfg_mod.normalize_config({"language": "de"})
        self.assertEqual(config["language"], "")
        self.assertTrue(any(e["field"] == "language" for e in errors))

    def test_invalid_currency_rejected(self):
        config, errors = cfg_mod.normalize_config({"currency": "DOLLAR"})
        self.assertEqual(config["currency"], "")
        self.assertTrue(any(e["field"] == "currency" for e in errors))

    def test_invalid_timezone_rejected(self):
        config, errors = cfg_mod.normalize_config({"timezone": "Marte/Olimpo"})
        self.assertEqual(config["timezone"], "")
        self.assertTrue(any(e["field"] == "timezone" for e in errors))

    def test_invalid_date_format_rejected(self):
        config, errors = cfg_mod.normalize_config({"date_format": "yy/mm"})
        self.assertEqual(config["date_format"], "")
        self.assertTrue(any(e["field"] == "date_format" for e in errors))

    def test_invalid_number_format_rejected(self):
        config, errors = cfg_mod.normalize_config({"number_format": "fr"})
        self.assertEqual(config["number_format"], "")
        self.assertTrue(any(e["field"] == "number_format" for e in errors))

    def test_company_config_complete(self):
        from tenant.models import Company
        c = Company(company_id="x", name="X", country="España",
                    language="es", currency="EUR", timezone="Europe/Madrid")
        view = cfg_mod.company_config(c)
        self.assertTrue(view["complete"])
        self.assertEqual(view["missing"], [])

    def test_company_config_incomplete_lists_missing(self):
        from tenant.models import Company
        c = Company(company_id="x", name="X")
        view = cfg_mod.company_config(c)
        self.assertFalse(view["complete"])
        self.assertIn("currency", view["missing"])
        self.assertIn("country", view["missing"])

    def test_normalize_deterministic(self):
        data = {"language": "en", "currency": "usd"}
        self.assertEqual(cfg_mod.normalize_config(data),
                         cfg_mod.normalize_config(data))


# ================= formato =================

class FormattingTest(unittest.TestCase):
    def test_number_es(self):
        self.assertEqual(fmt.format_number(1234.56, "es", 2), "1.234,56")

    def test_number_en(self):
        self.assertEqual(fmt.format_number(1234.56, "en", 2), "1,234.56")

    def test_money_dop_es(self):
        self.assertEqual(fmt.format_money(1234.5, "DOP", "es"), "RD$1.234")

    def test_money_usd_en(self):
        self.assertEqual(fmt.format_money(1234.5, "USD", "en"), "$1,234")

    def test_money_gbp(self):
        self.assertEqual(fmt.format_money(100, "GBP", "en"), "£100")

    def test_money_missing_currency_honest_es(self):
        out = fmt.format_money(1234.5, "", "es")
        self.assertIn("Moneda no configurada", out)
        self.assertIn("1.234", out)

    def test_money_missing_currency_honest_en(self):
        out = fmt.format_money(1234.5, None, "en", "en")
        self.assertIn("Currency not configured", out)

    def test_money_unknown_code_shown_as_code(self):
        # Sin inventar símbolos: el código ISO se muestra tal cual.
        out = fmt.format_money(100, "XXX", "es")
        self.assertTrue(out.startswith("XXX"))

    def test_money_never_assumes(self):
        # El valor numérico no cambia entre monedas: no hay conversión.
        self.assertIn("1.234", fmt.format_money(1234.5, "DOP", "es"))
        self.assertIn("1,234", fmt.format_money(1234.5, "USD", "en"))

    def test_date_dd_mm_yyyy(self):
        self.assertEqual(fmt.format_date("2026-10-08", "dd/mm/yyyy"), "08/10/2026")

    def test_date_mm_dd_yyyy(self):
        self.assertEqual(fmt.format_date("2026-10-08", "mm/dd/yyyy"), "10/08/2026")

    def test_date_iso(self):
        self.assertEqual(fmt.format_date("2026-10-08", "yyyy-mm-dd"), "2026-10-08")

    def test_date_with_time_stripped(self):
        self.assertEqual(fmt.format_date("2026-10-08T15:30:00Z", "dd/mm/yyyy"),
                         "08/10/2026")

    def test_date_invalid_passthrough(self):
        self.assertEqual(fmt.format_date("no-fecha", "dd/mm/yyyy"), "no-fecha")


# ================= i18n =================

class I18nTest(unittest.TestCase):
    def test_t_es(self):
        self.assertEqual(i18n.t("nav.diagnostic", "es"), "Diagnóstico")

    def test_t_en(self):
        self.assertEqual(i18n.t("nav.diagnostic", "en"), "Diagnostic")

    def test_t_fallback_to_es(self):
        self.assertEqual(i18n.t("nav.diagnostic", "fr"), "Diagnóstico")

    def test_t_unknown_key_returns_key(self):
        self.assertEqual(i18n.t("clave.inexistente", "en"), "clave.inexistente")

    def test_register_pt_extensible(self):
        i18n.register_language("pt", {"nav.diagnostic": "Diagnóstico"})
        try:
            self.assertEqual(i18n.t("nav.diagnostic", "pt"), "Diagnóstico")
            # Las demás claves caen a es sin romper.
            self.assertEqual(i18n.t("nav.audit", "pt"), "Auditoría")
            self.assertIn("pt", i18n.supported_languages())
        finally:
            # Limpieza: pt era solo para la prueba de extensibilidad.
            del i18n._STRINGS["pt"]

    def test_supported_languages(self):
        self.assertIn("es", i18n.supported_languages())
        self.assertIn("en", i18n.supported_languages())


# ================= modelo Company =================

class CompanyModelTest(unittest.TestCase):
    def test_old_company_json_loads(self):
        # Compatibilidad: empresas guardadas antes de 7C (sin campos nuevos).
        from tenant.models import Company
        old = {"company_id": "old-1", "name": "Vieja",
               "status": "active", "is_demo": False,
               "created_at": "", "updated_at": ""}
        c = Company(**old)
        self.assertEqual(c.language, "")
        self.assertEqual(c.currency, "")
        view = cfg_mod.company_config(c)
        self.assertFalse(view["complete"])

    def test_new_fields_persist(self):
        from tenant.models import Company
        tmp = tempfile.mkdtemp()
        try:
            store = TenantStore(data_dir=os.path.join(tmp, "tenant"))
            comp = create_company(store, "Persistente")
            comp.country = "España"
            comp.language = "es"
            comp.currency = "EUR"
            comp.timezone = "Europe/Madrid"
            store.save_company(comp)
            loaded = store.get_company(comp.company_id)
            self.assertEqual(loaded.currency, "EUR")
            self.assertEqual(loaded.timezone, "Europe/Madrid")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ================= demo =================

class DemoConfigTest(unittest.TestCase):
    def test_demo_gets_gbp_config(self):
        tmp = tempfile.mkdtemp()
        try:
            store = TenantStore(data_dir=os.path.join(tmp, "tenant"))
            demo = store.ensure_demo_company()
            self.assertTrue(demo.is_demo)
            self.assertEqual(demo.currency, "GBP")
            self.assertEqual(demo.country, "Reino Unido")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_demo_backfill_existing(self):
        tmp = tempfile.mkdtemp()
        try:
            store = TenantStore(data_dir=os.path.join(tmp, "tenant"))
            comp = create_company(store, "Demo vieja", is_demo=True)
            # Simula empresa demo creada antes de 7C (sin config).
            self.assertEqual(comp.currency, "")
            from tenant.store import DEMO_COMPANY_ID
            # La re-ejecución completa la configuración sin duplicar.
            store._companies_file = store._companies_file  # no-op
            demo2 = store.ensure_demo_company()
            self.assertEqual(demo2.currency, "GBP")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


# ================= permisos =================

class ConfigPermissionsTest(unittest.TestCase):
    def test_owner_can_configure(self):
        self.assertIn("company.config", roles_mod.permissions_for("owner"))

    def test_admin_can_configure(self):
        self.assertIn("company.config", roles_mod.permissions_for("admin"))

    def test_analyst_cannot_configure(self):
        self.assertNotIn("company.config", roles_mod.permissions_for("analyst"))

    def test_viewer_cannot_configure(self):
        self.assertNotIn("company.config", roles_mod.permissions_for("viewer"))

    def test_audit_constant_exists(self):
        self.assertEqual(audit_mod.COMPANY_CONFIG_UPDATED, "COMPANY_CONFIG_UPDATED")


# ================= servidor =================

def _start_server(store):
    server_mod.STORE = store
    server_mod._DATA_CACHE.clear()
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.WebappHandler)
    port = httpd.server_address[1]
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    return httpd, port


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
            with opener.open(req) as r:
                return r.status, json.loads(r.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as e:
            try:
                payload = json.loads(e.read().decode("utf-8") or "{}")
            except Exception:
                payload = {}
            return e.code, payload

    def login(self, email, password):
        return self._req("POST", "/api/login",
                         {"email": email, "password": password})

    def get(self, path):
        return self._req("GET", path)

    def put(self, path, body):
        return self._req("PUT", path, body)


class ServerConfigTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp()
        cls.store = TenantStore(data_dir=os.path.join(cls.tmp, "tenant"))
        # COMPANY_A: RD / es / DOP (owner)
        cls.comp_a = create_company(cls.store, "Empresa A")
        cls.comp_a.country = "Dominican Republic"
        cls.comp_a.language = "es"
        cls.comp_a.currency = "DOP"
        cls.comp_a.timezone = "America/Santo_Domingo"
        cls.comp_a.date_format = "dd/mm/yyyy"
        cls.comp_a.number_format = "es"
        cls.store.save_company(cls.comp_a)
        create_user(cls.store, company_id=cls.comp_a.company_id,
                    email="ownerA@test.do", name="Owner A",
                    password=PASS, role_id="owner")
        create_user(cls.store, company_id=cls.comp_a.company_id,
                    email="viewerA@test.do", name="Viewer A",
                    password=PASS, role_id="viewer")
        # COMPANY_B: USA / en / USD (owner)
        cls.comp_b = create_company(cls.store, "Empresa B")
        cls.comp_b.country = "United States"
        cls.comp_b.language = "en"
        cls.comp_b.currency = "USD"
        cls.comp_b.timezone = "America/New_York"
        cls.comp_b.date_format = "mm/dd/yyyy"
        cls.comp_b.number_format = "en"
        cls.store.save_company(cls.comp_b)
        create_user(cls.store, company_id=cls.comp_b.company_id,
                    email="ownerB@test.do", name="Owner B",
                    password=PASS, role_id="owner")
        # COMPANY_C: Spain / es / EUR
        cls.comp_c = create_company(cls.store, "Empresa C")
        cls.comp_c.country = "Spain"
        cls.comp_c.language = "es"
        cls.comp_c.currency = "EUR"
        cls.comp_c.timezone = "Europe/Madrid"
        cls.store.save_company(cls.comp_c)
        cls.httpd, cls.port = _start_server(cls.store)

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def _client_a(self):
        c = Client(self.port)
        st, _ = c.login("ownerA@test.do", PASS)
        self.assertEqual(st, 200)
        return c

    def test_me_includes_config(self):
        c = self._client_a()
        st, body = c.get("/api/me")
        self.assertEqual(st, 200)
        cfg = body["company"]["config"]
        self.assertEqual(cfg["currency"], "DOP")
        self.assertEqual(cfg["language"], "es")
        self.assertTrue(cfg["complete"])

    def test_get_config_own_company(self):
        c = self._client_a()
        st, body = c.get("/api/company/config")
        self.assertEqual(st, 200)
        self.assertEqual(body["config"]["country"], "Dominican Republic")
        self.assertTrue(body["can_edit"])

    def test_url_company_id_ignored(self):
        # ?company_id=COMPANY_A desde sesión de B → se ignora (B ve lo suyo).
        c = Client(self.port)
        st, _ = c.login("ownerB@test.do", PASS)
        self.assertEqual(st, 200)
        st, body = c.get("/api/company/config?company_id=" + self.comp_a.company_id)
        self.assertEqual(st, 200)
        self.assertEqual(body["config"]["currency"], "USD")
        self.assertEqual(body["config"]["country"], "United States")

    def test_tenant_isolation_configs(self):
        ca = self._client_a()
        _, ba = ca.get("/api/company/config")
        cb = Client(self.port)
        cb.login("ownerB@test.do", PASS)
        _, bb = cb.get("/api/company/config")
        self.assertNotEqual(ba["config"]["currency"], bb["config"]["currency"])
        self.assertEqual(bb["config"]["language"], "en")

    def test_put_config_owner_ok(self):
        c = self._client_a()
        st, body = c.put("/api/company/config", {
            "industry": "Retail", "currency": "DOP",
            "timezone": "America/Santo_Domingo", "language": "es",
            "date_format": "dd/mm/yyyy", "number_format": "es",
            "country": "Dominican Republic"})
        self.assertEqual(st, 200)
        self.assertTrue(body["ok"])
        self.assertIn("industry", body["changed"])
        self.assertEqual(body["config"]["industry"], "Retail")

    def test_put_config_viewer_denied(self):
        c = Client(self.port)
        st, _ = c.login("viewerA@test.do", PASS)
        self.assertEqual(st, 200)
        st, body = c.put("/api/company/config", {"currency": "USD"})
        self.assertEqual(st, 403)

    def test_put_config_invalid_rejected(self):
        c = self._client_a()
        st, body = c.put("/api/company/config", {"currency": "PESOS"})
        self.assertEqual(st, 400)
        self.assertIn("fields", body)

    def test_put_config_demo_locked(self):
        demo = self.store.ensure_demo_company()
        create_user(self.store, company_id=demo.company_id,
                    email="ownerDemo@test.do", name="Owner Demo",
                    password=PASS, role_id="owner")
        c = Client(self.port)
        st, _ = c.login("ownerDemo@test.do", PASS)
        self.assertEqual(st, 200)
        st, _ = c.put("/api/company/config", {"currency": "USD"})
        self.assertEqual(st, 403)
        # La demo conserva su contexto GBP.
        self.assertEqual(self.store.get_company(demo.company_id).currency, "GBP")

    def test_put_config_partial_preserves_others(self):
        # PATCH parcial: los campos ausentes NO se borran.
        c = self._client_a()
        st, before = c.get("/api/company/config")
        self.assertEqual(before["config"]["currency"], "DOP")
        st, body = c.put("/api/company/config", {"industry": "Solo industria"})
        self.assertEqual(st, 200)
        self.assertEqual(body["config"]["industry"], "Solo industria")
        self.assertEqual(body["config"]["currency"], "DOP")
        self.assertEqual(body["config"]["country"], "Dominican Republic")

    def test_config_update_audited(self):
        c = self._client_a()
        c.put("/api/company/config", {"industry": "Auditoría"})
        events = self.store.list_audit() if hasattr(self.store, "list_audit") else []
        actions = [e.action if hasattr(e, "action") else e.get("action") for e in events]
        self.assertIn(audit_mod.COMPANY_CONFIG_UPDATED, actions)

    def test_incomplete_company_honest(self):
        # Empresa sin configuración: complete=False, missing visible.
        comp = create_company(self.store, "Sin Config")
        create_user(self.store, company_id=comp.company_id,
                    email="ownerN@test.do", name="Owner N",
                    password=PASS, role_id="owner")
        c = Client(self.port)
        c.login("ownerN@test.do", PASS)
        st, body = c.get("/api/company/config")
        self.assertEqual(st, 200)
        self.assertFalse(body["config"]["complete"])
        self.assertIn("currency", body["config"]["missing"])


# ================= frontend =================

class FrontendTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.static = os.path.join(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__))), "webapp", "static")

    def test_i18n_js_exists(self):
        path = os.path.join(self.static, "i18n.js")
        self.assertTrue(os.path.exists(path))
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn("ZBI18N", src)
        self.assertIn("nav.diagnostic", src)

    def test_index_loads_i18n_before_app(self):
        path = os.path.join(self.static, "index.html")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        self.assertIn('src="i18n.js"', src)
        self.assertLess(src.index('src="i18n.js"'), src.index('src="app.js"'))

    def test_no_hardcoded_gbp_in_fmtmoney(self):
        path = os.path.join(self.static, "app.js")
        with open(path, encoding="utf-8") as f:
            src = f.read()
        # fmtMoney ya no asume GBP: delega a la config de la empresa.
        self.assertIn("ZBI18N.fmtMoney", src)
        self.assertNotIn('"£" + n.toLocaleString', src)

    def test_i18n_served(self):
        tmp = tempfile.mkdtemp()
        try:
            store = TenantStore(data_dir=os.path.join(tmp, "tenant"))
            httpd, port = _start_server(store)
            try:
                req = urllib.request.Request(f"http://127.0.0.1:{port}/i18n.js")
                with urllib.request.urlopen(req) as r:
                    self.assertEqual(r.status, 200)
                    self.assertIn("ZBI18N", r.read().decode("utf-8"))
            finally:
                httpd.shutdown()
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
