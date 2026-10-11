"""QA-005 — Compuerta de suficiencia de datos (INSUFFICIENT_DATA).

Garantiza que un archivo sin evidencia suficiente no se presente como
análisis válido:

- CSV basura (valores no numéricos, fechas inválidas) → INSUFFICIENT_DATA
  (antes: READY silencioso), sin auto-activar, con advertencias propagadas.
- CSV válido pequeño (10 filas) → sigue READY (no se castiga lo pequeño).
- La compuerta no usa solo el número de filas: combina revisión + perfil.
- Aislamiento entre empresas y reintento tras insuficiencia.

Datos 100% sintéticos. Ejecutar: .venv/bin/python -m unittest tests.test_qa_005_sufficiency
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tenant import TenantStore, create_company

import datasets.store as ds_store_mod
from datasets import (
    DatasetStore, receive_upload, build_review, suggest_mapping,
    confirm_mapping, process_dataset,
)
from datasets.processing import _sufficiency_check, _propagate_review_warnings
from datasets.models import Dataset
from datasets.store import utcnow_iso

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE_DIRS = ["datasets", "uploads", "processed", "profiles", "anomalies",
              "findings", "context", "predictions", "prediction_intelligence",
              "prediction_validation", "business_context"]

COLS = "Invoice,StockCode,Description,Quantity,InvoiceDate,UnitPrice,CustomerID,Country"


def _basura_bytes():
    # Como el fixture QA de staging: columnas válidas, valores inválidos.
    lines = [COLS,
             "1,1,prod A,MUCHOS,ayer,caro,,",
             "2,2,prod B,,mañana,,,"]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _valido_bytes(n=10):
    lines = [COLS]
    for i in range(1, n + 1):
        lines.append(
            f"{50000 + i},P{i % 4},Producto {i % 4},{(i % 5) + 1},"
            f"2024-{(i % 12) + 1:02d}-10,{(i % 9) + 1}.50,C{i % 3},España")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _chunks(data: bytes, size: int = 4096):
    for i in range(0, len(data), size):
        yield data[i:i + size]


def _cleanup_company_data(company_id: str):
    for stage in STAGE_DIRS:
        path = os.path.join(PROJECT_ROOT, "data", stage, company_id)
        shutil.rmtree(path, ignore_errors=True)


class SufficiencyGateTest(unittest.TestCase):
    """QA-005: la compuerta decide con evidencia, no solo con filas."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zayvero_qa5_")
        self.orig_uploads = ds_store_mod.UPLOADS_DIR
        ds_store_mod.UPLOADS_DIR = os.path.join(self.tmp, "uploads")
        self.tmp_tenant = tempfile.mkdtemp(prefix="zayvero_qa5_tenant_")
        self.tenant_store = TenantStore(self.tmp_tenant)
        self.comp = create_company(self.tenant_store, "Empresa QA5 Test")
        self.cid = self.comp.company_id

    def tearDown(self):
        ds_store_mod.UPLOADS_DIR = self.orig_uploads
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.tmp_tenant, ignore_errors=True)
        _cleanup_company_data(self.cid)

    # -- helpers ------------------------------------------------------
    def _store(self, cid=None):
        return DatasetStore(cid or self.cid)

    def _upload_confirm(self, st, data: bytes, filename: str):
        ds = receive_upload(st, filename=filename, size_bytes=len(data),
                            content_iter=_chunks(data), user_id="USR-1")
        ds.review = build_review(ds.upload["path"])
        sug = suggest_mapping(ds.review["columns"])
        mp = {s["canonical"]: s["source"] for s in sug["suggestions"]
              if s["status"] == "suggested"}
        res = confirm_mapping(ds.review["columns"], mp)
        self.assertTrue(res["ok"], res["errors"])
        ds.mapping = {"confirmed": True, "mapping": res["mapping"],
                      "confirmed_at": utcnow_iso(), "auto": False}
        st.save_dataset(ds)
        return ds

    # -- 1. unidades de la compuerta ----------------------------------
    def test_01_sin_filas_es_insuficiente(self):
        ds = Dataset(dataset_id="DS-x", company_id="C1", nombre="t",
                     source="csv", created_at="", updated_at="",
                     row_count=0, review={"checks": []})
        ok, motivo = _sufficiency_check(ds, {"date_range": {}})
        self.assertFalse(ok)
        self.assertEqual(motivo, "no_rows")

    def test_02_sin_fechas_es_insuficiente(self):
        ds = Dataset(dataset_id="DS-x", company_id="C1", nombre="t",
                     source="csv", created_at="", updated_at="",
                     row_count=2,
                     review={"checks": [
                         {"key": "dates", "ok": False,
                          "detail": "No se detectaron fechas"}]})
        ok, motivo = _sufficiency_check(ds, {"date_range": {}})
        self.assertFalse(ok)
        self.assertEqual(motivo, "no_dates")

    def test_03_valido_es_suficiente(self):
        ds = Dataset(dataset_id="DS-x", company_id="C1", nombre="t",
                     source="csv", created_at="", updated_at="",
                     row_count=10,
                     review={"checks": [{"key": "dates", "ok": True}]})
        ok, motivo = _sufficiency_check(
            ds, {"date_range": {"min_date": "2024-01-10",
                               "max_date": "2024-12-10"}})
        self.assertTrue(ok)
        self.assertEqual(motivo, "")

    def test_04_propagate_warnings_dedup(self):
        ds = Dataset(dataset_id="DS-x", company_id="C1", nombre="t",
                     source="csv", created_at="", updated_at="",
                     review={"warnings": [
                         {"type": "nulls", "label": "Vacíos",
                          "detail": "Se encontraron 2 valores vacíos."},
                         {"type": "nulls", "label": "Vacíos",
                          "detail": "Se encontraron 2 valores vacíos."},
                         "texto plano"]})
        _propagate_review_warnings(ds)
        self.assertEqual(ds.warnings,
                         ["Se encontraron 2 valores vacíos.", "texto plano"])

    # -- 2. integración: basura → INSUFFICIENT_DATA --------------------
    def test_05_basura_no_llega_a_ready(self):
        st = self._store()
        ds = self._upload_confirm(st, _basura_bytes(), "basura.csv")
        out = process_dataset(st, ds.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out.status, "INSUFFICIENT_DATA")
        self.assertFalse(out.is_active,
                         "un dataset insuficiente no se auto-activa")
        self.assertTrue(out.warnings,
                        "las advertencias de la revisión deben propagarse")
        # Reintentable: el estado permite volver a procesar / subir otro.
        self.assertIn(out.status, ("INSUFFICIENT_DATA", "ERROR", "READY"))

    # -- 3. integración: válido pequeño → READY ------------------------
    def test_06_valido_pequeno_sigue_ready(self):
        st = self._store()
        ds = self._upload_confirm(st, _valido_bytes(10), "valido10.csv")
        out = process_dataset(st, ds.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out.status, "READY")
        self.assertTrue(out.is_active,
                        "el primer dataset válido se auto-activa")

    # -- 4. aislamiento entre empresas ---------------------------------
    def test_07_basura_no_contamina_otra_empresa(self):
        comp_b = create_company(self.tenant_store, "Empresa QA5 B")
        st_a, st_b = self._store(self.cid), self._store(comp_b.company_id)
        ds = self._upload_confirm(st_a, _basura_bytes(), "basura-a.csv")
        out = process_dataset(st_a, ds.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out.status, "INSUFFICIENT_DATA")
        self.assertEqual(st_b.list_datasets(), [])
        _cleanup_company_data(comp_b.company_id)

    # -- 5. reintento tras insuficiencia --------------------------------
    def test_08_reintento_con_valido_llega_a_ready(self):
        st = self._store()
        ds = self._upload_confirm(st, _basura_bytes(), "basura.csv")
        out = process_dataset(st, ds.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out.status, "INSUFFICIENT_DATA")
        ds2 = self._upload_confirm(st, _valido_bytes(12), "valido12.csv")
        out2 = process_dataset(st, ds2.dataset_id,
                               tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out2.status, "READY")

    # -- 6. obs. revisión 2: insuficiente nunca queda activo --------------
    def test_09_insuficiente_desactiva_activo_previo(self):
        st = self._store()
        ds = self._upload_confirm(st, _valido_bytes(10), "valido.csv")
        out = process_dataset(st, ds.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out.status, "READY")
        self.assertTrue(out.is_active)
        # Se reemplaza el archivo por basura y se reprocesa el MISMO
        # dataset por vía directa (la API bloquea reprocesar un READY).
        with open(out.upload["path"], "wb") as fh:
            fh.write(_basura_bytes())
        ds2 = st.get_dataset(ds.dataset_id)
        ds2.review = build_review(ds2.upload["path"])
        sug = suggest_mapping(ds2.review["columns"])
        mp = {s["canonical"]: s["source"] for s in sug["suggestions"]
              if s["status"] == "suggested"}
        res = confirm_mapping(ds2.review["columns"], mp)
        self.assertTrue(res["ok"], res["errors"])
        ds2.mapping = {"confirmed": True, "mapping": res["mapping"],
                       "confirmed_at": utcnow_iso(), "auto": False}
        st.save_dataset(ds2)
        out2 = process_dataset(st, ds.dataset_id,
                               tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out2.status, "INSUFFICIENT_DATA")
        self.assertFalse(
            out2.is_active,
            "un dataset insuficiente no permanece activo como análisis válido")

    def test_10_estado_y_warnings_sobreviven_recarga(self):
        st = self._store()
        ds = self._upload_confirm(st, _basura_bytes(), "basura.csv")
        out = process_dataset(st, ds.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out.status, "INSUFFICIENT_DATA")
        # Recarga desde disco con un store nuevo.
        st2 = DatasetStore(self.cid)
        seen = st2.get_dataset(ds.dataset_id)
        self.assertEqual(seen.status, "INSUFFICIENT_DATA")
        self.assertEqual(seen.warnings, out.warnings)
        self.assertTrue(seen.warnings)
        self.assertFalse(seen.is_active)

    def test_11_fallo_persistencia_final_no_deja_estado_inconsistente(self):
        st = self._store()
        ds = self._upload_confirm(st, _basura_bytes(), "basura.csv")
        orig_save = st.save_dataset

        def boom_final(d):
            if d.status == "INSUFFICIENT_DATA":
                raise OSError("disco simulado lleno en guardado final")
            return orig_save(d)

        st.save_dataset = boom_final
        try:
            out = process_dataset(st, ds.dataset_id,
                                  tenant_store=self.tenant_store,
                                  user_id="USR-1")
        finally:
            st.save_dataset = orig_save
        # Sin propagación: el fallo queda auditado.
        actions = [e.action for e in
                   self.tenant_store.list_audit(self.cid)]
        self.assertIn("DATASET_PROCESSING_FAILED", actions)
        self.assertTrue(out is None or hasattr(out, "status"))
        # En disco no hay estado a medio aplicar: ERROR coherente,
        # sin warnings parciales ni activo.
        seen = st.get_dataset(ds.dataset_id)
        self.assertEqual(seen.status, "ERROR")
        self.assertEqual(seen.warnings, [])
        self.assertFalse(seen.is_active)

    def test_12_set_active_rechaza_insuficiente(self):
        # Restricción documentada: solo un READY puede activarse
        # (datasets/store.py::set_active). La API la hereda.
        st = self._store()
        ds = self._upload_confirm(st, _basura_bytes(), "basura.csv")
        out = process_dataset(st, ds.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out.status, "INSUFFICIENT_DATA")
        with self.assertRaises(ValueError):
            st.set_active(ds.dataset_id)


if __name__ == "__main__":
    unittest.main()
