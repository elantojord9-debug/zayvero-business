#!/usr/bin/env python3
"""Suite crítica contra PostgreSQL REAL — TALENTO (P0 #7, cierre pre-staging).

====================================================================
POSTGRESQL: esta es la suite contra PostgreSQL verdadero (no SQLite).
La contraparte SQLite vive en test_factura*.py. El reporte final DEBE
distinguir: "SQLite: X/X" vs "PostgreSQL: Y/Y".
====================================================================

Reproduce los casos críticos del motor contra un PostgreSQL 16 real:

 1. Autenticación: registro de empresa, login válido/inválido, hash de
    contraseña (nunca en claro en la BD).
 2. Permisos: un cajero NO puede hacer lo de admin — a nivel de motor
    (matriz config.tiene_permiso) y a nivel HTTP (403 / sin efecto).
 3. Aislamiento multiempresa: dos empresas no ven ni tocan los datos de
    la otra (clientes, facturas, stock, secuencias).
 4. Ventas: factura con ITBIS exacto; numeración sin duplicados bajo
    concurrencia (hilos paralelos).
 5. Stock: vender descuenta inventario; no permite vender más del
    disponible (incluye prueba de condición de carrera con hilos).
 6. Pagos: abono parcial a CxC; cuadre de caja (apertura/movimientos/
    cierre con diferencia 0).
 7. e-CF simulado: borrador -> validar -> aprobar -> emitir en modo
    mock, con el aviso de SIMULACIÓN presente y cadena de hash íntegra.
 8. Concurrencia mixta: escrituras paralelas no pierden datos ni
    duplican secuencias.
 9. Migraciones: 001..009 (+ las nuevas que existan) aplicadas desde
    cero en esquema vacío; re-correr es idempotente.
10. Restauración: pg_dump del esquema de prueba, restore en esquema
    nuevo, conteos por tabla idénticos.

Aislamiento: cada prueba crea su PROPIO esquema (`pgtest_<pid>_<n>`)
dentro de la BD operativa y lo borra (CASCADE) al terminar. El DSN se
lee de ~/.pg_dsn_factura_app (0600) de forma transitoria: nunca se
imprime ni se guarda en archivos. Datos 100% sintéticos.

Si PostgreSQL no está disponible, cada prueba se OMITE con skip
(no falla).

Las migraciones se aplican explícitamente con db.migrate() en el
setUp (tolera que el arranque del servidor las aplique o no).

Uso:
    python -m pytest tests/test_pg_real.py -v   # con el venv del proyecto activado
"""
from __future__ import annotations

import base64
import http.client
import itertools
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.parse
from datetime import datetime, timezone
from decimal import Decimal
from urllib.parse import quote, urlsplit, urlunsplit

# pg_dump del PATH (portable); si no está instalado, el test de restore
# falla con un mensaje claro en vez de una ruta fija inexistente.
_PG_DUMP = shutil.which("pg_dump") or "pg_dump"

# --- entorno ANTES de importar config -------------------------------------
# Secreto de sesión fijo: evita que auth escriba .factura_secret en el repo.
os.environ.setdefault("FACTURA_SECRET", "secreto-solo-pruebas-pg")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

# Ruta a los motores reutilizados del Business OS (solo lectura):
# convención de directorio hermano del proyecto; sobreescribible con
# FACTURA_BIZ_PATH. Va antes de importar config.
os.environ.setdefault("FACTURA_BIZ_PATH",
                      os.path.join(os.path.dirname(BASE), "zayvero-business"))

import config  # noqa: E402
from factura_persist import db  # noqa: E402
from factura_auth import auth as auth_mod  # noqa: E402
from factura_auth import registro as registro_mod  # noqa: E402
from factura_core import clientes, inventario, ventas, pagos, caja  # noqa: E402
from factura_ecf import service as ecf  # noqa: E402

_CLAVE = "Clave-Secreta-PG-12345"  # sintética, solo para pruebas (>=12 chars)

# --- DSN (transitorio, jamás se imprime ni se guarda) ------------------------
_DSN_PATH = os.path.expanduser("~/.pg_dsn_factura_app")


def _leer_dsn() -> str | None:
    if os.environ.get("FACTURA_TEST_ALLOW_DDL") != "1":
        return None
    return os.environ.get("FACTURA_TEST_DATABASE_URL") or None



_DSN_RAW = _leer_dsn()


def _pg_disponible() -> tuple[bool, str]:
    if not _DSN_RAW:
        return False, f"sin DSN en {_DSN_PATH}"
    try:
        import psycopg
        conn = psycopg.connect(_DSN_RAW, connect_timeout=5)
        conn.close()
        return True, ""
    except Exception as exc:  # noqa: BLE001 - solo para el motivo del skip
        return False, type(exc).__name__


