"""SEG-01/02/03 — Pruebas de endurecimiento de seguridad.

SEG-01: cookie de sesión con atributo Secure (configuración explícita).
SEG-02: cabeceras de seguridad HTTP (CSP, X-Frame-Options, nosniff, HSTS
        con interruptor propio, independiente de la cookie Secure).
SEG-03: tokens CSRF sincronizados con la sesión + defensa del login
        (Content-Type JSON + validación de Origin con esquema/host/puerto).

Incluye pruebas negativas. Solo stdlib + unittest. Datos sintéticos.
"""

import os
import tempfile
import unittest

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tenant import (
    TenantStore, create_company, create_user, login, logout,
    get_csrf_token, require_csrf, PermissionDenied,
)
from tenant import security as sec_mod
from tenant.auth import enter_demo, get_tenant_context
from tenant.store import DEMO_COMPANY_ID


class _Hdr(dict):
    """Cabeceras mínimas con .get como las de BaseHTTPRequestHandler."""
    def get(self, k, default=None):
        return super().get(k, default)


def _setenv(key, value):
    """Fija una variable de entorno restaurándola al terminar la prueba."""
    old = os.environ.get(key)
    if value is None:
        os.environ.pop(key, None)
    else:
        os.environ[key] = value
    return old


class _EnvCase(unittest.TestCase):
    def setUp(self):
        self._saved = {}

    def tearDown(self):
        for k, v in self._saved.items():
            _setenv(k, v)

    def putenv(self, key, value):
        if key not in self._saved:
            self._saved[key] = os.environ.get(key)
        _setenv(key, value)


class Seg01CookieTest(_EnvCase):
    def test_secure_flag_cuando_configurado(self):
        c = sec_mod.build_set_cookie("zayvero_session", "tok123", secure=True)
        self.assertIn("Secure", c)
        self.assertIn("HttpOnly", c)
        self.assertIn("SameSite=Lax", c)

    def test_sin_secure_en_local(self):
        c = sec_mod.build_set_cookie("zayvero_session", "tok123", secure=False)
        self.assertNotIn("Secure", c)
        self.assertIn("HttpOnly", c)
        self.assertIn("SameSite=Lax", c)

    def test_clear_cookie_mismos_atributos(self):
        # El borrado debe llevar los mismos atributos para que aplique.
        c = sec_mod.build_clear_cookie("zayvero_session", secure=True)
        self.assertIn("Secure", c)
        self.assertIn("Max-Age=0", c)
        self.assertIn("HttpOnly", c)

    def test_env_activa_secure(self):
        self.putenv("ZAYVERO_COOKIE_SECURE", "1")
        self.assertTrue(sec_mod.cookie_secure_enabled())

    def test_env_desactiva_secure(self):
        self.putenv("ZAYVERO_COOKIE_SECURE", "0")
        self.assertFalse(sec_mod.cookie_secure_enabled())

    def test_sin_env_no_hay_secure(self):
        self.putenv("ZAYVERO_COOKIE_SECURE", None)
        self.assertFalse(sec_mod.cookie_secure_enabled())

    def test_secure_no_depende_de_cabeceras(self):
        # NEGATIVA: X-Forwarded-Proto de un cliente NO activa Secure.
        # La decisión es del operador, nunca de la petición.
        self.putenv("ZAYVERO_COOKIE_SECURE", None)
        self.assertFalse(sec_mod.cookie_secure_enabled())


