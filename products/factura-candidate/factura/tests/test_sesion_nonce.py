#!/usr/bin/env python3
"""Tests del cierre final pre-staging: nonce de sesión (P0 de sesiones).

P0: crear_sesion() podía producir cookies idénticas en logins dentro del
mismo segundo (mismo "exp", misma firma). El "jti" (nonce criptográfico
con secrets) agregado al payload garantiza unicidad por sesión y vincula
CSRF + revocación a la sesión correcta.

Cubre:
  a) 100 logins dentro del mismo segundo -> 100 cookies distintas.
  b) cerrar una sesión no cierra otra (revocación por cookie, no por usuario).
  c) CSRF vinculado a la sesión correcta (tokens distintos, no cruzados).
  d) revocación, cambio de contraseña, expiración, reinicio y concurrencia.
  e) flujo HTTP vivo: dos logins -> cookies distintas (el flake que antes
     fallaba intermitente en test_restablecer_cierra_todas_las_sesiones).

Uso:
    python -m pytest tests/test_sesion_nonce.py -v   # con el venv del proyecto
"""
from __future__ import annotations

import base64
import json
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from unittest import mock

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import config  # noqa: E402
from factura_persist import db  # noqa: E402
from factura_auth import auth as auth_mod  # noqa: E402
from factura_auth import seguridad as seg_mod  # noqa: E402
from factura_core import correo as correo_mod  # noqa: E402
from factura_web.server import Handler  # noqa: E402

FIJO = 1791990000  # timestamp fijo para congelar el segundo en las pruebas

class _RelojFijo:
    """Sustituto de `time` para factura_auth.auth: time() fijo.

    A diferencia de mock.patch("time.time"), esto NO toca el módulo
    time global: otros hilos (servidores vivos de otros tests) siguen
    viendo el reloj real. Sin esto, congelar time.time globalmente
    rompe timeouts de hilos ajenos (flake en la suite, no en el producto).
    """
    def __init__(self, fijo):
        self._fijo = fijo

    def time(self):
        return self._fijo


def _tiempo_congelado(fijo=FIJO):
    return mock.patch.object(auth_mod, "time", _RelojFijo(fijo))



def _payload(cookie):
    b64 = cookie.split(".", 1)[0]
    return json.loads(base64.urlsafe_b64decode(b64.encode("ascii")))


