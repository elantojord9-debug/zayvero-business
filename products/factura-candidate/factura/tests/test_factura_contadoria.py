#!/usr/bin/env python3
"""Tests del Contador IA de TALENTO (unittest, stdlib).

Cubre: cálculos exactos del análisis (resumen del mes, margen, tops,
CxC, inventario, gastos), recomendaciones deterministas que citan datos
reales, garantía de SOLO LECTURA (ningún INSERT/UPDATE/DELETE al
analizar), matriz de permisos y la ruta HTTP /contador-ia
(200 para admin/contador, 403 para cajero).

Uso:
    python -m pytest tests/   # con el venv del proyecto activado
"""
from __future__ import annotations

import http.client
import os
import sys
import tempfile
import threading
import unittest
from datetime import date
from decimal import Decimal
from http.server import ThreadingHTTPServer

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import config  # noqa: E402
from factura_persist import db  # noqa: E402
from factura_auth import auth as auth_mod  # noqa: E402
from factura_core import (clientes, contador_ia, gastos as gastos_mod,  # noqa: E402
                          inventario, ventas)
from factura_web import server as srv  # noqa: E402

# Fecha de referencia fija: el análisis es determinista (CxC usa esta
# referencia en vez de date.today()).
REF = date(2026, 10, 14)


def _ahora():
    return "2026-10-14T12:00:00+00:00"


class BaseFactura(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="factura-ia-test-")
        config.SQLITE_PATH = os.path.join(self.tmp, "test.db")
        db.reset_backend_cache()
        assert db.backend() == "sqlite"
        db.migrate()

    def tearDown(self):
        db.reset_backend_cache()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _empresa(self, nombre="Demo IA", rnc="999888777"):
        conn = db.connect()
        try:
            cur = conn.cursor()
            with db.tx(conn):
                eid = db.insert_id(
                    cur, "INSERT INTO factura_empresas (nombre, rnc, telefono,"
                         " direccion, plan, itbis_tasa, e_cf_mode, creada)"
                         " VALUES (%s,%s,'','','basico','18','disabled',%s)",
                    (nombre, rnc, _ahora()))
            return eid
        finally:
            conn.close()

    def _reubicar_venta(self, eid, venta_id, creada):
        """Fija la fecha de creación de una venta (fixture determinista)."""
        conn = db.connect()
        try:
            cur = conn.cursor()
            with db.tx(conn):
                db.execute(cur, "UPDATE factura_ventas SET creada=%s"
                                " WHERE empresa_id=%s AND id=%s",
                           (creada, eid, venta_id))
        finally:
            conn.close()

    def _fixture(self):
        """Empresa demo con ventas en oct-2026 y sep-2026, gastos, CxC."""
        eid = self._empresa()
        p1 = inventario.crear(eid, "A1", "Arroz", 200, 120, stock=100,
                              stock_minimo=10)
        p2 = inventario.crear(eid, "A2", "Aceite", 500, 300, stock=14,
                              stock_minimo=10)
        p3 = inventario.crear(eid, "A3", "Azúcar", 100, 60, stock=50,
                              stock_minimo=10)
        inventario.crear(eid, "A4", "Café", 1000, 500, stock=30,
                         stock_minimo=10)
        c1 = clientes.crear(eid, "Bodega Juan", rnc="101010101")
        c2 = clientes.crear(eid, "Colmado Ana", rnc="202020202")

        def item(p, cant):
            return {"producto_id": p["id"], "nombre": p["nombre"],
                    "cantidad": cant, "precio": p["precio"],
                    "itbis_exento": True}

        # V1: octubre, a crédito vencida (vencimiento 01-oct < 14-oct)
        v1 = ventas.crear_venta(
            eid, "factura", c1["id"],
            [item(p1, 10), item(p2, 10)], creada_por=None,
            estado="pendiente", vencimiento="2026-10-01")
        self._reubicar_venta(eid, v1["id"], "2026-10-05T10:00:00+00:00")
        # V2: octubre, pagada
        v2 = ventas.crear_venta(
            eid, "factura", c1["id"], [item(p1, 20)], creada_por=None,
            estado="pendiente", metodo_pago="efectivo")
        self._reubicar_venta(eid, v2["id"], "2026-10-06T10:00:00+00:00")
        # V3: octubre, a crédito no vencida
        v3 = ventas.crear_venta(
            eid, "factura", c2["id"], [item(p3, 5)], creada_por=None,
            estado="pendiente", vencimiento="2026-11-15")
        self._reubicar_venta(eid, v3["id"], "2026-10-07T10:00:00+00:00")
        # V4: septiembre, pagada
        v4 = ventas.crear_venta(
            eid, "factura", c2["id"], [item(p3, 20)], creada_por=None,
            estado="pendiente", metodo_pago="efectivo")
        self._reubicar_venta(eid, v4["id"], "2026-09-10T10:00:00+00:00")

        gastos_mod.crear(eid, "gasto", "2026-10-02", "alquiler", "Renta",
                         "5000")
        gastos_mod.crear(eid, "gasto", "2026-10-03", "nómina", "Sueldos",
                         "8000")
        gastos_mod.crear(eid, "gasto", "2026-09-05", "alquiler",
                         "Renta sep", "4000")
        gastos_mod.crear(eid, "gasto", "2026-09-06", "nómina",
                         "Sueldos sep", "8000")
        return eid


