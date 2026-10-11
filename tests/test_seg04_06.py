"""SEG-04/05/06 — Pruebas de endurecimiento de autenticación.

SEG-04: rate limiting por cuenta y por IP (429, Retry-After, ventanas,
        recuperación, anti-abuso de memoria, concurrencia, XFF).
SEG-05: política de contraseñas (mínimo 12 en nuevas, heredadas intactas).
SEG-06: respuestas de autenticación homogéneas (sin enumeración).

Incluye pruebas unitarias y de integración HTTP real (WebappHandler en
127.0.0.1). Solo stdlib + unittest. Datos sintéticos.
"""

from __future__ import annotations

import http.cookiejar
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
import urllib.error
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tenant import TenantStore, create_company
from tenant.auth import AuthError, create_user, login, update_user
from tenant import audit as audit_mod
from tenant import rate_limit as rate_limit_mod
from tenant.rate_limit import LoginRateLimiter, client_origin_ip
from tenant import password_policy as pwd_policy_mod
from tenant.crypto import hash_password


# ======================================================================
# SEG-04 — unitarias del limitador
# ======================================================================

class Seg04LimiterUnitTest(unittest.TestCase):
    def _limiter(self, **kw):
        args = dict(account_max=5, account_window_s=900,
                    ip_max=20, ip_window_s=900, max_keys=10000)
        args.update(kw)
        return LoginRateLimiter(**args)

    def _fail(self, lim, ip, account):
        ok, _, res = lim.try_acquire(ip=ip, account=account)
        self.assertTrue(ok)
        self.assertIsNotNone(res)
        lim.settle(res, success=False)

    def _ok(self, lim, ip, account):
        ok, _, res = lim.try_acquire(ip=ip, account=account)
        self.assertTrue(ok)
        lim.settle(res, success=True)
        return res

    def test_permite_al_inicio(self):
        lim = self._limiter()
        ok, retry, res = lim.try_acquire(ip="1.2.3.4", account="a@t.test")
        self.assertTrue(ok)
        self.assertEqual(retry, 0.0)
        self.assertIsNotNone(res)
        lim.settle(res, success=True)

    def test_limite_por_cuenta(self):
        lim = self._limiter()
        for _ in range(5):
            self._fail(lim, "1.2.3.4", "victima@t.test")
        ok, retry, res = lim.try_acquire(ip="1.2.3.4",
                                         account="victima@t.test")
        self.assertFalse(ok)
        self.assertGreater(retry, 0)
        self.assertIsNone(res)
        # Otra cuenta desde la misma IP sigue permitida (límite IP=20).
        self._ok(lim, "1.2.3.4", "otro@t.test")

    def test_limite_por_ip(self):
        lim = self._limiter()
        for i in range(20):
            self._fail(lim, "9.9.9.9", f"u{i}@t.test")
        # Una cuenta NUEVA desde esa IP también queda limitada.
        ok, retry, res = lim.try_acquire(ip="9.9.9.9", account="nueva@t.test")
        self.assertFalse(ok)
        self.assertGreater(retry, 0)
        self.assertIsNone(res)

    def test_distintos_origenes_independientes(self):
        lim = self._limiter()
        for _ in range(5):
            self._fail(lim, "1.1.1.1", "a@t.test")
        ok, _, res = lim.try_acquire(ip="2.2.2.2", account="a@t.test")
        # La cubeta por cuenta es global (no depende de IP): sigue limitada.
        self.assertFalse(ok)
        self.assertIsNone(res)
        self._ok(lim, "2.2.2.2", "b@t.test")

    def test_ventana_expira_y_recupera(self):
        lim = self._limiter(account_max=2, account_window_s=0.3,
                            ip_max=100, ip_window_s=60)
        self._fail(lim, "1.2.3.4", "a@t.test")
        self._fail(lim, "1.2.3.4", "a@t.test")
        ok, _, res = lim.try_acquire(ip="1.2.3.4", account="a@t.test")
        self.assertFalse(ok)
        self.assertIsNone(res)
        time.sleep(0.4)
        self._ok(lim, "1.2.3.4", "a@t.test")  # cuenta legítima recuperada

    def test_exito_limpia_cubeta_cuenta(self):
        lim = self._limiter()
        for _ in range(4):
            self._fail(lim, "1.2.3.4", "a@t.test")
        self._ok(lim, "1.2.3.4", "a@t.test")
        # Tras el éxito, caben 5 fallos nuevos sin bloquear.
        for _ in range(5):
            self._fail(lim, "1.2.3.4", "a@t.test")
        ok, _, res = lim.try_acquire(ip="1.2.3.4", account="a@t.test")
        self.assertFalse(ok)
        self.assertIsNone(res)

    def test_claves_distintas_cubetas_distintas(self):
        # Contrato: el limitador no normaliza solo; el servidor normaliza
        # el email (strip+lower) ANTES de llamar, igual que el login.
        lim = self._limiter()
        for _ in range(5):
            self._fail(lim, "1.2.3.4", "A@T.TEST ")
        self._ok(lim, "1.2.3.4", "a@t.test")

    def test_crecimiento_acotado(self):
        # Con capacidad llena, los identificadores nuevos usan la vía
        # fail-open acotada por saturación (algunos se deniegan): lo que
        # se exige es memoria acotada, no que todos pasen.
        lim = self._limiter(max_keys=50)
        for i in range(500):
            ok, _, res = lim.try_acquire(ip=f"10.0.0.{i % 250}",
                                         account=f"u{i}@t.test")
            if ok:
                lim.settle(res, success=False)
        self.assertLessEqual(lim.bucket_sizes(), 50)
        self.assertEqual(lim.pending_size(), 0)
        self.assertEqual(lim.reservations_size(), 0)

    def test_concurrente_sin_bypass(self):
        # FIX-2 (negativa): 20 hilos simultáneos con límite 5 deben
        # obtener EXACTAMENTE 5 autorizaciones. Con is_allowed() +
        # record_failure() separados, los 20 pasaban (race TOCTOU).
        # Solo se liquida lo adquirido (nunca settle si rejected).
        lim = self._limiter(account_max=5, account_window_s=60,
                            ip_max=1000, ip_window_s=60)
        results = []
        lock = threading.Lock()

        def worker():
            ok, _, res = lim.try_acquire(ip="1.2.3.4",
                                         account="victima@t.test")
            with lock:
                results.append(ok)
            # Simular el trabajo (PBKDF2) FUERA del candado.
            time.sleep(0.01)
            if ok:
                lim.settle(res, success=False)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 20)
        self.assertEqual(sum(results), 5)

    def test_desalojo_no_resetea_bloqueo_activo(self):
        # FIX-3 (negativa): llenar la capacidad con cuentas nuevas NO
        # debe desalojar (y con ello desbloquear) una cuenta limitada.
        lim = self._limiter(account_max=2, account_window_s=3600,
                            ip_max=10000, ip_window_s=3600, max_keys=10)
        self._fail(lim, "1.1.1.1", "victima@t.test")
        self._fail(lim, "1.1.1.1", "victima@t.test")
        ok, _, res = lim.try_acquire(ip="1.1.1.1", account="victima@t.test")
        self.assertFalse(ok)  # bloqueada
        self.assertIsNone(res)
        # Presión de memoria: 50 cuentas adicionales.
        for i in range(50):
            self._fail(lim, "2.2.2.2", f"junk{i}@t.test")
        # La víctima SIGUE bloqueada; memoria acotada.
        ok, _, res = lim.try_acquire(ip="1.1.1.1", account="victima@t.test")
        self.assertFalse(ok)
        self.assertIsNone(res)
        self.assertLessEqual(lim.bucket_sizes(), 10)

    def test_desalojo_reutiliza_expiradas(self):
        # Las cubetas expiradas SÍ se reutilizan (no se pierde capacidad).
        lim = self._limiter(account_max=1, account_window_s=0.2,
                            ip_max=10000, ip_window_s=3600, max_keys=4)
        for i in range(4):
            self._fail(lim, "3.3.3.3", f"old{i}@t.test")
        self.assertEqual(lim.bucket_sizes(), 4)
        time.sleep(0.3)  # todas expiran
        self._fail(lim, "3.3.3.3", "nueva@t.test")  # reutiliza una expirada
        self.assertLessEqual(lim.bucket_sizes(), 4)

    # ---- FIX-4: la cubeta inexistente no salta el límite de la otra ----
    def test_cuenta_bloqueada_ip_nueva_sin_capacidad(self):
        # FIX-4.1 (negativa): cuenta bloqueada + IP nueva + cubetas
        # llenas → el bloqueo de CUENTA se mantiene. (Antes: la IP nueva
        # sin espacio devolvía True sin mirar la cuenta.)
        lim = self._limiter(account_max=2, account_window_s=3600,
                            ip_max=10000, ip_window_s=3600, max_keys=6,
                            saturation_max=1000, saturation_window_s=60)
        self._fail(lim, "1.1.1.1", "victima@t.test")
        self._fail(lim, "1.1.1.1", "victima@t.test")
        for i in range(10):  # llenar capacidad con junk
            self._fail(lim, "2.2.2.2", f"junk{i}@t.test")
        self.assertEqual(lim.bucket_sizes(), 6)
        ok, _, res = lim.try_acquire(ip="9.9.9.9", account="victima@t.test")
        self.assertFalse(ok)
        self.assertIsNone(res)

    def test_ip_bloqueada_cuenta_nueva_sin_capacidad(self):
        # FIX-4.2 (negativa): IP bloqueada + cuenta nueva + cubetas
        # llenas → el bloqueo de IP se mantiene.
        lim = self._limiter(account_max=10000, account_window_s=3600,
                            ip_max=3, ip_window_s=3600, max_keys=8,
                            saturation_max=1000, saturation_window_s=60)
        for i in range(3):
            self._fail(lim, "9.9.9.9", f"u{i}@t.test")
        # Llenar capacidad con junk en IPs DISTINTAS (la IP 9.9.9.9 ya
        # está en su límite: el junk no debe compartirla).
        for i in range(2):
            self._fail(lim, f"10.8.{i}.1", f"junk{i}@t.test")
        self.assertEqual(lim.bucket_sizes(), 8)
        ok, _, res = lim.try_acquire(ip="9.9.9.9", account="nueva@t.test")
        self.assertFalse(ok)
        self.assertIsNone(res)

    def test_saturacion_acota_fail_open(self):
        # FIX-4.3 (negativa): con todo lleno e identificadores nuevos,
        # los intentos NO son ilimitados: la cubeta global de saturación
        # los acota explícitamente (degradación, no denegación total).
        lim = self._limiter(account_max=10000, account_window_s=3600,
                            ip_max=10000, ip_window_s=3600, max_keys=6,
                            saturation_max=5, saturation_window_s=60)
        # Llenado exacto sin consumir saturación: 1 IP + 5 cuentas = 6.
        for i in range(5):
            self._fail(lim, "2.2.2.2", f"junk{i}@t.test")
        self.assertEqual(lim.bucket_sizes(), 6)
        allowed = 0
        for i in range(20):
            ok, _, res = lim.try_acquire(ip=f"10.9.{i}.1",
                                         account=f"nuevo{i}@t.test")
            if ok:
                allowed += 1
                lim.settle(res, success=False)
        self.assertLessEqual(allowed, 5)
        self.assertGreater(allowed, 0)

    # ---- FIX-5: _pending acotado y settle exactly-once ----
    def test_pending_sin_ceros_tras_miles(self):
        # FIX-5.4 (negativa): miles de solicitudes completadas no deben
        # acumular entradas con contador cero en _pending.
        lim = self._limiter(max_keys=100000)
        for i in range(2000):
            self._ok(lim, f"10.1.{i % 250}.1", f"u{i}@t.test")
        self.assertEqual(lim.pending_size(), 0)
        self.assertEqual(lim.reservations_size(), 0)

    def test_settle_sin_reserva_no_crea_nada(self):
        # FIX-5.5 (negativa): settle() sin acquire previo no crea
        # registros en _pending ni en _buckets.
        lim = self._limiter()
        lim.settle(None, success=False)
        lim.settle(999999, success=False)
        lim.settle(999999, success=True)
        self.assertEqual(lim.pending_size(), 0)
        self.assertEqual(lim.bucket_sizes(), 0)
        self.assertEqual(lim.reservations_size(), 0)

    def test_settle_doble_es_noop(self):
        # Cada reserva se liquida exactamente una vez: el segundo
        # settle no duplica el fallo ni crea registros.
        lim = self._limiter(account_max=5, account_window_s=3600,
                            ip_max=10000, ip_window_s=3600)
        ok, _, res = lim.try_acquire(ip="1.2.3.4", account="a@t.test")
        self.assertTrue(ok)
        lim.settle(res, success=False)
        lim.settle(res, success=False)  # no-op
        lim.settle(res, success=True)   # no-op
        # Solo UN fallo registrado: caben 4 más.
        for _ in range(4):
            self._fail(lim, "1.2.3.4", "a@t.test")
        ok, _, res = lim.try_acquire(ip="1.2.3.4", account="a@t.test")
        self.assertFalse(ok)
        self.assertIsNone(res)
        self.assertEqual(lim.pending_size(), 0)


