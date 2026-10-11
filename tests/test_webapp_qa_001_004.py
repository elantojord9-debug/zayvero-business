"""QA-001 a QA-004 — Regresión estática del frontend (sin navegador).

Verifica sobre webapp/static/app.js y webapp/static/i18n.js que:

- QA-001: showLogin() sin mensaje oculta el error (primer render limpio);
  el login fallido muestra "Credenciales incorrectas" y el 401 en sesión
  activa sigue mostrando "Sesión expirada".
- QA-002: el estado de la empresa se pinta con companyStatusLabel().
- QA-003: los identificadores internos se presentan con enumLabel()
  (identificadores y valores de filtros intactos) y el catálogo enum.*
  tiene cobertura es/en con fail-safe.
- QA-004: no hay truncamientos .slice() sobre textos visibles al usuario.

Patrón tomado de tests/test_webapp_js_scope.py.
"""

from __future__ import annotations

import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(REPO, "webapp", "static", "app.js")
I18N_JS = os.path.join(REPO, "webapp", "static", "i18n.js")


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _func_body(src, name):
    """Cuerpo de 'function name(' con balance de llaves (sin strings)."""
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
                return src[i:j + 1]
        j += 1
    raise AssertionError("cuerpo sin cerrar para %s" % name)


class Qa001LoginMessagesTest(unittest.TestCase):
    def test_showlogin_sin_mensaje_oculta_error(self):
        body = _func_body(_read(APP_JS), "showLogin")
        self.assertIn('loginError.classList.add("hidden")', body,
                      "QA-001: showLogin() sin mensaje debe ocultar el error")

    def test_login_fallido_muestra_credenciales_incorrectas(self):
        src = _read(APP_JS)
        # El mensaje exacto lo decide loginErrorMessage() (probado por
        # comportamiento en test_webapp_login_behavior.py); aquí se verifica
        # el cableado: el submit lo usa y no menciona "sesión expirada".
        m = re.search(r'getElementById\("login-form"\)\.addEventListener\("submit"',
                      src)
        self.assertIsNotNone(m)
        handler = src[m.start():m.start() + 2500]
        self.assertIn("loginErrorMessage(err)", handler)
        self.assertNotIn("Sesión expirada", handler,
                         "QA-001: el login fallido no debe hablar de sesión expirada")

    def test_401_en_sesion_activa_sigue_mostrando_sesion_expirada(self):
        src = _read(APP_JS)
        self.assertIn('showLogin("Sesión expirada o no autenticado.")', src,
                      "QA-001: el 401 durante sesión activa debe seguir "
                      "mostrando 'Sesión expirada'")

    def test_api_adjunta_status_al_error(self):
        # Obs. revisión 1: el 429 se distingue por err.status.
        src = _read(APP_JS)
        self.assertIn("err.status = res.status", src,
                      "api() debe adjuntar el status HTTP al error")

    def test_submit_usa_loginErrorMessage(self):
        src = _read(APP_JS)
        self.assertIn("loginErrorMessage(err)", src,
                      "el formulario debe decidir el mensaje con loginErrorMessage()")
        self.assertIn("function loginErrorMessage(err)", src)


class Qa002CompanyStatusTest(unittest.TestCase):
    def test_estado_empresa_usa_etiqueta(self):
        src = _read(APP_JS)
        self.assertIn("companyStatusLabel(c.status)", src,
                      "QA-002: el estado debe pintarse con companyStatusLabel()")
        self.assertNotIn("esc(dash(c.status))", src,
                         "QA-002: no debe pintarse c.status crudo")

    def test_catalogo_estado_empresa_es_en(self):
        src = _read(I18N_JS)
        for key in ("company.status.active", "company.status.disabled"):
            self.assertIn('"%s"' % key, src,
                          "QA-002: falta la clave %s en i18n.js" % key)


class Qa003EnumLabelsTest(unittest.TestCase):
    # Campos que antes se pintaban crudos y ahora pasan por enumLabel().
    SITIOS = [
        "enumLabel(att)",
        "enumLabel(f.business_priority)",
        "enumLabel(f.finding_type)",
        "enumLabel(i.forecast_quality)",
        "enumLabel(i.trend)",
        "enumLabel(i.forecast_direction)",
        "enumLabel(i.decline_risk)",
        "enumLabel(i.method)",
        "enumLabel(r.risk_type)",
        "enumLabel(r.severity)",
        "enumLabel(st.attention_level)",
    ]

    def test_sitios_usan_enumlabel(self):
        src = _read(APP_JS)
        for sitio in self.SITIOS:
            self.assertIn(sitio, src,
                          "QA-003: falta %s en app.js" % sitio)

    def test_identificadores_internos_intactos(self):
        # Los filtros y las clases CSS conservan el identificador crudo.
        src = _read(APP_JS)
        self.assertIn('"URGENT", "IMPORTANT", "REVIEW", "MONITOR"', src)
        self.assertIn('"SALES_ANOMALY", "PRODUCT_ANOMALY"', src)
        self.assertIn("prio-\" + esc(f.business_priority)", src)
        self.assertIn("attention-\" + esc(att)", src)

    def test_catalogo_enum_es_en_con_failsafe(self):
        src = _read(I18N_JS)
        for code in ("LOW", "MEDIUM", "HIGH", "URGENT", "IMPORTANT",
                     "REVIEW", "MONITOR", "INSUFFICIENT_DATA", "UNSTABLE",
                     "FLAT", "UNKNOWN", "moving_average", "SALES_ANOMALY",
                     "PREDICTION_RISK"):
            self.assertIn('"enum.%s"' % code, src,
                          "QA-003: falta enum.%s en i18n.js" % code)
        # fail-safe: código desconocido → se devuelve el código original.
        body = _func_body(src, "labelEnum")
        self.assertIn("return String(code)", body)
        self.assertIn("global.ZBI18N", src)
        self.assertIn("labelEnum: labelEnum", src)

    def test_flat_significa_sin_cambio_material(self):
        # QA-003: FLAT = cambio proyectado no material (<20%), no "plano".
        src = _read(I18N_JS)
        self.assertIn('"enum.FLAT": "Sin cambio material"', src)


class Qa004NoTruncamientoTest(unittest.TestCase):
    def test_sin_slice_en_textos_visibles(self):
        src = _read(APP_JS)
        for campo in ("business_explanation", "business_interpretation"):
            for m in re.finditer(r"\.slice\(0,\s*\d+\)", src):
                contexto = src[max(0, m.start() - 80):m.start()]
                self.assertNotIn(campo, contexto,
                                 "QA-004: %s no debe truncarse con .slice()" % campo)


class Qa005WarningsEnInterfazTest(unittest.TestCase):
    """Obs. revisión 2.2: las advertencias propagadas se muestran en la UI."""

    def test_detalle_muestra_warn_box(self):
        src = _read(APP_JS)
        self.assertIn("warn-box", src)
        self.assertIn("d.warnings", src,
                      "QA-005: el detalle del dataset debe pintar d.warnings")
        self.assertIn("Advertencias sobre estos datos", src)


if __name__ == "__main__":
    unittest.main()
