"""FASE 3 — Dashboard MVP: pruebas.

Cubre: carga de findings, conteo por prioridad, orden por impacto,
filtros, búsqueda, detalle, evidencia LOW, ausencia de datos,
valores derivados, compatibilidad con outputs 2B/2C y smoke test HTTP.
"""

import json
import threading
import time
import unittest
import urllib.request
import urllib.error

from dashboard import adapter, service
from dashboard.server import DashboardHandler, get_dataset
from http.server import ThreadingHTTPServer



def _load_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)

class TestLoad(unittest.TestCase):
    def setUp(self):
        self.ds = get_dataset()

    def test_carga_correcta(self):
        self.assertEqual(len(self.ds["findings"]), 708)
        self.assertTrue(all(v["finding_id"] for v in self.ds["findings"]))
        self.assertIn("UCI", self.ds["dataset"])

    def test_conteo_por_prioridad(self):
        counts = service.priority_counts(self.ds["findings"])
        self.assertEqual(counts["URGENT"], 25)
        self.assertEqual(counts["IMPORTANT"], 480)
        self.assertEqual(counts["REVIEW"], 203)
        self.assertEqual(counts["MONITOR"], 0)
        self.assertEqual(sum(counts.values()), 708)


class TestSort(unittest.TestCase):
    def test_orden_por_prioridad_e_impacto(self):
        ordered = service.sort_findings(get_dataset()["findings"])
        self.assertEqual(ordered[0]["finding_id"], "FND-000001")
        keys = [service.sort_key(v) for v in ordered]
        self.assertEqual(keys, sorted(keys))
        # URGENT antes que IMPORTANT antes que REVIEW
        prios = [v["business_priority"] for v in ordered]
        self.assertEqual(prios.index("IMPORTANT") > 0, True)
        first_important = prios.index("IMPORTANT")
        self.assertTrue(all(p == "URGENT" for p in prios[:first_important]))


class TestFilters(unittest.TestCase):
    def setUp(self):
        self.findings = get_dataset()["findings"]

    def test_filtro_prioridad(self):
        urg = service.filter_findings(self.findings, priority="URGENT")
        self.assertEqual(len(urg), 25)
        self.assertTrue(all(v["business_priority"] == "URGENT" for v in urg))

    def test_filtro_tipo(self):
        prod = service.filter_findings(self.findings, ftype="PRODUCT_ANOMALY")
        self.assertEqual(len(prod), 193)
        cust = service.filter_findings(self.findings, ftype="CUSTOMER_ANOMALY")
        self.assertEqual(len(cust), 166)

    def test_filtro_periodo(self):
        ref = service.reference_date(self.findings)
        self.assertIsNotNone(ref)
        last90 = service.filter_findings(self.findings, period="last_90d", ref=ref)
        older = service.filter_findings(self.findings, period="older", ref=ref)
        allf = service.filter_findings(self.findings, period="all", ref=ref)
        self.assertEqual(len(allf), 708)
        self.assertTrue(len(last90) + len(older) <= 708)
        self.assertTrue(len(last90) > 0)

    def test_filtros_combinados(self):
        res = service.filter_findings(
            self.findings, priority="URGENT", ftype="PRODUCT_ANOMALY")
        self.assertTrue(all(
            v["business_priority"] == "URGENT" and v["type"] == "PRODUCT_ANOMALY"
            for v in res))
        self.assertTrue(len(res) > 0)


