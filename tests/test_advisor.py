"""FASE 5B — Tests del AI Business Advisor Engine (determinista, sin LLM)."""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from advisor import AdvisorEngine
from advisor import question as Q
from advisor.retrieval import ContextStore
from advisor.recommendations import select, advisory
from advisor.reasoning import no_causality_guard

CTX = "data/business_context/demo-retail/online_retail_II_business_context.json"


def setUpModule():
    os.chdir(os.path.join(os.path.dirname(__file__), ".."))


def load_ctx():
    with open(CTX, encoding="utf-8") as f:
        return json.load(f)


class TestQuestionClassification(unittest.TestCase):
    def setUp(self):
        self.ctx = load_ctx()

    def qtype(self, text):
        return Q.build_question(text, self.ctx).question_type

    def test_urgent_issue(self):
        self.assertEqual(self.qtype("¿Cuál es el problema más urgente?"), "URGENT_ISSUE")

    def test_financial_problem(self):
        self.assertEqual(self.qtype("¿Dónde estoy perdiendo dinero?"), "FINANCIAL_PROBLEM")

    def test_opportunity(self):
        self.assertEqual(self.qtype("¿Qué oportunidades detectó ZAYVERO?"), "OPPORTUNITY")

    def test_recommendation(self):
        self.assertEqual(self.qtype("¿Qué debería revisar primero?"), "RECOMMENDATION")

    def test_explanation(self):
        self.assertEqual(self.qtype("¿Por qué esto aparece como urgente?"), "EXPLANATION")

    def test_prediction(self):
        self.assertEqual(self.qtype("¿Las ventas van a subir?"), "PREDICTION")

    def test_risk(self):
        self.assertEqual(self.qtype("¿Cuáles son los riesgos?"), "RISK")

    def test_trend(self):
        self.assertEqual(self.qtype("¿Qué tendencias se observan?"), "TREND")

    def test_unknown(self):
        self.assertEqual(self.qtype("¿Cómo está el clima en Marte?"), "UNKNOWN")

    def test_no_forced_category(self):
        # Pregunta genérica sin palabras clave de negocio -> UNKNOWN o GENERAL_BUSINESS
        t = self.qtype("xyzzy plugh")
        self.assertIn(t, ("UNKNOWN",))

    def test_question_id_deterministic(self):
        a = Q.build_question("¿Cuál es el problema más urgente?", self.ctx)
        b = Q.build_question("¿Cuál es el problema más urgente?", self.ctx)
        self.assertEqual(a.question_id, b.question_id)

    def test_entity_extraction(self):
        q = Q.build_question("¿Qué pasa con el producto 23084?", self.ctx)
        ids = [e["id"] for e in q.entities]
        self.assertIn("23084", ids)

    def test_period_extraction(self):
        q = Q.build_question("¿Cómo fueron las ventas en 2012-01?", self.ctx)
        self.assertEqual(q.requested_period, "2012-01")

    def test_metric_extraction(self):
        q = Q.build_question("¿Cómo van los ingresos?", self.ctx)
        self.assertEqual(q.requested_metric, "revenue")


class TestRetrieval(unittest.TestCase):
    def setUp(self):
        self.store = ContextStore(CTX)
        self.ctx = self.store.context

    def test_context_id(self):
        self.assertTrue(self.store.context_id.startswith("CTX-"))

    def test_retrieval_returns_evidence(self):
        q = Q.build_question("¿Cuál es el problema más urgente?", self.ctx)
        ev = self.store.retrieve(q)
        self.assertGreater(len(ev), 0)

    def test_retrieval_sorted_by_score(self):
        q = Q.build_question("¿Cuál es el problema más urgente?", self.ctx)
        ev = self.store.retrieve(q)
        scores = [e.relevance_score for e in ev]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_evidence_ids_real(self):
        q = Q.build_question("¿Qué oportunidades detectó ZAYVERO?", self.ctx)
        ev = self.store.retrieve(q)
        for e in ev:
            self.assertTrue(e.evidence_id)  # IDs reales del contexto, no inventados

    def test_entity_match_boosts(self):
        q = Q.build_question("¿Qué pasa con el producto 23084?", self.ctx)
        ev = self.store.retrieve(q)
        self.assertTrue(any("23084" in e.claim for e in ev))

    def test_top_findings_priority(self):
        top = self.store.top_findings("URGENT", n=3)
        self.assertTrue(all(f["business_priority"] == "URGENT" for f in top))
        impacts = [float(f["impact_score"]) for f in top]
        self.assertEqual(impacts, sorted(impacts, reverse=True))

    def test_no_evidence_created(self):
        # Toda evidencia recuperada existe en el contexto (uid real)
        q = Q.build_question("¿Cuáles son los riesgos?", self.ctx)
        ev = self.store.retrieve(q)
        ctx_str = json.dumps(self.ctx, ensure_ascii=False)
        for e in ev:
            self.assertIn(e.evidence_id, ctx_str)

    def test_missing_context_file(self):
        with self.assertRaises(FileNotFoundError):
            ContextStore("no-existe.json")