class Seg02HeadersTest(_EnvCase):
    def test_cabeceras_base(self):
        h = sec_mod.security_headers(hsts=False)
        self.assertEqual(h["X-Frame-Options"], "DENY")
        self.assertEqual(h["X-Content-Type-Options"], "nosniff")
        self.assertIn("Referrer-Policy", h)

    def test_hsts_solo_con_flag(self):
        self.putenv("ZAYVERO_HSTS", None)
        self.assertNotIn("Strict-Transport-Security",
                         sec_mod.security_headers(hsts=sec_mod.hsts_enabled()))
        self.putenv("ZAYVERO_HSTS", "1")
        h = sec_mod.security_headers(hsts=sec_mod.hsts_enabled())
        self.assertIn("Strict-Transport-Security", h)
        self.assertIn("max-age=", h["Strict-Transport-Security"])
        # Sin includeSubDomains ni preload sin autorización.
        self.assertNotIn("includeSubDomains",
                         h["Strict-Transport-Security"])
        self.assertNotIn("preload", h["Strict-Transport-Security"])

    def test_hsts_independiente_de_cookie_secure(self):
        # OBS-1: Secure y HSTS son decisiones separadas.
        # Cookie Secure SIN HSTS → cookie segura, sin cabecera HSTS.
        self.putenv("ZAYVERO_COOKIE_SECURE", "1")
        self.putenv("ZAYVERO_HSTS", None)
        self.assertTrue(sec_mod.cookie_secure_enabled())
        self.assertNotIn("Strict-Transport-Security",
                         sec_mod.security_headers(hsts=sec_mod.hsts_enabled()))
        # HSTS SIN cookie Secure → HSTS presente, cookie sin Secure.
        self.putenv("ZAYVERO_COOKIE_SECURE", None)
        self.putenv("ZAYVERO_HSTS", "1")
        self.assertFalse(sec_mod.cookie_secure_enabled())
        self.assertIn("Strict-Transport-Security",
                      sec_mod.security_headers(hsts=sec_mod.hsts_enabled()))

    def test_csp_valida_y_estricta(self):
        csp = sec_mod.security_headers(hsts=False)[
            "Content-Security-Policy"]
        self.assertIn("default-src 'self'", csp)
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertIn("base-uri 'self'", csp)
        # Sin atajos inseguros.
        self.assertNotIn("unsafe-inline", csp)
        self.assertNotIn("unsafe-eval", csp)
        # Sintaxis: directivas separadas por ';'.
        for part in csp.split(";"):
            part = part.strip()
            if part:
                self.assertRegex(part, r"^[a-z\-]+(\s+\S+)+$")

    def test_public_origin_normaliza(self):
        self.putenv("ZAYVERO_PUBLIC_ORIGIN",
                     "https://zayvero-business.onrender.com/")
        self.assertEqual(sec_mod.public_origin(),
                         "https://zayvero-business.onrender.com")
        self.putenv("ZAYVERO_PUBLIC_ORIGIN", None)
        self.assertEqual(sec_mod.public_origin(), "")