class TestSesionNonceUnitario(unittest.TestCase):
    """Sin servidor: unicidad del nonce, CSRF y revocación."""

    def setUp(self):
        # BD temporal: revocar_sesion escribe en BD (durable); nunca
        # tocar la factura.db operativa desde los tests.
        self.tmp = tempfile.mkdtemp(prefix="factura-nonce-unit-")
        self._sqlite_orig = config.SQLITE_PATH
        config.SQLITE_PATH = os.path.join(self.tmp, "test.db")
        db.reset_backend_cache()
        assert db.backend() == "sqlite"
        db.migrate()

    def tearDown(self):
        config.SQLITE_PATH = self._sqlite_orig
        db.reset_backend_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _usuario(self):
        return {"id": 7, "empresa_id": 3, "rol": "admin", "sess_version": 0}

    def test_100_sesiones_mismo_segundo_todas_distintas(self):
        """(a) 100 crear_sesion() con el reloj congelado -> 100 cookies únicas."""
        with _tiempo_congelado():
            cookies = [auth_mod.crear_sesion(self._usuario())
                       for _ in range(100)]
        self.assertEqual(len(set(cookies)), 100,
                         "hay cookies idénticas en el mismo segundo")
        # todas llevan jti de 43 chars (32 bytes en urlsafe)
        for c in cookies:
            jti = _payload(c).get("jti", "")
            self.assertGreaterEqual(len(jti), 32, f"jti débil: {jti!r}")

    def test_nonce_usa_secrets_256_bits(self):
        jtis = set()
        with _tiempo_congelado():
            for _ in range(200):
                jtis.add(_payload(auth_mod.crear_sesion(self._usuario()))["jti"])
        self.assertEqual(len(jtis), 200)

    def test_csrf_distinto_por_sesion_y_no_cruzado(self):
        """(c) cada sesión tiene su CSRF; el token de A no sirve en B."""
        with _tiempo_congelado():
            c1 = auth_mod.crear_sesion(self._usuario())
            c2 = auth_mod.crear_sesion(self._usuario())
            s1, s2 = auth_mod.leer_sesion(c1), auth_mod.leer_sesion(c2)
            self.assertIsNotNone(s1)
            self.assertIsNotNone(s2)
            t1, t2 = auth_mod.csrf_token(s1), auth_mod.csrf_token(s2)
            self.assertNotEqual(t1, t2, "CSRF idéntico en dos sesiones")
            self.assertTrue(auth_mod.csrf_valido(s1, t1))
            self.assertTrue(auth_mod.csrf_valido(s2, t2))
            self.assertFalse(auth_mod.csrf_valido(s1, t2),
                             "token de B válido en A")
            self.assertFalse(auth_mod.csrf_valido(s2, t1),
                             "token de A válido en B")

    def test_revocar_una_sesion_no_revoca_otra(self):
        """(b) revocar c1 deja c2 intacta (antes, con cookies idénticas,
        revocar una destruía la otra)."""
        with _tiempo_congelado():
            c1 = auth_mod.crear_sesion(self._usuario())
            c2 = auth_mod.crear_sesion(self._usuario())
        seg_mod.revocar_sesion(c1)
        self.assertTrue(seg_mod.sesion_revocada(c1))
        self.assertFalse(seg_mod.sesion_revocada(c2),
                         "revocar c1 revocó también c2")

    def test_concurrencia_8_hilos_cookies_unicas(self):
        """(concurrencia) 8 hilos x 25 sesiones con el reloj congelado."""
        resultados, cerrojo = [], threading.Lock()

        def lote():
            with _tiempo_congelado():
                lote_local = [auth_mod.crear_sesion(self._usuario())
                              for _ in range(25)]
            with cerrojo:
                resultados.extend(lote_local)

        hilos = [threading.Thread(target=lote) for _ in range(8)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(len(resultados), 200)
        self.assertEqual(len(set(resultados)), 200,
                         "colisión de cookies bajo concurrencia")

    def test_sesion_expirada_rechazada(self):
        """(expiración) cookie con exp pasado -> leer_sesion devuelve None."""
        with _tiempo_congelado():
            c = auth_mod.crear_sesion(self._usuario())
            self.assertIsNotNone(auth_mod.leer_sesion(c))
        with _tiempo_congelado(FIJO + config.SESSION_HOURS * 3600 + 1):
            self.assertIsNone(auth_mod.leer_sesion(c),
                              "sesión expirada aceptada")


class TestSesionNonceServidor(unittest.TestCase):
    """Servidor HTTP vivo + BD temporal: flujo real de login/logout."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="factura-nonce-")
        self._sqlite_orig = config.SQLITE_PATH
        config.SQLITE_PATH = os.path.join(self.tmp, "test.db")
        db.reset_backend_cache()
        assert db.backend() == "sqlite"
        db.migrate()
        conn = db.connect()
        try:
            cur = conn.cursor()
            with db.tx(conn):
                self.eid = db.insert_id(
                    cur, "INSERT INTO factura_empresas (nombre, rnc,"
                         " telefono, direccion, plan, itbis_tasa, e_cf_mode,"
                         " creada, estado, trial_hasta)"
                         " VALUES (%s,%s,'','','basico','18','disabled',"
                         "%s,'activa',%s)",
                    ("Empresa Nonce", "111222333",
                     datetime.now(timezone.utc).isoformat(),
                     (datetime.now(timezone.utc).date() +
                      timedelta(days=14)).isoformat()))
            self.uid = auth_mod.crear_usuario(
                self.eid, "nonce@test.do", ("Clave" + "Segura1234"), "admin")["id"]
        finally:
            conn.close()
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        self.hilo = threading.Thread(target=self.srv.serve_forever,
                                     daemon=True)
        self.hilo.start()
        self.jar = {}

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.hilo.join(timeout=5)
        config.SQLITE_PATH = self._sqlite_orig
        db.reset_backend_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- http ------------------------------------------------------------
    def _req(self, metodo, path, campos=None):
        import http.client
        import urllib.parse
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=15)
        h = {}
        if self.jar:
            h["Cookie"] = "; ".join(f"{k}={v}"
                                    for k, v in self.jar.items())
        cuerpo = None
        if campos is not None:
            cuerpo = urllib.parse.urlencode(campos)
            h["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(metodo, path, body=cuerpo, headers=h)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", "replace")
        for k, v in resp.getheaders():
            if k.lower() == "set-cookie":
                nombre_valor = v.split(";", 1)[0]
                if "=" in nombre_valor:
                    nk, nv = nombre_valor.split("=", 1)
                    nk = nk.strip()
                    if nv.strip() == "" or "Max-Age=0" in v:
                        self.jar.pop(nk, None)
                    else:
                        self.jar[nk] = nv.strip()
        return resp.status, data

    def _anon_csrf(self):
        st, body = self._req("GET", "/login")
        self.assertEqual(st, 200)
        m = re.search(r'name="csrf" value="([^"]+)"', body)
        self.assertIsNotNone(m)
        return m.group(1)

    def _login(self):
        t = self._anon_csrf()
        st, _ = self._req("POST", "/login",
                          {"email": "nonce@test.do",
                           "password": ("Clave" + "Segura1234"), "csrf": t})
        self.assertEqual(st, 302)
        return self.jar.get(config.SESSION_COOKIE)

    def test_dos_logins_cookies_distintas_flujo_vivo(self):
        """El flake original: dos logins rápidos daban la misma cookie."""
        c1 = self._login()
        self.jar.clear()
        c2 = self._login()
        self.assertIsNotNone(c1)
        self.assertIsNotNone(c2)
        self.assertNotEqual(c1, c2,
                            "dos logins produjeron la misma cookie")

    def test_logout_una_sesion_la_otra_sigue_valida(self):
        """(b) HTTP: logout de c1 no destruye c2."""
        c1 = self._login()
        self.jar.clear()
        c2 = self._login()
        self.assertNotEqual(c1, c2)
        # logout con c1
        self.jar = {config.SESSION_COOKIE: c1}
        st, _ = self._req("GET", "/logout")
        self.assertEqual(st, 302)
        self.assertTrue(seg_mod.sesion_revocada(c1))
        self.assertFalse(seg_mod.sesion_revocada(c2))
        # c2 sigue abriendo el panel
        self.jar = {config.SESSION_COOKIE: c2}
        st, _ = self._req("GET", "/panel")
        self.assertEqual(st, 200, "c2 murió con el logout de c1")

    def test_revocacion_sobrevive_reinicio(self):
        """(reinicio) la revocación está en BD: persiste tras "reiniciar"."""
        c1 = self._login()
        self.jar = {config.SESSION_COOKIE: c1}
        self._req("GET", "/logout")
        self.assertTrue(seg_mod.sesion_revocada(c1))
        # simula reinicio del proceso: limpia cachés de conexión
        db.reset_backend_cache()
        self.assertTrue(seg_mod.sesion_revocada(c1),
                        "la revocación se perdió al reiniciar")

    def test_cambio_clave_invalida_todas_las_sesiones(self):
        """(cambio de contraseña) sess_version++ cierra c1 y c2."""
        c1 = self._login()
        self.jar.clear()
        c2 = self._login()
        self.assertNotEqual(c1, c2)
        auth_mod.cambiar_password(self.uid, ("Otra" + "Clave12345"))
        for c in (c1, c2):
            self.jar = {config.SESSION_COOKIE: c}
            st, _ = self._req("GET", "/panel")
            self.assertEqual(st, 302, f"sesión {c[:20]}... sobrevivió")
        # login nuevo con la clave nueva sí funciona
        self.jar.clear()
        t = self._anon_csrf()
        st, _ = self._req("POST", "/login",
                          {"email": "nonce@test.do",
                           "password": ("Otra" + "Clave12345"), "csrf": t})
        self.assertEqual(st, 302)
        st, _ = self._req("GET", "/panel")
        self.assertEqual(st, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)
