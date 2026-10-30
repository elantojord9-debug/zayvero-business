#!/usr/bin/env python3
"""Tests del cierre pre-staging de seguridad (P0 #1, #2, #3).

NO toca los tests existentes. Cubre con pruebas reales (servidor HTTP
vivo + BD temporal):

P0 #1 - Recuperación de contraseñas (/recuperar, /restablecer):
  - respuesta genérica anti-enumeración (idéntica exista o no el correo)
  - el token NUNCA vuelve al HTML ni hay endpoint que lo exponga
  - token como HASH SHA-256 en BD, 30 min, un solo uso
  - rate limit 5/hora por correo y por IP
  - despachador de correo en modo demo -> .demo-bandeja/
P0 #2 - Superadmin:
  - arranque negado en modo cloud con credenciales por defecto/débiles
  - fuerza bruta: 5 intentos por IP/15min, bloqueo en BD + auditoría
  - login ok, y todo POST /superadmin/* exige sesión + CSRF
P0 #3 - Sesiones y HTTPS:
  - cookies HttpOnly + SameSite=Lax siempre; Secure con FACTURA_HTTPS=1
  - TODAS las rutas POST públicas exigen CSRF válido
  - versión de sesión: cambiar la clave invalida sesiones viejas
  - logout revoca del lado servidor; "cerrar todas las sesiones"
  - login: 5 fallidos por correo+IP/15min -> bloqueo temporal genérico

Uso:
    python -m pytest tests/test_seguridad_p0.py -v   # con el venv del proyecto activado
"""
from __future__ import annotations

import http.client
import os
import re
import shutil
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import config  # noqa: E402
from factura_persist import db  # noqa: E402
from factura_auth import auth as auth_mod  # noqa: E402
from factura_auth import seguridad as seg_mod  # noqa: E402
from factura_core import saas  # noqa: E402
from factura_core import correo as correo_mod  # noqa: E402
from factura_web.server import Handler  # noqa: E402


def _ahora():
    return datetime.now(timezone.utc).isoformat()


