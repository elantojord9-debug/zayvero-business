"""Cierre de sesión — un logout fallido NUNCA se presenta como exitoso.

A diferencia de los tests estáticos, estos EJECUTAN la lógica extraída de
app.js en node:

- `logoutErrorMessage(err)` (pura): el mensaje ante cada fallo nunca
  afirma que la sesión se cerró; siempre comunica que el cierre no está
  confirmado y que la sesión puede seguir activa.
- `doLogout(deps)` (inyectable): ante fallo de /api/logout (403 por CSRF,
  500, red) NO navega ni muestra el login; informa con toast y conserva
  la vista actual. Solo el 200 limpia el estado y muestra el login.
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

MSG_CASES = [
    # (nombre, construcción JS del err, fragmentos que DEBE contener)
    ("csrf_403",
     '{message: "permiso"}',
     ["No se pudo confirmar el cierre de sesión",
      "puede seguir activa", "Inténtalo de nuevo"]),
    ("error_500",
     '{message: "servidor"}',
     ["No se pudo confirmar el cierre de sesión",
      "puede seguir activa", "más tarde"]),
    ("fallo_red",
     'new TypeError("fetch failed")',
     ["No se pudo conectar", "No se pudo confirmar el cierre de sesión",
      "puede seguir activa"]),
    ("generico",
     '{message: "error"}',
     ["No se pudo confirmar el cierre de sesión", "puede seguir activa"]),
    ("nulo",
     'null',
     ["No se pudo confirmar el cierre de sesión", "puede seguir activa"]),
]

# Afirmaciones de éxito que el mensaje jamás debe contener.
AFIRMACIONES_PROHIBIDAS = (
    "sesión cerrada", "se cerró la sesión", "has salido",
    "éxito", "exitosamente", "correctamente",
)

FLOW_CASES = ["ok_200", "csrf_403", "error_500", "fallo_red"]


def _read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def _func_body(src, name):
    m = re.search(r"(async\s+)?function\s+" + re.escape(name) + r"\s*\(", src)
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


def _run_node():
    src = _read(APP_JS)
    prelude = _func_body(src, "logoutErrorMessage") + "\n" + \
        _func_body(src, "doLogout")
    harness = prelude + """
