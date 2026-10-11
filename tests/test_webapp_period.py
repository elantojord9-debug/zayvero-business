"""Regresión: 'Período analizado' nunca muestra '[object Object]'.

El campo business_period llega como objeto {start, end, days_with_activity}
y la vista Resumen lo concatenaba como texto, mostrando
'Período analizado: [object Object]'. La función periodText() de app.js lo
convierte a un rango legible o a "" (el segmento se omite).

Este test extrae la función REAL de webapp/static/app.js, la ejecuta en
node con el fmtDate REAL de webapp/static/i18n.js y verifica:
1. Período con inicio y fin válidos -> rango legible.
2. Período nulo o ausente -> "" (sin segmento).
3. Objeto incompleto o con fechas inválidas -> "" o solo la parte válida.
4. Ningún caso produce '[object Object]', 'undefined' ni 'Invalid Date'.
"""

import json
import os
import subprocess
import sys
import unittest

try:
    from test_webapp_js_scope import _function_spans
except ModuleNotFoundError:  # python -m unittest tests.test_webapp_period
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from test_webapp_js_scope import _function_spans

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(REPO, "webapp", "static", "app.js")
I18N_JS = os.path.join(REPO, "webapp", "static", "i18n.js")

FORBIDDEN = ("[object Object]", "undefined", "Invalid Date")


def _period_text_source():
    with open(APP_JS, encoding="utf-8") as fh:
        src = fh.read()
    for name, start, end in _function_spans(src):
        if name == "periodText":
            return src[start:end + 1]
    raise AssertionError("no se encontró periodText en app.js")


def _run_cases(cases):
    """Ejecuta periodText(caso) en node; devuelve [(input, output)]."""
    driver = (
        "global.window = global;\n"
        f"require({json.dumps(I18N_JS)});\n"
        "var fmtDate = function (s) { return global.ZBI18N.fmtDate(s); };\n"
        + _period_text_source() + "\n"
        "var out = CASES.map(function (bp) { return periodText(bp); });\n"
        "console.log(JSON.stringify(out));\n"
    )
    payload = "var CASES = %s;\n%s" % (json.dumps(cases), driver)
    proc = subprocess.run(
        ["node", "-e", payload], capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise AssertionError(f"node falló: {proc.stderr[:300]}")
    return list(zip(cases, json.loads(proc.stdout.strip().splitlines()[-1])))


class BusinessPeriodTextTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.results = _run_cases([
            {"start": "2009-12-01T07:45:00",
             "end": "2011-12-09T12:50:00", "days_with_activity": 604},
            None,
            {},
            {"start": None, "end": None},
            {"start": "2009-12-01T07:45:00"},
            {"end": "2011-12-09T12:50:00"},
            {"start": "no-fecha", "end": "tampoco"},
            {"start": "2026-02-30T00:00:00", "end": "2026-03-01T00:00:00"},
            {"start": "2026-13-45", "end": "2026-01-01"},
            {"start": "2025-02-29", "end": "2025-03-01"},
            {"start": "2024-02-29", "end": "2024-03-01"},
            {"start": "2023-02-29", "end": "2023-03-01"},
            "texto",
        ])
        cls.by = {json.dumps(c, sort_keys=True): r for c, r in cls.results}

    def _result(self, case):
        return self.by[json.dumps(case, sort_keys=True)]

    def test_1_periodo_valido_muestra_rango(self):
        r = self._result({"start": "2009-12-01T07:45:00",
                          "end": "2011-12-09T12:50:00",
                          "days_with_activity": 604})
        self.assertEqual(r, "01/12/2009 → 09/12/2011")

    def test_2_periodo_nulo_o_ausente_sin_segmento(self):
        self.assertEqual(self._result(None), "")
        self.assertEqual(self._result({}), "")

    def test_3_incompleto_o_invalido(self):
        self.assertEqual(self._result({"start": None, "end": None}), "")
        self.assertEqual(self._result({"start": "2009-12-01T07:45:00"}),
                         "01/12/2009")
        self.assertEqual(self._result({"end": "2011-12-09T12:50:00"}),
                         "09/12/2011")
        self.assertEqual(self._result({"start": "no-fecha",
                                        "end": "tampoco"}), "")
        self.assertEqual(self._result("texto"), "")

    def test_5_fechas_imposibles_no_se_muestran(self):
        # 2026-02-30 y 2025-02-29 no existen; 2026-13-45 ni siquiera
        # tiene mes válido. Ninguna debe mostrarse como fecha real.
        self.assertEqual(
            self._result({"start": "2026-02-30T00:00:00",
                          "end": "2026-03-01T00:00:00"}), "01/03/2026")
        self.assertEqual(
            self._result({"start": "2026-13-45", "end": "2026-01-01"}),
            "01/01/2026")
        self.assertEqual(
            self._result({"start": "2025-02-29", "end": "2025-03-01"}),
            "01/03/2025")
        # 2024 sí fue bisiesto: 29 de febrero es válido.
        self.assertEqual(
            self._result({"start": "2024-02-29", "end": "2024-03-01"}),
            "29/02/2024 → 01/03/2024")
        # 2023 no fue bisiesto.
        self.assertEqual(
            self._result({"start": "2023-02-29", "end": "2023-03-01"}),
            "01/03/2023")

    def test_4_nunca_textos_prohibidos(self):
        for case, r in self.results:
            for bad in FORBIDDEN:
                self.assertNotIn(
                    bad, r,
                    f"periodText({case!r}) produjo texto prohibido: {r!r}")