class ServidorVivo(unittest.TestCase):
    """Levanta el Handler real en un puerto efímero con BD temporal."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="factura-seg-")
        self._sqlite_orig = config.SQLITE_PATH
        config.SQLITE_PATH = os.path.join(self.tmp, "test.db")
        db.reset_backend_cache()
        assert db.backend() == "sqlite"
        db.migrate()
        self._bandeja_antes = set(os.listdir(correo_mod.BANDEJA_DEMO)) \
            if os.path.isdir(correo_mod.BANDEJA_DEMO) else set()
        self.eid = self._empresa()
        self.victima = self._usuario(self.eid, "victima@test.do")
        self.atacante = self._usuario(self.eid, "atacante@test.do")
        self.srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.srv.daemon_threads = True
        self.port = self.srv.server_address[1]
        self.hilo = threading.Thread(target=self.srv.serve_forever,
                                     daemon=True)
        self.hilo.start()
        self.jar = {}  # cookies nombre -> valor

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        self.hilo.join(timeout=5)
        config.SQLITE_PATH = self._sqlite_orig
        db.reset_backend_cache()
        # Limpieza de la bandeja demo: solo lo creado por este test.
        if os.path.isdir(correo_mod.BANDEJA_DEMO):
            for f in os.listdir(correo_mod.BANDEJA_DEMO):
                if f not in self._bandeja_antes:
                    try:
                        os.remove(os.path.join(correo_mod.BANDEJA_DEMO, f))
                    except OSError:
                        pass
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- seed ------------------------------------------------------------
    def _empresa(self, nombre="Empresa Seg", rnc="999888777"):
        conn = db.connect()
        try:
            cur = conn.cursor()
            with db.tx(conn):
                eid = db.insert_id(
                    cur, "INSERT INTO factura_empresas (nombre, rnc,"
                         " telefono, direccion, plan, itbis_tasa, e_cf_mode,"
                         " creada, estado, trial_hasta)"
                         " VALUES (%s,%s,'','','basico','18','disabled',"
                         "%s,'activa',%s)",
                    (nombre, rnc, _ahora(),
                     (datetime.now(timezone.utc).date() +
                      timedelta(days=14)).isoformat()))
            return eid
        finally:
            conn.close()

    def _usuario(self, eid, email, password="ClaveSegura1234"):
        return auth_mod.crear_usuario(eid, email, password, "admin")

    # -- http ------------------------------------------------------------
    def _req(self, metodo, path, campos=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port,
                                          timeout=15)
        h = dict(headers or {})
        if self.jar:
            h["Cookie"] = "; ".join(f"{k}={v}"
                                   for k, v in self.jar.items())
        cuerpo = None
        if campos is not None:
            import urllib.parse
            cuerpo = urllib.parse.urlencode(campos)
            h["Content-Type"] = "application/x-www-form-urlencoded"
        conn.request(metodo, path, body=cuerpo, headers=h)
        resp = conn.getresponse()
        data = resp.read().decode("utf-8", "replace")
        crudas = [v for k, v in resp.getheaders()
                  if k.lower() == "set-cookie"]
        for v in crudas:
            nombre_valor = v.split(";", 1)[0]
            if "=" in nombre_valor:
                nk, nv = nombre_valor.split("=", 1)
                nk = nk.strip()
                if nv.strip() == "" or "Max-Age=0" in v:
                    self.jar.pop(nk, None)
                else:
                    self.jar[nk] = nv.strip()
        return resp.status, dict(resp.getheaders()), data, crudas

    def _get(self, path):
        return self._req("GET", path)

    def _post(self, path, campos):
        return self._req("POST", path, campos)

    def _anon(self, pagina="/recuperar"):
        """GET de página pública -> csrf_token. Guarda la cookie anon."""
        st, _, body, _ = self._get(pagina)
        self.assertEqual(st, 200, pagina)
        m = re.search(r'name="csrf" value="([^"]+)"', body)
        self.assertIsNotNone(m, f"sin campo csrf en {pagina}")
        return m.group(1)

    def _login(self, email, password="ClaveSegura1234"):
        t = self._anon("/login")
        st, _, _, _ = self._post("/login", {"email": email,
                                            "password": password,
                                            "csrf": t})
        self.assertEqual(st, 302, f"login {email}")
        return self.jar.get(config.SESSION_COOKIE)

    def _token_de_bandeja(self):
        """Lee el token crudo del último archivo de la bandeja demo."""
        nuevos = [f for f in os.listdir(correo_mod.BANDEJA_DEMO)
                  if f not in self._bandeja_antes]
        self.assertTrue(nuevos, "la bandeja demo no recibió el correo")
        toks = []
        for f in nuevos:
            with open(os.path.join(correo_mod.BANDEJA_DEMO, f),
                      encoding="utf-8") as fh:
                txt = fh.read()
            m = re.search(r"/restablecer\?token=([A-Za-z0-9_-]+)", txt)
            if m:
                toks.append(m.group(1))
        self.assertTrue(toks, "el correo demo no trae el enlace")
        return toks[-1]


# ======================================================================
# P0 #1 — recuperación de contraseñas
# ======================================================================

class TestRecuperacion(ServidorVivo):

    def test_respuesta_generica_anti_enumeracion(self):
        """MISMA página exista o no el correo; el token nunca sale en HTML."""
        t = self._anon("/recuperar")
        st1, _, body1, _ = self._post(
            "/recuperar", {"email": "victima@test.do", "csrf": t})
        t = self._anon("/recuperar")
        st2, _, body2, _ = self._post(
            "/recuperar", {"email": "nadie-existe@test.do", "csrf": t})
        self.assertEqual(st1, 200)
        self.assertEqual(st2, 200)
        self.assertEqual(body1, body2,
                         "la respuesta difiere según exista el correo")
        self.assertIn("Revisa tu correo", body1)
        self.assertNotIn("token=", body1)
        self.assertNotIn("/restablecer?token", body1)

    def test_atacante_no_puede_derivar_token_de_otro(self):
        """PRUEBA NEGATIVA: el atacante pide reset para la víctima y no
        obtiene ni puede derivar el token por ningún endpoint."""
        t = self._anon("/recuperar")
        st, _, body, _ = self._post(
            "/recuperar", {"email": "victima@test.do", "csrf": t})
        self.assertEqual(st, 200)
        # 1) el HTML no contiene ningún token
        self.assertNotRegex(body, r"token=[A-Za-z0-9_-]{20,}")
        # 2) en BD solo existe el hash: no hay endpoint que exponga el crudo
        conn = db.connect()
        try:
            cur = conn.cursor()
            fila = db.fetchone(
                cur, "SELECT token FROM factura_reset_tokens"
                     " ORDER BY id DESC LIMIT 1")
        finally:
            conn.close()
        self.assertIsNotNone(fila)
        guardado = fila[0]
        self.assertEqual(len(guardado), 64)  # sha256 hex, no el token crudo
        # 3) adivinar falla
        self.assertIsNone(saas.consumir_token_reset("token-inventado"))
        # 4) el token crudo sí funciona para la víctima (vía bandeja demo)
        real = self._token_de_bandeja()
        self.assertNotEqual(real, guardado)
        self.assertEqual(guardado, seg_mod.hash_token(real))

    def test_token_hash_un_solo_uso_y_30_min(self):
        tok = saas.crear_token_reset("victima@test.do")
        self.assertIsNotNone(tok)
        conn = db.connect()
        try:
            cur = conn.cursor()
            fila = db.fetchone(
                cur, "SELECT token, expira_en, creado_en FROM"
                     " factura_reset_tokens ORDER BY id DESC LIMIT 1")
        finally:
            conn.close()
        # guardado como hash, nunca en claro
        self.assertEqual(fila[0], seg_mod.hash_token(tok))
        self.assertNotIn(tok, fila[0])
        # expiración 30 minutos
        creado = datetime.fromisoformat(fila[2])
        expira = datetime.fromisoformat(fila[1])
        self.assertAlmostEqual((expira - creado).total_seconds(), 1800,
                               delta=120)
        # un solo uso
        u = saas.consumir_token_reset(tok)
        self.assertIsNotNone(u)
        self.assertEqual(u["email"], "victima@test.do")
        self.assertIsNone(saas.consumir_token_reset(tok))
        # vencido -> rechazado
        tok2 = saas.crear_token_reset("victima@test.do")
        conn = db.connect()
        try:
            cur = conn.cursor()
            with db.tx(conn):
                db.execute(cur, "UPDATE factura_reset_tokens SET"
                                " expira_en=%s WHERE token=%s",
                           ((datetime.now(timezone.utc) -
                             timedelta(minutes=1)).isoformat(),
                            seg_mod.hash_token(tok2)))
        finally:
            conn.close()
        self.assertIsNone(saas.consumir_token_reset(tok2))

    def test_crear_token_correo_inexistente_no_lanza(self):
        self.assertIsNone(saas.crear_token_reset("fantasma@test.do"))
        self.assertIsNone(saas.crear_token_reset(""))

    def test_rate_limit_recuperar_5_por_hora(self):
        for i in range(5):
            t = self._anon("/recuperar")
            st, _, _, _ = self._post(
                "/recuperar", {"email": f"rl{i}@test.do", "csrf": t})
            self.assertEqual(st, 200, f"intento {i+1}")
        t = self._anon("/recuperar")
        st, _, body, _ = self._post(
            "/recuperar", {"email": "rl5@test.do", "csrf": t})
        self.assertEqual(st, 429)
        # la respuesta limitada sigue siendo genérica (no revela nada)
        self.assertIn("Revisa tu correo", body)
        self.assertNotIn("token=", body)

    def test_flujo_restablecer_completo(self):
        t = self._anon("/recuperar")
        self._post("/recuperar", {"email": "victima@test.do", "csrf": t})
        tok = self._token_de_bandeja()
        t2 = self._anon("/restablecer")
        st, _, _, _ = self._post("/restablecer",
                                 {"token": tok,
                                  "password": "NuevaClave12345",
                                  "csrf": t2})
        self.assertEqual(st, 200)
        # la clave vieja ya no entra; la nueva sí
        t3 = self._anon("/login")
        st, _, _, _ = self._post("/login",
                                 {"email": "victima@test.do",
                                  "password": "ClaveSegura1234",
                                  "csrf": t3})
        self.assertEqual(st, 401)
        self._login("victima@test.do", "NuevaClave12345")


# ======================================================================
# P0 #2 — superadmin
# ======================================================================

class TestSuperadminArranque(unittest.TestCase):
    """validar_superadmin_arranque() en proceso fresco (subprocess)."""

    def _check(self, env):
        import subprocess as sp
        e = dict(os.environ)
        e.update(env)
        codigo = ("import sys; sys.path.insert(0, %r); import config; "
                  "config.validar_superadmin_arranque(); "
                  "print('ARRANQUE-OK')" % BASE)
        r = sp.run([sys.executable, "-c", codigo], capture_output=True,
                   text=True, env=e, cwd=BASE, timeout=60)
        return r.returncode, (r.stdout + r.stderr)

    def test_cloud_sin_credenciales_rechaza(self):
        rc, out = self._check({"FACTURA_ENV": "cloud"})
        self.assertNotEqual(rc, 0)
        self.assertIn("FACTURA_SUPERADMIN", out)

    def test_cloud_con_valores_dev_rechaza(self):
        rc, out = self._check({
            "FACTURA_ENV": "staging",
            "FACTURA_SUPERADMIN_EMAIL": "superadmin@local",
            "FACTURA_SUPERADMIN_PASS": "cambiar-esta-clave"})
        self.assertNotEqual(rc, 0)

    def test_cloud_con_clave_corta_rechaza(self):
        rc, out = self._check({
            "FACTURA_ENV": "production",
            "FACTURA_SUPERADMIN_EMAIL": "jefe@zayvero.do",
            "FACTURA_SUPERADMIN_PASS": "corta123"})
        self.assertNotEqual(rc, 0)
        self.assertIn("16", out)

    def test_cloud_con_credenciales_fuertes_acepta(self):
        rc, out = self._check({
            "FACTURA_ENV": "cloud",
            "FACTURA_SUPERADMIN_EMAIL": "jefe@zayvero.do",
            "FACTURA_SUPERADMIN_PASS": "clave-muy-larga-y-segura-123"})
        self.assertEqual(rc, 0, out)
        self.assertIn("ARRANQUE-OK", out)

    def test_local_con_defaults_acepta(self):
        rc, out = self._check({"FACTURA_ENV": "local"})
        self.assertEqual(rc, 0, out)


class TestSuperadminLogin(ServidorVivo):

    def _super_login(self, password, csrf=None):
        t = csrf or self._anon("/superadmin/login")
        return self._post("/superadmin/login",
                          {"email": "superadmin@local",
                           "password": password, "csrf": t})

    def _super_csrf(self):
        """Deriva el CSRF del panel superadmin desde la cookie (mismo
        cálculo que _super_leer)."""
        import base64
        import json
        crudo = self.jar.get(config.SUPERADMIN_COOKIE)
        self.assertIsNotNone(crudo)
        b64 = crudo.rsplit(".", 1)[0]
        payload = json.loads(
            base64.urlsafe_b64decode(b64.encode("ascii")))
        return auth_mod.csrf_token({"u": 0, "e": 0,
                                    "exp": payload["exp"]})

    def test_login_correcto_abre_panel(self):
        st, hdrs, _, crudas = self._super_login("cambiar-esta-clave")
        self.assertEqual(st, 302)
        self.assertTrue(any(config.SUPERADMIN_COOKIE in c for c in crudas))
        st, _, body, _ = self._get("/superadmin/panel")
        self.assertEqual(st, 200)
        self.assertIn("Superadmin", body)

    def test_fuerza_bruta_bloquea_por_ip(self):
        for _ in range(5):
            st, _, _, _ = self._super_login("clave-mala")
            self.assertEqual(st, 401)
        # 6to intento: bloqueado aunque la clave sea la correcta
        st, _, body, _ = self._super_login("cambiar-esta-clave")
        self.assertEqual(st, 429)
        self.assertIn("Demasiados intentos", body)
        # el bloqueo vive en BD (sobrevive reinicios)
        n = seg_mod.intentos_recientes(seg_mod.T_SUPERADMIN, "127.0.0.1",
                                       seg_mod.SUPERADMIN_VENTANA_MIN)
        self.assertGreaterEqual(n, 5)
        # auditoría del bloqueo y de los fallos
        conn = db.connect()
        try:
            cur = conn.cursor()
            accs = [r[0] for r in db.fetchall(
                cur, "SELECT accion FROM factura_auditoria"
                     " WHERE accion LIKE 'superadmin%'")]
        finally:
            conn.close()
        self.assertIn("superadmin_login_fallido", accs)
        self.assertIn("superadmin_bloqueado", accs)

    def test_post_sin_sesion_redirige_a_login(self):
        st, hdrs, _, _ = self._post("/superadmin/empresa/estado",
                                    {"empresa_id": "1", "estado": "activa",
                                     "csrf": "x"})
        self.assertEqual(st, 302)
        self.assertIn("/superadmin/login", hdrs.get("Location", ""))

    def test_post_con_sesion_pero_csrf_malo_es_403(self):
        self._super_login("cambiar-esta-clave")
        st, _, _, _ = self._post("/superadmin/empresa/estado",
                                 {"empresa_id": str(self.eid),
                                  "estado": "suspendida",
                                  "csrf": "invalido"})
        self.assertEqual(st, 403)

    def test_post_con_sesion_y_csrf_valido_procesa(self):
        self._super_login("cambiar-esta-clave")
        csrf = self._super_csrf()
        st, hdrs, _, _ = self._post("/superadmin/empresa/estado",
                                    {"empresa_id": str(self.eid),
                                     "estado": "suspendida",
                                     "csrf": csrf})
        self.assertEqual(st, 302)
        self.assertIn("/superadmin/panel", hdrs.get("Location", ""))

    def test_logout_superadmin_revoca(self):
        self._super_login("cambiar-esta-clave")
        crudo = self.jar.get(config.SUPERADMIN_COOKIE)
        self._get("/superadmin/logout")
        # la cookie firmada sigue siendo válida criptográficamente...
        self.assertIsNotNone(crudo)
        # ...pero el servidor la revocó
        self.assertTrue(seg_mod.sesion_revocada(crudo))
        st, hdrs, _, _ = self._get("/superadmin/panel")
        self.assertEqual(st, 302)
        self.assertIn("/superadmin/login", hdrs.get("Location", ""))


# ======================================================================
# P0 #3 — sesiones y HTTPS
# ======================================================================

class TestSesionesHTTPS(ServidorVivo):

    def test_cookie_siempre_httponly_y_samesite_lax(self):
        t = self._anon("/login")
        _, _, _, crudas = self._post("/login",
                                     {"email": "victima@test.do",
                                      "password": "ClaveSegura1234",
                                      "csrf": t})
        sess = [c for c in crudas if c.startswith(config.SESSION_COOKIE)]
        self.assertTrue(sess)
        self.assertIn("HttpOnly", sess[0])
        self.assertIn("SameSite=Lax", sess[0])

    def test_secure_solo_con_factura_https(self):
        orig = config.FACTURA_HTTPS
        try:
            config.FACTURA_HTTPS = True
            t = self._anon("/login")
            _, _, _, crudas = self._post(
                "/login", {"email": "victima@test.do",
                           "password": "ClaveSegura1234", "csrf": t})
            sess = [c for c in crudas
                    if c.startswith(config.SESSION_COOKIE)][0]
            self.assertIn("Secure", sess)

            config.FACTURA_HTTPS = False
            self.jar.clear()
            t = self._anon("/login")
            _, _, _, crudas = self._post(
                "/login", {"email": "victima@test.do",
                           "password": "ClaveSegura1234", "csrf": t})
            sess = [c for c in crudas
                    if c.startswith(config.SESSION_COOKIE)][0]
            self.assertNotIn("Secure", sess)
        finally:
            config.FACTURA_HTTPS = orig

    def test_post_publicos_exigen_csrf(self):
        rutas = [("/login", {"email": "a", "password": "b"}),
                 ("/registro", {"nombre": "x"}),
                 ("/recuperar", {"email": "a@b.c"}),
                 ("/restablecer", {"token": "t", "password": "x"}),
                 ("/solicitar-demo", {"nombre": "x"}),
                 ("/superadmin/login",
                  {"email": "a", "password": "b"})]
        for path, campos in rutas:
            st, _, _, _ = self._post(path, campos)  # sin csrf
            self.assertEqual(st, 403, f"{path} aceptó POST sin CSRF")
            st, _, _, _ = self._post(path, dict(campos, csrf="falso"))
            self.assertEqual(st, 403, f"{path} aceptó CSRF falso")

    def test_paginas_publicas_traen_csrf(self):
        for pagina in ["/", "/login", "/registro", "/recuperar",
                       "/restablecer?token=x", "/superadmin/login"]:
            st, _, body, _ = self._get(pagina)
            self.assertEqual(st, 200, pagina)
            if pagina == "/":
                self.assertIn('action="/solicitar-demo"', body)
            self.assertIn('name="csrf"', body, pagina)

    def test_version_sesion_invalida_viejas_tras_cambio_clave(self):
        c1 = self._login("victima@test.do")
        st, _, _, _ = self._get("/panel")
        self.assertEqual(st, 200)
        # cambio de contraseña -> sess_version++
        auth_mod.cambiar_password(self.victima["id"], "OtraClave12345")
        # la cookie vieja (firma válida) ya no abre nada
        st, _, _, _ = self._get("/panel")
        self.assertEqual(st, 302)
        # y a nivel de auth la versión ya no coincide
        payload = auth_mod.leer_sesion(c1)
        self.assertIsNotNone(payload)  # firma/expiración intactas
        self.assertFalse(auth_mod.version_sesion_ok(payload))
        # login nuevo sí funciona
        self._login("victima@test.do", "OtraClave12345")
        st, _, _, _ = self._get("/panel")
        self.assertEqual(st, 200)

    def test_logout_revoca_lado_servidor(self):
        c1 = self._login("victima@test.do")
        st, _, _, _ = self._get("/panel")
        self.assertEqual(st, 200)
        self._get("/logout")
        # la firma de la cookie sigue siendo válida...
        self.assertIsNotNone(auth_mod.leer_sesion(c1))
        # ...pero el servidor la destruyó
        self.assertTrue(seg_mod.sesion_revocada(c1))
        st, hdrs, _, _ = self._get("/panel")
        self.assertEqual(st, 302)
        self.assertIn("/login", hdrs.get("Location", ""))

    def test_restablecer_cierra_todas_las_sesiones(self):
        c1 = self._login("victima@test.do")
        self.jar.clear()
        c2 = self._login("victima@test.do")
        self.assertNotEqual(c1, c2)
        # flujo de restablecimiento
        self.jar.clear()
        t = self._anon("/recuperar")
        self._post("/recuperar", {"email": "victima@test.do", "csrf": t})
        tok = self._token_de_bandeja()
        t2 = self._anon("/restablecer")
        st, _, _, _ = self._post("/restablecer",
                                 {"token": tok,
                                  "password": "ClaveNueva12345",
                                  "csrf": t2})
        self.assertEqual(st, 200)
        # ambas sesiones viejas muertas
        for c in (c1, c2):
            self.jar[config.SESSION_COOKIE] = c
            st, _, _, _ = self._get("/panel")
            self.assertEqual(st, 302, "sesión vieja sigue viva")
        self.jar.clear()

    def test_login_fuerza_bruta_bloquea_correo_mas_ip(self):
        for _ in range(5):
            t = self._anon("/login")
            st, _, body, _ = self._post(
                "/login", {"email": "victima@test.do",
                           "password": "clave-mala-123", "csrf": t})
            self.assertEqual(st, 401)
            self.assertIn("Credenciales inválidas", body)
        # 6to: bloqueado con mensaje genérico (no revela si existe)
        t = self._anon("/login")
        st, _, body, _ = self._post(
            "/login", {"email": "victima@test.do",
                       "password": "ClaveSegura1234", "csrf": t})
        self.assertEqual(st, 429)
        self.assertIn("Demasiados intentos", body)
        self.assertNotIn("no existe", body.lower())
        # otro correo desde la misma IP NO está bloqueado
        t = self._anon("/login")
        st, _, _, _ = self._post(
            "/login", {"email": "atacante@test.do",
                       "password": "ClaveSegura1234", "csrf": t})
        self.assertEqual(st, 302)
        # auditoría
        conn = db.connect()
        try:
            cur = conn.cursor()
            accs = [r[0] for r in db.fetchall(
                cur, "SELECT accion FROM factura_auditoria"
                     " WHERE accion IN ('login_fallido','login_bloqueado')")]
        finally:
            conn.close()
        self.assertEqual(accs.count("login_fallido"), 5)
        self.assertIn("login_bloqueado", accs)


class TestUnidadesSeguridad(unittest.TestCase):
    """Unidades puras (sin HTTP)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="factura-seg-u-")
        self._o = config.SQLITE_PATH
        config.SQLITE_PATH = os.path.join(self.tmp, "t.db")
        db.reset_backend_cache()
        db.migrate()

    def tearDown(self):
        config.SQLITE_PATH = self._o
        db.reset_backend_cache()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_hash_token_formato(self):
        h = seg_mod.hash_token("token-crudo-123")
        self.assertEqual(len(h), 64)
        self.assertRegex(h, r"^[0-9a-f]{64}$")
        self.assertNotIn("token-crudo-123", h)

    def test_csrf_anon_roundtrip(self):
        token, firmado = auth_mod.csrf_anon_nuevo()
        self.assertTrue(auth_mod.csrf_anon_valido(firmado, token))
        self.assertFalse(auth_mod.csrf_anon_valido(firmado, "otro"))
        self.assertFalse(auth_mod.csrf_anon_valido(firmado + "x", token))
        self.assertFalse(auth_mod.csrf_anon_valido(None, token))
        self.assertFalse(auth_mod.csrf_anon_valido(firmado, None))

    def test_revocacion_roundtrip(self):
        self.assertFalse(seg_mod.sesion_revocada("cookie-x"))
        seg_mod.revocar_sesion("cookie-x")
        self.assertTrue(seg_mod.sesion_revocada("cookie-x"))
        self.assertGreater(seg_mod.purgar_revocadas(), -1)

    def test_rate_limit_unidades(self):
        for _ in range(3):
            seg_mod.registrar_intento("prueba", "clave1", "1.2.3.4")
        self.assertFalse(seg_mod.bloqueado("prueba", "clave1", 5, 60))
        for _ in range(2):
            seg_mod.registrar_intento("prueba", "clave1", "1.2.3.4")
        self.assertTrue(seg_mod.bloqueado("prueba", "clave1", 5, 60))
        self.assertFalse(seg_mod.bloqueado("prueba", "otra-clave", 5, 60))


