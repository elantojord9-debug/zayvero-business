"""FASE 7B — Tests del Diagnóstico Ejecutivo ZAYVERO.

Cubre: generación, estructura, números reales (no hardcode), resumen,
estado, atención prioritaria, riesgos, oportunidades, tendencias,
predicciones, recomendaciones, limitaciones, insuficiencia de datos,
DEMO, integración con Advisor, trazabilidad, permisos, auditoría,
tenant isolation, determinismo.

Ejecutar: .venv/bin/python -m pytest tests/test_diagnostic.py -x -q
(o con unittest).
"""

from __future__ import annotations

import copy
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

from diagnostic import (
    build_diagnostic,
    validate,
    DIAGNOSTIC_VERSION,
    AVAILABLE,
    LIMITED,
    INSUFFICIENT,
    STATUS_NOTES,
    SUGGESTED_ADVISOR_QUESTIONS,
)
from diagnostic.models import REQUIRED_TOP_LEVEL, SECTIONS
from tenant import TenantStore, create_company, create_user
import webapp.server as server_mod

PASS = "DiagTest123"
CTX5A = "data/business_context/demo-retail/online_retail_II_business_context.json"
INT4B = ("data/prediction_intelligence/demo-retail/"
         "online_retail_II_prediction_intelligence.json")
VAL4C = ("data/prediction_validation/demo-retail/"
         "online_retail_II_prediction_validation.json")


_INPUTS_CACHE = None


def _load_inputs():
    global _INPUTS_CACHE
    if _INPUTS_CACHE is None:
        with open(CTX5A, encoding="utf-8") as fh:
            ctx5a = json.load(fh)
        with open(INT4B, encoding="utf-8") as fh:
            i4b = json.load(fh)
        with open(VAL4C, encoding="utf-8") as fh:
            v4c = json.load(fh)
        _INPUTS_CACHE = (ctx5a, i4b, v4c)
    return _INPUTS_CACHE


def _build_demo(**overrides):
    ctx5a, i4b, v4c = _load_inputs()
    kw = dict(company_id="demo-retail", company_name="Demo Retail",
              is_demo=True, dataset_state="READY")
    kw.update(overrides)
    return build_diagnostic(ctx5a, i4b, v4c, **kw), ctx5a, i4b, v4c


