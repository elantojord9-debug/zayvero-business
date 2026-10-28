#!/usr/bin/env python3
"""Tests de TALENTO (unittest, stdlib).

Cubre: aislamiento multiempresa, secuencias sin duplicados bajo
concurrencia, ITBIS exacto con Decimal, máquina de estados e-CF y login.

Uso:
    python -m unittest discover -s tests -v   # con el venv del proyecto activado
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from decimal import Decimal

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import config  # noqa: E402
from factura_persist import db  # noqa: E402
from factura_auth import auth as auth_mod  # noqa: E402
from factura_core import clientes, inventario, ventas, pagos  # noqa: E402
from factura_ecf import service as ecf  # noqa: E402


def _ahora():
    return datetime.now(timezone.utc).isoformat()


class BaseFactura(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="factura-test-")
        config.SQLITE_PATH = os.path.join(self.tmp, "test.db")
        db.reset_backend_cache()
        assert db.backend() == "sqlite"
        db.migrate()

    def tearDown(self):
        db.reset_backend_cache()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _empresa(self, nombre="Empresa Test", rnc="111222333",
                 modo="mock"):
        conn = db.connect()
        try:
            cur = conn.cursor()
            with db.tx(conn):
                eid = db.insert_id(
                    cur, "INSERT INTO factura_empresas (nombre, rnc, telefono,"
                         " direccion, plan, itbis_tasa, e_cf_mode, creada)"
                         " VALUES (%s,%s,'','','basico','18',%s,%s)",
                    (nombre, rnc, modo, _ahora()))
            return eid
        finally:
            conn.close()


class TestAislamiento(BaseFactura):
    def test_empresa_a_no_ve_datos_de_b(self):
        a = self._empresa("A", "111111111")
        b = self._empresa("B", "222222222")
        pa = inventario.crear(a, "SKU-A", "Producto A", 100, 50, stock=10)
        ca = clientes.crear(a, "Cliente A", "111111111")
        va = ventas.crear_venta(a, "factura", ca["id"],
                                [{"producto_id": pa["id"], "nombre": "Producto A",
                                  "cantidad": 1, "precio": 100,
                                  "itbis_exento": False}],
                                creada_por=None, metodo_pago="efectivo")
        # B no ve nada de A
        self.assertEqual(inventario.listar(b), [])
        self.assertEqual(clientes.listar(b), [])
        self.assertEqual(ventas.listar(b), [])
        self.assertIsNone(inventario.obtener(b, pa["id"]))
        self.assertIsNone(clientes.obtener(b, ca["id"]))
        self.assertIsNone(ventas.obtener(b, va["id"]))
        self.assertEqual(pagos.cxc_pendientes(b), [])
        # A sí ve lo suyo
        self.assertEqual(len(inventario.listar(a)), 1)
        self.assertEqual(len(ventas.listar(a)), 1)

    def test_ecf_aislado_por_empresa(self):
        a = self._empresa("A", "111111111")
        b = self._empresa("B", "222222222")
        pa = inventario.crear(a, "SKU-A", "Producto A", 100, 50, stock=10)
        ca = clientes.crear(a, "Cliente A", "333333333")
        va = ventas.crear_venta(a, "factura", ca["id"],
                                [{"producto_id": pa["id"], "nombre": "Producto A",
                                  "cantidad": 1, "precio": 100,
                                  "itbis_exento": False}],
                                creada_por=None, metodo_pago="efectivo")
        comp = ecf.crear(a, va["id"], "32", actor="test")
        self.assertEqual(ecf.listar(b), [])
        self.assertIsNone(ecf.obtener(b, comp["id"]))
        self.assertEqual(len(ecf.listar(a)), 1)


class TestSecuencias(BaseFactura):
    def test_sin_duplicados_bajo_concurrencia(self):
        eid = self._empresa()
        resultados = []
        errores = []

        def worker(n):
            try:
                conn = db.connect()
                try:
                    with db.locked(conn):
                        num = ventas.reservar_numero(conn, "FACTURA", eid)
                        resultados.append(num)
                finally:
                    conn.close()
            except Exception as exc:  # noqa: BLE001
                errores.append(exc)

        hilos = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(errores, [])
        self.assertEqual(len(resultados), 20)
        self.assertEqual(len(set(resultados)), 20, "números duplicados")
        nums = sorted(int(r.split("-")[1]) for r in resultados)
        self.assertEqual(nums, list(range(1, 21)), "secuencia no contigua")

    def test_encf_sin_duplicados(self):
        eid = self._empresa()  # noqa: F841 (rnc fijo del helper)
        vistos = set()
        conn = db.connect()
        try:
            for _ in range(5):
                with db.locked(conn):
                    e = ecf._reservar_e_ncf(conn, "111222333", "31", "mock")
                self.assertNotIn(e, vistos)
                vistos.add(e)
                self.assertRegex(e, r"^E31\d{10}$")
        finally:
            conn.close()


class TestITBIS(BaseFactura):
    def test_itbis_exacto_decimal(self):
        eid = self._empresa()
        items, tot = ventas.normalizar_items(
            eid,
            [{"nombre": "Gravado", "cantidad": 1, "precio": "100",
              "itbis_exento": False},
             {"nombre": "Exento", "cantidad": 2, "precio": "50",
              "itbis_exento": True}],
            Decimal("18"))
        self.assertEqual(Decimal(tot["subtotal"]), Decimal("200.00"))
        self.assertEqual(Decimal(tot["itbis"]), Decimal("18.00"))
        self.assertEqual(Decimal(tot["total"]), Decimal("218.00"))

    def test_itbis_reporte_usa_motor(self):
        from factura_core import reportes
        eid = self._empresa()
        ventas.crear_venta(
            eid, "factura", None,
            [{"nombre": "Gravado", "cantidad": 1, "precio": "1000",
              "itbis_exento": False}],
            creada_por=None, metodo_pago="efectivo")
        r = reportes.resumen_itbis(eid, tasa="18")
        self.assertEqual(Decimal(r["base_gravada"]), Decimal("1000"))
        self.assertEqual(Decimal(r["debito"]), Decimal("180"))
        self.assertEqual(Decimal(r["a_pagar"]), Decimal("180"))


class TestECF(BaseFactura):
    def _venta_lista(self, eid, rnc_cliente="111111111"):
        p = inventario.crear(eid, "SKU1", "Prod 1", 200, 100, stock=10)
        c = clientes.crear(eid, "Cliente X", rnc_cliente)
        v = ventas.crear_venta(
            eid, "factura", c["id"],
            [{"producto_id": p["id"], "nombre": "Prod 1", "cantidad": 1,
              "precio": 200, "itbis_exento": False}],
            creada_por=None, metodo_pago="efectivo")
        return v

    def test_flujo_completo_aceptado(self):
        eid = self._empresa(modo="mock")
        v = self._venta_lista(eid)
        comp = ecf.crear(eid, v["id"], "32", actor="tester")
        self.assertEqual(comp["comprobante"]["estado"], "borrador")
        self.assertRegex(comp["e_ncf"], r"^E32\d{10}$")

        comp = ecf.validar(eid, comp["id"], "tester")
        self.assertEqual(comp["comprobante"]["estado"], "validado")

        # emitir sin aprobación humana -> bloqueado
        with self.assertRaises(ValueError):
            ecf.emitir(eid, comp["id"], "tester")

        comp = ecf.aprobar(eid, comp["id"], "humano-responsable")
        self.assertEqual(comp["comprobante"]["estado"], "pendiente_aprobacion")

        comp = ecf.emitir(eid, comp["id"], "humano-responsable")
        self.assertEqual(comp["comprobante"]["estado"], "aceptado")
        self.assertTrue(comp["historial_ok"])
        self.assertTrue(ecf.verificar_cadena(eid, comp["id"]))

    def test_transicion_invalida_rechazada(self):
        eid = self._empresa(modo="mock")
        v = self._venta_lista(eid)
        comp = ecf.crear(eid, v["id"], "32", actor="tester")
        # borrador -> pendiente_aprobacion directo no permitido
        with self.assertRaises(ValueError):
            ecf.aprobar(eid, comp["id"], "tester")
        # emitir desde borrador tampoco
        with self.assertRaises(ValueError):
            ecf.emitir(eid, comp["id"], "tester")

    def test_modo_disabled_bloquea_transmision(self):
        eid = self._empresa(modo="disabled")
        v = self._venta_lista(eid)
        comp = ecf.crear(eid, v["id"], "32", actor="tester")
        comp = ecf.validar(eid, comp["id"], "tester")
        comp = ecf.aprobar(eid, comp["id"], "tester")
        with self.assertRaises(ValueError) as ctx:
            ecf.emitir(eid, comp["id"], "tester")
        self.assertIn("disabled", str(ctx.exception))

    def test_mock_observado_con_rnc_sintetico(self):
        eid = self._empresa(modo="mock")
        v = self._venta_lista(eid, rnc_cliente="999999999")
        comp = ecf.crear(eid, v["id"], "32", actor="tester")
        comp = ecf.validar(eid, comp["id"], "tester")
        comp = ecf.aprobar(eid, comp["id"], "tester")
        comp = ecf.emitir(eid, comp["id"], "tester")
        self.assertEqual(comp["comprobante"]["estado"], "observado")

    def test_mock_rechaza_total_cero(self):
        from os_ecf.proveedor import MockProvider
        from os_ecf.modelos import Comprobante, Emisor, Receptor, Linea
        c = Comprobante(
            id="t0", company_id="1", tipo="32",
            emisor=Emisor(rnc="111111111", razon_social="E"),
            receptor=Receptor(rnc="222222222", razon_social="R"),
            lineas=[Linea(descripcion="Gratis", cantidad=Decimal("1"),
                          precio_unitario=Decimal("0"))],
            modo="mock")
        r = MockProvider().transmitir("<xml/>", c)
        self.assertEqual(r.estado, "rechazado")
        self.assertIn("SIMULACIÓN", r.mensaje)

    def test_xml_descargable(self):
        eid = self._empresa(modo="mock")
        v = self._venta_lista(eid)
        comp = ecf.crear(eid, v["id"], "31", actor="tester")
        e_ncf, xml = ecf.xml_texto(eid, comp["id"])
        self.assertEqual(e_ncf, comp["e_ncf"])
        self.assertIn("SIMULACI", xml.upper())
        self.assertIn(e_ncf, xml)


class TestAuth(BaseFactura):
    def test_login_rechaza_clave_mala(self):
        eid = self._empresa()
        auth_mod.crear_usuario(eid, "caja@test.do", "ClaveSegura1234", "cajero")
        self.assertIsNone(auth_mod.autenticar("caja@test.do", "otra-clave-000"))
        self.assertIsNone(auth_mod.autenticar("caja@test.do", ""))
        self.assertIsNone(auth_mod.autenticar("nadie@test.do", "ClaveSegura1234"))
        ok = auth_mod.autenticar("caja@test.do", "ClaveSegura1234")
        self.assertIsNotNone(ok)
        self.assertEqual(ok["rol"], "cajero")

    def test_sesion_firmada_y_expiracion(self):
        eid = self._empresa()
        u = auth_mod.crear_usuario(eid, "admin@test.do", "ClaveSegura1234",
                                   "admin")
        u["id"] = u["id"]
        cookie = auth_mod.crear_sesion({"id": u["id"], "empresa_id": eid,
                                        "rol": "admin"})
        ses = auth_mod.leer_sesion(cookie)
        self.assertIsNotNone(ses)
        self.assertEqual(ses["e"], eid)
        # manipulada -> rechazada
        self.assertIsNone(auth_mod.leer_sesion(cookie + "x"))
        self.assertIsNone(auth_mod.leer_sesion("basura"))

    def test_politica_clave_aplicada(self):
        eid = self._empresa()
        with self.assertRaises(ValueError):
            auth_mod.crear_usuario(eid, "x@test.do", "corta", "cajero")


class TestIdempotencia(BaseFactura):
    """FASE 2: reintentos (doble clic, recarga, red) no duplican."""

    def _venta_lista(self, eid):
        p = inventario.crear(eid, "SKU-I", "Prod I", 100, 50, stock=100)
        return [{"producto_id": p["id"], "nombre": "Prod I",
                 "cantidad": 2, "precio": 100, "itbis_exento": True}]

    def test_reintento_misma_clave_no_duplica(self):
        eid = self._empresa()
        items = self._venta_lista(eid)
        v1 = ventas.crear_venta(eid, "factura", None, items,
                                idempotencia="clave-abc-123")
        v2 = ventas.crear_venta(eid, "factura", None, items,
                                idempotencia="clave-abc-123")
        self.assertEqual(v1["id"], v2["id"])
        self.assertEqual(v1["numero"], v2["numero"])
        # Una sola fila y el stock se descontó una sola vez (100-2=98)
        self.assertEqual(len(ventas.listar(eid)), 1)
        prod = inventario.listar(eid)[0]
        self.assertEqual(str(prod["stock"]), "98")

    def test_claves_distintas_crean_ventas_distintas(self):
        eid = self._empresa()
        items = self._venta_lista(eid)
        v1 = ventas.crear_venta(eid, "factura", None, items,
                                idempotencia="k-1")
        v2 = ventas.crear_venta(eid, "factura", None, items,
                                idempotencia="k-2")
        self.assertNotEqual(v1["id"], v2["id"])
        self.assertEqual(len(ventas.listar(eid)), 2)

    def test_pago_idempotente_no_duplica_cobro(self):
        eid = self._empresa()
        v = ventas.crear_venta(eid, "factura", None, self._venta_lista(eid))
        r1 = pagos.registrar_pago(eid, v["id"], 50, idempotencia="pago-k-1")
        r2 = pagos.registrar_pago(eid, v["id"], 50, idempotencia="pago-k-1")
        self.assertTrue(r2.get("duplicado_evitado"))
        # Un solo pago registrado; el balance refleja un solo abono
        self.assertEqual(len(pagos.pagos_de_venta(eid, v["id"])), 1)
        self.assertEqual(r1["balance_restante"], r2["balance_restante"])

    def test_concurrencia_misma_clave_una_venta(self):
        eid = self._empresa()
        items = self._venta_lista(eid)
        ids = []
        errores = []

        def _crear():
            try:
                v = ventas.crear_venta(eid, "pos", None, items,
                                       idempotencia="conc-k")
                ids.append(v["id"])
            except Exception as exc:  # noqa: BLE001
                errores.append(exc)

        hilos = [threading.Thread(target=_crear) for _ in range(10)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(errores, [])
        self.assertEqual(len(set(ids)), 1)
        self.assertEqual(len(ventas.listar(eid)), 1)


class TestECFEmisor(BaseFactura):
    """FASE 3 (issue #11): unicidad e-NCF por emisor fiscal normalizado."""

    def _venta_con_cliente(self, eid, rnc_cliente="333333333"):
        p = inventario.crear(eid, "SKU-E", "Prod E", 100, 50, stock=50)
        c = clientes.crear(eid, "Cliente E", rnc_cliente)
        return ventas.crear_venta(
            eid, "factura", c["id"],
            [{"producto_id": p["id"], "nombre": "Prod E", "cantidad": 1,
              "precio": 100, "itbis_exento": True}])

    def test_rncs_distintos_comparten_numero(self):
        a = self._empresa("A", "111111111")
        b = self._empresa("B", "222222222")
        va = self._venta_con_cliente(a)
        vb = self._venta_con_cliente(b)
        ca = ecf.crear(a, va["id"], "32", actor="test")
        cb = ecf.crear(b, vb["id"], "32", actor="test")
        # Mismo número, distintos emisores: sin conflicto
        self.assertEqual(ca["e_ncf"], "E320000000001")
        self.assertEqual(cb["e_ncf"], "E320000000001")
        self.assertEqual(ca["emisor_rnc_norm"], "111111111")
        self.assertEqual(cb["emisor_rnc_norm"], "222222222")

    def test_mismo_rnc_dos_empresas_secuencia_canonica(self):
        a = self._empresa("A", "444444444")
        b = self._empresa("B", "444444444")  # mismo RNC, otra cuenta
        va = self._venta_con_cliente(a)
        vb = self._venta_con_cliente(b)
        ca = ecf.crear(a, va["id"], "32", actor="test")
        cb = ecf.crear(b, vb["id"], "32", actor="test")
        # Secuencia canónica por RNC: no se duplica, continúa
        self.assertEqual(ca["e_ncf"], "E320000000001")
        self.assertEqual(cb["e_ncf"], "E320000000002")

    def test_db_rechaza_duplicado_mismo_emisor(self):
        import sqlite3
        eid = self._empresa("A", "555555555")
        v = self._venta_con_cliente(eid)
        c = ecf.crear(eid, v["id"], "32", actor="test")
        conn = db.connect()
        try:
            cur = conn.cursor()
            with self.assertRaises(Exception):
                db.execute(cur, "INSERT INTO factura_ecf (empresa_id,"
                                " emisor_rnc_norm, e_ncf, tipo, estado, modo,"
                                " creado_en, actualizado_en)"
                                " VALUES (%s,%s,%s,'32','borrador','mock',%s,%s)",
                           (eid, "555555555", c["e_ncf"], "x", "x"))
                conn.commit()
            conn.rollback()
        finally:
            conn.close()

    def test_reserva_concurrente_por_emisor_unica(self):
        eid = self._empresa("A", "666666666")
        nums = []
        errores = []

        def _reservar():
            try:
                conn = db.connect()
                try:
                    with db.locked(conn):
                        n = ecf._reservar_e_ncf(conn, "666666666", "31",
                                                "mock")
                        nums.append(n)
                finally:
                    conn.close()
            except Exception as exc:  # noqa: BLE001
                errores.append(exc)

        hilos = [threading.Thread(target=_reservar) for _ in range(8)]
        for h in hilos:
            h.start()
        for h in hilos:
            h.join()
        self.assertEqual(errores, [])
        self.assertEqual(len(set(nums)), 8)
        self.assertEqual(sorted(nums),
                         [f"E3100000000{i:02d}" for i in range(1, 9)])

    def test_mock_y_real_namespaces_separados(self):
        conn = db.connect()
        try:
            with db.locked(conn):
                m1 = ecf._reservar_e_ncf(conn, "777777777", "31", "mock")
                r1 = ecf._reservar_e_ncf(conn, "777777777", "31", "real")
                m2 = ecf._reservar_e_ncf(conn, "777777777", "31", "mock")
            self.assertEqual(m1, "E310000000001")
            self.assertEqual(r1, "E310000000001")
            self.assertEqual(m2, "E310000000002")
        finally:
            conn.close()

    def test_rnc_con_guiones_normaliza_igual(self):
        a = self._empresa("A", "888-888-888")
        v = self._venta_con_cliente(a)
        c = ecf.crear(a, v["id"], "32", actor="test")
        self.assertEqual(c["emisor_rnc_norm"], "888888888")


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestCaja(BaseFactura):
    def test_abrir_cerrar_con_diferencia(self):
        from factura_core import caja
        eid = self._empresa()
        u = auth_mod.crear_usuario(eid, "caja@t.do", "ClaveSegura1234",
                                   "cajero")
        t = caja.abrir(eid, u["id"], 1000)
        self.assertEqual(t["estado"], "abierta")
        self.assertEqual(str(t["monto_inicial"]), "1000")
        # doble apertura falla
        with self.assertRaises(ValueError):
            caja.abrir(eid, u["id"], 500)
        # exigir_abierta pasa
        self.assertIsNotNone(caja.exigir_abierta(eid))
        caja.registrar_movimiento(eid, "venta", 250, "venta P-000001",
                                  u["id"])
        cerr = caja.cerrar(eid, u["id"], 1200)
        self.assertEqual(cerr["estado"], "cerrada")
        # esperado = 1000 (apertura) + 250 = 1250; conteo 1200 -> dif -50
        self.assertEqual(str(cerr["diferencia"]), "-50")
        with self.assertRaises(ValueError):
            caja.exigir_abierta(eid)

    def test_cerrar_sin_turno_falla(self):
        from factura_core import caja
        eid = self._empresa()
        with self.assertRaises(ValueError):
            caja.cerrar(eid, 1, 100)


class TestDevoluciones(BaseFactura):
    def _venta(self, eid):
        p = inventario.crear(eid, "SKU-D", "Prod D", 100, 50, stock=10)
        return ventas.crear_venta(
            eid, "factura", None,
            [{"producto_id": p["id"], "nombre": "Prod D", "cantidad": 3,
              "precio": 100, "itbis_exento": True}],
            creada_por=None, metodo_pago="efectivo"), p["id"]

    def test_devolucion_restaura_stock_sin_tocar_venta(self):
        from factura_core import devoluciones
        eid = self._empresa()
        v, pid = self._venta(eid)
        self.assertEqual(str(inventario.obtener(eid, pid)["stock"]), "7")
        d = devoluciones.crear_devolucion(
            eid, v["id"], [{"producto_id": pid, "cantidad": 2}],
            motivo="defectuoso", creada_por=None)
        self.assertEqual(d["numero"], "D-000001")
        self.assertEqual(str(d["total"]), "200")
        # stock restaurado
        self.assertEqual(str(inventario.obtener(eid, pid)["stock"]), "9")
        # la venta original no se editó
        v2 = ventas.obtener(eid, v["id"])
        self.assertEqual(v2["total"], v["total"])
        self.assertEqual(v2["estado"], "pagada")

    def test_no_se_puede_devolver_mas_de_lo_vendido(self):
        from factura_core import devoluciones
        eid = self._empresa()
        v, pid = self._venta(eid)
        with self.assertRaises(ValueError):
            devoluciones.crear_devolucion(
                eid, v["id"], [{"producto_id": pid, "cantidad": 5}],
                motivo="x")
        # segunda devolución parcial sí, hasta agotar
        devoluciones.crear_devolucion(
            eid, v["id"], [{"producto_id": pid, "cantidad": 2}],
            motivo="a")
        with self.assertRaises(ValueError):
            devoluciones.crear_devolucion(
                eid, v["id"], [{"producto_id": pid, "cantidad": 2}],
                motivo="b")  # solo quedaba 1

    def test_ajuste_manual_con_motivo(self):
        from factura_core import devoluciones
        eid = self._empresa()
        p = inventario.crear(eid, "SKU-A", "Prod A", 50, 20, stock=10)
        a = devoluciones.ajuste_manual(eid, p["id"], 25,
                                       motivo="conteo físico")
        self.assertEqual(str(a["stock_antes"]), "10")
        self.assertEqual(str(a["stock_despues"]), "25")
        self.assertEqual(str(inventario.obtener(eid, p["id"])["stock"]),
                         "25")
        with self.assertRaises(ValueError):
            devoluciones.ajuste_manual(eid, p["id"], -1, motivo="x")
        with self.assertRaises(ValueError):
            devoluciones.ajuste_manual(eid, p["id"], 5, motivo="")


class TestSucursales(BaseFactura):
    def test_crud_y_limite_del_plan(self):
        from factura_core import sucursales
        eid = self._empresa()
        # el plan básico permite 1 (la semilla 008 no corre en tests;
        # se crea aquí)
        s1 = sucursales.crear(eid, "Principal")
        self.assertEqual(s1["nombre"], "Principal")
        with self.assertRaises(ValueError) as ctx:
            sucursales.crear(eid, "Segunda")
        self.assertIn("límite del plan", str(ctx.exception))
        s1b = sucursales.actualizar(eid, s1["id"], "Principal Norte",
                                    activa=True)
        self.assertEqual(s1b["nombre"], "Principal Norte")
        # desactivar libera el cupo
        sucursales.actualizar(eid, s1["id"], "Principal", activa=False)
        s2 = sucursales.crear(eid, "Segunda")
        self.assertTrue(s2["id"] != s1["id"])


class TestSaas(BaseFactura):
    def test_limite_usuarios_por_plan(self):
        from factura_core import saas
        eid = self._empresa()  # plan basico: 1 usuario
        auth_mod.crear_usuario(eid, "u1@t.do", "ClaveSegura1234", "admin")
        with self.assertRaises(ValueError) as ctx:
            saas.verificar_limite_usuarios(eid)
        self.assertIn("límite del plan", str(ctx.exception))

    def test_uso_actual_estructura(self):
        from factura_core import saas
        eid = self._empresa()
        u = saas.uso_actual(eid)
        self.assertEqual(u["plan"], "basico")
        self.assertEqual(u["usuarios"]["limite"], 1)
        self.assertEqual(u["sucursales"]["limite"], 1)
        self.assertIn("documentos_mes", u)

    def test_registro_publico_crea_empresa_con_trial(self):
        from factura_auth import registro as reg_mod
        from factura_core import saas
        res = reg_mod.registrar_empresa("Mi Colmado", "130123456",
                                        "809-555-0000", "Calle 1",
                                        "admin@micoldamo.do",
                                        "ClaveSegura1234")
        eid = res["empresa_id"]
        emp = saas.obtener_empresa(eid)
        self.assertEqual(emp["estado"], "activa")
        self.assertEqual(emp["plan"], "basico")
        self.assertEqual(saas.trial_dias_restantes(emp), config.TRIAL_DIAS)
        self.assertFalse(saas.bloqueada_por_trial(emp))
        # sucursal Principal creada
        from factura_core import sucursales
        self.assertEqual(len(sucursales.listar(eid)), 1)
        # email duplicado falla
        with self.assertRaises(ValueError):
            reg_mod.registrar_empresa("Otra", "130654321", "", "",
                                      "admin@micoldamo.do",
                                      "ClaveSegura1234")

    def test_trial_vencido_bloquea(self):
        from factura_core import saas
        from datetime import date, timedelta
        eid = self._empresa()
        ayer = (date.today() - timedelta(days=1)).isoformat()
        conn = db.connect()
        try:
            cur = conn.cursor()
            with db.tx(conn):
                db.execute(cur, "UPDATE factura_empresas SET trial_hasta=%s"
                                " WHERE id=%s", (ayer, eid))
        finally:
            conn.close()
        emp = saas.obtener_empresa(eid)
        self.assertTrue(saas.bloqueada_por_trial(emp))
        self.assertLess(saas.trial_dias_restantes(emp), 0)

    def test_reset_token_un_solo_uso(self):
        from factura_core import saas
        eid = self._empresa()
        u = auth_mod.crear_usuario(eid, "rec@t.do", "ClaveSegura1234",
                                   "admin")
        token = saas.crear_token_reset("rec@t.do")
        self.assertTrue(token)
        u2 = saas.consumir_token_reset(token)
        self.assertEqual(u2["id"], u["id"])
        # segundo uso falla
        self.assertIsNone(saas.consumir_token_reset(token))
        self.assertIsNone(saas.consumir_token_reset("inexistente"))

    def test_superadmin_suspende_y_login_bloquea(self):
        from factura_core import saas
        eid = self._empresa()
        auth_mod.crear_usuario(eid, "adm@t.do", "ClaveSegura1234", "admin")
        saas.cambiar_estado(eid, "suspendida")
        emp = saas.obtener_empresa(eid)
        self.assertEqual(emp["estado"], "suspendida")
        self.assertTrue(saas.bloqueada_por_trial(emp))
        saas.cambiar_estado(eid, "activa")
        self.assertEqual(saas.obtener_empresa(eid)["estado"], "activa")


class TestPermisos(BaseFactura):
    def test_matriz_de_roles(self):
        self.assertTrue(config.tiene_permiso("admin", "config"))
        self.assertTrue(config.tiene_permiso("admin", "cualquier_cosa"))
        self.assertTrue(config.tiene_permiso("cajero", "pos"))
        self.assertTrue(config.tiene_permiso("cajero", "caja"))
        self.assertFalse(config.tiene_permiso("cajero", "config"))
        self.assertFalse(config.tiene_permiso("cajero", "reportes"))
        self.assertFalse(config.tiene_permiso("cajero", "usuarios"))
        self.assertTrue(config.tiene_permiso("contador", "reportes"))
        self.assertTrue(config.tiene_permiso("contador", "auditoria"))
        self.assertFalse(config.tiene_permiso("contador", "caja"))
        self.assertFalse(config.tiene_permiso("contador", "config"))
        self.assertFalse(config.tiene_permiso("contador", "pos"))
        self.assertIn("contador", config.ROLES)

    def test_gestion_usuarios_nuevas_funciones(self):
        eid = self._empresa()
        u = auth_mod.crear_usuario(eid, "w@t.do", "ClaveSegura1234",
                                   "cajero")
        u2 = auth_mod.cambiar_rol(eid, u["id"], "contador")
        self.assertEqual(u2["rol"], "contador")
        auth_mod.activar_desactivar(eid, u["id"], False)
        self.assertIsNone(auth_mod.autenticar("w@t.do", "ClaveSegura1234"))
        auth_mod.activar_desactivar(eid, u["id"], True)
        self.assertIsNotNone(auth_mod.autenticar("w@t.do",
                                                 "ClaveSegura1234"))


class TestImportacion(BaseFactura):
    def test_previsualizar_clientes_valida_rnc(self):
        from factura_core import importacion
        prev = importacion.previsualizar_clientes(
            "nombre,rnc,telefono\nJuan,130123456,809-1\nMala,ABC,809-2")
        self.assertEqual(len(prev["filas"]), 1)
        self.assertEqual(len(prev["errores"]), 1)

    def test_confirmar_clientes(self):
        from factura_core import importacion
        eid = self._empresa()
        prev = importacion.previsualizar_clientes(
            "nombre,rnc\nAna,130123456\nLuis,")
        res = importacion.confirmar_clientes(eid, prev["filas"])
        self.assertEqual(res["creados"], 2)
        self.assertEqual(len(clientes.listar(eid)), 2)

    def test_previsualizar_productos_valida_precio(self):
        from factura_core import importacion
        prev = importacion.previsualizar_productos(
            "sku,nombre,precio,stock\nA1,Arroz,68,10\nA2,Mal,-5,3")
        self.assertEqual(len(prev["filas"]), 1)
        self.assertEqual(len(prev["errores"]), 1)


class TestAuditoria(BaseFactura):
    def test_registrar_y_listar(self):
        from factura_auditoria import registrar, listar
        eid = self._empresa()
        registrar(eid, 7, "login", detalle={"email": "a@b.do"}, ip="1.2.3.4")
        registrar(eid, 7, "venta_creada", "venta", 3, {"total": "100"})
        evs = listar(eid)
        self.assertEqual(len(evs), 2)
        self.assertEqual(evs[0]["accion"], "venta_creada")
        self.assertEqual(evs[1]["accion"], "login")
        solo_login = listar(eid, accion="login")
        self.assertEqual(len(solo_login), 1)
        self.assertEqual(listar(99999), [])


class TestExport(BaseFactura):
    def test_csv_y_spreadsheetml(self):
        from factura_web import export as ex
        cols = ["Nombre", "Total"]
        filas = [["Arroz", "68.00"], ["Ñoño, Jr.", "100"]]
        csv_b = ex.a_csv(cols, filas)
        self.assertIn(b"Nombre,Total", csv_b)
        self.assertIn("Ñoño".encode("utf-8"), csv_b)
        xls = ex.a_spreadsheetml("Ventas", cols, filas)
        self.assertIn(b"<Workbook", xls)
        self.assertIn(b"Arroz", xls)
        # número detectado como Number
        self.assertIn(b'ss:Type="Number"', xls)

    def test_filas_ventas(self):
        from factura_web import export as ex
        eid = self._empresa()
        p = inventario.crear(eid, "SKU-X", "Prod X", 100, 50, stock=5)
        ventas.crear_venta(eid, "factura", None,
                           [{"producto_id": p["id"], "nombre": "Prod X",
                             "cantidad": 1, "precio": 100,
                             "itbis_exento": True}],
                           creada_por=None, metodo_pago="efectivo")
        cols, filas = ex.filas_ventas(ventas.listar(eid))
        self.assertEqual(len(cols), 9)
        self.assertEqual(len(filas), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
