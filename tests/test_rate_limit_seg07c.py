"""SEG-07C — Modelo de confianza revisado para CF-Connecting-IP.

Verificado en vivo 2026-10-11: todo el tráfico público (*.onrender.com
y el dominio propio) pasa por el edge de Cloudflare (`Server:
cloudflare` + CF-RAY), que sobrescribe CF-Connecting-IP con la IP real
del visitante. El salto edge→contenedor de Render NO usa RFC1918
(peer_rfc1918=0 observado), por lo que exigir peer privado solo
desactivaba la función. La defensa contra la falsificación es el
propio edge, no el peer.

Cubre, con opt-in ZAYVERO_BEHIND_CLOUDFLARE (desactivado por defecto):
- Cabeceras legítimas (IPv4/IPv6).
- X-Forwarded-For falsificado: se ignora cuando CF es confiable.
- CF-Connecting-IP falsificado (vacío/duplicado/malformado/con comas):
  se rechaza y se cae al peer (fail-closed).
- Cadenas XFF de distintas longitudes con trusted proxies.
- Cabeceras duplicadas, valores inválidos, ausencia de cabeceras.
- Concurrencia: resolución consistente y sin carreras.
- Opt-in desactivado: siempre peer (o trusted proxies declarado).
- La cubeta por CUENTA es inmune a la suplantación de IP.

NOTA HONESTA: una IP bien formada pero falsificada, enviada por una
ruta que evitara el edge de Cloudflare, sería indistinguible de una
legítima. El modelo descansa en la premisa verificada de que no
existe tal ruta pública; si la infraestructura cambia, el opt-in debe
revalidarse. Aun si la premisa fallara, la cubeta por cuenta (5
intentos/cuenta/15min) seguiría protegiendo contra fuerza bruta.
"""

from __future__ import annotations

import os
import threading
import unittest
from email.message import Message

from tenant import rate_limit as rl


class _FakeHandler:
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


