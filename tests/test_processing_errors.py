"""M1 — Manejo de errores del procesamiento en segundo plano.

Garantiza que ningún fallo del procesamiento deje un dataset en un
estado engañoso o silencioso:

- Fallo ANTES de registrar PROCESSING (dataset inexistente, error al
  leer o al persistir el estado inicial) → estado ERROR coherente y
  consultable, o al menos auditoría. Nunca una excepción sin registro.
- Fallo DURANTE el procesamiento → ERROR con mensaje empresarial.
- El estado ERROR permite reintentar de forma segura.
- Sin resultados parciales marcados como válidos ni mezcla entre empresas.

Datos 100% sintéticos. Ejecutar: .venv/bin/python -m unittest tests.test_processing_errors
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tenant import TenantStore, create_company
from tenant import audit as audit_mod

import datasets.store as ds_store_mod
from datasets import (
    DatasetStore, receive_upload, build_review, suggest_mapping,
    confirm_mapping, process_dataset, process_in_background,
)
from datasets.store import utcnow_iso

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE_DIRS = ["datasets", "uploads", "processed", "profiles", "anomalies",
              "findings", "context", "predictions", "prediction_intelligence",
              "prediction_validation", "business_context"]


def _csv_bytes(n=40):
    lines = ["factura,producto,cantidad,fecha_venta,precio,cliente"]
    for i in range(1, n + 1):
        lines.append(
            f"F{i:05d},PROD-{i % 5},{(i % 7) + 1},2024-{(i % 12) + 1:02d}-15,25.50,CLI-{i % 8}")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _chunks(data: bytes, size: int = 4096):
    for i in range(0, len(data), size):
        yield data[i:i + size]


def _cleanup_company_data(company_id: str):
    for stage in STAGE_DIRS:
        path = os.path.join(PROJECT_ROOT, "data", stage, company_id)
        shutil.rmtree(path, ignore_errors=True)


NO_TRACE_WORDS = ("Traceback", "File \"", "/home/", "/tmp/", ".py",
                  "pipeline", "dataframe", "backend")


class ProcessingErrorHandlingTest(unittest.TestCase):
    """M1: ningún fallo deja un estado silencioso o engañoso."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zayvero_m1_")
        self.orig_uploads = ds_store_mod.UPLOADS_DIR
        ds_store_mod.UPLOADS_DIR = os.path.join(self.tmp, "uploads")
        self.tmp_tenant = tempfile.mkdtemp(prefix="zayvero_m1_tenant_")
        self.tenant_store = TenantStore(self.tmp_tenant)
        self.comp = create_company(self.tenant_store, "Empresa M1 Test")
        self.cid = self.comp.company_id

    def tearDown(self):
        ds_store_mod.UPLOADS_DIR = self.orig_uploads
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.tmp_tenant, ignore_errors=True)
        _cleanup_company_data(self.cid)

    # -- helpers ------------------------------------------------------
    def _store(self, cid=None):
        return DatasetStore(cid or self.cid)

    def _upload(self, st, n=40, filename="ventas.csv", confirm=True):
        data = _csv_bytes(n)
        ds = receive_upload(st, filename=filename, size_bytes=len(data),
                            content_iter=_chunks(data), user_id="USR-1")
        if confirm:
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

    def _audit_actions(self, company_id=None):
        return [e.action for e in
                self.tenant_store.list_audit(company_id or self.cid)]

    # -- 1. procesamiento normal --------------------------------------
    def test_01_procesamiento_normal_ready(self):
        st = self._store()
        ds = self._upload(st)
        out = process_dataset(st, ds.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out.status, "READY")
        self.assertIsNone((out.processing or {}).get("error"))
        self.assertIn("DATASET_PROCESSING_COMPLETED", self._audit_actions())

    # -- 2. fallo ANTES de PROCESSING: dataset inexistente ------------
    def test_02_dataset_inexistente_no_propaga(self):
        # M1 (negativa): antes del fix, esto lanzaba KeyError sin dejar
        # ningún registro: el hilo moría en silencio.
        st = self._store()
        out = process_dataset(st, "DS-no-existe-123",
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertIsNone(out)  # no hay dataset que marcar
        actions = self._audit_actions()
        self.assertIn("DATASET_PROCESSING_FAILED", actions)

    # -- 2b. fallo ANTES de PROCESSING: get_dataset lanza ------------
    def test_03_get_dataset_lanza_excepcion(self):
        st = self._store()
        ds = self._upload(st)
        orig = st.get_dataset

        def boom(dataset_id):
            raise RuntimeError("fallo simulado de lectura")
        st.get_dataset = boom
        try:
            out = process_dataset(st, ds.dataset_id,
                                  tenant_store=self.tenant_store,
                                  user_id="USR-1")
        finally:
            st.get_dataset = orig
        # Sin propagación; la auditoría registra el fallo.
        self.assertIsNone(out)
        self.assertIn("DATASET_PROCESSING_FAILED", self._audit_actions())

    # -- 3. fallo DURANTE el procesamiento -----------------------------
    def test_04_fallo_durante_procesamiento(self):
        st = self._store()
        ds = self._upload(st)
        os.remove(ds.upload["path"])  # el archivo desaparece
        out = process_dataset(st, ds.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out.status, "ERROR")
        msg = (out.processing or {}).get("error", "")
        self.assertTrue(msg)
        for word in NO_TRACE_WORDS:
            self.assertNotIn(word, msg)
        self.assertIn("DATASET_PROCESSING_FAILED", self._audit_actions())

    # -- 4. excepción inesperada dentro del hilo ------------------------
    def test_05_excepcion_en_hilo_no_escapa(self):
        # run_pipeline se importa dentro de process_dataset
        # (from pipeline import run_pipeline), así que se parchea el
        # módulo pipeline: la importación lo recoge en tiempo de llamada.
        import pipeline as pipeline_mod
        st = self._store()
        ds = self._upload(st)
        orig_run = pipeline_mod.run_pipeline

        def boom(*a, **k):
            raise ValueError("fallo inesperado simulado")
        pipeline_mod.run_pipeline = boom
        try:
            t = process_in_background(st, ds.dataset_id,
                                      tenant_store=self.tenant_store,
                                      user_id="USR-1")
            t.join(timeout=120)
        finally:
            pipeline_mod.run_pipeline = orig_run
        self.assertFalse(t.is_alive())
        final = st.get_dataset(ds.dataset_id)
        self.assertEqual(final.status, "ERROR")
        msg = (final.processing or {}).get("error", "")
        self.assertTrue(msg)
        for word in NO_TRACE_WORDS:
            self.assertNotIn(word, msg)

    # -- 5. error visible y consultable ----------------------------------
    def test_06_error_consultable_por_usuario(self):
        st = self._store()
        ds = self._upload(st)
        os.remove(ds.upload["path"])
        process_dataset(st, ds.dataset_id,
                        tenant_store=self.tenant_store, user_id="USR-1")
        seen = st.get_dataset(ds.dataset_id)
        self.assertEqual(seen.status, "ERROR")
        err = (seen.processing or {}).get("error")
        self.assertTrue(err)
        self.assertIsNotNone((seen.processing or {}).get("finished_at"))

    # -- 6. reintento después de un fallo --------------------------------
    def test_07_reintento_despues_de_fallo(self):
        st = self._store()
        ds = self._upload(st)
        path = ds.upload["path"]
        os.remove(path)
        out1 = process_dataset(st, ds.dataset_id,
                               tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out1.status, "ERROR")
        # Se restaura el archivo y se reintenta: debe llegar a READY.
        with open(path, "wb") as fh:
            fh.write(_csv_bytes(40))
        out2 = process_dataset(st, ds.dataset_id,
                               tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out2.status, "READY")
        self.assertIsNone((out2.processing or {}).get("error"))

    # -- 7. sin resultados parciales válidos ------------------------------
    def test_08_fallo_no_deja_activo_ni_ready(self):
        st = self._store()
        ds = self._upload(st)
        os.remove(ds.upload["path"])
        out = process_dataset(st, ds.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertNotEqual(out.status, "READY")
        self.assertFalse(out.is_active)
        self.assertFalse(out.outputs)

    # -- 8. sin mezcla entre empresas --------------------------------------
    def test_09_fallo_no_toca_otra_empresa(self):
        st_a = self._store()
        ds_a = self._upload(st_a)
        comp_b = create_company(self.tenant_store, "Empresa M1 B")
        cid_b = comp_b.company_id
        self.addCleanup(_cleanup_company_data, cid_b)
        st_b = self._store(cid_b)
        # Procesar el dataset de A con el store de B: no existe en B.
        out = process_dataset(st_b, ds_a.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-9")
        self.assertIsNone(out)
        # El dataset de A queda intacto (ni ERROR ni tocado).
        seen_a = st_a.get_dataset(ds_a.dataset_id)
        self.assertEqual(seen_a.status, "DATA_UPLOADED")
        # La auditoría del fallo queda bajo la empresa B.
        actions_b = self._audit_actions(cid_b)
        self.assertIn("DATASET_PROCESSING_FAILED", actions_b)

    # -- 9. procesamientos simultáneos coherentes ---------------------------
    def test_10_simultaneos_llegan_a_estado_terminal(self):
        st = self._store()
        ds1 = self._upload(st, filename="v1.csv")
        ds2 = self._upload(st, filename="v2.csv")
        t1 = process_in_background(st, ds1.dataset_id,
                                   tenant_store=self.tenant_store,
                                   user_id="USR-1")
        t2 = process_in_background(st, ds2.dataset_id,
                                   tenant_store=self.tenant_store,
                                   user_id="USR-1")
        t1.join(timeout=180)
        t2.join(timeout=180)
        self.assertFalse(t1.is_alive())
        self.assertFalse(t2.is_alive())
        for ds in (ds1, ds2):
            final = st.get_dataset(ds.dataset_id)
            self.assertIn(final.status, ("READY", "ERROR"),
                          f"{ds.dataset_id} atascado en {final.status}")
            self.assertNotEqual(final.status, "PROCESSING")

    # -- 10. error al persistir el estado -----------------------------------
    def test_11_error_al_persistir_no_propaga(self):
        # M1 (negativa): si save_dataset falla al registrar PROCESSING,
        # el hilo no debe morir en silencio: queda auditoría.
        st = self._store()
        ds = self._upload(st)
        orig_save = st.save_dataset
        calls = {"n": 0}

        def boom_save(d):
            calls["n"] += 1
            raise OSError("disco simulado lleno")
        st.save_dataset = boom_save
        try:
            out = process_dataset(st, ds.dataset_id,
                                  tenant_store=self.tenant_store,
                                  user_id="USR-1")
        finally:
            st.save_dataset = orig_save
        self.assertTrue(calls["n"] >= 1)
        # Sin propagación y con auditoría del fallo.
        self.assertIn("DATASET_PROCESSING_FAILED", self._audit_actions())
        # out es None o un dataset; nunca una excepción.
        self.assertTrue(out is None or hasattr(out, "status"))


    # -- 12. fallo de auditoría en PROCESSING_STARTED ----------------------
    def test_12_fallo_auditoria_no_atasca_procesamiento(self):
        # Negativa M1: si log_event lanza en DATASET_PROCESSING_STARTED,
        # el procesamiento debe continuar hasta un estado terminal
        # coherente (no quedarse atascado en PROCESSING ni morir el hilo).
        import io
        from contextlib import redirect_stderr
        st = self._store()
        ds = self._upload(st)
        orig_log = audit_mod.log_event

        # log_event se llama con kwargs (action=...).
        def boom_kw(*args, **kwargs):
            if kwargs.get("action") == "DATASET_PROCESSING_STARTED":
                raise RuntimeError("auditoría simulada caída")
            return orig_log(*args, **kwargs)

        audit_mod.log_event = boom_kw
        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                out = process_dataset(st, ds.dataset_id,
                                      tenant_store=self.tenant_store,
                                      user_id="USR-1")
        finally:
            audit_mod.log_event = orig_log
        # Sin propagación y con estado terminal coherente: el
        # procesamiento continúa aunque la auditoría falle.
        self.assertEqual(out.status, "READY")
        self.assertIsNone((out.processing or {}).get("error"))
        # El fallo de auditoría quedó en el log de diagnóstico.
        self.assertIn("audit_failed", buf.getvalue())
        self.assertIn("DATASET_PROCESSING_STARTED", buf.getvalue())
        # El reintento / procesamiento posterior sigue funcionando.
        ds2 = self._upload(st, filename="v2.csv")
        out2 = process_dataset(st, ds2.dataset_id,
                               tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out2.status, "READY")


    # -- 13. el diagnóstico no filtra datos sensibles ----------------------
    def test_13_diag_no_expone_mensaje_de_excepcion(self):
        # El mensaje de una excepción puede traer contraseñas o rutas
        # privadas: el log de diagnóstico solo debe registrar el TIPO.
        import io
        from contextlib import redirect_stderr
        from datasets.processing import _diag
        secret_pw = "ClaveFalsa-Sensitiva-987"
        secret_path = "/home/privado/secreto.txt"
        exc = RuntimeError(f"fallo con password={secret_pw} en {secret_path}")

        buf = io.StringIO()
        with redirect_stderr(buf):
            _diag("audit_failed", exc, action="DATASET_PROCESSING_STARTED",
                  dataset_id="DS-X", company_id="CMP-X")
        out = buf.getvalue()
        self.assertNotIn(secret_pw, out)
        self.assertNotIn(secret_path, out)
        self.assertNotIn("password=", out)
        # Identificadores técnicos seguros sí quedan registrados.
        self.assertIn("error_type=RuntimeError", out)
        self.assertIn("dataset_id=DS-X", out)
        self.assertIn("company_id=CMP-X", out)
        self.assertIn("DATASET_PROCESSING_STARTED", out)

    def test_14_auditoria_con_mensaje_sensible_no_filtra(self):
        # Vía process_dataset: la excepción de auditoría trae datos
        # sensibles en su mensaje; nada de eso debe llegar a stderr.
        import io
        from contextlib import redirect_stderr
        st = self._store()
        ds = self._upload(st)
        secret_pw = "ClaveFalsa-Otra-456"
        secret_path = "/etc/secrets/users.json"
        orig_log = audit_mod.log_event

        def boom_kw(*args, **kwargs):
            if kwargs.get("action") == "DATASET_PROCESSING_STARTED":
                raise RuntimeError(
                    f"caída con password={secret_pw} en {secret_path}")
            return orig_log(*args, **kwargs)

        audit_mod.log_event = boom_kw
        buf = io.StringIO()
        try:
            with redirect_stderr(buf):
                out = process_dataset(st, ds.dataset_id,
                                      tenant_store=self.tenant_store,
                                      user_id="USR-1")
        finally:
            audit_mod.log_event = orig_log
        err_text = buf.getvalue()
        self.assertNotIn(secret_pw, err_text)
        self.assertNotIn(secret_path, err_text)
        self.assertIn("error_type=RuntimeError", err_text)
        # El procesamiento continúa hasta un estado terminal coherente.
        self.assertEqual(out.status, "READY")


if __name__ == "__main__":
    unittest.main()