class TestSearch(unittest.TestCase):
    def setUp(self):
        self.findings = get_dataset()["findings"]

    def test_busqueda_producto(self):
        res = service.filter_findings(self.findings, query="23084")
        self.assertTrue(len(res) >= 1)
        self.assertTrue(any("23084" in (v["title"] or "") for v in res))

    def test_busqueda_cliente(self):
        res = service.filter_findings(self.findings, query="14156")
        self.assertTrue(len(res) >= 1)

    def test_busqueda_tipo(self):
        res = service.filter_findings(self.findings, query="temporal")
        self.assertTrue(all(v["type"] == "TEMPORAL_ANOMALY" for v in res))

    def test_busqueda_titulo(self):
        res = service.filter_findings(self.findings, query="rabbit night")
        self.assertTrue(len(res) >= 1)

    def test_busqueda_sin_resultados(self):
        res = service.filter_findings(self.findings, query="zzz-producto-inexistente-999")
        self.assertEqual(res, [])

    def test_busqueda_multi_token(self):
        res = service.filter_findings(self.findings, query="rabbit light")
        self.assertTrue(len(res) >= 1)


class TestDetail(unittest.TestCase):
    def setUp(self):
        self.findings = get_dataset()["findings"]

    def test_detalle_completo(self):
        d = service.get_detail(self.findings, "FND-000001")
        self.assertIsNotNone(d)
        # campos 2B
        self.assertEqual(d["business_priority"], "URGENT")
        self.assertEqual(d["observed_value"], 34330.51)
        self.assertEqual(d["expected_value"], 1239.0)
        self.assertIsNotNone(d["statistical_explanation"])
        self.assertIsNotNone(d["business_explanation"])
        # campos 2C
        self.assertEqual(d["context_status"], "contextualized")
        self.assertTrue(len(d["facts"]) > 0)
        self.assertTrue(len(d["recommendations"]) > 0)
        self.assertEqual(d["n_recommendations"], len(d["recommendations"]))
        self.assertIsNotNone(d["historical_context"])

    def test_detalle_inexistente(self):
        self.assertIsNone(service.get_detail(self.findings, "FND-XXXXXX"))

    def test_detalle_sin_valores_inventados(self):
        # el valor observado del detalle coincide con el JSON crudo de 2B
        raw = _load_json(adapter.DEFAULT_FINDINGS_PATH)
        raw_by_id = {f["finding_id"]: f for f in raw["findings"]}
        d = service.get_detail(self.findings, "FND-000010")
        r = raw_by_id["FND-000010"]
        self.assertEqual(d["observed_value"], r["observed_value"])
        self.assertEqual(d["difference"], r["difference"])
        self.assertEqual(d["impact_score"], r["impact_score"])


class TestEvidenceLow(unittest.TestCase):
    def test_evidencia_low_transparencia(self):
        findings = get_dataset()["findings"]
        low = [v for v in findings if v["evidence_quality"] == "LOW"]
        self.assertTrue(len(low) > 0)
        v = low[0]
        # la vista conserva la marca LOW y la nota de 2C
        self.assertEqual(v["evidence_quality"], "LOW")
        self.assertIsNotNone(v.get("evidence_quality_note"))


class TestMissingData(unittest.TestCase):
    def test_build_view_vacio_no_rompe(self):
        v = adapter.build_view({}, None)
        self.assertIsNotNone(v)
        self.assertIsNone(v["observed_value"])
        self.assertEqual(v["n_recommendations"], 0)
        self.assertEqual(v["facts"], [])

    def test_finding_sin_contexto(self):
        v = adapter.build_view({"finding_id": "X", "title": "t"}, None)
        self.assertEqual(v["finding_id"], "X")
        self.assertIsNone(v["context_status"])
        self.assertEqual(v["recommendations"], [])

    def test_panorama_vacio(self):
        p = service.panorama([])
        self.assertEqual(p["total"], 0)
        self.assertIsNone(p["avg_impact_score"])


class TestDerivedValues(unittest.TestCase):
    def test_panorama(self):
        p = service.panorama(get_dataset()["findings"])
        self.assertEqual(p["total"], 708)
        self.assertEqual(p["by_priority"]["URGENT"], 25)
        self.assertAlmostEqual(p["avg_impact_score"], 71.6, delta=0.2)
        self.assertAlmostEqual(p["avg_confidence"], 82.3, delta=0.5)
        # recurrent + rarely_recurrent = 347+17, isolated = 322
        self.assertEqual(p["recurrent"], 364)
        self.assertEqual(p["isolated"], 322)