class BuilderTest(unittest.TestCase):
    """Estructura y contenido del BusinessDiagnostic."""

    # ---- generación y estructura -------------------------------------
    def test_01_estructura_completa(self):
        d, _, _, _ = _build_demo()
        self.assertEqual(validate(d), [], f"problemas: {validate(d)}")
        for f in REQUIRED_TOP_LEVEL:
            self.assertIn(f, d)
        for s in SECTIONS:
            self.assertIn(s, d)

    def test_02_version(self):
        d, _, _, _ = _build_demo()
        self.assertEqual(d["version"], DIAGNOSTIC_VERSION)

    def test_03_determinismo_id(self):
        d1, _, _, _ = _build_demo()
        d2, _, _, _ = _build_demo()
        self.assertEqual(d1["diagnostic_id"], d2["diagnostic_id"])
        self.assertTrue(d1["diagnostic_id"].startswith("DG-"))

    def test_04_determinismo_contenido(self):
        d1, _, _, _ = _build_demo()
        d2, _, _, _ = _build_demo()
        c1 = dict(d1); c1.pop("generated_at"); c1["trace"] = dict(d1["trace"]); c1["trace"].pop("generated_at")
        c2 = dict(d2); c2.pop("generated_at"); c2["trace"] = dict(d2["trace"]); c2["trace"].pop("generated_at")
        self.assertEqual(c1, c2)

    def test_05_serializable_json(self):
        d, _, _, _ = _build_demo()
        self.assertTrue(len(json.dumps(d)) > 1000)

    def test_06_sin_secretos(self):
        d, _, _, _ = _build_demo()
        raw = json.dumps(d).lower()
        # Patrones de credenciales reales; "secret" a secas colisiona con el
        # nombre de un producto del dataset ("RED METAL BOX TOP SECRET").
        for bad in ("password", "api_key", "apikey", "secret_key",
                    "pbkdf2", "session_token", "bearer "):
            self.assertNotIn(bad, raw, f"posible secreto en salida: {bad}")

    # ---- números reales, no hardcode ----------------------------------
    def test_07_resumen_usa_numeros_reales(self):
        d, ctx5a, _, _ = _build_demo()
        signals = ctx5a["attention_summary"]["signals"]
        text = d["executive_summary"]["text"]
        self.assertIn(str(signals["urgent_findings"]), text)
        self.assertIn(str(signals["important_findings"]), text)
        self.assertIn(str(len(ctx5a["key_opportunities"])), text)

    def test_08_resumen_cambia_con_inputs(self):
        """Si cambian los inputs, el resumen cambia: no está hardcodeado."""
        d1, ctx5a, i4b, v4c = _build_demo()
        ctx2 = copy.deepcopy(ctx5a)
        ctx2["attention_summary"]["signals"]["urgent_findings"] = 3
        d2 = build_diagnostic(ctx2, i4b, v4c, company_id="demo-retail",
                              company_name="Demo", is_demo=True,
                              dataset_state="READY")
        self.assertNotEqual(d1["executive_summary"]["text"],
                            d2["executive_summary"]["text"])
        self.assertIn("3 hallazgos urgentes", d2["executive_summary"]["text"])

    def test_09_sin_lenguaje_alarmista(self):
        d, _, _, _ = _build_demo()
        raw = json.dumps(d).lower()
        self.assertNotIn("perdiendo dinero", raw)
        self.assertNotIn("está en crisis", raw)

    def test_10_sin_causalidad_afirmada(self):
        d, _, _, _ = _build_demo()
        raw = json.dumps(d).lower()
        self.assertNotIn("está causando", raw)
        self.assertNotIn("definitivamente", raw)

    # ---- estado de la empresa ------------------------------------------
    def test_11_estado_atencion(self):
        d, ctx5a, _, _ = _build_demo()
        st = d["business_status"]
        self.assertEqual(st["attention_level"],
                         ctx5a["attention_summary"]["attention_level"])
        self.assertEqual(st["urgent_findings"],
                         ctx5a["attention_summary"]["signals"]["urgent_findings"])

    def test_12_estado_confianza_y_calidad(self):
        d, ctx5a, _, _ = _build_demo()
        st = d["business_status"]
        self.assertEqual(
            st["context_confidence_score"],
            ctx5a["context_confidence"]["context_confidence_score"])
        self.assertEqual(
            st["data_quality_score"],
            ctx5a["attention_summary"]["signals"]["data_quality_score"])

    # ---- atención prioritaria -------------------------------------------
    def test_13_atencion_orden(self):
        d, _, _, _ = _build_demo()
        att = d["priority_attention"]
        self.assertTrue(len(att) > 0)
        rank = {"URGENT": 0, "IMPORTANT": 1}
        ranks = [rank.get(a["business_priority"], 9) for a in att]
        self.assertEqual(ranks, sorted(ranks))

    def test_14_atencion_campos(self):
        d, _, _, _ = _build_demo()
        for a in d["priority_attention"]:
            for f in ("finding_id", "title", "business_priority",
                      "impact_score", "confidence_score", "entity", "period",
                      "summary"):
                self.assertIn(f, a)

    def test_15_atencion_sin_recalculo(self):
        """El impact_score del diagnóstico es el de 2B, sin recalcular."""
        d, ctx5a, _, _ = _build_demo()
        by_id = {f["finding_id"]: f for f in ctx5a["critical_findings"]}
        for a in d["priority_attention"]:
            self.assertEqual(a["impact_score"],
                             by_id[a["finding_id"]]["impact_score"])

    # ---- riesgos ---------------------------------------------------------
    def test_16_riesgos_severidad(self):
        d, _, _, _ = _build_demo()
        self.assertTrue(len(d["risks"]) > 0)
        rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        ranks = [rank.get(r["severity"], 9) for r in d["risks"]]
        self.assertEqual(ranks, sorted(ranks))

    def test_17_riesgos_campos(self):
        d, _, _, _ = _build_demo()
        for r in d["risks"]:
            for f in ("risk_id", "risk_type", "severity", "explanation",
                      "recommendation", "evidence_id"):
                self.assertIn(f, r)

    def test_18_riesgos_lenguaje(self):
        d, _, _, _ = _build_demo()
        raw = json.dumps(d["risks"]).lower()
        self.assertNotIn("definitivamente está causando", raw)

    # ---- oportunidades ----------------------------------------------------
    def test_19_oportunidades_etiqueta(self):
        d, _, _, _ = _build_demo()
        self.assertTrue(len(d["opportunities"]) > 0)
        for o in d["opportunities"]:
            self.assertEqual(o["label"], "Posible oportunidad")
            self.assertIn("No es una garantía de resultado", o["disclaimer"])

    def test_20_oportunidades_sin_roi_inventado(self):
        d, _, _, _ = _build_demo()
        raw = json.dumps(d["opportunities"]).lower()
        self.assertNotIn("ganancia garantizada", raw)
        self.assertNotIn("roi", raw)

    # ---- tendencias --------------------------------------------------------
    def test_21_tendencias_separadas(self):
        d, _, _, _ = _build_demo()
        tr = d["trends"]
        for t in tr["observed"]:
            self.assertEqual(t["trend_type"], "OBSERVED_TREND")
        for t in tr["projected"]:
            self.assertNotEqual(t["trend_type"], "OBSERVED_TREND")

    def test_22_tendencias_no_mezcladas(self):
        d, _, _, _ = _build_demo()
        for t in d["trends"]["projected"]:
            self.assertIn("proyecci", t["note"].lower())

    # ---- predicciones -------------------------------------------------------
    def test_23_predicciones_campos(self):
        d, _, _, _ = _build_demo()
        self.assertTrue(len(d["predictions"]) > 0)
        for p in d["predictions"]:
            for f in ("prediction_id", "period", "predicted_value",
                      "lower_bound", "upper_bound", "confidence_score",
                      "forecast_quality", "trend", "decline_risk", "method",
                      "validation_status", "validation_label"):
                self.assertIn(f, p)

    def test_24_prediccion_pending_etiqueta(self):
        d, _, _, _ = _build_demo()
        pendings = [p for p in d["predictions"]
                    if p["validation_status"] == "PENDING"]
        self.assertTrue(len(pendings) > 0)
        for p in pendings:
            self.assertEqual(p["validation_label"], "Pendiente de validación")

    def test_25_predicciones_no_certeza(self):
        d, _, _, _ = _build_demo()
        raw = json.dumps(d["predictions"]).lower()
        self.assertNotIn("predice con certeza", raw)

    def test_26_validacion_cero_honesta(self):
        d, _, _, v4c = _build_demo()
        self.assertEqual(v4c["summary"]["validated"], 0)
        self.assertIn("aún no cuentan con resultados reales",
                      d["executive_summary"]["text"])

    # ---- recomendaciones -----------------------------------------------------
    def test_27_recomendaciones_orden(self):
        d, _, _, _ = _build_demo()
        self.assertTrue(len(d["recommendations"]) > 0)
        first = d["recommendations"][0]
        self.assertIn(first["priority"], ("URGENT", "HIGH"))

    def test_28_recomendaciones_tipo(self):
        d, _, _, _ = _build_demo()
        for r in d["recommendations"]:
            self.assertEqual(r["kind_code"], "EXISTING_RECOMMENDATION")
            self.assertIn("no ejecuta ninguna acción", r["note"].lower())

    # ---- limitaciones ----------------------------------------------------------
    def test_29_limitaciones_presentes(self):
        d, ctx5a, _, _ = _build_demo()
        self.assertEqual(len(d["limitations"]), len(ctx5a["limitations"]))
        self.assertTrue(len(d["limitations"]) > 0)

    def test_30_limitaciones_no_ocultas(self):
        d, _, _, _ = _build_demo()
        texts = " ".join(l["text"] for l in d["limitations"]).lower()
        # Las predicciones no validadas deben declararse como limitación.
        self.assertIn("desempeño de las predicciones", texts)

    # ---- calidad de datos --------------------------------------------------------
    def test_31_calidad_datos(self):
        d, ctx5a, _, _ = _build_demo()
        dq = d["data_quality"]
        self.assertEqual(
            dq["score"],
            ctx5a["attention_summary"]["signals"]["data_quality_score"])
        self.assertIsNotNone(dq["period_start"])
        self.assertIsNotNone(dq["period_end"])
        self.assertIn("periodo disponible", dq["note"])

    # ---- próximos pasos -------------------------------------------------------------
    def test_32_next_steps(self):
        d, _, _, _ = _build_demo()
        steps = [s["title"] for s in d["next_steps"]]
        self.assertIn("Revisar hallazgos urgentes", steps)
        self.assertIn("Consultar al Advisor", steps)
        self.assertEqual([s["step"] for s in d["next_steps"]],
                         list(range(1, len(d["next_steps"]) + 1)))

    def test_33_next_steps_sin_tareas_automaticas(self):
        d, _, _, _ = _build_demo()
        raw = json.dumps(d["next_steps"]).lower()
        self.assertNotIn("asignar", raw)
        self.assertNotIn("notificaci", raw)

    # ---- evidencia y trazabilidad ------------------------------------------------------
    def test_34_evidence_ids_reales(self):
        d, ctx5a, _, _ = _build_demo()
        index_ids = {e["evidence_id"] for e in ctx5a["evidence_index"]}
        for eid in d["evidence_used"]:
            # IDs de findings/risks/opps/recs/lims también son válidos
            self.assertTrue(
                eid in index_ids or eid.startswith(
                    ("FND-", "RISK-", "OPP-", "INS-", "REC-", "LIM-",
                     "TRD-", "EV-")),
                f"evidence ID sospechoso: {eid}")

    def test_35_evidence_ids_encontrados_en_indice(self):
        d, ctx5a, _, _ = _build_demo()
        index_ids = {e["evidence_id"] for e in ctx5a["evidence_index"]}
        ev_ids = [e for e in d["evidence_used"] if e.startswith("EV-")]
        self.assertTrue(len(ev_ids) > 0)
        for eid in ev_ids:
            self.assertIn(eid, index_ids)

    def test_36_trace(self):
        d, ctx5a, _, _ = _build_demo()
        tr = d["trace"]
        self.assertEqual(tr["context_id"], ctx5a["identity"]["context_id"])
        self.assertIn("rules", tr)
        self.assertIn("generated_at", tr)
        self.assertIn("FASE_5A", tr["source_modules"])

    # ---- DEMO ----------------------------------------------------------------------------
    def test_37_demo_flag(self):
        d, _, _, _ = _build_demo()
        self.assertTrue(d["is_demo"])

    def test_38_no_demo_company(self):
        d, ctx5a, i4b, v4c = _build_demo()
        d2 = build_diagnostic(ctx5a, i4b, v4c, company_id="CMP-XYZ",
                              company_name="Empresa X", is_demo=False,
                              dataset_state="READY")
        self.assertFalse(d2["is_demo"])
        self.assertNotEqual(d["diagnostic_id"], d2["diagnostic_id"])

    # ---- insuficiencia de datos -----------------------------------------------------------------
    def test_39_insufficient_sin_datos(self):
        d = build_diagnostic(None, None, None, company_id="CMP-1",
                             company_name="E1", is_demo=False,
                             dataset_state="NO_DATA")
        self.assertEqual(d["diagnostic_status"], INSUFFICIENT)
        self.assertEqual(d["status_note"], STATUS_NOTES[INSUFFICIENT])
        self.assertIn("Información insuficiente", d["status_note"])
        self.assertEqual(d["priority_attention"], [])
        self.assertEqual(validate(d), [])

    def test_40_insufficient_no_fabrica(self):
        d = build_diagnostic(None, None, None, company_id="CMP-1",
                             company_name="E1", is_demo=False,
                             dataset_state="PROCESSING")
        self.assertEqual(d["diagnostic_status"], INSUFFICIENT)
        self.assertEqual(d["risks"], [])
        self.assertEqual(d["predictions"], [])

    def test_41_limited_pocos_datos(self):
        """READY pero sin hallazgos ni predicciones → LIMITED."""
        _, ctx5a, i4b, v4c = _build_demo()
        ctx2 = copy.deepcopy(ctx5a)
        ctx2["critical_findings"] = []
        i4b2 = copy.deepcopy(i4b)
        i4b2["prediction_insights"] = []
        d = build_diagnostic(ctx2, i4b2, v4c, company_id="demo-retail",
                             company_name="Demo", is_demo=True,
                             dataset_state="READY")
        self.assertEqual(d["diagnostic_status"], LIMITED)
        self.assertEqual(d["status_note"], STATUS_NOTES[LIMITED])

    # ---- tenant / empresa ---------------------------------------------------------------------------
    def test_42_company_id_de_sesion(self):
        d, _, _, _ = _build_demo()
        self.assertEqual(d["company_id"], "demo-retail")

    def test_43_tenant_isolation_datos(self):
        """El company_id del diagnóstico es el que recibe el builder y el
        diagnostic_id cambia por empresa. El aislamiento real (cada empresa
        consume sus propios archivos) se verifica en test_47/test_48 a
        nivel servidor: aquí se alimenta el contexto del demo a otra
        empresa, por lo que sus rutas internas lo mencionan."""
        _, ctx5a, i4b, v4c = _build_demo()
        d = build_diagnostic(ctx5a, i4b, v4c, company_id="CMP-OTHER",
                             company_name="Otra", is_demo=False,
                             dataset_state="READY")
        self.assertEqual(d["company_id"], "CMP-OTHER")
        d_demo, _, _, _ = _build_demo()
        self.assertNotEqual(d["diagnostic_id"], d_demo["diagnostic_id"])
        self.assertFalse(d["is_demo"])