class _FakeHandler:
    def __init__(self, peer, xff=None):
        self.client_address = (peer, 12345)
        self.headers = {"X-Forwarded-For": xff} if xff is not None else {}


class Seg04OriginIpTest(unittest.TestCase):
    """FIX-1: identificación de IP con topología explícita.

    Sin proxies declarados, X-Forwarded-For se ignora siempre. Con
    ZAYVERO_TRUSTED_PROXIES, solo se usa si el peer directo es un proxy
    confiable, recorriendo de derecha a izquierda (sin posiciones fijas).
    """

    def setUp(self):
        self._saved = os.environ.get("ZAYVERO_TRUSTED_PROXIES")
        os.environ.pop("ZAYVERO_TRUSTED_PROXIES", None)

    def tearDown(self):
        if self._saved is None:
            os.environ.pop("ZAYVERO_TRUSTED_PROXIES", None)
        else:
            os.environ["ZAYVERO_TRUSTED_PROXIES"] = self._saved

    def test_sin_proxies_ignora_xff(self):
        # NEGATIVA: XFF falsificada por un cliente directo no se usa.
        h = _FakeHandler("203.0.113.7", xff="1.2.3.4, 5.6.7.8")
        self.assertEqual(client_origin_ip(h), "203.0.113.7")

    def test_sin_proxies_sin_xff(self):
        h = _FakeHandler("203.0.113.7")
        self.assertEqual(client_origin_ip(h), "203.0.113.7")

    def test_peer_confiable_un_proxy(self):
        os.environ["ZAYVERO_TRUSTED_PROXIES"] = "10.0.0.1"
        h = _FakeHandler("10.0.0.1", xff="198.51.100.23")
        self.assertEqual(client_origin_ip(h), "198.51.100.23")

    def test_peer_confiable_cadena_multi_proxy(self):
        # FIX-1: dos proxies en cadena; el algoritmo no usa posición fija.
        os.environ["ZAYVERO_TRUSTED_PROXIES"] = "10.0.0.1, 10.0.0.2"
        # cliente -> P1(10.0.0.1) -> P2(10.0.0.2) -> app
        h = _FakeHandler("10.0.0.2", xff="198.51.100.23, 10.0.0.1")
        self.assertEqual(client_origin_ip(h), "198.51.100.23")

    def test_peer_confiable_xff_falsificada_izquierda(self):
        # FIX-1 (negativa): el atacante añade entradas a la IZQUIERDA;
        # el recorrido derecha-a-izquierda las ignora.
        os.environ["ZAYVERO_TRUSTED_PROXIES"] = "10.0.0.1"
        h = _FakeHandler("10.0.0.1",
                         xff="6.6.6.6, 7.7.7.7, 198.51.100.23")
        self.assertEqual(client_origin_ip(h), "198.51.100.23")

    def test_peer_no_confiable_con_proxies_declarados(self):
        # FIX-1 (negativa): peer directo NO declarado → XFF se ignora
        # aunque haya proxies configurados.
        os.environ["ZAYVERO_TRUSTED_PROXIES"] = "10.0.0.1"
        h = _FakeHandler("203.0.113.7", xff="198.51.100.23")
        self.assertEqual(client_origin_ip(h), "203.0.113.7")

    def test_hasta_primer_no_confiable(self):
        # Solo se "ve a través" de los proxies declarados: ante un
        # salto no declarado se devuelve ese salto (lo más cercano
        # verificable), no el extremo falsificable.
        os.environ["ZAYVERO_TRUSTED_PROXIES"] = "10.0.0.2"
        h = _FakeHandler("10.0.0.2", xff="198.51.100.23, 10.0.0.1")
        self.assertEqual(client_origin_ip(h), "10.0.0.1")

    def test_peer_confiable_sin_xff_fallback_seguro(self):
        # FIX-1: sin XFF no se puede determinar la IP; alternativa
        # segura y documentada: el peer (cubeta compartida del proxy).
        os.environ["ZAYVERO_TRUSTED_PROXIES"] = "10.0.0.1"
        h = _FakeHandler("10.0.0.1")
        self.assertEqual(client_origin_ip(h), "10.0.0.1")


