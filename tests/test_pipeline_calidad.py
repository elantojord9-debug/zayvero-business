"""Regresión: corrección del pipeline de importación (rama fix/pipeline-calidad).

Cubre:
 1. detect_cancellation(pd.NA) -> "unknown" (comportamiento documentado).
 2. Calidad: ausencia estructural vs valores faltantes (structural_gaps).
 3. Advertencia de Invoice sin asignar en suggest_mapping.
 4. Pipeline completo con CSV sintético de 30 filas (caso zayvero_demo.csv).
"""

import json
import os
import sys
import unittest

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from normalize import detect_cancellation, normalize  # noqa: E402
from datasets.mapping import suggest_mapping, confirm_mapping  # noqa: E402
from profiling.quality_score import compute_quality_score  # noqa: E402


def _demo_cols():
    return ["Tipo", "Producto_o_concepto", "Cantidad",
            "Precio_unitario_DOP", "Gasto_DOP", "Fecha"]


def _synthetic_csv(path):
    """CSV sintético equivalente a zayvero_demo.csv: 30 filas,
    22 ventas (precio positivo) + 8 gastos (precio 0)."""
    rows = []
    gasto_idx = {2, 6, 10, 14, 18, 22, 26, 29}
    for i in range(30):
        es_gasto = i in gasto_idx
        rows.append({
            "Tipo": "gasto" if es_gasto else "venta",
            "Producto_o_concepto": f"Concepto {i + 1}",
            "Cantidad": (i % 5) + 1,
            "Precio_unitario_DOP": 0 if es_gasto else 100 * (i + 1),
            "Gasto_DOP": 50 if es_gasto else 0,
            "Fecha": f"2026-09-{(i % 30) + 1:02d}",
        })
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


class CancellationTest(unittest.TestCase):
    """detect_cancellation: ausentes -> unknown (documentado)."""

    def test_01_pd_na_es_unknown(self):
        self.assertEqual(detect_cancellation(pd.NA), "unknown")

    def test_02_none_es_unknown(self):
        self.assertEqual(detect_cancellation(None), "unknown")

    def test_03_nan_es_unknown(self):
        self.assertEqual(detect_cancellation(float("nan")), "unknown")
        self.assertEqual(detect_cancellation(pd.NaT), "unknown")

    def test_04_prefijo_cancela(self):
        self.assertEqual(detect_cancellation("C12345"), "cancelled")

    def test_05_valida_es_completed(self):
        self.assertEqual(detect_cancellation("12345"), "completed")
        self.assertEqual(detect_cancellation("FAC-2026-001"), "completed")

    def test_06_reglas_personalizadas(self):
        self.assertEqual(
            detect_cancellation("X1", {"invoice_values": ["X1", "X2"]}),
            "cancelled",
        )
        self.assertEqual(
            detect_cancellation("Y9", {"invoice_values": ["X1"]}), "completed"
        )


class StructuralQualityTest(unittest.TestCase):
    """structural_gaps vs missing_critical."""

    def _base_df(self, with_orig_invoice=True, null_transactions=0, n=10):
        df = pd.DataFrame({
            "company_id": ["c1"] * n,
            "Transaction": [f"F{i}" for i in range(n)],
            "Date": pd.to_datetime(["2026-09-01"] * n),
            "Quantity": [float(i + 1) for i in range(n)],
            "UnitPrice": [100.0 + i for i in range(n)],
            "transaction_status": ["unknown"] * n,
        })
        if with_orig_invoice:
            df["orig_Invoice"] = [f"F{i}" for i in range(n)]
            if null_transactions:
                df.loc[: null_transactions - 1, "Transaction"] = None
                df.loc[: null_transactions - 1, "orig_Invoice"] = None
        df["orig_InvoiceDate"] = ["2026-09-01"] * n
        df["orig_Quantity"] = [str(i + 1) for i in range(n)]
        df["orig_UnitPrice"] = [str(100.0 + i) for i in range(n)]
        return df

    def test_10_ausencia_estructural_no_maximiza(self):
        # Sin orig_Invoice: Transaction es estructuralmente ausente.
        df = self._base_df(with_orig_invoice=False)
        qs = compute_quality_score(df, {"column_resolution": {"unmapped": []}})
        comps = {d["component"]: d for d in qs["deductions"]}
        self.assertIn("structural_gaps", comps)
        self.assertEqual(comps["structural_gaps"]["points"], 8)
        # missing_critical NO debe estar maximizado: los datos existen.
        self.assertNotIn("missing_critical", comps)
        self.assertIn("Transaction", comps["structural_gaps"]["detail"])

    def test_11_nulos_reales_siguen_penalizando(self):
        # Con orig_Invoice presente pero 2 Transaction nulos de 50:
        # es dato faltante -> missing_critical 4% x 3 = 12 pts, sin tope.
        df = self._base_df(with_orig_invoice=True, null_transactions=2, n=50)
        qs = compute_quality_score(df, {"column_resolution": {"unmapped": []}})
        comps = {d["component"]: d for d in qs["deductions"]}
        self.assertIn("missing_critical", comps)
        self.assertNotIn("structural_gaps", comps)
        self.assertEqual(comps["missing_critical"]["points"], 12)

    def test_12_sin_orig_compatibilidad_anterior(self):
        # DataFrame sin ninguna columna orig_: comportamiento anterior.
        df = pd.DataFrame({
            "Transaction": [None, None],
            "Date": pd.to_datetime(["2026-09-01", "2026-09-02"]),
            "Quantity": [1.0, 2.0],
            "UnitPrice": [10.0, 20.0],
        })
        qs = compute_quality_score(df, {"column_resolution": {"unmapped": []}})
        comps = {d["component"]: d for d in qs["deductions"]}
        self.assertIn("missing_critical", comps)
        self.assertNotIn("structural_gaps", comps)

    def test_13_dataset_completo_sin_cambios(self):
        # Como Online Retail II: todo presente y válido -> sin deducciones nuevas.
        df = self._base_df(with_orig_invoice=True)
        qs = compute_quality_score(df, {"column_resolution": {"unmapped": []}})
        comps = {d["component"]: d for d in qs["deductions"]}
        self.assertNotIn("structural_gaps", comps)
        self.assertNotIn("missing_critical", comps)
        self.assertEqual(qs["score"], 100)