class ServerTest(unittest.TestCase):
    """Integración: endpoint /api/diagnostic con servidor real."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.mkdtemp(prefix="zayvero7b_")
        store = TenantStore(cls.tmp)
        store.ensure_demo_company()
        create_user(store, company_id="demo-retail", email="owner@demo.test",
                    name="Demo Owner", password=PASS, role_id="owner")
        create_user(store, company_id="demo-retail", email="viewer@demo.test",
                    name="Demo Viewer", password=PASS, role_id="viewer")
        cls.comp_a = create_company(store, "Empresa A Test")
        cls.comp_b = create_company(store, "Empresa B Test")
        create_user(store, company_id=cls.comp_a.company_id,
                    email="ownera@test.com", name="Owner A",
                    password=PASS, role_id="owner")
        create_user(store, company_id=cls.comp_b.company_id,
                    email="ownerb@test.com", name="Owner B",
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

    def _client(self):
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(jar))
        # SEG-03: el cliente de pruebas se comporta como el frontend real.
        csrf = {"token": ""}

        def call(method, path, body=None):
            data = json.dumps(body).encode() if body is not None else None
            headers = {"Content-Type": "application/json"}
            if method in ("POST", "PUT", "PATCH", "DELETE") and csrf["token"]:
                headers["X-CSRF-Token"] = csrf["token"]
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}{path}", data=data,
                headers=headers, method=method)
            try:
                with opener.open(req) as res:
                    raw = res.read().decode("utf-8")
                    payload = json.loads(raw) if raw else {}
                    if isinstance(payload, dict) and payload.get("csrf_token"):
                        csrf["token"] = payload["csrf_token"]
                    return res.status, payload
            except urllib.error.HTTPError as e:
                raw = e.read().decode("utf-8")
                try:
                    payload = json.loads(raw) if raw else {}
                except Exception:
                    payload = {}
                return e.code, payload
        return call

    def _login(self, call, email, password=PASS):
        code, data = call("POST", "/api/login",
                          {"email": email, "password": password})
        self.assertEqual(code, 200, f"login falló: {data}")
        return data

    # ---- endpoint ------------------------------------------------------------------
    def test_44_sin_login_401(self):
        call = self._client()
        code, _ = call("GET", "/api/diagnostic")
        self.assertEqual(code, 401)

    def test_45_diagnostic_demo(self):
        call = self._client()
        self._login(call, "owner@demo.test")
        code, data = call("GET", "/api/diagnostic")
        self.assertEqual(code, 200)
        d = data["diagnostic"]
        self.assertEqual(validate(d), [])
        self.assertEqual(d["diagnostic_status"], AVAILABLE)
        self.assertEqual(d["company_id"], "demo-retail")
        self.assertTrue(d["is_demo"])
        self.assertIn("25 hallazgos urgentes",
                      d["executive_summary"]["text"])

    def test_46_viewer_puede_ver(self):
        call = self._client()
        self._login(call, "viewer@demo.test")
        code, data = call("GET", "/api/diagnostic")
        self.assertEqual(code, 200)
        self.assertIn("diagnostic", data)

    def test_47_empresa_sin_datos_insufficient(self):
        call = self._client()
        self._login(call, "ownera@test.com")
        code, data = call("GET", "/api/diagnostic")
        self.assertEqual(code, 200)
        d = data["diagnostic"]
        self.assertEqual(d["diagnostic_status"], INSUFFICIENT)
        self.assertEqual(d["company_id"], self.comp_a.company_id)
        # No ve datos del demo
        raw = json.dumps(d)
        self.assertNotIn("RABBIT NIGHT LIGHT", raw)

    def test_48_tenant_isolation(self):
        call_a = self._client()
        self._login(call_a, "ownera@test.com")
        _, da = call_a("GET", "/api/diagnostic")
        call_b = self._client()
        self._login(call_b, "ownerb@test.com")
        _, db = call_b("GET", "/api/diagnostic")
        self.assertEqual(da["diagnostic"]["company_id"],
                         self.comp_a.company_id)
        self.assertEqual(db["diagnostic"]["company_id"],
                         self.comp_b.company_id)
        self.assertNotEqual(da["diagnostic"]["diagnostic_id"],
                            db["diagnostic"]["diagnostic_id"])

    def test_49_auditoria_diagnostic(self):
        call = self._client()
        self._login(call, "owner@demo.test")
        call("GET", "/api/diagnostic")
        code, data = call("GET", "/api/audit")
        self.assertEqual(code, 200)
        actions = [e["action"] for e in data["events"]]
        self.assertIn("DIAGNOSTIC_VIEWED", actions)
        self.assertIn("DIAGNOSTIC_GENERATED", actions)
        # Sin secretos en auditoría
        raw = json.dumps(data["events"]).lower()
        self.assertNotIn("password", raw)

    def test_50_logout_luego_401(self):
        call = self._client()
        self._login(call, "owner@demo.test")
        call("POST", "/api/logout")
        code, _ = call("GET", "/api/diagnostic")
        self.assertEqual(code, 401)

    def test_51_secciones_presentes_en_respuesta(self):
        call = self._client()
        self._login(call, "owner@demo.test")
        _, data = call("GET", "/api/diagnostic")
        d = data["diagnostic"]
        for s in ("executive_summary", "business_status",
                  "priority_attention", "risks", "opportunities", "trends",
                  "predictions", "recommendations", "data_quality",
                  "limitations", "next_steps"):
            self.assertIn(s, d, f"falta sección {s}")

    def test_52_advisor_cta_questions(self):
        call = self._client()
        self._login(call, "owner@demo.test")
        _, data = call("GET", "/api/diagnostic")
        qs = data["diagnostic"]["suggested_advisor_questions"]
        self.assertTrue(len(qs) >= 4)
        self.assertIn("¿Qué debería revisar primero?", qs)

    def test_53_cache_determinista(self):
        """Dos llamadas devuelven el mismo diagnostic_id (cache)."""
        call = self._client()
        self._login(call, "owner@demo.test")
        _, d1 = call("GET", "/api/diagnostic")
        _, d2 = call("GET", "/api/diagnostic")
        self.assertEqual(d1["diagnostic"]["diagnostic_id"],
                         d2["diagnostic"]["diagnostic_id"])

    def test_54_sin_deadlock_concurrente(self):
        """Regresión: llamadas concurrentes a /api/diagnostic no se
        bloquean (el lock de TenantData no es reentrante)."""
        import concurrent.futures
        call = self._client()
        self._login(call, "owner@demo.test")
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            futs = [ex.submit(call, "GET", "/api/diagnostic")
                    for _ in range(4)]
            results = [f.result(timeout=60) for f in futs]
        for code, data in results:
            self.assertEqual(code, 200)
            self.assertIn("diagnostic", data)
        ids = {d["diagnostic"]["diagnostic_id"] for _, d in results}
        self.assertEqual(len(ids), 1)


if __name__ == "__main__":
    unittest.main()