_PG_OK, _PG_MOTIVO = _pg_disponible()


def _dsn_esquema(esquema: str) -> str:
    """DSN con search_path fijo al esquema de la prueba (toda conexión del
    motor —incluidas las que abre el código de la app— cae en ese esquema)."""
    p = urlsplit(_DSN_RAW)
    q = ((p.query + "&") if p.query else "") + "options=" + quote(
        f"-c search_path={esquema}", safe="")
    return urlunsplit((p.scheme, p.netloc, p.path, q, p.fragment))


def _admin_ddl(sql: str) -> None:
    """DDL de esquemas con el DSN crudo (sin search_path), autocommit."""
    import psycopg
    conn = psycopg.connect(_DSN_RAW, autocommit=True)
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
    finally:
        conn.close()


def _env_pg_dump() -> dict:
    """Variables PG* para pg_dump/psql (credencial transitoria en entorno,
    nunca impresa ni escrita a disco)."""
    p = urlsplit(_DSN_RAW)
    env = dict(os.environ)
    env["PGHOST"] = p.hostname or "127.0.0.1"
    env["PGPORT"] = str(p.port or 5432)
    env["PGUSER"] = p.username or ""
    env["PGPASSWORD"] = p.password or ""
    env["PGDATABASE"] = (p.path or "/").lstrip("/") or "postgres"
    return env


_PID = os.getpid()
_SEQ = itertools.count(1)
_SEQ_LOCK = threading.Lock()


def _nuevo_esquema() -> str:
    with _SEQ_LOCK:
        n = next(_SEQ)
    return f"pgtest_{_PID}_{n}"


class PGBase(unittest.TestCase):
    """Base PostgreSQL: esquema propio por prueba, migrado desde cero."""

    def setUp(self):
        if not _PG_OK:
            self.skipTest(f"PostgreSQL no disponible ({_PG_MOTIVO})")
        self.esquema = _nuevo_esquema()
        _admin_ddl(f'CREATE SCHEMA "{self.esquema}"')
        # Migraciones aplicadas EXPLÍCITAMENTE (el arranque del servidor
        # puede o no aplicarlas; la suite no depende de ese comportamiento).
        os.environ["FACTURA_DATABASE_URL"] = _dsn_esquema(self.esquema)
        db.reset_backend_cache()
        self.assertEqual(db.backend(), "pg",
                         "el backend no es PostgreSQL real")
        self.migradas = db.migrate()
        self._n = 0

    def tearDown(self):
        try:
            _admin_ddl(f'DROP SCHEMA IF EXISTS "{self.esquema}" CASCADE')
        finally:
            os.environ.pop("FACTURA_DATABASE_URL", None)
            db.reset_backend_cache()

    # -- fábricas de datos 100% sintéticos ----------------------------------
    def _email(self, pref="pg"):
        self._n += 1
        return f"{pref}{_PID}_{self._n}@test.local"

    def _rnc(self):
        self._n += 1
        return f"{200000000 + self._n:09d}"

    def _empresa(self, nombre=None, modo_ecf="disabled"):
        """Registra empresa + admin. Devuelve (empresa_id, admin_id, email)."""
        self._n += 1
        email = self._email("adm")
        r = registro_mod.registrar_empresa(
            nombre or f"PG Test {_PID}-{self._n} SA",
            self._rnc(), "", "", email, _CLAVE)
        if modo_ecf != "disabled":
            conn = db.connect()
            try:
                cur = conn.cursor()
                with db.tx(conn):
                    db.execute(cur, "UPDATE factura_empresas SET e_cf_mode=%s"
                                    " WHERE id=%s",
                               (modo_ecf, r["empresa_id"]))
            finally:
                conn.close()
        return r["empresa_id"], r["usuario"]["id"], email

    def _cajero(self, empresa_id):
        email = self._email("caj")
        u = auth_mod.crear_usuario(empresa_id, email, _CLAVE, "cajero")
        return u["id"], email

    def _producto(self, empresa_id, stock=100, precio=1000, sku=None):
        self._n += 1
        return inventario.crear(
            empresa_id, sku or f"PG-SKU-{_PID}-{self._n}",
            f"Producto PG {self._n}", precio, 0, False, stock)

    def _venta(self, empresa_id, producto, cantidad=1, creada_por=None,
               cliente_id=None, tipo="factura"):
        return ventas.crear_venta(
            empresa_id, tipo, cliente_id,
            [{"producto_id": producto["id"], "nombre": producto["nombre"],
              "cantidad": cantidad, "precio": producto["precio"]}],
            creada_por=creada_por)

    def _conteo_tablas(self, esquema=None):
        """{tabla: filas} del esquema de la prueba (o del indicado)."""
        conn = db.connect()
        try:
            cur = conn.cursor()
            if esquema is None:
                filas = db.fetchall(
                    cur, "SELECT tablename FROM pg_tables"
                         " WHERE schemaname=current_schema() ORDER BY 1")
                califica = lambda t: f'"{t}"'  # noqa: E731
            else:
                filas = db.fetchall(
                    cur, "SELECT tablename FROM pg_tables"
                         " WHERE schemaname=%s ORDER BY 1", (esquema,))
                califica = lambda t: f'"{esquema}"."{t}"'  # noqa: E731
            return {t[0]: db.fetchall(
                cur, f"SELECT count(*) FROM {califica(t[0])}")[0][0]
                for t in filas}
        finally:
            conn.close()