class TestCalculos(BaseFactura):
    def test_resumen_mes(self):
        eid = self._fixture()
        r = contador_ia.resumen_mes(eid, REF)
        self.assertEqual(r["mes"], "octubre 2026")
        self.assertEqual(Decimal(r["ventas_total"]), Decimal("11500"))
        self.assertEqual(r["n_facturas"], 3)
        self.assertEqual(Decimal(r["ticket_promedio"]),
                         Decimal("3833.33"))
        self.assertEqual(Decimal(r["ventas_mes_anterior"]),
                         Decimal("2000"))
        self.assertEqual(Decimal(r["cambio_pct"]), Decimal("475.0"))

    def test_ventanas_borde_de_ano(self):
        v = contador_ia._ventanas(date(2026, 12, 15))
        self.assertEqual((v["mes_ini"], v["mes_fin"]),
                         ("2026-12-01", "2026-12-31"))
        self.assertEqual((v["prev_ini"], v["prev_fin"]),
                         ("2026-11-01", "2026-11-30"))
        v2 = contador_ia._ventanas(date(2027, 1, 10))
        self.assertEqual((v2["mes_ini"], v2["mes_fin"]),
                         ("2027-01-01", "2027-01-31"))
        self.assertEqual((v2["prev_ini"], v2["prev_fin"]),
                         ("2026-12-01", "2026-12-31"))

    def test_margen_mes(self):
        eid = self._fixture()
        m = contador_ia.margen_mes(eid, REF)
        self.assertEqual(Decimal(m["ventas"]), Decimal("11500"))
        # costo: Arroz 30x120 + Aceite 10x300 + Azúcar 5x60
        self.assertEqual(Decimal(m["costo_ventas"]), Decimal("6900"))
        self.assertEqual(Decimal(m["utilidad_bruta"]), Decimal("4600"))
        self.assertEqual(Decimal(m["gastos"]), Decimal("13000"))
        self.assertEqual(Decimal(m["utilidad_neta"]), Decimal("-8400"))
        self.assertEqual(Decimal(m["margen_neto_pct"]), Decimal("-73.0"))
        self.assertIn("aproximación", m["nota"])

    def test_top_productos(self):
        eid = self._fixture()
        ti = contador_ia.top_productos(eid, "2026-10-01", "2026-10-31",
                                       por="ingreso")
        self.assertEqual(ti[0]["nombre"], "Arroz")
        self.assertEqual(Decimal(ti[0]["ingreso"]), Decimal("6000"))
        self.assertEqual(ti[1]["nombre"], "Aceite")
        tu = contador_ia.top_productos(eid, "2026-10-01", "2026-10-31",
                                       por="unidades")
        self.assertEqual(tu[0]["nombre"], "Arroz")
        self.assertEqual(Decimal(tu[0]["cantidad"]), Decimal("30"))

    def test_top_clientes(self):
        eid = self._fixture()
        tc = contador_ia.top_clientes(eid, "2026-10-01", "2026-10-31")
        self.assertEqual(tc[0]["nombre"], "Bodega Juan")
        self.assertEqual(Decimal(tc[0]["total"]), Decimal("11000"))
        self.assertEqual(tc[0]["facturas"], 2)
        self.assertEqual(tc[1]["nombre"], "Colmado Ana")

    def test_analisis_cxc(self):
        eid = self._fixture()
        c = contador_ia.analisis_cxc(eid, REF)
        self.assertEqual(Decimal(c["total_pendiente"]), Decimal("7500"))
        self.assertEqual(c["n_pendientes"], 2)
        self.assertEqual(Decimal(c["total_vencido"]), Decimal("7000"))
        self.assertEqual(c["n_vencidas"], 1)
        self.assertEqual(c["n_clientes_vencidos"], 1)
        self.assertEqual(c["dias_promedio_vencidos"], 13)
        self.assertEqual(Decimal(c["antiguedad"]["0-30"]),
                         Decimal("7000"))
        self.assertEqual(Decimal(c["antiguedad"]["61+"]), Decimal("0"))

    def test_analisis_inventario(self):
        eid = self._fixture()
        inv = contador_ia.analisis_inventario(eid, REF)
        # stock tras ventas: Arroz 70, Aceite 4, Azúcar 25, Café 30
        # val: 70*120 + 4*300 + 25*60 + 30*500 = 26100
        self.assertEqual(Decimal(inv["valorizacion_total"]),
                         Decimal("26100"))
        self.assertEqual(inv["n_alertas"], 1)       # solo Aceite (bajo)
        self.assertEqual(inv["n_agotados"], 0)
        a0 = inv["alertas"][0]
        self.assertEqual(a0["nombre"], "Aceite")
        self.assertEqual(a0["dias_stock"], 12.0)
        # Café no se vendió en 30 días → capital inmovilizado 30*500
        self.assertEqual(Decimal(inv["valorizacion_inmovilizada"]),
                         Decimal("15000"))

    def test_gastos_vs_mes_anterior(self):
        eid = self._fixture()
        g = contador_ia.gastos_vs_mes_anterior(eid, REF)
        self.assertEqual(Decimal(g["total_mes"]), Decimal("13000"))
        self.assertEqual(Decimal(g["total_mes_anterior"]),
                         Decimal("12000"))
        self.assertEqual(Decimal(g["cambio_pct"]), Decimal("8.3"))
        alq = next(x for x in g["categorias"]
                   if x["categoria"] == "alquiler")
        self.assertEqual(Decimal(alq["cambio_pct"]), Decimal("25.0"))