# ======================================================================
# SEG-04/06 — integración HTTP real
# ======================================================================

class RawClient:
    def __init__(self, port):
        self.port = port
        self.jar = http.cookiejar.CookieJar()

    def _req(self, method, path, body=None, headers=None):
        opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(self.jar))
        data = None
        hdrs = dict(headers or {})
        if body is not None:
            data = json.dumps(body).encode("utf-8")
            hdrs["Content-Type"] = "application/json"
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=data,
            headers=hdrs, method=method)
        try:
            with opener.open(req) as res:
                raw = res.read().decode("utf-8")
                try:
                    payload = json.loads(raw) if raw else {}
                except Exception:
                    payload = {"_raw": raw}
                return res.status, payload, dict(res.headers)
        except urllib.error.HTTPError as e:
            raw = e.read().decode("utf-8")
            try:
                payload = json.loads(raw) if raw else {}
            except Exception:
                payload = {}
            return e.code, payload, dict(e.headers)

    def login(self, email, password, origin=None):
        headers = {"Origin": origin} if origin else None
        st, payload, hdrs = self._req(
            "POST", "/api/login",
            body={"email": email, "password": password}, headers=headers)
        return st, payload, hdrs


class Seg0406HttpTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tenant.auth import create_user as _cu
        import webapp.server as server_mod

        cls.tmp = tempfile.mkdtemp(prefix="zb_seg0406_")
        store = TenantStore(cls.tmp)
        store.ensure_demo_company()
        cls.comp = create_company(store, "Empresa Seg0406")
        _cu(store, company_id=cls.comp.company_id,
            email="rl-a@seg0406.test", name="RL A",
            password="ClaveLarga0406A!", role_id="owner")
        _cu(store, company_id=cls.comp.company_id,
            email="rl-b@seg0406.test", name="RL B",
            password="ClaveLarga0406B!", role_id="owner")
        # Usuario deshabilitado (SEG-06).
        u = _cu(store, company_id=cls.comp.company_id,
                email="rl-off@seg0406.test", name="RL Off",
                password="ClaveLarga0406C!", role_id="viewer")
        update_user(store, u.user_id, status="disabled")
        cls.store = store
        server_mod.STORE = store
        server_mod._DATA_CACHE.clear()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0),
                                        server_mod.WebappHandler)
        cls.port = cls.httpd.server_address[1]
        t = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        t.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        # Ventanas cortas y deterministas para estas pruebas HTTP.
        # Se restauran en tearDown para no contaminar otros archivos.
        self._saved_env = {}
        for k, v in (("ZAYVERO_RL_ACCOUNT_MAX", "3"),
                     ("ZAYVERO_RL_ACCOUNT_WINDOW", "30"),
                     ("ZAYVERO_RL_IP_MAX", "100"),
                     ("ZAYVERO_RL_IP_WINDOW", "30")):
            self._saved_env[k] = os.environ.get(k)
            os.environ[k] = v
        rate_limit_mod._limiter = None  # fuerza relectura de env

    def tearDown(self):
        for k, v in self._saved_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        rate_limit_mod._limiter = None

    def _login(self, email, password):
        c = RawClient(self.port)
        return c.login(email, password)

    # ---- SEG-04 HTTP ----
    def test_429_tras_fallos_repetidos(self):
        for _ in range(3):
            st, _, _ = self._login("rl-a@seg0406.test", "ClaveMala000!")
            self.assertEqual(st, 401)
        st, payload, hdrs = self._login("rl-a@seg0406.test", "ClaveMala000!")
        self.assertEqual(st, 429)
        self.assertIn("Retry-After", hdrs)
        self.assertNotIn("rl-a@seg0406.test", json.dumps(payload))
        # Mensaje genérico, sin contadores ni políticas internas.
        self.assertNotIn("3", payload.get("error", ""))

    def test_otra_cuenta_no_afectada(self):
        for _ in range(3):
            self._login("rl-a@seg0406.test", "ClaveMala000!")
        # La cuenta B, desde la misma IP, sigue recibiendo 401 (no 429).
        st, _, _ = self._login("rl-b@seg0406.test", "ClaveMala000!")
        self.assertEqual(st, 401)

    def test_exito_recupera_cuenta(self):
        for _ in range(2):
            self._login("rl-a@seg0406.test", "ClaveMala000!")
        st, payload, _ = self._login("rl-a@seg0406.test", "ClaveLarga0406A!")
        self.assertEqual(st, 200)
        self.assertIn("csrf_token", payload)
        # Tras el éxito, la cubeta se limpió: 3 fallos más no dan 429.
        for _ in range(3):
            st, _, _ = self._login("rl-a@seg0406.test", "ClaveMala000!")
            self.assertEqual(st, 401)

    def test_inexistente_tambien_429(self):
        # SEG-04+06: el limitador no distingue existente/inexistente.
        for _ in range(3):
            st, _, _ = self._login("nadie@seg0406.test", "ClaveMala000!")
            self.assertEqual(st, 401)
        st, _, _ = self._login("nadie@seg0406.test", "ClaveMala000!")
        self.assertEqual(st, 429)

    def test_concurrente_http_sin_bypass(self):
        # FIX-2 (negativa, servidor real): 10 hilos simultáneos con
        # límite 3 → exactamente 3 obtienen 401 (PBKDF2) y 7 obtienen
        # 429. Sin reserva atómica, los 10 pasarían el chequeo.
        results = []
        lock = threading.Lock()

        def worker():
            st, _, _ = self._login("rl-b@seg0406.test", "ClaveMala000!")
            with lock:
                results.append(st)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(results), 10)
        self.assertEqual(results.count(401), 3)
        self.assertEqual(results.count(429), 7)

    # ---- SEG-06 HTTP ----
    def test_respuestas_homogeneas(self):
        st1, p1, _ = self._login("nadie@seg0406.test", "ClaveLarga0406A!")
        st2, p2, _ = self._login("rl-a@seg0406.test", "ClaveMala000!")
        st3, p3, _ = self._login("rl-off@seg0406.test", "ClaveLarga0406C!")
        # Las tres: 401 con el MISMO cuerpo genérico.
        for st, p in ((st1, p1), (st2, p2), (st3, p3)):
            self.assertEqual(st, 401)
        self.assertEqual(p1, p2)
        self.assertEqual(p2, p3)
        blob = json.dumps(p1).lower()
        self.assertNotIn("deshabilitado", blob)
        self.assertNotIn("disabled", blob)
        self.assertNotIn("existe", blob)

    def test_login_habilitado_ok(self):
        st, payload, _ = self._login("rl-b@seg0406.test", "ClaveLarga0406B!")
        self.assertEqual(st, 200)
        self.assertIn("csrf_token", payload)

    def test_auditoria_interna_conserva_motivo(self):
        self._login("nadie@seg0406.test", "x")
        self._login("rl-off@seg0406.test", "ClaveLarga0406C!")
        # Nota: el fallo de email desconocido se registra con company_id
        # vacío (no se puede atribuir a empresa); se busca sin filtro.
        events = self.store.list_audit()
        reasons = set()
        for e in events:
            d = e.to_dict()
            if d.get("action") == audit_mod.LOGIN_FAILED:
                md = d.get("metadata") or {}
                if "reason" in md:
                    reasons.add(md["reason"])
        # Distinción interna conservada (no expuesta al navegador).
        self.assertIn("unknown_user", reasons)
        self.assertIn("disabled", reasons)

    def test_sin_secretos_en_auditoria(self):
        secreto = "ClaveMala000!"
        self._login("rl-a@seg0406.test", secreto)
        events = self.store.list_audit(self.comp.company_id)
        blob = "".join(e.to_dict().__str__() for e in events)
        self.assertNotIn(secreto, blob)