class MappingInvoiceWarningTest(unittest.TestCase):
    """Advertencia clara si Invoice queda sin asignar (antes de confirmar)."""

    def test_20_avisa_sin_invoice(self):
        sug = suggest_mapping(_demo_cols())
        self.assertTrue(sug["warnings"])
        blob = " ".join(sug["warnings"]).lower()
        self.assertIn("invoice", blob)

    def test_21_no_avisa_con_invoice(self):
        sug = suggest_mapping(["factura", "cantidad", "fecha", "precio"])
        by = {s["canonical"]: s for s in sug["suggestions"]}
        self.assertEqual(by["Invoice"]["source"], "factura")
        self.assertEqual(sug["warnings"], [])

    def test_22_confirm_permite_continuar_sin_invoice(self):
        # El mapeo sin Invoice se puede confirmar (no es bloqueante).
        res = confirm_mapping(
            _demo_cols(),
            {"Quantity": "Cantidad", "InvoiceDate": "Fecha",
             "UnitPrice": "Precio_unitario_DOP"},
        )
        self.assertTrue(res["ok"])


class FullPipelineReproTest(unittest.TestCase):
    """CSV sintético de 30 filas por el pipeline real (caso zayvero_demo.csv)."""

    def test_30_pipeline_completo(self):
        from datasets.processing import _apply_mapping_work_copy
        from ingestion.aliases import resolve_columns

        csv_path = _synthetic_csv("/tmp/repro_demo.csv")
        res = confirm_mapping(
            _demo_cols(),
            {"Quantity": "Cantidad", "InvoiceDate": "Fecha",
             "UnitPrice": "Precio_unitario_DOP"},
        )
        self.assertTrue(res["ok"])
        work = _apply_mapping_work_copy(csv_path, res["mapping"], "/tmp/work_repro2")
        df_canon, resolution = resolve_columns(pd.read_csv(work))
        df_norm, _ = normalize(df_canon, "test-company")

        # Transaction: ausente estructural -> status unknown (no "completed").
        self.assertEqual(int(df_norm["Transaction"].isna().sum()), 30)
        self.assertTrue((df_norm["transaction_status"] == "unknown").all())

        # Calidad: sin penalización máxima por ausencia estructural.
        audit_record = {"column_resolution": resolution.to_dict()}
        qs = compute_quality_score(df_norm, audit_record)
        comps = {d["component"]: d for d in qs["deductions"]}
        self.assertIn("structural_gaps", comps)
        self.assertNotIn("missing_critical", comps)
        # 8 gastos con precio 0 -> non_positive se mantiene (regla sin cambiar).
        # 26.67% x 1 = 26.67, con tope 10 -> 10 pts.
        self.assertEqual(comps["non_positive_prices"]["points"], 10)
        # 3 sin mapear -> 6 pts.
        self.assertEqual(comps["unmapped_columns"]["points"], 6)
        # Score honesto: 100 - 8 (estructural) - 10 (precios<=0) - 6 (unmapped)
        # = 76 (antes: 100 - 30 - 10 - 6 = 54 con -30 injusto por Transaction).
        self.assertAlmostEqual(qs["score"], 76.0, places=1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