# ======================================================================
# P0 — anti Host-header injection en el enlace de recuperación
# ======================================================================
# El endpoint /recuperar construía el enlace con el encabezado Host de la
# petición, que el atacante puede falsificar para redirigir el token de
# restablecimiento a su dominio. El origen ahora es fijo por configuración
# (FACTURA_PUBLIC_ORIGIN; defecto "http://localhost" en local) y en modo
# cloud es obligatorio con https://; si falta o es inválido el arranque se
# niega. El encabezado Host NUNCA se usa.


class TestOrigenPublicoAntiHost(ServidorVivo):
    """Pruebas con servidor vivo: un Host malicioso no contamina el enlace."""

    def setUp(self):
        super().setUp()
        self._env_orig = dict(os.environ)
        os.environ["FACTURA_PUBLIC_ORIGIN"] = "http://localhost:8801"
        self.addCleanup(self._restaurar_env)

    def _restaurar_env(self):
        os.environ.clear()
        os.environ.update(self._env_orig)

    def _cuerpo_correo(self):
        nuevos = [f for f in os.listdir(correo_mod.BANDEJA_DEMO)
                  if f not in self._bandeja_antes]
        self.assertTrue(nuevos, "la bandeja demo no recibió el correo")
        ruta = os.path.join(correo_mod.BANDEJA_DEMO, nuevos[-1])
        with open(ruta, encoding="utf-8") as fh:
            return fh.read()

    def _solicitar_recuperar(self, host_cabecera):
        t = self._anon("/recuperar")
        st, _, _, _ = self._req(
            "POST", "/recuperar",
            {"email": "victima@test.do", "csrf": t},
            headers={"Host": host_cabecera})
        self.assertEqual(st, 200, f"POST /recuperar con Host {host_cabecera}")
        return self._cuerpo_correo()

    def _assert_enlace_fijo(self, cuerpo):
        m = re.search(
            r"https?://[^\s\"']+/restablecer\?token=[A-Za-z0-9_-]+", cuerpo)
        self.assertIsNotNone(m, "el correo no trae el enlace de "
                                "restablecimiento")
        enlace = m.group(0)
        self.assertTrue(
            enlace.startswith("http://localhost:8801/restablecer?token="),
            f"el enlace no usa el origen fijo configurado: {enlace}")
        return enlace

    def test_host_malicioso_no_aparece_en_enlace(self):
        """Host: evil.com -> el enlace NO contiene evil.com."""
        cuerpo = self._solicitar_recuperar("evil.com")
        self.assertNotIn("evil.com", cuerpo)
        self._assert_enlace_fijo(cuerpo)

    def test_host_con_puerto_raro_no_aparece_en_enlace(self):
        """Host: dominio-atacante con puerto -> el enlace usa el origen fijo."""
        cuerpo = self._solicitar_recuperar("atacante.evil.com:99999")
        self.assertNotIn("evil.com", cuerpo)
        self.assertNotIn("99999", cuerpo)
        self._assert_enlace_fijo(cuerpo)

    def test_host_localhost_spoof_no_aparece_en_enlace(self):
        """Host: localhost.evil.com -> ni el subdominio atacante contamina."""
        cuerpo = self._solicitar_recuperar("localhost.evil.com")
        self.assertNotIn("evil.com", cuerpo)
        self._assert_enlace_fijo(cuerpo)


