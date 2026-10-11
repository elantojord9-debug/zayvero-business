"""SEG-07 — Proxy y rate limiting: identificación de origen e IP.

Cubre el reconocimiento controlado de CF-Connecting-IP (opt-in
ZAYVERO_BEHIND_CLOUDFLARE, desactivado por defecto) y la
preservación del comportamiento anterior:

- Sin opt-in: se usa el peer TCP; CF-Connecting-IP y X-Forwarded-For
  se ignoran (salvo ZAYVERO_TRUSTED_PROXIES declarado).
- Con opt-in + peer privado: CF-Connecting-IP válida (una sola
  IPv4/IPv6) identifica al visitante.
- Escenarios inseguros (cabecera falsificada, peer público, valores
  inválidos/duplicados): fallan de forma conservadora al peer.
- La cubeta por CUENTA sigue siendo inmune a la suplantación de IP.
"""

from __future__ import annotations

import os
import threading
import time
import unittest
from email.message import Message

from tenant import rate_limit as rl


class _FakeHandler:
    """Imita lo que el limitador necesita del request handler.

    Usa email.message.Message real (la clase base de HTTPMessage):
    reproduce el comportamiento auténtico de get()/get_all() ante
    cabeceras duplicadas, como las vería el servidor en producción.
    """

    def __init__(self, peer, cf_ips=(), xff=None):
        self.client_address = (peer, 12345)
        self._headers = Message()
        for ip in cf_ips:
            self._headers.add_header("CF-Connecting-IP", ip)
        if xff is not None:
            self._headers.add_header("X-Forwarded-For", xff)

    @property
    def headers(self):
        return self._headers


def _attempt(lim, ip, account, ok=False):
    allowed, retry, tok = lim.try_acquire(ip=ip, account=account)
    if allowed:
        lim.settle(tok, success=ok)
    return allowed, retry