const assert = require('assert');
const out = { mensajes: [], flujos: [] };
function mkErr(kind) {
  if (kind === 'csrf_403') return Object.assign(new Error('permiso'), {});
  if (kind === 'error_500') return new Error('servidor');
  if (kind === 'fallo_red') return new TypeError('fetch failed');
  if (kind === 'generico') return new Error('error');
  return null;
}
for (const kind of ['csrf_403', 'error_500', 'fallo_red', 'generico', 'nulo']) {
  out.mensajes.push({name: kind, got: logoutErrorMessage(mkErr(kind))});
}
// ---- flujos doLogout ----
function mkDeps(apiImpl) {
  const calls = [];
  const deps = {
    api: apiImpl,
    toast: (m) => calls.push(['toast', m]),
    showLogin: () => calls.push(['showLogin']),
    setHash: (h) => calls.push(['setHash', h]),
    btn: { disabled: false },
    state: { csrfToken: 'tok-abc', me: { email: 'a@a' } },
  };
  return { deps, calls };
}
function apiFor(kind) {
  if (kind === 'ok_200') return async () => ({ ok: true });
  if (kind === 'csrf_403') return async () => { throw new Error('permiso'); };
  if (kind === 'error_500') return async () => { throw new Error('servidor'); };
  return async () => { throw new TypeError('fetch failed'); };
}
(async () => {
  for (const kind of ['ok_200', 'csrf_403', 'error_500', 'fallo_red']) {
    const { deps, calls } = mkDeps(apiFor(kind));
    const ret = await doLogout(deps);
    out.flujos.push({
      name: kind, ret,
      calls,
      btnDisabled: deps.btn.disabled,
      state: deps.state,
    });
  }
  console.log(JSON.stringify(out));
})();
"""
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


class LogoutErrorMessageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("node no disponible")
        cls.data = _run_node()
        cls.msgs = {r["name"]: r["got"] for r in cls.data["mensajes"]}

    def test_mensajes_informan_fallo_sin_afirmar_exito(self):
        for name, got in self.msgs.items():
            with self.subTest(name=name):
                self.assertIn("No se pudo confirmar el cierre de sesión", got)
                self.assertIn("puede seguir activa", got.lower())
                for prohibida in AFIRMACIONES_PROHIBIDAS:
                    self.assertNotIn(prohibida, got.lower(),
                                     "el mensaje afirma éxito: %r" % got)

    def test_csrf_403_pide_reintento(self):
        self.assertIn("Inténtalo de nuevo", self.msgs["csrf_403"])

    def test_500_sugiere_mas_tarde(self):
        self.assertIn("más tarde", self.msgs["error_500"])

    def test_red_menciona_conexion(self):
        self.assertIn("No se pudo conectar", self.msgs["fallo_red"])


class DoLogoutFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("node no disponible")
        cls.data = _run_node()
        cls.flujos = {r["name"]: r for r in cls.data["flujos"]}

    def _kinds(self, f, kind):
        return [c[0] for c in f["calls"] if c[0] == kind]

    def test_exito_muestra_login_y_limpia_estado(self):
        f = self.flujos["ok_200"]
        self.assertTrue(f["ret"])
        self.assertEqual(self._kinds(f, "showLogin"), ["showLogin"])
        self.assertIn(["setHash", "#/inicio"], f["calls"])
        self.assertEqual(self._kinds(f, "toast"), [])
        self.assertIsNone(f["state"]["csrfToken"])
        self.assertIsNone(f["state"]["me"])
        self.assertFalse(f["btnDisabled"])

    def _assert_fallo_no_exitoso(self, name):
        f = self.flujos[name]
        with self.subTest(name=name):
            # Nunca se presenta como exitoso: ni login ni navegación.
            self.assertFalse(f["ret"])
            self.assertEqual(self._kinds(f, "showLogin"), [],
                             "logout fallido mostró el login como exitoso")
            self.assertEqual(self._kinds(f, "setHash"), [],
                             "logout fallido navegó como si hubiera salido")
            # Se informa con claridad mediante toast.
            toasts = [c[1] for c in f["calls"] if c[0] == "toast"]
            self.assertEqual(len(toasts), 1)
            self.assertIn("No se pudo confirmar el cierre de sesión",
                          toasts[0])
            self.assertIn("puede seguir activa", toasts[0].lower())
            # Estado coherente: sigue "dentro" (no se limpia la sesión local
            # que el servidor no revocó) y el botón queda habilitado.
            self.assertEqual(f["state"]["csrfToken"], "tok-abc")
            self.assertEqual(f["state"]["me"], {"email": "a@a"})
            self.assertFalse(f["btnDisabled"])

    def test_csrf_403_no_se_presenta_como_exitoso(self):
        self._assert_fallo_no_exitoso("csrf_403")

    def test_error_500_no_se_presenta_como_exitoso(self):
        self._assert_fallo_no_exitoso("error_500")

    def test_fallo_red_no_se_presenta_como_exitoso(self):
        self._assert_fallo_no_exitoso("fallo_red")


# Mensaje esperado ante sesión expirada (contrato de UI; el mismo que
# muestra api() ante 401). Fijado aquí para detectar derivas.
EXPECTED_EXPIRED_MSG = "Sesión expirada o no autenticado."


def _run_node_integrado():
    """Flujo INTEGRADO: el api() real + el doLogout() real + el cableado
    real del botón (onLogoutClick) de app.js, con fetch y DOM simulados.
    Cubre lo que los mocks aislados no ven:
    - api() disparaba showLogin() automáticamente ante 401 antes de que
      doLogout() capturara la excepción (silent401 lo corrige);
    - el wrapper del listener descartaba el mensaje de showLogin(msg);
    - silent401 no debe llegar a las opciones nativas de fetch;
    - clics repetidos con logout en curso no deben duplicar la solicitud.
    """
    src = _read(APP_JS)
    prelude = (
        _func_body(src, "logoutErrorMessage") + "\n"
        + _func_body(src, "doLogout") + "\n"
        + _func_body(src, "api") + "\n"
        + _func_body(src, "onLogoutClick")
    )
    harness = prelude + """