# ============================================================================
# 1. AUTENTICACIÓN (PostgreSQL)
# ============================================================================
class TestPGAuth(PGBase):

    def test_pg_auth_registro_login_hash(self):
        """PG-AUTH: registro de empresa, login válido/inválido y hash."""
        eid, admin_id, email = self._empresa()

        # login válido
        u = auth_mod.autenticar(email, _CLAVE)
        self.assertIsNotNone(u)
        self.assertEqual(u["id"], admin_id)
        self.assertEqual(u["rol"], "admin")

        # login inválido: clave errónea / email inexistente
        self.assertIsNone(auth_mod.autenticar(email, "clave-erronea-larga-000"))
        self.assertIsNone(auth_mod.autenticar("nadie@test.local", _CLAVE))
        self.assertIsNone(auth_mod.autenticar("", ""))

        # el hash en la BD nunca es la clave en claro
        conn = db.connect()
        try:
            cur = conn.cursor()
            fila = db.fetchone(cur, "SELECT hash FROM factura_usuarios"
                                    " WHERE id=%s", (admin_id,))
        finally:
            conn.close()
        h = fila[0]
        self.assertNotIn(_CLAVE, h)
        self.assertNotEqual(h, _CLAVE)
        from tenant.crypto import verify_password
        self.assertTrue(verify_password(_CLAVE, h))

        # un cajero también se autentica con su rol
        caj_id, caj_email = self._cajero(eid)
        uc = auth_mod.autenticar(caj_email, _CLAVE)
        self.assertIsNotNone(uc)
        self.assertEqual(uc["rol"], "cajero")
        self.assertEqual(uc["id"], caj_id)


