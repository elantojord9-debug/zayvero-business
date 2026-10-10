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
    return ["Nota", "Fecha", "Tipo", "Producto_o_concepto", "Cantidad",
            "Precio_unitario_DOP", "Gasto_DOP"]


def _synthetic_csv(path, empty_price_row=False):
    """CSV sintético equivalente al zayvero_demo.csv real: 7 columnas,
    30 filas (21 ventas + 9 gastos, 1-30 sep 2026). Los 9 gastos suman
    RD$17,350. Si empty_price_row=True, un gasto lleva la celda de precio
    vacía (reproduce el conteo 8 de producción)."""
    gastos_montos = [2000, 1500, 3000, 1200, 2500, 1800, 2200, 1950, 1200]
    assert sum(gastos_montos) == 17350
    gasto_dias = {3: 0, 7: 1, 11: 2, 14: 3, 18: 4, 21: 5, 24: 6, 27: 7, 30: 8}
    rows = []
    for d in range(1, 31):
        fecha = f"2026-09-{d:02d}"
        if d in gasto_dias:
            gi = gasto_dias[d]
            precio = "" if (empty_price_row and gi == 0) else 0
            rows.append({
                "Nota": f"Gasto operativo {d}", "Fecha": fecha, "Tipo": "Gasto",
                "Producto_o_concepto": f"Gasto #{gi + 1}", "Cantidad": 1,
                "Precio_unitario_DOP": precio, "Gasto_DOP": gastos_montos[gi],
            })
        else:
            rows.append({
                "Nota": "", "Fecha": fecha, "Tipo": "Venta",
                "Producto_o_concepto": f"Producto {d}",
                "Cantidad": (d % 5) + 1,
                "Precio_unitario_DOP": 250 + d * 137, "Gasto_DOP": 0,
            })
    pd.DataFrame(rows, columns=_demo_cols()).to_csv(path, index=False)
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

    def test_14_sin_canonicos_sigue_dando_cero_filas(self):
        # Regresión test_28: un archivo sin ninguna columna canónica debe
        # producir 0 filas (el pipeline lo reporta como ERROR por descuadre),
        # no pasar a READY. El bucle de orig_ no debe crear filas solo.
        df = pd.DataFrame({"esto": [1], "no": [2], "valido": [None]})
        df_norm, _ = normalize(df, "test-company")
        self.assertEqual(len(df_norm), 0)
        self.assertNotIn("orig_esto", df_norm.columns)

    def test_15_con_canonicos_conserva_orig(self):
        # Con al menos un canónico, las columnas no canónicas sí se conservan.
        df = pd.DataFrame({"Quantity": [2], "Tipo": ["Gasto"]})
        df_norm, _ = normalize(df, "test-company")
        self.assertEqual(len(df_norm), 1)
        self.assertIn("orig_Tipo", df_norm.columns)
        self.assertEqual(df_norm["orig_Tipo"].iloc[0], "Gasto")

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

    def _run_pipeline(self, csv_path, work_dir):
        from datasets.processing import _apply_mapping_work_copy
        from ingestion.aliases import resolve_columns

        res = confirm_mapping(
            _demo_cols(),
            {"Quantity": "Cantidad", "InvoiceDate": "Fecha",
             "UnitPrice": "Precio_unitario_DOP"},
        )
        self.assertTrue(res["ok"])
        work = _apply_mapping_work_copy(csv_path, res["mapping"], work_dir)
        df_canon, resolution = resolve_columns(pd.read_csv(work))
        df_norm, _ = normalize(df_canon, "test-company")
        return df_norm, resolution

    def test_30_pipeline_completo(self):
        from profiling.quality_score import compute_quality_score

        df_norm, resolution = self._run_pipeline(
            _synthetic_csv("/tmp/repro_demo.csv"), "/tmp/work_repro3")
        # Las 30 filas se procesan (ninguna descartada).
        self.assertEqual(len(df_norm), 30)
        # Columnas no canónicas conservadas como orig_ (no descartadas).
        for c in ("orig_Nota", "orig_Tipo", "orig_Gasto_DOP"):
            self.assertIn(c, df_norm.columns)

        # Transaction: ausente estructural -> status unknown (no "completed").
        self.assertEqual(int(df_norm["Transaction"].isna().sum()), 30)
        self.assertTrue((df_norm["transaction_status"] == "unknown").all())

        # Calidad: sin penalización máxima por ausencia estructural.
        audit_record = {"column_resolution": resolution.to_dict()}
        qs = compute_quality_score(df_norm, audit_record)
        comps = {d["component"]: d for d in qs["deductions"]}
        self.assertIn("structural_gaps", comps)
        self.assertNotIn("missing_critical", comps)
        # Los 9 gastos (Tipo=gasto, precio 0) NO penalizan como ventas.
        self.assertNotIn("non_positive_prices", comps)
        # 4 sin mapear (Nota, Tipo, Producto_o_concepto, Gasto_DOP) -> 8 pts.
        self.assertEqual(comps["unmapped_columns"]["points"], 8)
        # Score honesto: 100 - 8 (estructural) - 8 (unmapped) = 84.
        self.assertAlmostEqual(qs["score"], 84.0, places=1)

    def test_31_ventas_y_gastos_identificados(self):
        from profiling.quality_score import _identify_expenses

        df_norm, _ = self._run_pipeline(
            _synthetic_csv("/tmp/repro_demo2.csv"), "/tmp/work_repro4")
        is_exp = _identify_expenses(df_norm)
        # 21 ventas y 9 gastos identificados correctamente.
        self.assertEqual(int(is_exp.sum()), 9)
        self.assertEqual(int((~is_exp).sum()), 21)
        # Ningún gasto clasificado como venta con precio inválido.
        bad_sales = ((df_norm["UnitPrice"] <= 0) & ~is_exp).sum()
        self.assertEqual(int(bad_sales), 0)
        # Los 9 gastos suman RD$17,350 (importe en Gasto_DOP conservado).
        total = pd.to_numeric(df_norm.loc[is_exp, "orig_Gasto_DOP"],
                              errors="coerce").sum()
        self.assertEqual(int(total), 17350)

    def test_32_venta_precio_cero_si_alerta(self):
        # Una VENTA con precio 0 sí genera la alerta.
        from profiling.quality_score import compute_quality_score

        df_norm, _ = self._run_pipeline(
            _synthetic_csv("/tmp/repro_demo3.csv"), "/tmp/work_repro5")
        df_norm.loc[df_norm["orig_InvoiceDate"] == "2026-09-01",
                    "UnitPrice"] = 0
        qs = compute_quality_score(
            df_norm, {"column_resolution": {"unmapped": []}})
        comps = {d["component"]: d for d in qs["deductions"]}
        self.assertIn("non_positive_prices", comps)
        self.assertIn("1 precios", comps["non_positive_prices"]["detail"])

    def test_33_sin_tipo_es_conservador(self):
        # Registro sin tipo identificable + precio 0 -> sí cuenta (conservador).
        from profiling.quality_score import compute_quality_score

        df_norm, _ = self._run_pipeline(
            _synthetic_csv("/tmp/repro_demo4.csv"), "/tmp/work_repro6")
        mask = df_norm["orig_InvoiceDate"] == "2026-09-02"
        df_norm.loc[mask, "orig_Tipo"] = None
        df_norm.loc[mask, "UnitPrice"] = 0
        qs = compute_quality_score(
            df_norm, {"column_resolution": {"unmapped": []}})
        comps = {d["component"]: d for d in qs["deductions"]}
        self.assertIn("non_positive_prices", comps)

    def test_34_discrepancia_8_vs_9(self):
        # Causa raíz: una celda VACÍA (no "0") -> NaN -> no cuenta en <= 0.
        from profiling.quality_score import compute_quality_score

        df_norm, resolution = self._run_pipeline(
            _synthetic_csv("/tmp/repro_demo5.csv", empty_price_row=True),
            "/tmp/work_repro7")
        n_le0 = int((df_norm["UnitPrice"] <= 0).sum())
        self.assertEqual(n_le0, 8)  # producción contó 8, no 9
        qs = compute_quality_score(
            df_norm, {"column_resolution": resolution.to_dict()})
        comps = {d["component"]: d for d in qs["deductions"]}
        # Con la regla corregida, los 8 gastos restantes tampoco penalizan.
        self.assertNotIn("non_positive_prices", comps)

    def test_35_online_retail_ii_sin_cambios(self):
        # Online Retail II conserva los resultados previos (94.33).
        import glob

        from profiling.quality_score import compute_quality_score

        cands = glob.glob(
            os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))),
                "data/processed/demo-retail/*.parquet"))
        if not cands:
            self.skipTest("sin parquet demo local")
        df = pd.read_parquet(cands[0])
        if "company_id" in df.columns:
            df = df[df["company_id"] == "demo-retail"].copy()
        qs = compute_quality_score(df, None)
        self.assertAlmostEqual(qs["score"], 94.33, places=1)
        comps = {d["component"] for d in qs["deductions"]}
        self.assertNotIn("structural_gaps", comps)