# ======================================================================
# SEG-05 — política de contraseñas
# ======================================================================

class Seg05PasswordPolicyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zb_seg05_")
        self.store = TenantStore(data_dir=os.path.join(self.tmp, "tenant"))
        self.store.ensure_demo_company()
        self.comp = create_company(self.store, "Empresa Seg05")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mk(self, email, password):
        return create_user(self.store, company_id=self.comp.company_id,
                           email=email, name="T", password=password,
                           role_id="viewer")

    def test_8_caracteres_rechazada(self):
        with self.assertRaises(AuthError):
            self._mk("p8@t.test", "ocho1234")

    def test_11_caracteres_rechazada(self):
        with self.assertRaises(AuthError):
            self._mk("p11@t.test", "once1234567")

    def test_12_caracteres_aceptada(self):
        u = self._mk("p12@t.test", "doce12345678")
        self.assertEqual(u.email, "p12@t.test")

    def test_frase_larga_aceptada(self):
        frase = "mi frase de contraseña favorita es muy larga y segura"
        u = self._mk("frase@t.test", frase)
        token, _ = login(self.store, "frase@t.test", frase)
        self.assertTrue(token)

    def test_espacios_y_unicode(self):
        pwd = "contraseña con espacios 🔒 y ñ"
        u = self._mk("uni@t.test", pwd)
        token, _ = login(self.store, "uni@t.test", pwd)
        self.assertTrue(token)

    def test_maximo_128_aceptado_129_rechazado(self):
        self._mk("max@t.test", "x" * 128)
        with self.assertRaises(AuthError):
            self._mk("max2@t.test", "x" * 129)

    def test_heredada_8_caracteres_sigue_funcionando(self):
        # Contraseña heredada (creada fuera de create_user, como las
        # existentes de 8-11): el login la sigue aceptando.
        from tenant.models import User
        import uuid
        legacy = User(
            user_id="USR-" + uuid.uuid4().hex[:12],
            company_id=self.comp.company_id, email="legacy@t.test",
            name="Legacy", status="active", role_id="viewer",
            password_hash=hash_password("ocho1234"),
            created_at="2024-01-01T00:00:00Z",
            updated_at="2024-01-01T00:00:00Z",
        )
        self.store.save_user(legacy)
        token, _ = login(self.store, "legacy@t.test", "ocho1234")
        self.assertTrue(token)

    def test_hash_sin_texto_plano(self):
        u = self._mk("hash@t.test", "ClaveSegura12!")
        stored = self.store.get_user(u.user_id)
        self.assertTrue(stored.password_hash.startswith("pbkdf2_sha256$"))
        self.assertNotIn("ClaveSegura12!", stored.password_hash)

    def test_validate_centralizada(self):
        self.assertEqual(pwd_policy_mod.validate_new_password("corta"), [
            "la contraseña debe tener al menos 12 caracteres"])
        self.assertEqual(pwd_policy_mod.validate_new_password("x" * 129), [
            "la contraseña no puede superar 128 caracteres"])
        self.assertEqual(
            pwd_policy_mod.validate_new_password("doce12345678"), [])