class TestRecomendaciones(BaseFactura):
    def test_estructura_y_orden(self):
        eid = self._fixture()
        recs = contador_ia.generar_recomendaciones(eid, REF)
        self.assertLessEqual(len(recs), 8)
        self.assertGreaterEqual(len(recs), 5)
        for r in recs:
            for k in ("titulo", "explicacion", "dato", "accion",
                      "impacto", "disclaimer"):
                self.assertIn(k, r, f"falta {k} en {r.get('titulo')}")
                self.assertTrue(r[k], f"{k} vacío en {r.get('titulo')}")
            self.assertIn("informativa", r["disclaimer"])
        impactos = [r["impacto"] for r in recs]
        self.assertEqual(impactos, sorted(impactos, reverse=True))

    def test_reglas_disparan_con_datos_reales(self):
        eid = self._fixture()
        recs = contador_ia.generar_recomendaciones(eid, REF)
        titulos = [r["titulo"] for r in recs]

        venc = next(r for r in recs if "vencidas" in r["titulo"])
        self.assertIn("7,000.00", venc["dato"])      # RD$ 7,000.00 real
        self.assertEqual(venc["impacto"], 5)

        agot = next(r for r in recs if "Repón inventario" in r["titulo"])
        self.assertIn("Aceite", agot["dato"])
        self.assertIn("12", agot["dato"])            # ~12 días de stock

        alq = next(r for r in recs if "alquiler" in r["titulo"])
        self.assertIn("+25.0%", alq["dato"])

        conc = next(r for r in recs if "pocos productos" in r["titulo"])
        self.assertIn("100.0%", conc["dato"])

        perdida = next(r for r in recs if "pérdida" in r["titulo"])
        self.assertIn("-8,400.00", perdida["dato"])
        self.assertEqual(perdida["impacto"], 5)

    def test_sin_datos(self):
        eid = self._empresa("Vacía")
        recs = contador_ia.generar_recomendaciones(eid, REF)
        self.assertEqual(len(recs), 1)
        self.assertIn("Registra tu actividad", recs[0]["titulo"])