class TestClientOriginIp(unittest.TestCase):
    def setUp(self):
        self._old_cf = os.environ.get("ZAYVERO_BEHIND_CLOUDFLARE")
        self._old_tp = os.environ.get("ZAYVERO_TRUSTED_PROXIES")
        os.environ.pop("ZAYVERO_BEHIND_CLOUDFLARE", None)
        os.environ.pop("ZAYVERO_TRUSTED_PROXIES", None)

    def tearDown(self):
        if self._old_cf is None:
            os.environ.pop("ZAYVERO_BEHIND_CLOUDFLARE", None)
        else:
            os.environ["ZAYVERO_BEHIND_CLOUDFLARE"] = self._old_cf
        if self._old_tp is None:
            os.environ.pop("ZAYVERO_TRUSTED_PROXIES", None)
        else:
            os.environ["ZAYVERO_TRUSTED_PROXIES"] = self._old_tp

    # --- Comportamiento anterior: sin opt-in ---

    def test_sin_optin_ignora_cf_y_usa_peer(self):
        h = _FakeHandler("10.0.0.5", cf_ips=["203.0.113.9"])
        self.assertEqual(rl.client_origin_ip(h), "10.0.0.5")

    def test_sin_optin_ignora_xff_falsificada(self):
        h = _FakeHandler("203.0.113.7", xff="1.2.3.4, 5.6.7.8")
        self.assertEqual(rl.client_origin_ip(h), "203.0.113.7")

    # --- Ruta confiable: opt-in + peer privado ---

    def test_optin_peer_privado_cf_ipv4_valida(self):
        os.environ["ZAYVERO_BEHIND_CLOUDFLARE"] = "1"
        h = _FakeHandler("10.0.0.5", cf_ips=["203.0.113.9"])
        self.assertEqual(rl.client_origin_ip(h), "203.0.113.9")

    def test_optin_peer_privado_cf_ipv6_valida(self):
        os.environ["ZAYVERO_BEHIND_CLOUDFLARE"] = "true"
        h = _FakeHandler("10.0.0.5", cf_ips=["2001:db8::1"])
        self.assertEqual(rl.client_origin_ip(h), "2001:db8::1")

    def test_optin_cf_falsificada_vacia_cae_a_peer(self):
        os.environ["ZAYVERO_BEHIND_CLOUDFLARE"] = "1"
        h = _FakeHandler("10.0.0.5", cf_ips=["   "])
        self.assertEqual(rl.client_origin_ip(h), "10.0.0.5")

    def test_optin_cf_falsificada_multiple_cae_a_peer(self):
        os.environ["ZAYVERO_BEHIND_CLOUDFLARE"] = "1"
        h = _FakeHandler("10.0.0.5", cf_ips=["203.0.113.9, 198.51.100.7"])
        self.assertEqual(rl.client_origin_ip(h), "10.0.0.5")

    def test_optin_cf_malformada_cae_a_peer(self):
        os.environ["ZAYVERO_BEHIND_CLOUDFLARE"] = "1"
        for bad in ("no-es-ip", "999.999.999.999", "203.0.113.9:443",
                    "unknown", "<script>"):
            h = _FakeHandler("10.0.0.5", cf_ips=[bad])
            self.assertEqual(rl.client_origin_ip(h), "10.0.0.5",
                             f"valor: {bad!r}")

    def test_optin_cf_ausente_cae_a_peer(self):
        os.environ["ZAYVERO_BEHIND_CLOUDFLARE"] = "1"
        h = _FakeHandler("10.0.0.5")
        self.assertEqual(rl.client_origin_ip(h), "10.0.0.5")

    def test_optin_cf_lineas_duplicadas_ambas_validas_rechazadas(self):
        # Dos líneas CF-Connecting-IP independientes (ambas válidas):
        # la cabecera NO es confiable → se ignora por completo.
        os.environ["ZAYVERO_BEHIND_CLOUDFLARE"] = "1"
        h = _FakeHandler("10.0.0.5",
                         cf_ips=["203.0.113.9", "198.51.100.7"])
        self.assertEqual(
            h.headers.get_all("CF-Connecting-IP"),
            ["203.0.113.9", "198.51.100.7"])  # el simulador es fiel
        self.assertEqual(rl.client_origin_ip(h), "10.0.0.5")

    def test_optin_cf_lineas_duplicadas_una_malformada_rechazadas(self):
        # Una válida + una malformada: también se rechaza la cabecera.
        os.environ["ZAYVERO_BEHIND_CLOUDFLARE"] = "1"
        h = _FakeHandler("10.0.0.5",
                         cf_ips=["203.0.113.9", "no-es-ip"])
        self.assertEqual(rl.client_origin_ip(h), "10.0.0.5")

    def test_fallback_con_ambas_vars_no_abre_suplantacion(self):
        # Con opt-in + TRUSTED_PROXIES configurados, una CF duplicada
        # (no confiable) vuelve a la lógica preexistente SIN abrir una
        # ruta nueva: con peer confiable se aplica el recorrido XFF de
        # siempre; con peer no confiable se usa el peer.
        os.environ["ZAYVERO_BEHIND_CLOUDFLARE"] = "1"
        os.environ["ZAYVERO_TRUSTED_PROXIES"] = "10.0.0.5"
        h = _FakeHandler("10.0.0.5",
                         cf_ips=["203.0.113.9", "203.0.113.10"],
                         xff="9.9.9.9")
        self.assertEqual(rl.client_origin_ip(h), "9.9.9.9")
        h = _FakeHandler("10.9.9.9",
                         cf_ips=["203.0.113.9", "203.0.113.10"],
                         xff="9.9.9.9")
        self.assertEqual(rl.client_origin_ip(h), "10.9.9.9")

    # --- Casos conservadores ---

    def test_optin_peer_publico_ignora_cf(self):
        # Aunque el opt-in esté activo, un peer público (conexión
        # directa, sin pasar por la plataforma) nunca confía en CF.
        os.environ["ZAYVERO_BEHIND_CLOUDFLARE"] = "1"
        h = _FakeHandler("198.51.100.23", cf_ips=["203.0.113.9"])
        self.assertEqual(rl.client_origin_ip(h), "198.51.100.23")

    def test_peer_privado_sin_optin_no_confia(self):
        # "Peer privado" solo NO basta: sin opt-in explícito, la
        # cabecera se ignora.
        h = _FakeHandler("172.16.0.9", cf_ips=["203.0.113.9"])
        self.assertEqual(rl.client_origin_ip(h), "172.16.0.9")

    # --- ZAYVERO_TRUSTED_PROXIES intacto ---

    def test_trusted_proxies_sin_cambios(self):
        os.environ["ZAYVERO_TRUSTED_PROXIES"] = "10.0.0.1, 10.0.0.2"
        h = _FakeHandler("10.0.0.2", xff="9.9.9.9, 10.0.0.1")
        self.assertEqual(rl.client_origin_ip(h), "9.9.9.9")
        h = _FakeHandler("10.0.0.2", xff="1.1.1.1, 9.9.9.9, 10.0.0.1")
        self.assertEqual(rl.client_origin_ip(h), "9.9.9.9")
        h = _FakeHandler("10.0.0.1", xff="")
        self.assertEqual(rl.client_origin_ip(h), "10.0.0.1")

    def test_cf_tiene_prioridad_sobre_trusted_proxies(self):
        # Con opt-in, la señal de Cloudflare (sobrescrita por el edge)
        # prevalece sobre XFF.
        os.environ["ZAYVERO_BEHIND_CLOUDFLARE"] = "1"
        os.environ["ZAYVERO_TRUSTED_PROXIES"] = "10.0.0.5"
        h = _FakeHandler("10.0.0.5", cf_ips=["203.0.113.9"],
                         xff="9.9.9.9")
        self.assertEqual(rl.client_origin_ip(h), "203.0.113.9")