class TestCompatibility(unittest.TestCase):
    def test_todo_finding_tiene_contexto(self):
        raw_f = _load_json(adapter.DEFAULT_FINDINGS_PATH)
        raw_c = _load_json(adapter.DEFAULT_CONTEXT_PATH)
        fids = {f["finding_id"] for f in raw_f["findings"]}
        cids = {c["finding_id"] for c in raw_c["context_findings"]}
        self.assertEqual(len(fids - cids), 0)
        ds = get_dataset()
        self.assertTrue(all(v["finding_id"] in cids for v in ds["findings"]))

    def test_tipos_presentes(self):
        types = {v["type"] for v in get_dataset()["findings"]}
        self.assertTrue({"PRODUCT_ANOMALY", "CUSTOMER_ANOMALY",
                         "PRICE_ANOMALY", "QUANTITY_ANOMALY",
                         "TEMPORAL_ANOMALY"} <= types)


class TestServerSmoke(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()
        time.sleep(0.3)
        cls.base = f"http://127.0.0.1:{cls.port}"

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def get(self, path):
        with urllib.request.urlopen(self.base + path) as r:
            return r.status, json.loads(r.read().decode("utf-8")) if "json" in r.headers.get("Content-Type", "") else r.read()

    def test_health(self):
        status, body = self.get("/health")
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "ok")

    def test_meta(self):
        status, body = self.get("/api/meta")
        self.assertEqual(status, 200)
        self.assertEqual(body["total_findings"], 708)
        self.assertEqual(body["by_priority"]["URGENT"], 25)
        self.assertEqual(body["by_priority"]["IMPORTANT"], 480)
        self.assertEqual(body["by_priority"]["REVIEW"], 203)
        self.assertIn("demo_disclaimer", body)

    def test_findings_lista_y_paginacion(self):
        status, body = self.get("/api/findings?limit=24&offset=0")
        self.assertEqual(body["total"], 708)
        self.assertEqual(len(body["items"]), 24)
        self.assertEqual(body["items"][0]["finding_id"], "FND-000001")
        status, body2 = self.get("/api/findings?limit=24&offset=24")
        self.assertEqual(len(body2["items"]), 24)
        self.assertNotEqual(body["items"][0]["finding_id"], body2["items"][0]["finding_id"])

    def test_findings_filtros_http(self):
        _, body = self.get("/api/findings?priority=URGENT&limit=200")
        self.assertEqual(body["total"], 25)
        _, body = self.get("/api/findings?type=PRICE_ANOMALY&limit=200")
        self.assertEqual(body["total"], 78)
        _, body = self.get("/api/findings?q=23084&limit=200")
        self.assertTrue(body["total"] >= 1)

    def test_findings_detalle_http(self):
        status, body = self.get("/api/findings/FND-000001")
        self.assertEqual(status, 200)
        self.assertEqual(body["observed_value"], 34330.51)
        self.assertTrue(len(body["recommendations"]) > 0)
        self.assertTrue(len(body["possible_explanations"]) > 0)

    def test_findings_404(self):
        try:
            self.get("/api/findings/FND-NOEXISTE")
            self.fail("esperaba 404")
        except urllib.error.HTTPError as e:
            self.assertEqual(e.code, 404)

    def test_panorama_http(self):
        status, body = self.get("/api/panorama")
        self.assertEqual(body["total"], 708)
        self.assertEqual(body["recurrent"], 364)
        self.assertEqual(body["isolated"], 322)

    def test_index_html(self):
        status, body = self.get("/")
        self.assertEqual(status, 200)
        text = body.decode("utf-8")
        self.assertIn("ZAYVERO BUSINESS", text)
        self.assertIn("Lo que necesita tu atención", text)
        self.assertIn("Panorama actual", text)


if __name__ == "__main__":
    unittest.main()