# ============================================================================
# 2. PERMISOS (PostgreSQL) — motor y HTTP
# ============================================================================
class TestPGPermisos(PGBase):

    def test_pg_permisos_matriz_motor(self):
        """PG-PERMISOS (motor): la matriz niega al cajero lo de admin."""
        # admin: todo
        for permiso in ("anular_ventas", "config", "usuarios", "reportes",
                        "caja", "ecf_gestion", "importar"):
            self.assertTrue(config.tiene_permiso("admin", permiso),
                            f"admin debería tener {permiso}")
        # cajero: vende y usa POS/caja, pero NO anula ni ve config/usuarios
        self.assertFalse(config.tiene_permiso("cajero", "anular_ventas"))
        self.assertFalse(config.tiene_permiso("cajero", "config"))
        self.assertFalse(config.tiene_permiso("cajero", "usuarios"))
        self.assertTrue(config.tiene_permiso("cajero", "crear_ventas"))
        self.assertTrue(config.tiene_permiso("cajero", "pos"))
        self.assertTrue(config.tiene_permiso("cajero", "caja"))
        # contador: reportes sí, caja/config no
        self.assertTrue(config.tiene_permiso("contador", "reportes"))
        self.assertFalse(config.tiene_permiso("contador", "caja"))
        self.assertFalse(config.tiene_permiso("contador", "config"))
        # rol desconocido: nada
        self.assertFalse(config.tiene_permiso("invitado", "panel"))

    # -- HTTP ----------------------------------------------------------------
    @staticmethod
    def _servidor():
        from http.server import ThreadingHTTPServer
        from factura_web.server import Handler
        srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        hilo = threading.Thread(target=srv.serve_forever,
                                kwargs={"poll_interval": 0.05}, daemon=True)
        hilo.start()
        return srv

    @staticmethod
    def _anon_csrf(puerto):
        """GET /login -> (cookie_anon, token) del CSRF pre-sesión (P0 #3)."""
        cn = http.client.HTTPConnection("127.0.0.1", puerto, timeout=15)
        cn.request("GET", "/login")
        resp = cn.getresponse()
        html = resp.read().decode("utf-8", "replace")
        anon = None
        for h, v in resp.getheaders():
            if h.lower() == "set-cookie" and v.startswith(
                    config.ANON_CSRF_COOKIE + "="):
                anon = v.split(";", 1)[0].strip()
        cn.close()
        m = re.search(r'name="csrf" value="([^"]+)"', html)
        assert resp.status == 200 and anon and m, \
            "no se obtuvo el CSRF anónimo de /login"
        return anon, m.group(1)

    @classmethod
    def _post_login(cls, puerto, email, password):
        anon, token = cls._anon_csrf(puerto)
        cuerpo = urllib.parse.urlencode(
            {"email": email, "password": password, "csrf": token})
        cn = http.client.HTTPConnection("127.0.0.1", puerto, timeout=15)
        cn.request("POST", "/login", cuerpo,
                   {"Content-Type": "application/x-www-form-urlencoded",
                    "Cookie": anon})
        resp = cn.getresponse()
        resp.read()
        cookie = None
        for h, v in resp.getheaders():
            if h.lower() == "set-cookie" and v.startswith(
                    config.SESSION_COOKIE + "="):
                cookie = v.split(";", 1)[0].strip()
        cn.close()
        return resp.status, cookie

    @staticmethod
    def _get(puerto, ruta, cookie):
        cn = http.client.HTTPConnection("127.0.0.1", puerto, timeout=15)
        cn.request("GET", ruta, headers={"Cookie": cookie})
        resp = cn.getresponse()
        cuerpo = resp.read()
        cn.close()
        return resp.status, cuerpo

    @staticmethod
    def _post(puerto, ruta, cookie, form):
        cuerpo = urllib.parse.urlencode(form)
        cn = http.client.HTTPConnection("127.0.0.1", puerto, timeout=15)
        cn.request("POST", ruta, cuerpo,
                   {"Content-Type": "application/x-www-form-urlencoded",
                    "Cookie": cookie})
        resp = cn.getresponse()
        data = resp.read()
        cn.close()
        return resp.status, data

    @staticmethod
    def _csrf_de_cookie(cookie):
        valor = cookie.split("=", 1)[1]
        b64 = valor.split(".")[0]
        b64 += "=" * (-len(b64) % 4)
        ses = json.loads(base64.urlsafe_b64decode(b64))
        # la derivación incluye el "jti" (nonce de sesión); se pasa
        # el payload completo para que el token coincida con el servidor
        return auth_mod.csrf_token(ses)

    def test_pg_permisos_http_cajero_vs_admin(self):
        """PG-PERMISOS (HTTP): cajero recibe 403 en /config y NO puede
        anular; admin sí."""
        eid, admin_id, admin_email = self._empresa()
        _, caj_email = self._cajero(eid)
        prod = self._producto(eid, stock=10)
        v = self._venta(eid, prod, creada_por=admin_id)

        srv = self._servidor()
        try:
            puerto = srv.server_address[1]
            st, cook_admin = self._post_login(puerto, admin_email, _CLAVE)
            self.assertEqual(st, 302)
            self.assertIsNotNone(cook_admin)
            st, cook_caj = self._post_login(puerto, caj_email, _CLAVE)
            self.assertEqual(st, 302)
            self.assertIsNotNone(cook_caj)
            # login con clave mala -> 401, sin cookie
            st, cook_mala = self._post_login(
                puerto, admin_email, "clave-erronea-larga-000")
            self.assertEqual(st, 401)
            self.assertIsNone(cook_mala)

            # GET /config: cajero 403, admin 200
            st, _ = self._get(puerto, "/config", cook_caj)
            self.assertEqual(st, 403)
            st, _ = self._get(puerto, "/config", cook_admin)
            self.assertEqual(st, 200)

            # POST anular como cajero: redirige pero NO anula
            csrf_caj = self._csrf_de_cookie(cook_caj)
            st, _ = self._post(puerto, f"/facturas/{v['id']}/anular",
                               cook_caj, {"csrf": csrf_caj})
            self.assertEqual(st, 302)
            self.assertNotEqual(
                ventas.obtener(eid, v["id"])["estado"], "anulada")

            # POST anular como admin: sí anula
            csrf_adm = self._csrf_de_cookie(cook_admin)
            st, _ = self._post(puerto, f"/facturas/{v['id']}/anular",
                               cook_admin, {"csrf": csrf_adm})
            self.assertEqual(st, 302)
            self.assertEqual(
                ventas.obtener(eid, v["id"])["estado"], "anulada")
        finally:
            srv.shutdown()
            srv.server_close()