class ExpenseAmountValidationTest(unittest.TestCase):
    """Tipo=gasto solo se excluye de la alerta con Gasto_DOP positivo.

    Regresión: un registro con Tipo=gasto pero Gasto_DOP ausente, vacío,
    cero o negativo NO se excluye automáticamente de la alerta de precios
    no positivos (tratamiento conservador: se evalúa como venta).
    """

    def _df(self, rows):
        # rows: tuplas (orig_Tipo, orig_Gasto_DOP, UnitPrice)
        return pd.DataFrame(
            {
                "orig_Tipo": [r[0] for r in rows],
                "orig_Gasto_DOP": [r[1] for r in rows],
                "UnitPrice": [r[2] for r in rows],
            }
        )

    def _comps(self, df):
        from profiling.quality_score import compute_quality_score

        qs = compute_quality_score(df, {"column_resolution": {"unmapped": []}})
        return {d["component"]: d for d in qs["deductions"]}

    def test_36_gasto_sin_importe_si_alerta(self):
        # Tipo=gasto + Gasto_DOP vacío (None) + precio 0 -> SÍ alerta.
        comps = self._comps(self._df([("Gasto", None, 0)]))
        self.assertIn("non_positive_prices", comps)
        det = comps["non_positive_prices"]["detail"]
        self.assertIn("1 precios", det)
        self.assertIn("sin importe positivo", det)

    def test_37_gasto_importe_cero_si_alerta(self):
        # Tipo=gasto + Gasto_DOP = 0 + precio 0 -> SÍ alerta.
        comps = self._comps(self._df([("Gasto", 0, 0)]))
        self.assertIn("non_positive_prices", comps)

    def test_38_gasto_importe_negativo_si_alerta(self):
        # Tipo=gasto + Gasto_DOP negativo + precio 0 -> SÍ alerta.
        comps = self._comps(self._df([("Gasto", -500, 0)]))
        self.assertIn("non_positive_prices", comps)

    def test_39_gasto_importe_positivo_no_alerta(self):
        # Tipo=gasto + Gasto_DOP positivo + precio 0 -> NO alerta.
        comps = self._comps(self._df([("Gasto", 1500, 0)]))
        self.assertNotIn("non_positive_prices", comps)

    def test_40_gasto_sin_columna_importe_si_alerta(self):
        # Sin columna orig_Gasto_DOP: conservador, el gasto SÍ cuenta.
        df = pd.DataFrame({"orig_Tipo": ["Gasto"], "UnitPrice": [0]})
        comps = self._comps(df)
        self.assertIn("non_positive_prices", comps)

    def test_41_mensaje_distinguie_confirmados(self):
        # Mezcla: el detalle distingue confirmados de no confirmados.
        comps = self._comps(
            self._df([("Gasto", 1500, 0), ("Gasto", 0, 0), ("Venta", 0, 0)])
        )
        det = comps["non_positive_prices"]["detail"]
        self.assertIn("2 precios", det)  # gasto sin importe + venta
        self.assertIn("1 gasto(s) confirmado(s)", det)
        self.assertIn("sin importe positivo", det)

    def test_42_helper_solo_confirma_con_importe(self):
        # _identify_expenses exige ambas condiciones.
        from profiling.quality_score import _identify_expenses

        df = self._df(
            [
                ("Gasto", 1500, 0),   # confirmado
                ("Gasto", 0, 0),      # no confirmado
                ("Gasto", None, 0),   # no confirmado
                ("Venta", 1500, 0),   # no es gasto
                ("gasto", "2000", 0),  # confirmado (texto, minúsculas)
            ]
        )
        is_exp = _identify_expenses(df)
        self.assertEqual(list(is_exp), [True, False, False, False, True])

    def test_43_gasto_importe_ausente_arrow_string_si_alerta(self):
        # Caso real del pipeline: normalize conserva orig_Gasto_DOP como
        # texto Arrow-string; un ausente (pd.NA) no debe escapar de la
        # alerta ni convertirse en cero.
        from profiling.quality_score import _identify_expenses

        df = pd.DataFrame({
            "orig_Tipo": pd.Series(["Gasto", "Gasto"], dtype="string"),
            "orig_Gasto_DOP": pd.Series(["1500", None], dtype="string"),
            "UnitPrice": [0, 0],
        })
        is_exp = _identify_expenses(df)
        # La mascara no propaga ausentes: valores booleanos explicitos.
        self.assertFalse(bool(is_exp.isna().any()))
        self.assertEqual(list(is_exp.astype(bool)), [True, False])
        comps = self._comps(df)
        self.assertIn("non_positive_prices", comps)
        self.assertIn("sin importe positivo",
                      comps["non_positive_prices"]["detail"])
        # El ausente sigue identificado como faltante, no convertido a cero.
        self.assertTrue(bool(df["orig_Gasto_DOP"].isna()[1]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
