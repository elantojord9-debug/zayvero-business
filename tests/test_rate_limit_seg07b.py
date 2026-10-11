"""SEG-07B — Diagnóstico temporal de Cloudflare (solo booleanos).

Verifica que el diagnóstico:
- Está DESACTIVADO por defecto (sin opt-in no emite nada).
- Emite UNA línea a stdout con solo booleanos + identificador SEG07_DIAG.
- Nunca registra IPs, valores de cabeceras, emails ni datos sensibles.
- No altera respuestas HTTP, autenticación ni rate limiting.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import sys
import tempfile
import threading
import unittest
from email.message import Message

# Permite invocar el archivo directamente (python tests/test_...py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tenant import rate_limit as rl


class _FakeHandler:
    def __init__(self, peer, cf_ips=()):
        self.client_address = (peer, 12345)
        self._headers = Message()
        for ip in cf_ips:
            self._headers.add_header("CF-Connecting-IP", ip)

    @property
    def headers(self):
        return self._headers


def _emit(handler):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rl.seg07_emit_diag_once(handler)
    return buf.getvalue()


class TestSeg07Diag(unittest.TestCase):
    def setUp(self):
        self._old = os.environ.get("ZAYVERO_SEG07_DIAG")
        os.environ.pop("ZAYVERO_SEG07_DIAG", None)
        rl._seg07_diag_reset()

    def tearDown(self):
        if self._old is None:
            os.environ.pop("ZAYVERO_SEG07_DIAG", None)
        else:
            os.environ["ZAYVERO_SEG07_DIAG"] = self._old

    def test_1_sin_optin_no_emite_nada(self):
        h = _FakeHandler("10.0.0.5", cf_ips=["203.0.113.9"])
        self.assertEqual(_emit(h), "")

    def test_2_cabecera_presente_y_valida(self):
        os.environ["ZAYVERO_SEG07_DIAG"] = "1"
        h = _FakeHandler("10.0.0.5", cf_ips=["203.0.113.9"])
        out = _emit(h)
        self.assertIn("SEG07_DIAG", out)
        self.assertIn("cf_header_present=1", out)
        self.assertIn("cf_header_unique=1", out)
        self.assertIn("cf_header_valid=1", out)
        self.assertIn("peer_rfc1918=1", out)
        self.assertEqual(out.strip().count("\n"), 0)  # UNA sola línea

    def test_3_cabecera_ausente(self):
        os.environ["ZAYVERO_SEG07_DIAG"] = "1"
        h = _FakeHandler("10.0.0.5")
        out = _emit(h)
        self.assertIn("cf_header_present=0", out)
        self.assertIn("cf_header_unique=0", out)
        self.assertIn("cf_header_valid=0", out)

    def test_4_cabecera_duplicada(self):
        os.environ["ZAYVERO_SEG07_DIAG"] = "1"
        h = _FakeHandler("10.0.0.5",
                         cf_ips=["203.0.113.9", "198.51.100.7"])
        out = _emit(h)
        self.assertIn("cf_header_present=1", out)
        self.assertIn("cf_header_unique=0", out)
        self.assertIn("cf_header_valid=0", out)

    def test_5_cabecera_malformada(self):
        os.environ["ZAYVERO_SEG07_DIAG"] = "1"
        h = _FakeHandler("10.0.0.5", cf_ips=["no-es-ip"])
        out = _emit(h)
        self.assertIn("cf_header_present=1", out)
        self.assertIn("cf_header_unique=1", out)
        self.assertIn("cf_header_valid=0", out)

    def test_6_ningun_dato_sensible_en_registro(self):
        os.environ["ZAYVERO_SEG07_DIAG"] = "1"
        # Valores deliberadamente reconocibles: no deben aparecer.
        h = _FakeHandler("10.99.88.77",
                         cf_ips=["203.0.113.199", "198.51.100.188"])
        out = _emit(h)
        for secreto in ("203.0.113.199", "198.51.100.188", "10.99.88.77",
                        "victima@x.com", "X-Forwarded-For"):
            self.assertNotIn(secreto, out, f"fuga: {secreto}")
        # Solo el identificador y booleanos 0/1.
        cuerpo = out.replace("SEG07_DIAG", "").strip()
        for token in cuerpo.split():
            k, v = token.split("=")
            self.assertIn(v, ("0", "1"))

    def test_7_peer_no_privado(self):
        os.environ["ZAYVERO_SEG07_DIAG"] = "1"
        h = _FakeHandler("8.8.8.8", cf_ips=["2001:db8::1"])
        out = _emit(h)
        self.assertIn("peer_rfc1918=0", out)
        self.assertIn("cf_header_valid=1", out)  # IPv6 válida

    def test_8_no_altera_rate_limiting(self):
        os.environ["ZAYVERO_SEG07_DIAG"] = "1"
        lim = rl.LoginRateLimiter(account_max=2, account_window_s=900,
                                  ip_max=100, ip_window_s=900)
        h = _FakeHandler("10.0.0.5", cf_ips=["203.0.113.9"])
        with contextlib.redirect_stdout(io.StringIO()):
            rl.seg07_emit_diag_once(h)  # no debe lanzar ni interferir
        oks = []
        for i in range(3):
            ok, _, tok = lim.try_acquire(ip="10.0.0.5",
                                         account="u@x.com")
            if ok:
                lim.settle(tok, success=False)
            oks.append(ok)
        self.assertEqual(oks, [True, True, False])  # límite intacto




class TestSeg07DiagPlacement(unittest.TestCase):
    """El diagnóstico solo se emite tras validación + reserva autorizada.

    Servidor real en el mismo proceso (hilo): comparte el flag de
    emisión única y permite capturarlo con redirect_stdout.
    """


    @classmethod
    def setUpClass(cls):
        import sys
        sys.path.insert(0, os.path.dirname(
            os.path.dirname(os.path.abspath(__file__))))
        from http.server import ThreadingHTTPServer
        from webapp.server import WebappHandler
        import webapp.server as server_mod
        from tenant.store import TenantStore
        cls._old = os.environ.get("ZAYVERO_SEG07_DIAG")
        os.environ["ZAYVERO_SEG07_DIAG"] = "1"
        # Aislar el store global: otros archivos de tests pueden dejar
        # server_mod.STORE apuntando a un directorio temporal ya borrado
        # (su tearDownClass no lo restaura) y los logins fallarían con
        # FileNotFoundError en vez de 401.
        cls._tmp = tempfile.mkdtemp(prefix="zb_seg07b_")
        cls._prev_store = server_mod.STORE
        store = TenantStore(cls._tmp)
        store.ensure_demo_company()
        server_mod.STORE = store
        server_mod._DATA_CACHE.clear()
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0),
                                         WebappHandler)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever,
                                      daemon=True)
        cls.thread.start()
        cls._buf = io.StringIO()
        cls._redir = contextlib.redirect_stdout(cls._buf)
        cls._redir.__enter__()

    @classmethod
    def tearDownClass(cls):
        cls._redir.__exit__(None, None, None)
        cls.server.shutdown()
        cls.server.server_close()
        import webapp.server as server_mod
        server_mod.STORE = cls._prev_store
        server_mod._DATA_CACHE.clear()
        shutil.rmtree(cls._tmp, ignore_errors=True)
        if cls._old is None:
            os.environ.pop("ZAYVERO_SEG07_DIAG", None)
        else:
            os.environ["ZAYVERO_SEG07_DIAG"] = cls._old

    def setUp(self):
        rl._seg07_diag_reset()
        self._buf.seek(0)
        self._buf.truncate()

    def _post(self, email="diag-x@x.com", ctype="application/json"):
        import urllib.request
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/api/login",
            data=b'{"email":"%s","password":"x"}' % email.encode(),
            headers={"Content-Type": ctype},
            method="POST")
        try:
            with urllib.request.urlopen(req, timeout=10) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def _diag_lines(self):
        return [l for l in self._buf.getvalue().splitlines()
                if l.startswith("SEG07_DIAG")]

    def test_rechazada_por_validacion_no_emite(self):
        # Sin Content-Type JSON → 403 antes de la reserva.
        self.assertEqual(self._post(ctype="text/plain"), 403)
        self.assertEqual(self._diag_lines(), [])

    def test_rechazada_por_rate_limit_no_emite(self):
        email = "diag-ratelimit@x.com"
        for _ in range(5):
            self.assertEqual(self._post(email=email), 401)
        self.assertEqual(len(self._diag_lines()), 1)  # solo la primera
        rl._seg07_diag_reset()
        self._buf.seek(0)
        self._buf.truncate()
        self.assertEqual(self._post(email=email), 429)  # 6to: bloqueado
        self.assertEqual(self._diag_lines(), [])  # el 429 no emite

    def test_dos_validas_una_emision(self):
        self.assertEqual(self._post(email="diag-a@x.com"), 401)
        self.assertEqual(self._post(email="diag-b@x.com"), 401)
        self.assertEqual(len(self._diag_lines()), 1)

    def test_diez_concurrentes_una_emision(self):
        import urllib.request
        codes = []
        lock = threading.Lock()

        def worker(i):
            c = self._post(email=f"diag-c{i}@x.com")
            with lock:
                codes.append(c)

        ts = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
        for t in ts:
            t.start()
        for t in ts:
            t.join()
        self.assertTrue(all(c == 401 for c in codes))
        self.assertEqual(len(self._diag_lines()), 1)

    def test_sin_optin_cero_emisiones(self):
        os.environ.pop("ZAYVERO_SEG07_DIAG", None)
        try:
            self.assertEqual(self._post(email="diag-d@x.com"), 401)
            self.assertEqual(self._diag_lines(), [])
        finally:
            os.environ["ZAYVERO_SEG07_DIAG"] = "1"


if __name__ == "__main__":
    unittest.main()