# ============================================================================
# 3. AISLAMIENTO MULTIEMPRESA (PostgreSQL)
# ============================================================================
class TestPGAislamiento(PGBase):

    def test_pg_aislamiento_multiempresa(self):
        """PG-AISLAMIENTO: A y B no ven ni tocan los datos de la otra."""
        eid_a, uid_a, _ = self._empresa(nombre="PG Alfa SA")
        eid_b, uid_b, _ = self._empresa(nombre="PG Beta SA")

        cli_a = clientes.crear(eid_a, "Cliente Alfa", rnc=self._rnc())
        cli_b = clientes.crear(eid_b, "Cliente Beta", rnc=self._rnc())
        prod_a = self._producto(eid_a, stock=10)
        prod_b = self._producto(eid_b, stock=10)
        va = self._venta(eid_a, prod_a, cantidad=2, creada_por=uid_a,
                         cliente_id=cli_a["id"])
        vb = self._venta(eid_b, prod_b, cantidad=3, creada_por=uid_b,
                         cliente_id=cli_b["id"])

        # clientes: cada empresa solo los suyos
        self.assertEqual([c["id"] for c in clientes.listar(eid_a)],
                         [cli_a["id"]])
        self.assertEqual([c["id"] for c in clientes.listar(eid_b)],
                         [cli_b["id"]])
        self.assertIsNone(clientes.obtener(eid_a, cli_b["id"]))
        self.assertIsNone(clientes.obtener(eid_b, cli_a["id"]))

        # facturas: obtener/listar filtran por empresa
        self.assertIsNone(ventas.obtener(eid_a, vb["id"]))
        self.assertIsNone(ventas.obtener(eid_b, va["id"]))
        self.assertEqual([x["numero"] for x in ventas.listar(eid_a)],
                         [va["numero"]])
        self.assertEqual([x["numero"] for x in ventas.listar(eid_b)],
                         [vb["numero"]])

        # stock: movimientos de A no tocan a B
        self.assertEqual(
            Decimal(str(inventario.obtener(eid_a, prod_a["id"])["stock"])),
            Decimal("8"))
        self.assertEqual(
            Decimal(str(inventario.obtener(eid_b, prod_b["id"])["stock"])),
            Decimal("7"))
        self.assertIsNone(inventario.obtener(eid_a, prod_b["id"]))

        # secuencias independientes por empresa
        self.assertEqual(va["numero"], "F-000001")
        self.assertEqual(vb["numero"], "F-000001")