class TestRateLimitBuckets(unittest.TestCase):
    def test_dos_visitantes_distintos_cf(self):
        lim = rl.LoginRateLimiter(account_max=100, account_window_s=900,
                                  ip_max=3, ip_window_s=900)
        for i in range(3):
            _attempt(lim, "203.0.113.9", f"a{i}@x.com", ok=False)
        blocked, _ = _attempt(lim, "203.0.113.9", "nuevo@x.com", ok=False)
        allowed, _ = _attempt(lim, "198.51.100.4", "nuevo@x.com", ok=False)
        self.assertFalse(blocked)
        self.assertTrue(allowed)

    def test_dos_visitantes_detras_de_nat_comparten(self):
        lim = rl.LoginRateLimiter(account_max=100, account_window_s=900,
                                  ip_max=4, ip_window_s=900)
        for i in range(4):
            _attempt(lim, "203.0.113.9", f"cli{i}@x.com", ok=False)
        blocked, _ = _attempt(lim, "203.0.113.9", "otro@x.com", ok=False)
        self.assertFalse(blocked)

    def test_limites_por_cuenta_independientes(self):
        lim = rl.LoginRateLimiter(account_max=5, account_window_s=900,
                                  ip_max=100, ip_window_s=900)
        oks = [_attempt(lim, f"10.0.0.{i}", "victima@x.com", ok=False)[0]
               for i in range(7)]
        self.assertEqual(oks, [True] * 5 + [False] * 2)
        # La cubeta por cuenta no depende de la IP: ni rotando IPs se evade.
        lim2 = rl.LoginRateLimiter(account_max=2, account_window_s=900,
                                   ip_max=100, ip_window_s=900)
        _attempt(lim2, "10.0.0.1", "u@x.com", ok=False)
        _attempt(lim2, "10.0.0.2", "u@x.com", ok=False)
        blocked, _ = _attempt(lim2, "10.0.0.3", "u@x.com", ok=False)
        self.assertFalse(blocked)

    def test_exito_perdona_cuenta(self):
        lim = rl.LoginRateLimiter(account_max=2, account_window_s=900,
                                  ip_max=100, ip_window_s=900)
        _attempt(lim, "10.0.0.1", "u@x.com", ok=False)
        ok_allowed, _ = _attempt(lim, "10.0.0.1", "u@x.com", ok=True)
        again_allowed, _ = _attempt(lim, "10.0.0.1", "u@x.com", ok=False)
        self.assertTrue(ok_allowed and again_allowed)

    def test_intentos_concurrentes_atomicos(self):
        lim = rl.LoginRateLimiter(account_max=5, account_window_s=900,
                                  ip_max=1000, ip_window_s=900)
        granted = []
        lock = threading.Lock()

        def worker():
            ok, _, tok = lim.try_acquire(ip="7.7.7.7", account="race@x.com")
            if ok:
                with lock:
                    granted.append(tok)
                lim.settle(tok, success=False)

        threads = [threading.Thread(target=worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(granted), 5)

    def test_429_con_retry_after(self):
        lim = rl.LoginRateLimiter(account_max=1, account_window_s=900,
                                  ip_max=100, ip_window_s=900)
        _attempt(lim, "6.6.6.6", "h@x.com", ok=False)
        allowed, retry, tok = lim.try_acquire(ip="6.6.6.6",
                                              account="h@x.com")
        self.assertFalse(allowed)
        self.assertGreater(retry, 0)
        self.assertIsNone(tok)

    def test_tope_de_memoria(self):
        lim = rl.LoginRateLimiter(max_keys=100)
        for i in range(300):
            ok, _, tok = lim.try_acquire(
                ip=f"10.9.{i // 256}.{i % 256}", account=f"u{i}@x.com")
            if ok:
                lim.settle(tok, success=False)
        sizes = lim.bucket_sizes()
        self.assertLessEqual(sizes, 100)

    def test_reinicio_de_contadores_tras_ventana(self):
        lim = rl.LoginRateLimiter(account_max=2, account_window_s=1,
                                  ip_max=100, ip_window_s=900)
        _attempt(lim, "8.8.8.8", "g@x.com", ok=False)
        _attempt(lim, "8.8.8.8", "g@x.com", ok=False)
        blocked, _ = _attempt(lim, "8.8.8.8", "g@x.com", ok=False)
        self.assertFalse(blocked)
        time.sleep(1.2)
        allowed, _ = _attempt(lim, "8.8.8.8", "g@x.com", ok=False)
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