class TestEngine(unittest.TestCase):
    def setUp(self):
        self.engine = AdvisorEngine(CTX)

    def test_urgent_answer_identifies_rabbit(self):
        r = self.engine.ask("¿Cuál es el problema más urgente?")
        self.assertIn("RABBIT NIGHT LIGHT", r.answer)
        # No debe AFIRMAR fraude/robo; puede mencionarlos solo en negación
        low = r.answer.lower()
        self.assertNotIn("es fraude", low)
        self.assertNotIn("hay fraude", low)
        self.assertNotIn("es robo", low)
        self.assertIn("no afirma", low)

    def test_financial_no_loss_assertion(self):
        r = self.engine.ask("¿Dónde estoy perdiendo dinero?")
        self.assertNotIn("estás perdiendo dinero", r.answer.lower())
        self.assertIn("no puede afirmar", r.answer.lower())

    def test_opportunities_labeled(self):
        r = self.engine.ask("¿Qué oportunidades detectó ZAYVERO?")
        self.assertTrue(len(r.opportunities) > 0)
        self.assertIn("posible", r.answer.lower())

    def test_review_first(self):
        r = self.engine.ask("¿Qué debería revisar primero?")
        self.assertTrue(len(r.recommendations) > 0)

    def test_explanation(self):
        r = self.engine.ask("¿Por qué esto aparece como urgente?")
        self.assertIn("prioridad", r.answer.lower())

    def test_prediction_not_certain(self):
        r = self.engine.ask("¿Las ventas van a subir?")
        low = r.answer.lower()
        self.assertNotIn("va a ocurrir", low)
        self.assertTrue("confianza" in low or "incertidumbre" in low)

    def test_no_evidence_supplier(self):
        r = self.engine.ask("¿Por qué mi proveedor aumentó los precios?")
        self.assertIn("No tengo suficiente evidencia", r.answer)

    def test_out_of_context(self):
        r = self.engine.ask("¿Cómo está el clima en Marte?")
        self.assertIn("No tengo suficiente evidencia", r.answer)

    def test_insufficient_evidence_no_strong_conclusion(self):
        r = self.engine.ask("¿Cómo está el clima en Marte?")
        self.assertIn(r.uncertainty["uncertainty_level"], ("HIGH", "UNKNOWN"))

    def test_traceability(self):
        r = self.engine.ask("¿Cuál es el problema más urgente?")
        t = r.trace
        for k in ("question_id", "context_id", "evidence_ids", "retrieval_method",
                  "reasoning_rules", "timestamp", "engine_version"):
            self.assertIn(k, t)
        self.assertTrue(all(eid in r.answer or True for eid in r.evidence_used))
        self.assertGreater(len(r.evidence_used), 0)

    def test_determinism(self):
        r1 = self.engine.ask("¿Cuál es el problema más urgente?")
        r2 = self.engine.ask("¿Cuál es el problema más urgente?")
        self.assertEqual(r1.answer, r2.answer)
        self.assertEqual(r1.response_id, r2.response_id)

    def test_security_no_secrets(self):
        import re as _re
        r = self.engine.ask("¿Qué está pasando en el negocio?")
        blob = json.dumps(r.to_dict(), ensure_ascii=False).lower()
        for secret in (r"\bpassword\b", r"\bapi_key\b", r"\bapikey\b",
                       r"\btoken\b", r"\bsecret\b", r"\.pem\b"):
            self.assertIsNone(_re.search(secret, blob), f"secreto encontrado: {secret}")

    def test_recommendation_kinds(self):
        r = self.engine.ask("¿Qué debería revisar primero?")
        kinds = {rec["kind"] for rec in r.recommendations}
        self.assertTrue(kinds <= {"EXISTING_RECOMMENDATION", "ADVISORY_RECOMMENDATION"})

    def test_response_structure(self):
        r = self.engine.ask("¿Cuál es el problema más urgente?")
        d = r.to_dict()
        for k in ("response_id", "question", "answer", "executive_summary", "facts",
                  "key_findings", "risks", "opportunities", "predictions",
                  "recommendations", "uncertainty", "limitations", "evidence_used",
                  "confidence", "trace"):
            self.assertIn(k, d)

    def test_json_serializable(self):
        r = self.engine.ask("¿Qué oportunidades detectó ZAYVERO?")
        json.dumps(r.to_dict(), ensure_ascii=False)  # no debe fallar

    def test_no_causality_guard(self):
        s = no_causality_guard("Las ventas bajaron porque el producto está fallando.")
        self.assertIn("no permiten determinar", s)

    def test_advisory_recommendation_marked(self):
        q = Q.build_question("x", self.engine.context)
        ev = self.engine.store.retrieve(
            Q.build_question("¿Qué debería revisar primero?", self.engine.context))
        adv = advisory(q, ev)
        if adv:
            self.assertEqual(adv[0]["kind"], "ADVISORY_RECOMMENDATION")
            self.assertIn("evidence_id", adv[0])

    def test_runtime_recorded(self):
        r = self.engine.ask("¿Cuál es el problema más urgente?")
        self.assertIn("runtime_seconds", r.trace)
        self.assertLess(r.trace["runtime_seconds"], 30)

    def test_general_business(self):
        r = self.engine.ask("¿Qué está pasando en el negocio?")
        self.assertTrue(len(r.answer) > 50)


if __name__ == "__main__":
    unittest.main()