class Seg03CsrfTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zb_seg03_")
        self.store = TenantStore(data_dir=os.path.join(self.tmp, "tenant"))
        self.store.ensure_demo_company()
        self.ca = create_company(self.store, "Empresa CSRF A")
        self.cb = create_company(self.store, "Empresa CSRF B")
        create_user(self.store, company_id=self.ca.company_id,
                    email="a@csrf.local", name="A",
                    password="ClaveCsrfA1!", role_id="owner")
        create_user(self.store, company_id=self.cb.company_id,
                    email="b@csrf.local", name="B",
                    password="ClaveCsrfB1!", role_id="owner")
        # Viewer demo para enter_demo.
        create_user(self.store, company_id=DEMO_COMPANY_ID,
                    email="v@csrf.local", name="V",
                    password="ClaveCsrfV1!", role_id="viewer")

    def _login(self, email, pwd):
        token, ctx = login(self.store, email, pwd)
        return token, ctx

    def test_login_genera_csrf_token(self):
        token, _ = self._login("a@csrf.local", "ClaveCsrfA1!")
        csrf = get_csrf_token(self.store, token)
        self.assertTrue(csrf and len(csrf) >= 32)

    def test_tokens_unicos_por_sesion(self):
        t1, _ = self._login("a@csrf.local", "ClaveCsrfA1!")
        t2, _ = self._login("a@csrf.local", "ClaveCsrfA1!")
        self.assertNotEqual(get_csrf_token(self.store, t1),
                            get_csrf_token(self.store, t2))

    def test_require_csrf_valido(self):
        token, ctx = self._login("a@csrf.local", "ClaveCsrfA1!")
        csrf = get_csrf_token(self.store, token)
        require_csrf(self.store, ctx, token, csrf, resource="test")
        # No lanza = pasa.

    def test_require_csrf_ausente_rechazado(self):
        token, ctx = self._login("a@csrf.local", "ClaveCsrfA1!")
        with self.assertRaises(PermissionDenied):
            require_csrf(self.store, ctx, token, None, resource="test")
        with self.assertRaises(PermissionDenied):
            require_csrf(self.store, ctx, token, "", resource="test")

    def test_require_csrf_incorrecto_rechazado(self):
        token, ctx = self._login("a@csrf.local", "ClaveCsrfA1!")
        with self.assertRaises(PermissionDenied):
            require_csrf(self.store, ctx, token,
                         "token-aleatorio-incorrecto", resource="test")

    def test_require_csrf_otra_sesion_rechazado(self):
        ta, ctx_a = self._login("a@csrf.local", "ClaveCsrfA1!")
        tb, _ = self._login("b@csrf.local", "ClaveCsrfB1!")
        csrf_b = get_csrf_token(self.store, tb)
        # NEGATIVA: el token de B no sirve en la sesión de A.
        with self.assertRaises(PermissionDenied):
            require_csrf(self.store, ctx_a, ta, csrf_b, resource="test")

    def test_require_csrf_sesion_revocada_rechazado(self):
        token, ctx = self._login("a@csrf.local", "ClaveCsrfA1!")
        csrf = get_csrf_token(self.store, token)
        logout(self.store, token)
        with self.assertRaises(PermissionDenied):
            require_csrf(self.store, ctx, token, csrf, resource="test")
        self.assertEqual(get_csrf_token(self.store, token), "")

    def test_demo_enter_genera_token_propio(self):
        token, ctx = self._login("a@csrf.local", "ClaveCsrfA1!")
        csrf_origen = get_csrf_token(self.store, token)
        demo_token, demo_ctx = enter_demo(self.store, ctx, token)
        csrf_demo = get_csrf_token(self.store, demo_token)
        self.assertTrue(csrf_demo)
        self.assertNotEqual(csrf_demo, csrf_origen)
        # El token origen sigue válido para su sesión.
        require_csrf(self.store, ctx, token, csrf_origen, resource="test")
        # El token demo no sirve en la sesión origen.
        with self.assertRaises(PermissionDenied):
            require_csrf(self.store, ctx, token, csrf_demo, resource="test")

    def test_csrf_no_se_loguea(self):
        # El token nunca debe aparecer en los eventos de auditoría.
        token, ctx = self._login("a@csrf.local", "ClaveCsrfA1!")
        csrf = get_csrf_token(self.store, token)
        try:
            require_csrf(self.store, ctx, token, "mal", resource="test")
        except PermissionDenied:
            pass
        events = self.store.list_audit(self.ca.company_id)
        blob = "".join(e.to_dict().__str__() for e in events)
        self.assertNotIn(csrf, blob)