const out = { flujos: [], apiDirecto: null, cableado: null, duplicado: null };
// ---- entorno simulado (único punto de simulación: red y DOM) ----
let fetchStatus = 200;
let fetchThrows = null;
let lastHeaders = null;
let lastFetchOpts = null;
const showLoginCalls = [];
const toastCalls = [];
const state = { csrfToken: 'tok-vivo', me: { email: 'a@a' } };
global.fetch = async (path, opts) => {
  lastHeaders = (opts && opts.headers) || {};
  lastFetchOpts = opts || {};
  if (fetchThrows) throw fetchThrows;
  return { status: fetchStatus, ok: fetchStatus >= 200 && fetchStatus < 300,
           json: async () => ({}) };
};
global.showLogin = (msg) => showLoginCalls.push(msg === undefined ? null : msg);
global.toast = (msg) => toastCalls.push(msg);
global.location = { hash: '#/resumen' };
global.document = { getElementById: (id) => ({ disabled: false }) };
function mkDeps() {
  const calls = [];
  return {
    deps: {
      api: api,
      toast: (m) => calls.push(['toast', m]),
      showLogin: (msg) => calls.push(['showLogin', msg === undefined ? null : msg]),
      setHash: (h) => { global.location.hash = h; calls.push(['setHash', h]); },
      btn: { disabled: false },
      state: state,
    },
    calls,
  };
}
function reset() {
  fetchStatus = 200; fetchThrows = null;
  lastHeaders = null; lastFetchOpts = null;
  showLoginCalls.length = 0; toastCalls.length = 0;
  state.csrfToken = 'tok-vivo'; state.me = { email: 'a@a' };
  global.location.hash = '#/resumen';
}
(async () => {
  for (const status of [200, 401, 403, 500]) {
    reset();
    fetchStatus = status;
    const { deps, calls } = mkDeps();
    const ret = await doLogout(deps);
    out.flujos.push({ status, ret, calls,
                      apiShowLogin: showLoginCalls.slice(),
                      btnDisabled: deps.btn.disabled,
                      state: { csrfToken: state.csrfToken, me: state.me },
                      csrfHeader: lastHeaders['X-CSRF-Token'] || null,
                      silent401EnFetch: 'silent401' in lastFetchOpts });
  }
  // red caída
  reset();
  fetchThrows = new TypeError('fetch failed');
  {
    const { deps, calls } = mkDeps();
    const ret = await doLogout(deps);
    out.flujos.push({ status: 'red', ret, calls,
                      apiShowLogin: showLoginCalls.slice(),
                      btnDisabled: deps.btn.disabled,
                      state: { csrfToken: state.csrfToken, me: state.me } });
  }
  // api() directo sin silent401 conserva el 401 automático (otros callers)
  reset();
  fetchStatus = 401;
  let threw = null;
  try { await api('/api/me', {}); } catch (e) { threw = e.message; }
  out.apiDirecto = { showLoginCalls: showLoginCalls.slice(), threw };
  // cableado real del botón ante 401: el mensaje debe llegar a showLogin
  reset();
  fetchStatus = 401;
  await onLogoutClick();
  await new Promise((r) => setTimeout(r, 20));
  out.cableado = { showLoginCalls: showLoginCalls.slice(),
                   toastCalls: toastCalls.slice(),
                   hash: global.location.hash,
                   state: { csrfToken: state.csrfToken, me: state.me } };
  // clics repetidos con logout en curso: una sola solicitud
  reset();
  fetchStatus = 200;
  let slowCalls = 0;
  const realFetch = global.fetch;
  global.fetch = async (path, opts) => {
    slowCalls++;
    await new Promise((r) => setTimeout(r, 40));
    return realFetch(path, opts);
  };
  const sharedBtn = { disabled: false };
  const sharedState = { csrfToken: 'tok-vivo', me: { email: 'a@a' } };
  const dupDeps = () => ({ api: api, toast: (m) => {}, showLogin: () => {},
                           setHash: (h) => {}, btn: sharedBtn,
                           state: sharedState });
  const p1 = doLogout(dupDeps());
  const p2 = doLogout(dupDeps());
  const [r1, r2] = await Promise.all([p1, p2]);
  out.duplicado = { r1, r2, slowCalls, btnDisabled: sharedBtn.disabled,
                    state: { csrfToken: sharedState.csrfToken,
                             me: sharedState.me } };
  global.fetch = realFetch;
  console.log(JSON.stringify(out));
})();
"""
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


class DoLogoutIntegratedTest(unittest.TestCase):
    """401 durante el logout: el flujo integrado api()+doLogout()."""

    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("node no disponible")
        cls.data = _run_node_integrado()
        cls.flujos = {}
        for r in cls.data["flujos"]:
            cls.flujos[r["status"]] = r

    def test_401_sesion_expirada_flujo_coherente(self):
        f = self.flujos[401]
        # doLogout distingue el 401: no es un fallo "no confirmado".
        self.assertEqual(f["ret"], "expired")
        # Un solo showLogin, con el mensaje de sesión expirada, y ningún
        # toast engañoso de "puede seguir activa" (la sesión está muerta).
        do_calls = [c for c in f["calls"] if c[0] == "showLogin"]
        self.assertEqual(len(do_calls), 1)
        self.assertEqual(do_calls[0][1], EXPECTED_EXPIRED_MSG)
        self.assertEqual(f["apiShowLogin"], [],
                         "api() no debe disparar su showLogin automático "
                         "cuando doLogout gestiona el 401")
        toasts = [c[1] for c in f["calls"] if c[0] == "toast"]
        self.assertEqual(toasts, [])
        # Estado local limpio (la sesión ya no existe) y navegación hecha.
        self.assertIsNone(f["state"]["csrfToken"])
        self.assertIsNone(f["state"]["me"])
        self.assertIn(["setHash", "#/inicio"], f["calls"])
        self.assertFalse(f["btnDisabled"])

    def test_401_no_afirma_logout_exitoso(self):
        f = self.flujos[401]
        do_calls = [c for c in f["calls"] if c[0] == "showLogin"]
        self.assertEqual(do_calls[0][1], EXPECTED_EXPIRED_MSG)
        # Anti-enumeración: mensaje genérico, sin datos de cuenta.
        for palabra in ("existe", "registrado", "deshabilitado"):
            self.assertNotIn(palabra, do_calls[0][1].lower())

    def test_403_integrado_sigue_sin_presentarse_como_exitoso(self):
        f = self.flujos[403]
        self.assertFalse(f["ret"])
        self.assertEqual(f["apiShowLogin"], [])
        self.assertEqual([c for c in f["calls"] if c[0] == "showLogin"], [])
        self.assertEqual([c for c in f["calls"] if c[0] == "setHash"], [])
        toasts = [c[1] for c in f["calls"] if c[0] == "toast"]
        self.assertEqual(len(toasts), 1)
        self.assertIn("No se pudo confirmar", toasts[0])
        self.assertEqual(f["state"]["csrfToken"], "tok-vivo")

    def test_500_y_red_integrados_conservan_sesion(self):
        for key in (500, "red"):
            f = self.flujos[key]
            with self.subTest(status=key):
                self.assertFalse(f["ret"])
                self.assertEqual(f["apiShowLogin"], [])
                self.assertEqual(f["state"]["csrfToken"], "tok-vivo")
                self.assertFalse(f["btnDisabled"])

    def test_200_integrado_envia_csrf_y_completa(self):
        f = self.flujos[200]
        self.assertTrue(f["ret"])
        # CSRF intacto: el token viaja en la cabecera.
        self.assertEqual(f["csrfHeader"], "tok-vivo")
        self.assertIsNone(f["state"]["csrfToken"])
        self.assertIn(["setHash", "#/inicio"], f["calls"])

    def test_api_directo_conserva_401_automatico(self):
        d = self.data["apiDirecto"]
        self.assertEqual(d["threw"], "auth")
        self.assertEqual(d["showLoginCalls"], [EXPECTED_EXPIRED_MSG],
                         "el 401 automático de api() no debe cambiar para "
                         "los demás llamadores")

    def test_silent401_no_llega_a_fetch(self):
        for key in (200, 401, 403, 500):
            f = self.flujos[key]
            with self.subTest(status=key):
                self.assertFalse(
                    f["silent401EnFetch"],
                    "silent401 no debe propagarse a las opciones de fetch")

    def test_cableado_boton_transmite_mensaje_401(self):
        c = self.data["cableado"]
        # El cableado real (onLogoutClick + wrapper) entrega el mensaje.
        self.assertEqual(c["showLoginCalls"], [EXPECTED_EXPIRED_MSG])
        self.assertEqual(c["toastCalls"], [])
        self.assertEqual(c["hash"], "#/inicio")
        self.assertIsNone(c["state"]["csrfToken"])
        self.assertIsNone(c["state"]["me"])

    def test_clics_repetidos_no_duplican_solicitud(self):
        d = self.data["duplicado"]
        self.assertEqual(d["slowCalls"], 1,
                         "clics repetidos: una sola solicitud al servidor")
        self.assertEqual(d["r2"], "pending")
        self.assertTrue(d["r1"])
        self.assertFalse(d["btnDisabled"])
        self.assertIsNone(d["state"]["csrfToken"])


if __name__ == "__main__":
    unittest.main()