# ======================================================================
# SEG-06 — nivel auth (sin HTTP)
# ======================================================================

class Seg06AuthUnitTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="zb_seg06_")
        self.store = TenantStore(data_dir=os.path.join(self.tmp, "tenant"))
        self.store.ensure_demo_company()
        self.comp = create_company(self.store, "Empresa Seg06")
        create_user(self.store, company_id=self.comp.company_id,
                    email="ok@seg06.test", name="OK",
                    password="ClaveLarga0606!", role_id="viewer")
        u = create_user(self.store, company_id=self.comp.company_id,
                        email="off@seg06.test", name="Off",
                        password="ClaveLarga0606!", role_id="viewer")
        update_user(self.store, u.user_id, status="disabled")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _err(self, email, password):
        with self.assertRaises(AuthError) as cm:
            login(self.store, email, password)
        return str(cm.exception)

    def test_mensaje_identico_tres_casos(self):
        e1 = self._err("nadie@seg06.test", "ClaveLarga0606!")
        e2 = self._err("ok@seg06.test", "ClaveMala000!")
        e3 = self._err("off@seg06.test", "ClaveLarga0606!")
        self.assertEqual(e1, "credenciales inválidas")
        self.assertEqual(e2, "credenciales inválidas")
        self.assertEqual(e3, "credenciales inválidas")

    def test_habilitado_ok(self):
        token, ctx = login(self.store, "ok@seg06.test", "ClaveLarga0606!")
        self.assertTrue(token)
        self.assertEqual(ctx.email, "ok@seg06.test")


if __name__ == "__main__":
    unittest.main(verbosity=2)