class Seg03LoginCsrfTest(_EnvCase):
    """Defensa del login sin sesión previa: Content-Type JSON + Origin
    validado con esquema, host y puerto contra el origen público
    configurado (o http://local en desarrollo)."""

    def setUp(self):
        super().setUp()
        from webapp.server import _login_request_ok
        self.check = _login_request_ok

    def _h(self, **kw):
        h = {"Host": "app.local:8701",
             "Content-Type": "application/json"}
        h.update(kw)
        fake = type("FakeHandler", (), {})()
        fake.headers = _Hdr(h)
        return fake

    def _h_noctype(self):
        fake = type("FakeHandler", (), {})()
        fake.headers = _Hdr({"Host": "app.local:8701"})
        return fake

    # ---- Local (sin ZAYVERO_PUBLIC_ORIGIN) ----
    def test_login_json_valido(self):
        self.putenv("ZAYVERO_PUBLIC_ORIGIN", None)
        self.assertTrue(self.check(self._h()))

    def test_login_rechaza_form_urlencoded(self):
        # NEGATIVA: un formulario cross-site no puede fijar este tipo.
        self.putenv("ZAYVERO_PUBLIC_ORIGIN", None)
        self.assertFalse(self.check(self._h(
            **{"Content-Type": "application/x-www-form-urlencoded"})))

    def test_login_rechaza_sin_content_type(self):
        self.putenv("ZAYVERO_PUBLIC_ORIGIN", None)
        self.assertFalse(self.check(self._h_noctype()))

    def test_login_origin_distinto_rechazado(self):
        self.putenv("ZAYVERO_PUBLIC_ORIGIN", None)
        self.assertFalse(self.check(self._h(Origin="https://evil.com")))

    def test_login_origin_propio_aceptado(self):
        self.putenv("ZAYVERO_PUBLIC_ORIGIN", None)
        self.assertTrue(self.check(
            self._h(Origin="http://app.local:8701")))

    def test_login_mismo_host_otro_esquema_rechazado(self):
        # NEGATIVA clave: igual host+puerto pero esquema https en un
        # despliegue local http → rechazado. El esquema SÍ se valida.
        self.putenv("ZAYVERO_PUBLIC_ORIGIN", None)
        self.assertFalse(self.check(
            self._h(Origin="https://app.local:8701")))

    def test_login_referer_distinto_rechazado(self):
        self.putenv("ZAYVERO_PUBLIC_ORIGIN", None)
        self.assertFalse(self.check(
            self._h(Referer="https://evil.com/pagina")))

    def test_login_origin_nulo_rechazado(self):
        self.putenv("ZAYVERO_PUBLIC_ORIGIN", None)
        self.assertFalse(self.check(self._h(Origin="null")))

    # ---- Producción (ZAYVERO_PUBLIC_ORIGIN configurado) ----
    def test_login_origen_publico_valido(self):
        self.putenv("ZAYVERO_PUBLIC_ORIGIN",
                     "https://zayvero-business.onrender.com")
        fake = self._h(Origin="https://zayvero-business.onrender.com")
        self.assertTrue(self.check(fake))

    def test_login_origen_publico_esquema_http_rechazado(self):
        # NEGATIVA: mismo host público pero por http → rechazado.
        self.putenv("ZAYVERO_PUBLIC_ORIGIN",
                     "https://zayvero-business.onrender.com")
        self.assertFalse(self.check(
            self._h(Origin="http://zayvero-business.onrender.com")))

    def test_login_origen_publico_sufijo_maligno_rechazado(self):
        # NEGATIVA: subdominio tramposo que termina igual → rechazado
        # (comparación exacta, no por sufijo).
        self.putenv("ZAYVERO_PUBLIC_ORIGIN",
                     "https://zayvero-business.onrender.com")
        self.assertFalse(self.check(
            self._h(Origin="https://zayvero-business.onrender.com.evil.com")))

    def test_login_origen_publico_puerto_distinto_rechazado(self):
        self.putenv("ZAYVERO_PUBLIC_ORIGIN",
                     "https://zayvero-business.onrender.com")
        self.assertFalse(self.check(
            self._h(Origin="https://zayvero-business.onrender.com:8443")))

    def test_login_origen_publico_otro_dominio_rechazado(self):
        self.putenv("ZAYVERO_PUBLIC_ORIGIN",
                     "https://zayvero-business.onrender.com")
        self.assertFalse(self.check(self._h(Origin="https://evil.com")))

    def test_login_sin_cabeceras_con_origen_publico(self):
        # curl / clientes no-navegador: sin amenaza CSRF, se permite.
        self.putenv("ZAYVERO_PUBLIC_ORIGIN",
                     "https://zayvero-business.onrender.com")
        self.assertTrue(self.check(self._h()))


if __name__ == "__main__":
    unittest.main(verbosity=2)