class TestValidacionOrigenPublico(unittest.TestCase):
    """Validación de FACTURA_PUBLIC_ORIGIN y arranque en modo cloud."""

    def setUp(self):
        self._env_orig = dict(os.environ)
        self.addCleanup(self._restaurar_env)

    def _restaurar_env(self):
        os.environ.clear()
        os.environ.update(self._env_orig)

    def _cloud(self, origen):
        os.environ["FACTURA_ENV"] = "cloud"
        os.environ.pop("FACTURA_PUBLIC_ORIGIN", None)
        if origen is not None:
            os.environ["FACTURA_PUBLIC_ORIGIN"] = origen

    def _local(self, origen):
        os.environ.pop("FACTURA_ENV", None)  # local por defecto
        os.environ.pop("FACTURA_PUBLIC_ORIGIN", None)
        if origen is not None:
            os.environ["FACTURA_PUBLIC_ORIGIN"] = origen

    def test_cloud_sin_origen_niega_arranque(self):
        """Cloud sin FACTURA_PUBLIC_ORIGIN -> RuntimeError al arrancar."""
        self._cloud(None)
        with self.assertRaises(RuntimeError) as ctx:
            config.validar_origen_publico_arranque()
        self.assertIn("FACTURA_PUBLIC_ORIGIN", str(ctx.exception))
        with self.assertRaises(RuntimeError):
            config.origen_publico()

    def test_cloud_origen_http_niega_arranque(self):
        """Cloud con http:// (sin TLS) -> RuntimeError al arrancar."""
        self._cloud("http://factura.ejemplo.com")
        with self.assertRaises(RuntimeError) as ctx:
            config.validar_origen_publico_arranque()
        self.assertIn("https", str(ctx.exception))

    def test_cloud_origen_https_valido_arranca(self):
        """Cloud con https válido -> el arranque pasa y el origen se usa."""
        self._cloud("https://factura.ejemplo.com")
        config.validar_origen_publico_arranque()  # no lanza
        self.assertEqual(config.origen_publico(),
                         "https://factura.ejemplo.com")

    def test_cloud_origen_https_con_puerto_valido(self):
        self._cloud("https://factura.ejemplo.com:8443/")
        config.validar_origen_publico_arranque()  # no lanza ("/" final ok)
        self.assertEqual(config.origen_publico(),
                         "https://factura.ejemplo.com:8443")

    def test_cloud_origenes_invalidos_niegan_arranque(self):
        """Path, credenciales, query, caracteres raros, puerto malo, etc."""
        malos = [
            "https://factura.ejemplo.com/reset",   # path
            "https://user:pass@factura.ejemplo.com",  # credenciales
            "https://factura.ejemplo.com/?x=1",    # query
            "https://factura.ejemplo.com/#f",      # fragmento
            "https://factura .ejemplo.com",        # espacio en host
            "https://factura.ejemplo.com:99999",   # puerto fuera de rango
            "https://factura.ejemplo.com:abc",     # puerto no numérico
            "javascript:alert(1)",                 # esquema raro
            "factura.ejemplo.com",                 # sin esquema
            "https://",                           # sin host
            "https://<script>",                    # caracteres raros
            "https://evil.com@factura.ejemplo.com",  # userinfo sin pass
        ]
        for malo in malos:
            with self.subTest(origen=malo):
                self._cloud(malo)
                with self.assertRaises(RuntimeError):
                    config.validar_origen_publico_arranque()

    def test_local_sin_origen_usa_localhost(self):
        """En local sin la variable el arranque pasa y usa localhost."""
        self._local(None)
        config.validar_origen_publico_arranque()  # no lanza
        self.assertEqual(config.origen_publico(), "http://localhost")

    def test_local_origen_configurado_se_respeta(self):
        """En local se permite http y puerto configurado."""
        self._local("http://localhost:8801")
        self.assertEqual(config.origen_publico(), "http://localhost:8801")

    def test_local_origen_invalido_falla(self):
        """En local un valor inválido también se rechaza (fail fast)."""
        self._local("https://evil.com/reset")
        with self.assertRaises(RuntimeError):
            config.validar_origen_publico_arranque()
