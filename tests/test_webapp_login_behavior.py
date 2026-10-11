"""QA-001 (obs. revisión) — Pruebas de COMPORTAMIENTO del mensaje de login.

A diferencia de los tests estáticos, estos EJECUTAN la lógica de
`loginErrorMessage(err)` en node (función pura extraída de app.js) con
un objeto de error por escenario y verifican el mensaje resultante:

- credenciales equivocadas (401 de /api/login) → credenciales incorrectas
- 429 → límite de intentos, sin revelar información de cuentas
- 500 → fallo temporal, sin afirmar que la clave es incorrecta
- fallo de red (TypeError) → mensaje de conexión
- anti-enumeración: ningún mensaje distingue email existente/inexistente
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(REPO, "webapp", "static", "app.js")

CASES = [
    # (nombre, construcción JS del err, mensaje esperado exacto)
    ("credenciales_401",
     '{message: "auth"}',
     "Credenciales incorrectas. Revisa tu correo y contraseña."),
    ("limite_429",
     '{message: "error", status: 429, payload: {error: "demasiados intentos. Inténtalo de nuevo más tarde."}}',
     "Demasiados intentos. Inténtalo de nuevo más tarde."),
    ("error_500",
     '{message: "servidor"}',
     "Ocurrió un error interno. Inténtalo de nuevo más tarde."),
    ("fallo_red",
     'new TypeError("fetch failed")',
     "No se pudo conectar con el servidor. Verifica tu conexión."),
    ("generico",
     '{message: "error"}',
     "No se pudo completar la operación. Inténtalo de nuevo."),
    ("nulo",
     'null',
     "No se pudo completar la operación. Inténtalo de nuevo."),
]


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _func_body(src, name):
    m = re.search(r"function\s+" + re.escape(name) + r"\s*\(", src)
    assert m, "no existe function %s" % name
    i = src.index("{", m.end())
    depth, j = 0, i
    in_str, quote, esc = False, "", False
    while j < len(src):
        ch = src[j]
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                in_str = False
        elif ch in ("'", '"', "`"):
            in_str, quote = True, ch
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return src[m.start():j + 1]
        j += 1
    raise AssertionError("cuerpo sin cerrar para %s" % name)


def _run_node(cases):
    src = _read(APP_JS)
    prelude = _func_body(src, "friendlyError") + "\n" + \
        _func_body(src, "loginErrorMessage")
    harness = prelude + "\nconst assert = require('assert');\nconst out = [];\n"
    for name, js_err, expected in cases:
        harness += (
            "out.push({name: %s, got: loginErrorMessage(%s), want: %s});\n"
            % (json.dumps(name), js_err, json.dumps(expected)))
    harness += "console.log(JSON.stringify(out));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False) as fh:
        fh.write(harness)
        path = fh.name
    try:
        proc = subprocess.run(["node", path], capture_output=True, text=True,
                              timeout=30)
    finally:
        os.unlink(path)
    assert proc.returncode == 0, "node falló: %s" % proc.stderr[:500]
    return json.loads(proc.stdout)


class LoginErrorMessageBehaviorTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("node no disponible")
        cls.results = {r["name"]: r for r in _run_node(CASES)}

    def test_credenciales_401(self):
        r = self.results["credenciales_401"]
        self.assertEqual(r["got"], r["want"])

    def test_limite_429_sin_revelar_cuentas(self):
        r = self.results["limite_429"]
        self.assertEqual(r["got"], r["want"])
        # No revela si la cuenta existe ni su estado (anti-enumeración).
        for palabra in ("existe", "registrado", "deshabilitado", "cuenta"):
            self.assertNotIn(palabra, r["got"].lower())

    def test_error_500_no_afirma_clave_incorrecta(self):
        r = self.results["error_500"]
        self.assertEqual(r["got"], r["want"])
        self.assertNotIn("incorrecta", r["got"].lower())

    def test_fallo_red(self):
        r = self.results["fallo_red"]
        self.assertEqual(r["got"], r["want"])

    def test_generico_y_nulo(self):
        self.assertEqual(self.results["generico"]["got"],
                         self.results["generico"]["want"])
        self.assertEqual(self.results["nulo"]["got"],
                         self.results["nulo"]["want"])

    def test_anti_enumeracion_en_todos(self):
        # Ningún mensaje distingue email existente/inexistente/deshabilitado.
        for name, r in self.results.items():
            for palabra in ("no existe", "no registrado", "deshabilitado"):
                self.assertNotIn(palabra, r["got"].lower(),
                                 "el caso %s enumera usuarios" % name)


if __name__ == "__main__":
    unittest.main()