# ============================================================================
# 4. VENTAS (PostgreSQL) — ITBIS y numeración concurrente
# ============================================================================
class TestPGVentas(PGBase):

    def test_pg_venta_itbis_y_numeracion(self):
        """PG-VENTAS: ITBIS 18% exacto con Decimal y numeración F-00000N."""
        eid, uid, _ = self._empresa()
        prod = self._producto(eid, stock=50, precio=1000)

        v1 = self._venta(eid, prod, cantidad=1, creada_por=uid)
        self.assertEqual(v1["numero"], "F-000001")
        self.assertEqual(Decimal(v1["subtotal"]), Decimal("1000"))
        self.assertEqual(Decimal(v1["itbis"]), Decimal("180"))
        self.assertEqual(Decimal(v1["total"]), Decimal("1180"))

        v2 = self._venta(eid, prod, cantidad=2, creada_por=uid)
        self.assertEqual(v2["numero"], "F-000002")
        self.assertEqual(Decimal(v2["total"]), Decimal("2360"))

    def test_pg_venta_numeracion_concurrente(self):
        """PG-VENTAS (concurrencia): 8 hilos x 5 facturas -> 40 números
        únicos, sin duplicados ni saltos en la secuencia."""
        eid, uid, _ = self._empresa()
        prod = self._producto(eid, stock=1000, precio=100)
        HILOS, POR_HILO = 8, 5
        numeros, errores = [], []
        candado = threading.Lock()
        barrera = threading.Barrier(HILOS)

        def trabajo():
            barrera.wait()  # arranque simultáneo
            for _ in range(POR_HILO):
                try:
                    v = self._venta(eid, prod, creada_por=uid)
                    with candado:
                        numeros.append(v["numero"])
                except Exception as exc:  # noqa: BLE001
                    with candado:
                        errores.append(repr(exc)[:120])

        hilos = [threading.Thread(target=trabajo) for _ in range(HILOS)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        self.assertEqual(errores, [], f"errores en hilos: {errores[:3]}")
        self.assertEqual(len(numeros), HILOS * POR_HILO)
        self.assertEqual(len(set(numeros)), HILOS * POR_HILO,
                         "números de factura duplicados bajo concurrencia")
        conn = db.connect()
        try:
            cur = conn.cursor()
            fila = db.fetchone(
                cur, "SELECT ultimo_numero FROM factura_secuencias"
                     " WHERE tipo_doc='FACTURA' AND empresa_id=%s", (eid,))
        finally:
            conn.close()
        self.assertEqual(int(fila[0]), HILOS * POR_HILO)


# ============================================================================
# 5. STOCK (PostgreSQL) — descuento, tope y carrera
# ============================================================================
class TestPGStock(PGBase):

    def test_pg_stock_descuenta_y_bloquea_sobreventa(self):
        """PG-STOCK: vender descuenta; vender más del disponible falla y
        no mueve el inventario."""
        eid, uid, _ = self._empresa()
        prod = self._producto(eid, stock=10, precio=500)

        self._venta(eid, prod, cantidad=3, creada_por=uid)
        self.assertEqual(
            Decimal(str(inventario.obtener(eid, prod["id"])["stock"])),
            Decimal("7"))

        with self.assertRaises(ValueError):
            self._venta(eid, prod, cantidad=8, creada_por=uid)
        self.assertEqual(
            Decimal(str(inventario.obtener(eid, prod["id"])["stock"])),
            Decimal("7"))

    def test_pg_stock_carrera_hilos(self):
        """PG-STOCK (carrera): stock 20, 4 hilos venden 6 c/u (demanda 24).
        Solo 3 ventas prosperan, 1 falla por stock insuficiente, el stock
        final es 2 y lo vendido == lo descontado (sin sobreventa)."""
        eid, uid, _ = self._empresa()
        prod = self._producto(eid, stock=20, precio=100)
        HILOS, CANT = 4, 6
        ok, fallos = [], []
        candado = threading.Lock()
        barrera = threading.Barrier(HILOS)

        def vender():
            barrera.wait()
            try:
                v = self._venta(eid, prod, cantidad=CANT, creada_por=uid)
                with candado:
                    ok.append(v["numero"])
            except Exception as exc:  # noqa: BLE001
                with candado:
                    fallos.append(f"{type(exc).__name__}: {str(exc)[:80]}")

        hilos = [threading.Thread(target=vender) for _ in range(HILOS)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        stock_final = int(inventario.obtener(eid, prod["id"])["stock"])
        self.assertEqual(len(ok), 3, f"ok={ok} fallos={fallos}")
        self.assertEqual(len(fallos), 1)
        self.assertIn("stock insuficiente", fallos[0])
        self.assertEqual(stock_final, 2)
        # invariante contable: unidades vendidas == stock consumido
        self.assertEqual(len(ok) * CANT, 20 - stock_final)
        self.assertEqual(len(set(ok)), len(ok), "números duplicados")


# ============================================================================
# 6. PAGOS Y CAJA (PostgreSQL)
# ============================================================================
class TestPGPagosCaja(PGBase):

    def test_pg_pagos_abono_parcial(self):
        """PG-PAGOS: abono parcial deja balance; al saldar queda pagada;
        no admite sobrepago."""
        eid, uid, _ = self._empresa()
        prod = self._producto(eid, stock=10, precio=1180)
        v = self._venta(eid, prod, creada_por=uid)
        total = Decimal(v["total"])
        self.assertEqual(v["estado"], "pendiente")

        r1 = pagos.registrar_pago(eid, v["id"], 500, "efectivo",
                                  creado_por=uid)
        self.assertEqual(Decimal(r1["balance_restante"]), total - 500)
        self.assertEqual(pagos.balance(eid, v["id"]), total - 500)
        self.assertEqual(ventas.obtener(eid, v["id"])["estado"], "pendiente")

        pagos.registrar_pago(eid, v["id"], total - 500, "efectivo",
                             creado_por=uid)
        self.assertEqual(pagos.balance(eid, v["id"]), Decimal("0"))
        self.assertEqual(ventas.obtener(eid, v["id"])["estado"], "pagada")

        with self.assertRaises(ValueError):
            pagos.registrar_pago(eid, v["id"], 1, "efectivo",
                                 creado_por=uid)

    def test_pg_caja_cuadre(self):
        """PG-CAJA: apertura, movimientos con signo y cierre con
        diferencia 0 (cuadre exacto)."""
        eid, uid, _ = self._empresa()
        turno = caja.abrir(eid, uid, 5000)
        self.assertIsNotNone(turno)

        caja.registrar_movimiento(eid, "venta", 1180, "venta mostrador",
                                   creado_por=uid)
        caja.registrar_movimiento(eid, "gasto", -200, "compra insumos",
                                   creado_por=uid)
        self.assertEqual(caja.esperado(turno["id"], eid), Decimal("5980"))

        cierre = caja.cerrar(eid, uid, 5980)
        self.assertEqual(Decimal(str(cierre["diferencia"])), Decimal("0"))
        self.assertEqual(cierre["estado"], "cerrada")
        self.assertIsNone(caja.turno_abierto(eid))


# ============================================================================
# 7. e-CF SIMULADO (PostgreSQL)
# ============================================================================
class TestPGEcf(PGBase):

    def test_pg_ecf_flujo_mock_con_aviso(self):
        """PG-eCF: borrador -> validar -> aprobar -> emitir (mock) con el
        aviso de SIMULACIÓN presente y cadena de hash íntegra."""
        eid, uid, _ = self._empresa(modo_ecf="mock")
        cli = clientes.crear(eid, "Cliente Fiscal PG", rnc=self._rnc())
        prod = self._producto(eid, stock=20, precio=1000)
        v = self._venta(eid, prod, cantidad=2, creada_por=uid,
                        cliente_id=cli["id"])

        comp = ecf.crear(eid, v["id"], "31", actor="admin-pg@test.local")
        self.assertEqual(comp["estado"], "borrador")
        self.assertTrue(comp["e_ncf"].startswith("E31"))

        comp = ecf.validar(eid, comp["id"], actor="admin-pg@test.local")
        self.assertEqual(comp["estado"], "validado")

        # emitir sin aprobación humana -> bloqueado
        with self.assertRaises(ValueError):
            ecf.emitir(eid, comp["id"], actor="alguien@test.local")

        comp = ecf.aprobar(eid, comp["id"], actor="humano-pg@test.local",
                           detalle="aprobación de prueba")
        self.assertEqual(comp["estado"], "pendiente_aprobacion")

        comp = ecf.emitir(eid, comp["id"], actor="humano-pg@test.local")
        self.assertEqual(comp["estado"], "aceptado")  # MockProvider

        # aviso de simulación presente en datos y XML
        self.assertIn("SIMULACIÓN", comp["comprobante"]["aviso"])
        self.assertIn("SIMULACI", comp["xml"])
        # cadena de eventos íntegra
        self.assertTrue(comp["historial_ok"])
        self.assertTrue(ecf.verificar_cadena(eid, comp["id"]))


# ============================================================================
# 8. CONCURRENCIA MIXTA (PostgreSQL)
# ============================================================================
class TestPGConcurrencia(PGBase):

    def test_pg_escrituras_paralelas_mixtas(self):
        """PG-CONCURRENCIA: 6 hilos crean clientes + facturas + pagos a la
        vez; no se pierde ningún registro ni se duplican secuencias."""
        eid, uid, _ = self._empresa()
        prod = self._producto(eid, stock=500, precio=100)
        HILOS, VENTAS = 6, 3
        errores = []
        candado = threading.Lock()
        barrera = threading.Barrier(HILOS)

        def trabajo(i):
            barrera.wait()
            try:
                c = clientes.crear(eid, f"Cliente Hilo PG {i}")
                for _ in range(VENTAS):
                    vv = self._venta(eid, prod, creada_por=uid,
                                     cliente_id=c["id"])
                    pagos.registrar_pago(eid, vv["id"], 50, "efectivo",
                                         creado_por=uid)
            except Exception as exc:  # noqa: BLE001
                with candado:
                    errores.append(repr(exc)[:120])

        hilos = [threading.Thread(target=trabajo, args=(i,))
                 for i in range(HILOS)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()

        self.assertEqual(errores, [], f"errores en hilos: {errores[:3]}")

        lista = ventas.listar(eid, limite=1000)
        self.assertEqual(len(lista), HILOS * VENTAS)
        nums = [x["numero"] for x in lista]
        self.assertEqual(len(set(nums)), len(nums), "secuencia duplicada")

        conn = db.connect()
        try:
            cur = conn.cursor()
            n_pagos = db.fetchone(
                cur, "SELECT count(*) FROM factura_pagos"
                     " WHERE empresa_id=%s", (eid,))[0]
            n_cli = db.fetchone(
                cur, "SELECT count(*) FROM factura_clientes"
                     " WHERE empresa_id=%s", (eid,))[0]
            ultimo = db.fetchone(
                cur, "SELECT ultimo_numero FROM factura_secuencias"
                     " WHERE tipo_doc='FACTURA' AND empresa_id=%s",
                (eid,))[0]
        finally:
            conn.close()
        self.assertEqual(int(n_pagos), HILOS * VENTAS)
        self.assertEqual(int(n_cli), HILOS)
        self.assertEqual(int(ultimo), HILOS * VENTAS)


# ============================================================================
# 9. MIGRACIONES (PostgreSQL)
# ============================================================================
class TestPGMigraciones(PGBase):

    def test_pg_migraciones_desde_cero_e_idempotentes(self):
        """PG-MIGRACIONES: el esquema nació vacío y db.migrate() aplicó
        001..009 (+ nuevas); la tabla de versiones coincide con el
        manifiesto y re-correr no aplica nada."""
        orden = db._orden()  # manifiesto factura_persist/schema/_orden.txt
        self.assertGreaterEqual(len(orden), 9,
                                f"solo {len(orden)} migraciones")
        self.assertEqual(orden[0], "001_factura_base.sql")
        # el setUp migró desde cero: lo aplicado == manifiesto en orden
        self.assertEqual(self.migradas, orden)

        conn = db.connect()
        try:
            cur = conn.cursor()
            registradas = [r[0] for r in db.fetchall(
                cur, "SELECT archivo FROM factura_migrations ORDER BY 1")]
        finally:
            conn.close()
        self.assertEqual(registradas, sorted(orden))

        # idempotencia: segunda corrida no aplica nada
        self.assertEqual(db.migrate(), [])


# ============================================================================
# 10. RESTAURACIÓN pg_dump (PostgreSQL)
# ============================================================================
class TestPGRestore(PGBase):

    def test_pg_restore_pg_dump_datos_identicos(self):
        """PG-RESTORE: pg_dump del esquema de prueba -> restore en esquema
        nuevo -> conteo por tabla idéntico y datos puntuales iguales."""
        eid, uid, _ = self._empresa()
        cli = clientes.crear(eid, "Cliente Restore PG", rnc=self._rnc())
        prod = self._producto(eid, stock=40, precio=250)
        v = self._venta(eid, prod, cantidad=4, creada_por=uid,
                        cliente_id=cli["id"])
        pagos.registrar_pago(eid, v["id"], 100, "efectivo", creado_por=uid)

        conteo_orig = self._conteo_tablas()
        self.assertGreater(sum(conteo_orig.values()), 0)

        dump = os.path.join(tempfile.gettempdir(),
                            f"pgtest_dump_{_PID}_{self.esquema}.sql")
        esquema_r = self.esquema + "_r"
        dump_r = dump + ".restore.sql"
        try:
            # 1) pg_dump del esquema (credencial solo en entorno)
            r1 = subprocess.run(
                [_PG_DUMP, "--no-owner",
                 "-n", self.esquema, "-f", dump],
                env=_env_pg_dump(), capture_output=True, text=True,
                timeout=120)
            self.assertEqual(r1.returncode, 0, r1.stderr[:300])
            self.assertGreater(os.path.getsize(dump), 0)

            # 2) reescribir el nombre del esquema y restaurar en uno nuevo
            #    (los datos sintéticos nunca contienen el nombre del esquema)
            with open(dump, encoding="utf-8") as fh:
                src = fh.read()
            with open(dump_r, "w", encoding="utf-8") as fh:
                fh.write(re.sub(r"\b%s\b" % re.escape(self.esquema),
                                esquema_r, src))
            r2 = subprocess.run(
                ["psql", "-v", "ON_ERROR_STOP=1", "-f", dump_r],
                env=_env_pg_dump(), capture_output=True, text=True,
                timeout=120)
            self.assertEqual(r2.returncode, 0, r2.stderr[:300])

            # 3) conteos por tabla idénticos
            conteo_rst = self._conteo_tablas(esquema_r)
            self.assertEqual(set(conteo_rst), set(conteo_orig))
            self.assertEqual(conteo_rst, conteo_orig)

            # 4) verificación puntual de datos (venta + hash de usuario)
            conn = db.connect()
            try:
                cur = conn.cursor()
                f1 = db.fetchone(
                    cur, f'SELECT numero, total FROM "{self.esquema}"'
                         '.factura_ventas WHERE id=%s', (v["id"],))
                f2 = db.fetchone(
                    cur, f'SELECT numero, total FROM "{esquema_r}"'
                         '.factura_ventas WHERE id=%s', (v["id"],))
                h1 = db.fetchone(
                    cur, f'SELECT hash FROM "{self.esquema}"'
                         '.factura_usuarios WHERE id=%s', (uid,))
                h2 = db.fetchone(
                    cur, f'SELECT hash FROM "{esquema_r}"'
                         '.factura_usuarios WHERE id=%s', (uid,))
            finally:
                conn.close()
            self.assertEqual(tuple(f1), tuple(f2))
            self.assertEqual(h1[0], h2[0])
        finally:
            _admin_ddl(f'DROP SCHEMA IF EXISTS "{esquema_r}" CASCADE')
            for f in (dump, dump_r):
                if os.path.exists(f):
                    os.remove(f)


if __name__ == "__main__":
    unittest.main(verbosity=2)