class TestSoloLectura(BaseFactura):
    def _conteo_tablas(self):
        conn = db.connect()
        try:
            cur = conn.cursor()
            tablas = [f[0] for f in db.fetchall(
                cur, "SELECT name FROM sqlite_master WHERE type='table'"
                     " AND name LIKE 'factura_%'", ())]
            return {t: db.fetchone(cur, f"SELECT COUNT(*) FROM {t}")[0]
                    for t in tablas}
        finally:
            conn.close()

    def test_analisis_no_escribe(self):
        eid = self._fixture()
        antes = self._conteo_tablas()
        contador_ia.panel_contador(eid, REF)
        despues = self._conteo_tablas()
        self.assertEqual(antes, despues)

    def test_sin_escritura_en_codigo(self):
        import re
        src = open(os.path.join(BASE, "factura_core",
                                "contador_ia.py"), encoding="utf-8").read()
        for patron in (r"\bINSERT\b", r"\bUPDATE\b", r"\bDELETE\b",
                       r"\bCREATE\b", r"\bALTER\b", r"\bDROP\b",
                       r"db\.execute", r"db\.tx", r"db\.locked",
                       r"db\.insert_id", r"requests\.", r"urllib",
                       r"openai", r"anthropic"):
            self.assertIsNone(re.search(patron, src, re.IGNORECASE),
                              f"patrón prohibido en contador_ia.py: {patron}")


class TestPermisos(BaseFactura):
    def test_matriz(self):
        self.assertTrue(config.tiene_permiso("admin", "contador_ia_ver"))
        self.assertTrue(config.tiene_permiso("contador",
                                             "contador_ia_ver"))
        self.assertFalse(config.tiene_permiso("cajero",
                                              "contador_ia_ver"))


class TestRutaHTTP(BaseFactura):
    """Servidor real en puerto efímero: 200 admin/contador, 403 cajero."""

    def _servidor(self):
        srv_http = ThreadingHTTPServer(("127.0.0.1", 0),
                                       srv.Handler)
        hilo = threading.Thread(target=srv_http.serve_forever,
                                daemon=True)
        hilo.start()
        self.addCleanup(srv_http.shutdown)
        self.addCleanup(srv_http.server_close)
        return srv_http.server_address[1]

    def _cookie(self, eid, email, rol):
        u = auth_mod.crear_usuario(eid, email, "Clave1234567*", rol=rol)
        return auth_mod.crear_sesion(
            {"id": u["id"], "empresa_id": eid, "rol": rol})

    def _get(self, puerto, cookie):
        conn = http.client.HTTPConnection("127.0.0.1", puerto, timeout=10)
        conn.request("GET", "/contador-ia",
                     headers={"Cookie": f"{config.SESSION_COOKIE}={cookie}"})
        resp = conn.getresponse()
        cuerpo = resp.read().decode("utf-8", "replace")
        conn.close()
        return resp.status, cuerpo

    def test_permisos_http(self):
        eid = self._fixture()
        puerto = self._servidor()
        admin = self._cookie(eid, "admin@ia.test", "admin")
        conta = self._cookie(eid, "conta@ia.test", "contador")
        cajero = self._cookie(eid, "caja@ia.test", "cajero")

        st, cuerpo = self._get(puerto, admin)
        self.assertEqual(st, 200)
        self.assertIn("Contador IA", cuerpo)
        self.assertIn("Recomendaciones", cuerpo)
        # los números del análisis aparecen en la página
        self.assertIn("11,500.00", cuerpo)

        st, _ = self._get(puerto, conta)
        self.assertEqual(st, 200)

        st, cuerpo = self._get(puerto, cajero)
        self.assertEqual(st, 403)
        self.assertNotIn("Recomendaciones", cuerpo)


if __name__ == "__main__":
    unittest.main(verbosity=2)
