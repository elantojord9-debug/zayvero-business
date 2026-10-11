"""QA-006 — Invariante: si is_active=True entonces status=READY.

Un dataset en ERROR o INSUFFICIENT_DATA no puede continuar activo como
análisis de la empresa, ni siquiera si antes lo estaba y se reprocesa
por vía directa (la API ya bloquea reprocesar un READY).

Cubre las dos rutas de fallo de datasets/processing.py:
- _fail_before_processing() (fallo en la fase previa a PROCESSING).
- El bloque except final de process_dataset() (fallo en el guardado
  final u otra fase del pipeline).

Datos 100% sintéticos. Ejecutar: .venv/bin/python -m unittest tests.test_qa_006_error_deactivation
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tenant import TenantStore, create_company
from tenant import audit as tenant_audit

import datasets.store as ds_store_mod
from datasets import (
    DatasetStore, receive_upload, build_review, suggest_mapping,
    confirm_mapping, process_dataset,
)
from datasets.store import utcnow_iso

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STAGE_DIRS = ["datasets", "uploads", "processed", "profiles", "anomalies",
              "findings", "context", "predictions", "prediction_intelligence",
              "prediction_validation", "business_context"]

COLS = "Invoice,StockCode,Description,Quantity,InvoiceDate,UnitPrice,CustomerID,Country"


def _valido_bytes(n=12):
    lines = [COLS]
    for i in range(1, n + 1):
        lines.append(
            f"{60000 + i},P{i % 4},Producto {i % 4},{(i % 5) + 1},"
            f"2024-{(i % 12) + 1:02d}-10,{(i % 9) + 1}.50,C{i % 3},España")
    return ("\n".join(lines) + "\n").encode("utf-8")


def _basura_bytes():
    lines = [COLS,
             "1,1,prod A,MUCHOS,ayer,caro,,",
             "2,2,prod B,,mañana,,,"]
    return ("\n".join(lines) + "\n").encode("utf-8")


def _chunks(data: bytes, size: int = 4096):
    for i in range(0, len(data), size):
        yield data[i:i + size]


def _cleanup_company_data(company_id: str):
    for stage in STAGE_DIRS:
        path = os.path.join(PROJECT_ROOT, "data", stage, company_id)
        shutil.rmtree(path, ignore_errors=True)


class ErrorDeactivationTest(unittest.TestCase):
    """QA-006: ERROR nunca permanece activo."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zayvero_qa6_")
        self.orig_uploads = ds_store_mod.UPLOADS_DIR
        ds_store_mod.UPLOADS_DIR = os.path.join(self.tmp, "uploads")
        self.tmp_tenant = tempfile.mkdtemp(prefix="zayvero_qa6_tenant_")
        self.tenant_store = TenantStore(self.tmp_tenant)
        self.comp_a = create_company(self.tenant_store, "Empresa QA6 A")
        self.comp_b = create_company(self.tenant_store, "Empresa QA6 B")

    def tearDown(self):
        ds_store_mod.UPLOADS_DIR = self.orig_uploads
        shutil.rmtree(self.tmp, ignore_errors=True)
        shutil.rmtree(self.tmp_tenant, ignore_errors=True)
        _cleanup_company_data(self.comp_a.company_id)
        _cleanup_company_data(self.comp_b.company_id)

    # -- helpers ------------------------------------------------------
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

    def _activo_previo(self, st, cid):
        """Dataset válido procesado: READY y activo."""
        ds = self._upload_confirm(st, _valido_bytes(), "valido.csv")
        out = process_dataset(st, ds.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out.status, "READY")
        self.assertTrue(out.is_active)
        return ds

    def _audit_actions(self, cid):
        return [e.action for e in self.tenant_store.list_audit(cid)]

    # -- 1. activo previo + fallo en guardado final --------------------
    def test_01_fallo_guardado_final_desactiva(self):
        st = DatasetStore(self.comp_a.company_id)
        ds = self._activo_previo(st, self.comp_a.company_id)
        orig_save = st.save_dataset

        def boom_final(d):
            if d.status == "READY":  # el guardado final del reproceso
                raise OSError("disco simulado lleno")
            return orig_save(d)

        st.save_dataset = boom_final
        try:
            out = process_dataset(st, ds.dataset_id,
                                  tenant_store=self.tenant_store,
                                  user_id="USR-1")
        finally:
            st.save_dataset = orig_save
        self.assertEqual(out.status, "ERROR")
        self.assertFalse(out.is_active,
                         "QA-006: un ERROR no puede permanecer activo")
        disk = st.get_dataset(ds.dataset_id)
        self.assertEqual(disk.status, "ERROR")
        self.assertFalse(disk.is_active)

    # -- 2. get_active no devuelve el fallido -------------------------
    def test_02_get_active_ignora_fallido(self):
        st = DatasetStore(self.comp_a.company_id)
        ds = self._activo_previo(st, self.comp_a.company_id)
        orig_save = st.save_dataset
        st.save_dataset = lambda d: (_ for _ in ()).throw(
            OSError("x")) if d.status == "READY" else orig_save(d)
        try:
            process_dataset(st, ds.dataset_id,
                            tenant_store=self.tenant_store, user_id="USR-1")
        finally:
            st.save_dataset = orig_save
        self.assertIsNone(st.get_active(),
                          "QA-006: ningún análisis inválido como activo")

    # -- 3. fallo antes del procesamiento -----------------------------
    def test_03_fallo_previo_a_processing_desactiva(self):
        st = DatasetStore(self.comp_a.company_id)
        ds = self._activo_previo(st, self.comp_a.company_id)
        orig_save = st.save_dataset
        calls = {"n": 0}

        def boom_primero(d):
            calls["n"] += 1
            if calls["n"] == 1:  # el _mark(PROCESSING)
                raise OSError("fallo al persistir estado inicial")
            return orig_save(d)

        st.save_dataset = boom_primero
        try:
            out = process_dataset(st, ds.dataset_id,
                                  tenant_store=self.tenant_store,
                                  user_id="USR-1")
        finally:
            st.save_dataset = orig_save
        self.assertIsNotNone(out)
        self.assertEqual(out.status, "ERROR")
        self.assertFalse(out.is_active)
        disk = st.get_dataset(ds.dataset_id)
        self.assertEqual((disk.status, disk.is_active), ("ERROR", False))

    # -- 4. coherencia tras guardar y recargar ------------------------
    def test_04_recarga_coherente(self):
        st = DatasetStore(self.comp_a.company_id)
        ds = self._activo_previo(st, self.comp_a.company_id)
        orig_save = st.save_dataset
        st.save_dataset = lambda d: (_ for _ in ()).throw(
            OSError("x")) if d.status == "READY" else orig_save(d)
        try:
            process_dataset(st, ds.dataset_id,
                            tenant_store=self.tenant_store, user_id="USR-1")
        finally:
            st.save_dataset = orig_save
        st2 = DatasetStore(self.comp_a.company_id)
        seen = st2.get_dataset(ds.dataset_id)
        self.assertEqual(seen.status, "ERROR")
        self.assertFalse(seen.is_active)
        self.assertIsNone(st2.get_active())

    # -- 5. auditoría del fallo ---------------------------------------
    def test_05_auditoria_failed_registrada(self):
        st = DatasetStore(self.comp_a.company_id)
        ds = self._activo_previo(st, self.comp_a.company_id)
        orig_save = st.save_dataset
        st.save_dataset = lambda d: (_ for _ in ()).throw(
            OSError("x")) if d.status == "READY" else orig_save(d)
        try:
            process_dataset(st, ds.dataset_id,
                            tenant_store=self.tenant_store, user_id="USR-1")
        finally:
            st.save_dataset = orig_save
        self.assertIn("DATASET_PROCESSING_FAILED",
                      self._audit_actions(self.comp_a.company_id))

    # -- 6. aislamiento empresa B -------------------------------------
    def test_06_empresa_b_intacta(self):
        st_a = DatasetStore(self.comp_a.company_id)
        st_b = DatasetStore(self.comp_b.company_id)
        ds_b = self._upload_confirm(st_b, _valido_bytes(), "b.csv")
        out_b = process_dataset(st_b, ds_b.dataset_id,
                                tenant_store=self.tenant_store,
                                user_id="USR-2")
        self.assertEqual((out_b.status, out_b.is_active), ("READY", True))
        ds_a = self._activo_previo(st_a, self.comp_a.company_id)
        orig_save = st_a.save_dataset
        st_a.save_dataset = lambda d: (_ for _ in ()).throw(
            OSError("x")) if d.status == "READY" else orig_save(d)
        try:
            process_dataset(st_a, ds_a.dataset_id,
                            tenant_store=self.tenant_store, user_id="USR-1")
        finally:
            st_a.save_dataset = orig_save
        seen_b = st_b.get_dataset(ds_b.dataset_id)
        self.assertEqual((seen_b.status, seen_b.is_active), ("READY", True))
        self.assertEqual(st_b.dataset_state(), "READY")
        self.assertEqual(st_b.get_active().dataset_id, ds_b.dataset_id)

    # -- 7. válido → READY y activable --------------------------------
    def test_07_valido_ready_y_activable(self):
        st = DatasetStore(self.comp_a.company_id)
        ds = self._upload_confirm(st, _valido_bytes(), "v1.csv")
        out = process_dataset(st, ds.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out.status, "READY")
        ds2 = self._upload_confirm(st, _valido_bytes(), "v2.csv")
        out2 = process_dataset(st, ds2.dataset_id,
                               tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out2.status, "READY")
        act = st.set_active(ds2.dataset_id)
        self.assertTrue(act.is_active)
        self.assertFalse(st.get_dataset(ds.dataset_id).is_active)
        self.assertEqual(st.get_active().dataset_id, ds2.dataset_id)

    # -- 8. insuficiente → no activo ----------------------------------
    def test_08_insuficiente_no_activo(self):
        st = DatasetStore(self.comp_a.company_id)
        ds = self._upload_confirm(st, _basura_bytes(), "basura.csv")
        out = process_dataset(st, ds.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out.status, "INSUFFICIENT_DATA")
        self.assertFalse(out.is_active)
        self.assertIsNone(st.get_active())

    # -- 9. fallo simultáneo de almacenamiento y auditoría ------------
    def test_09_fallo_almacenamiento_y_auditoria_sin_falsa_afirmacion(self):
        """LÍMITE DE RECUPERACIÓN DOCUMENTADO: si el almacenamiento y la
        auditoría fallan a la vez, process_dataset NO afirma éxito falso.

        - Fase init (_fail_before_processing): totalmente defensiva;
          retorna None, deja en disco el último estado real y el fallo
          de auditoría queda en el log de diagnóstico.
        - Fase final (except principal): si el guardado del estado ERROR
          también falla, la excepción de almacenamiento propaga (no hay
          retorno que finja un ERROR persistido). En disco queda el
          último estado persistido (PROCESSING del _mark); la guarda 409
          de la API bloquearía un reintento inmediato sobre ese registro:
          un fallo total de almacenamiento requiere intervención del
          operador. QA-006 no cambia este límite preexistente."""
        st = DatasetStore(self.comp_a.company_id)
        ds = self._activo_previo(st, self.comp_a.company_id)

        def boom_audit(*a, **k):
            raise RuntimeError("auditoría caída")

        orig_log = tenant_audit.log_event
        tenant_audit.log_event = boom_audit
        try:
            # Sub-caso A: falla TODO guardado (_mark y el del ERROR) + auditoría.
            orig_save = st.save_dataset

            def boom_todo(d):
                raise OSError("almacenamiento caído")

            st.save_dataset = boom_todo
            try:
                out_a = process_dataset(
                    st, ds.dataset_id, tenant_store=self.tenant_store,
                    user_id="USR-1")
            finally:
                st.save_dataset = orig_save
            self.assertIsNone(out_a,
                              "fase init: sin afirmación falsa de éxito")
            disk_a = DatasetStore(self.comp_a.company_id).get_dataset(
                ds.dataset_id)
            self.assertEqual((disk_a.status, disk_a.is_active),
                             ("READY", True),
                             "el registro conserva su estado previo real")

            # Sub-caso B: falla el guardado final y el del ERROR + auditoría.
            # Con el ajuste final QA-006, el _mark ya persistió
            # PROCESSING con is_active=False.
            def boom_final_y_error(d):
                if d.status in ("READY", "ERROR"):
                    raise OSError("almacenamiento caído")
                return orig_save(d)

            st.save_dataset = boom_final_y_error
            try:
                with self.assertRaises(OSError):
                    process_dataset(st, ds.dataset_id,
                                    tenant_store=self.tenant_store,
                                    user_id="USR-1")
            finally:
                st.save_dataset = orig_save
            disk_b = DatasetStore(self.comp_a.company_id).get_dataset(
                ds.dataset_id)
            self.assertEqual((disk_b.status, disk_b.is_active),
                             ("PROCESSING", False),
                             "sin ERROR inventado: último estado real, "
                             "y PROCESSING nunca queda activo")
        finally:
            tenant_audit.log_event = orig_log


    # -- 10. PROCESSING nunca activo en reproceso directo -------------
    def test_10_processing_no_queda_activo_en_reproceso(self):
        st = DatasetStore(self.comp_a.company_id)
        ds = self._activo_previo(st, self.comp_a.company_id)
        orig_save = st.save_dataset
        seen = []

        def spy(d):
            seen.append((d.status, d.is_active))
            return orig_save(d)

        st.save_dataset = spy
        try:
            out = process_dataset(st, ds.dataset_id,
                                  tenant_store=self.tenant_store,
                                  user_id="USR-1")
        finally:
            st.save_dataset = orig_save
        self.assertEqual(out.status, "READY")
        processing_saves = [s for s in seen if s[0] == "PROCESSING"]
        self.assertTrue(processing_saves,
                        "el reproceso debe persistir PROCESSING")
        for _, active in processing_saves:
            self.assertFalse(
                active,
                "QA-006: PROCESSING nunca persiste con is_active=True")

    # -- 11. fallo final + ERROR → PROCESSING inactivo en disco --------
    def test_11_fallo_final_y_error_deja_processing_inactivo(self):
        st = DatasetStore(self.comp_a.company_id)
        ds = self._activo_previo(st, self.comp_a.company_id)
        orig_save = st.save_dataset

        def boom_final_y_error(d):
            if d.status in ("READY", "ERROR"):
                raise OSError("almacenamiento caído")
            return orig_save(d)

        st.save_dataset = boom_final_y_error
        try:
            with self.assertRaises(OSError):
                process_dataset(st, ds.dataset_id,
                                tenant_store=self.tenant_store,
                                user_id="USR-1")
        finally:
            st.save_dataset = orig_save
        disk = DatasetStore(self.comp_a.company_id).get_dataset(
            ds.dataset_id)
        self.assertEqual((disk.status, disk.is_active),
                         ("PROCESSING", False))

    # -- 12. fallo inicial: READY previo conserva su estado real -------
    def test_12_fallo_inicial_conserva_ready_previo(self):
        st = DatasetStore(self.comp_a.company_id)
        ds = self._activo_previo(st, self.comp_a.company_id)
        orig_save = st.save_dataset
        st.save_dataset = lambda d: (_ for _ in ()).throw(
            OSError("almacenamiento caído"))
        try:
            out = process_dataset(st, ds.dataset_id,
                                  tenant_store=self.tenant_store,
                                  user_id="USR-1")
        finally:
            st.save_dataset = orig_save
        self.assertIsNone(out)
        disk = DatasetStore(self.comp_a.company_id).get_dataset(
            ds.dataset_id)
        self.assertEqual((disk.status, disk.is_active), ("READY", True),
                         "sin cambios persistibles, el registro conserva "
                         "su estado real anterior")
        self.assertIn("DATASET_PROCESSING_FAILED",
                      self._audit_actions(self.comp_a.company_id))

    # -- 13. reproceso válido → READY y reactivable --------------------
    def test_13_reproceso_valido_reactivable(self):
        st = DatasetStore(self.comp_a.company_id)
        ds = self._activo_previo(st, self.comp_a.company_id)
        out = process_dataset(st, ds.dataset_id,
                              tenant_store=self.tenant_store, user_id="USR-1")
        self.assertEqual(out.status, "READY")
        # Reglas existentes: al ser READY sin otro activo, se reactiva.
        self.assertTrue(out.is_active)
        self.assertEqual(st.get_active().dataset_id, ds.dataset_id)
        # set_active sigue operativo con normalidad.
        ds2 = self._upload_confirm(st, _valido_bytes(), "v2.csv")
        process_dataset(st, ds2.dataset_id,
                        tenant_store=self.tenant_store, user_id="USR-1")
        act = st.set_active(ds2.dataset_id)
        self.assertTrue(act.is_active)
        self.assertFalse(st.get_dataset(ds.dataset_id).is_active)

    # -- 14. aislamiento durante el reproceso --------------------------
    def test_14_empresa_b_intacta_en_reproceso(self):
        st_a = DatasetStore(self.comp_a.company_id)
        st_b = DatasetStore(self.comp_b.company_id)
        ds_b = self._upload_confirm(st_b, _valido_bytes(), "b.csv")
        out_b = process_dataset(st_b, ds_b.dataset_id,
                                tenant_store=self.tenant_store,
                                user_id="USR-2")
        self.assertEqual((out_b.status, out_b.is_active), ("READY", True))
        ds_a = self._activo_previo(st_a, self.comp_a.company_id)
        out_a = process_dataset(st_a, ds_a.dataset_id,
                                tenant_store=self.tenant_store,
                                user_id="USR-1")
        self.assertEqual(out_a.status, "READY")
        seen_b = st_b.get_dataset(ds_b.dataset_id)
        self.assertEqual((seen_b.status, seen_b.is_active), ("READY", True))
        self.assertEqual(st_b.get_active().dataset_id, ds_b.dataset_id)


if __name__ == "__main__":
    unittest.main()