class TestSeg07CTrustModel(unittest.TestCase):
    # Peer público como el real de Render (no RFC1918).
    PEER = "203.0.113.25"

    def setUp(self):
        self._old_cf = os.environ.get("ZAYVERO_BEHIND_CLOUDFLARE")
        self._old_tp = os.environ.get("ZAYVERO_TRUSTED_PROXIES")
        os.environ.pop("ZAYVERO_BEHIND_CLOUDFLARE", None)
        os.environ.pop("ZAYVERO_TRUSTED_PROXIES", None)

    def tearDown(self):
        for k, v in (("ZAYVERO_BEHIND_CLOUDFLARE", self._old_cf),
                     ("ZAYVERO_TRUSTED_PROXIES", self._old_tp)):
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def _optin(self):
        os.environ["ZAYVERO_BEHIND_CLOUDFLARE"] = "1"

    # --- Cabeceras legítimas ---

    def test_legitima_ipv4(self):
        self._optin()
        h = _FakeHandler(self.PEER, cf_ips=["198.51.100.77"])
        self.assertEqual(rl.client_origin_ip(h), "198.51.100.77")

    def test_legitima_ipv6(self):
        self._optin()
        h = _FakeHandler(self.PEER, cf_ips=["2001:db8::77"])
        self.assertEqual(rl.client_origin_ip(h), "2001:db8::77")

    def test_legitima_ipv4_mapeada(self):
        self._optin()
        h = _FakeHandler(self.PEER, cf_ips=["::ffff:192.0.2.44"])
        # Python normaliza la forma; lo importante es que es determinista.
        self.assertEqual(rl.client_origin_ip(h), "::ffff:c000:22c")

    # --- X-Forwarded-For falsificado ---

    def test_xff_falsificado_se_ignora_con_cf_confiable(self):
        # Con CF válida, el XFF (falsificable por el cliente) no se usa.
        self._optin()
        h = _FakeHandler(self.PEER, cf_ips=["198.51.100.77"],
                         xff="1.2.3.4, 5.6.7.8")
        self.assertEqual(rl.client_origin_ip(h), "198.51.100.77")

    def test_xff_falsificado_sin_optin_cae_a_peer(self):
        h = _FakeHandler(self.PEER, xff="1.2.3.4")
        self.assertEqual(rl.client_origin_ip(h), self.PEER)

    def test_xff_falsificado_con_cf_invalida_cae_a_peer(self):
        # CF inválida → se ignora; XFF sin proxies confiables → peer.
        self._optin()
        h = _FakeHandler(self.PEER, cf_ips=["no-es-ip"],
                         xff="1.2.3.4")
        self.assertEqual(rl.client_origin_ip(h), self.PEER)

    # --- CF-Connecting-IP falsificado ---

    def test_cf_falsificada_vacia_rechazada(self):
        self._optin()
        h = _FakeHandler(self.PEER, cf_ips=["   "])
        self.assertEqual(rl.client_origin_ip(h), self.PEER)

    def test_cf_falsificada_con_comas_rechazada(self):
        self._optin()
        h = _FakeHandler(self.PEER, cf_ips=["198.51.100.1, 9.9.9.9"])
        self.assertEqual(rl.client_origin_ip(h), self.PEER)

    def test_cf_falsificada_malformada_rechazada(self):
        self._optin()
        for bad in ("999.999.999.999", "hola", "2001:db8:::1", ""):
            h = _FakeHandler(self.PEER, cf_ips=[bad])
            self.assertEqual(rl.client_origin_ip(h), self.PEER,
                             f"no rechazó {bad!r}")

    def test_cf_duplicada_rechazada(self):
        self._optin()
        h = _FakeHandler(self.PEER,
                         cf_ips=["198.51.100.77", "198.51.100.77"])
        self.assertEqual(rl.client_origin_ip(h), self.PEER)

    def test_cf_ausente_cae_a_peer(self):
        self._optin()
        h = _FakeHandler(self.PEER)
        self.assertEqual(rl.client_origin_ip(h), self.PEER)

    # --- Cadenas XFF de distintas longitudes (trusted proxies) ---

    def test_xff_cadenas_con_proxies_confiables(self):
        os.environ["ZAYVERO_TRUSTED_PROXIES"] = "10.0.0.1"
        for xff, esperado in (
            ("9.9.9.9", "9.9.9.9"),
            ("9.9.9.9, 10.0.0.1", "9.9.9.9"),
            ("8.8.8.8, 9.9.9.9, 10.0.0.1", "9.9.9.9"),
            ("10.0.0.1", "10.0.0.1"),  # toda confiable → peer
        ):
            h = _FakeHandler("10.0.0.1", xff=xff)
            self.assertEqual(rl.client_origin_ip(h), esperado,
                             f"XFF {xff!r}")

    # --- Opt-in desactivado ---

    def test_sin_optin_siempre_peer(self):
        for cf in ([], ["198.51.100.77"], ["a", "b"]):
            h = _FakeHandler(self.PEER, cf_ips=cf, xff="1.2.3.4")
            self.assertEqual(rl.client_origin_ip(h), self.PEER)

    # --- Concurrencia ---

    def test_resolucion_concurrente_consistente(self):
        self._optin()
        resultados = []
        lock = threading.Lock()

        def worker(i):
            ip = rl.client_origin_ip(
                _FakeHandler(self.PEER, cf_ips=[f"198.51.100.{i}"]))
            with lock:
                resultados.append(ip)

        hilos = [threading.Thread(target=worker, args=(i,))
                 for i in range(20)]
        for t in hilos:
            t.start()
        for t in hilos:
            t.join()
        self.assertEqual(len(resultados), 20)
        self.assertEqual(set(resultados),
                         {f"198.51.100.{i}" for i in range(20)})

    def test_limiter_concurrente_sin_carreras(self):
        # La cubeta por IP con CF confiable: 20 hilos, límite 10.
        self._optin()
        lim = rl.LoginRateLimiter(account_max=100, account_window_s=900,
                                  ip_max=10, ip_window_s=900)
        permitidos = []
        lock = threading.Lock()

        def worker():
            ip = rl.client_origin_ip(
                _FakeHandler(self.PEER, cf_ips=["198.51.100.99"]))
            ok, _, tok = lim.try_acquire(ip=ip, account="u@x.com")
            if ok:
                lim.settle(tok, success=False)
            with lock:
                permitidos.append(ok)

        hilos = [threading.Thread(target=worker) for _ in range(20)]
        for t in hilos:
            t.start()
        for t in hilos:
            t.join()
        self.assertEqual(sum(permitidos), 10)

    # --- La cubeta por cuenta es inmune a la suplantación ---

    def test_cuenta_inmune_a_suplantacion_de_ip(self):
        # Atacante rotando IPs falsificadas: la cuenta se bloquea igual.
        lim = rl.LoginRateLimiter(account_max=3, account_window_s=900,
                                  ip_max=100, ip_window_s=900)
        oks = []
        for i in range(5):
            ok, _, tok = lim.try_acquire(ip=f"10.9.9.{i}",
                                         account="victima@x.com")
            if ok:
                lim.settle(tok, success=False)
            oks.append(ok)
        self.assertEqual(oks, [True, True, True, False, False])


if __name__ == "__main__":
    unittest.main()
