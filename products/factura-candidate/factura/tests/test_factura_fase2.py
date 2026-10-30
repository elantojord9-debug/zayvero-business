#!/usr/bin/env python3
"""Tests FASE 2 de TALENTO (unittest, stdlib).

Cubre: gastos/ingresos, proveedores, servicios (sin stock), alertas de
inventario por mínimo configurable, CxC vencidas y reportes nuevos
(gastos por categoría + estado de resultados).

Uso:
    python -m pytest tests/   # con el venv del proyecto activado
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE)

import config  # noqa: E402
from factura_persist import db  # noqa: E402
from factura_core import (clientes, gastos as gastos_mod, inventario, pagos,
                          proveedores as prov_mod, reportes, ventas)  # noqa: E402


def _ahora():
    return datetime.now(timezone.utc).isoformat()


class BaseFactura(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="factura-f2-test-")
        config.SQLITE_PATH = os.path.join(self.tmp, "test.db")
        db.reset_backend_cache()
        assert db.backend() == "sqlite"
        db.migrate()

    def tearDown(self):
        db.reset_backend_cache()
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _empresa(self, nombre="Empresa F2", rnc="111222333"):
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


class TestGastos(BaseFactura):
    def test_crud_gasto_completo(self):
        eid = self._empresa()
        g = gastos_mod.crear(eid, "gasto", "2026-10-05", "alquiler",
                             "Alquiler de octubre", "25000", "transferencia",
                             comprobante="REC-1")
        self.assertEqual(g["categoria"], "alquiler")
        self.assertEqual(g["tipo"], "gasto")
        self.assertEqual(str(g["monto"]), "25000")
        self.assertEqual(gastos_mod.total(eid, "gasto"), Decimal("25000"))
        # actualizar
        g2 = gastos_mod.actualizar(eid, "gasto", g["id"], "2026-10-06",
                                   "nómina", "Sueldo", "30000")
        self.assertEqual(g2["categoria"], "nómina")
        self.assertEqual(str(g2["monto"]), "30000")
        # eliminar
        gastos_mod.eliminar(eid, "gasto", g["id"])
        self.assertIsNone(gastos_mod.obtener(eid, "gasto", g["id"]))
        self.assertEqual(gastos_mod.total(eid, "gasto"), Decimal("0"))

    def test_validaciones(self):
        eid = self._empresa()
        with self.assertRaises(ValueError):
            gastos_mod.crear(eid, "gasto", "2026-10-05", "nave-espacial",
                             "x", "100")
        with self.assertRaises(ValueError):
            gastos_mod.crear(eid, "gasto", "2026-10-05", "alquiler",
                             "x", "-100")
        with self.assertRaises(ValueError):
            gastos_mod.crear(eid, "gasto", "05/10/2026", "alquiler",
                             "x", "100")
        with self.assertRaises(ValueError):
            gastos_mod.crear(eid, "gasto", "2026-10-05", "alquiler", "",
                             "100")
        with self.assertRaises(ValueError):
            gastos_mod.crear(eid, "gasto", "2026-10-05", "alquiler",
                             "x", "100", metodo="bitcoin")

    def test_ingreso_no_venta_sin_proveedor(self):
        eid = self._empresa()
        i = gastos_mod.crear(eid, "ingreso", "2026-10-05", "otros ingresos",
                             "Reparación cobrada", "1500")
        self.assertEqual(i["tipo"], "ingreso")
        self.assertEqual(gastos_mod.total(eid, "ingreso"), Decimal("1500"))
        # un ingreso no puede llevar proveedor
        p = prov_mod.crear(eid, "Suplidor X")
        with self.assertRaises(ValueError):
            gastos_mod.crear(eid, "ingreso", "2026-10-05", "otros ingresos",
                             "x", "100", proveedor_id=p["id"])

    def test_gasto_enlazado_a_proveedor_y_sucursal(self):
        eid = self._empresa()
        from factura_core import sucursales
        s = sucursales.crear(eid, "Principal")
        p = prov_mod.crear(eid, "Suplidor X")
        g = gastos_mod.crear(eid, "gasto", "2026-10-05", "mercancía",
                             "Compra de arroz", "5000", sucursal_id=s["id"],
                             proveedor_id=p["id"])
        self.assertEqual(g["proveedor_id"], p["id"])
        self.assertEqual(g["sucursal_id"], s["id"])
        hist = prov_mod.gastos_de_proveedor(eid, p["id"])
        self.assertEqual(len(hist), 1)
        self.assertEqual(hist[0]["id"], g["id"])
        # proveedor de otra empresa no vale
        b = self._empresa("B", "222222222")
        pb = prov_mod.crear(b, "Otro")
        with self.assertRaises(ValueError):
            gastos_mod.crear(eid, "gasto", "2026-10-05", "mercancía", "x",
                             "100", proveedor_id=pb["id"])

    def test_aislamiento_por_empresa(self):
        a = self._empresa("A", "111111111")
        b = self._empresa("B", "222222222")
        gastos_mod.crear(a, "gasto", "2026-10-05", "alquiler", "Renta",
                         "1000")
        self.assertEqual(len(gastos_mod.listar(b, "gasto")), 1 * 0)
        self.assertEqual(gastos_mod.total(b, "gasto"), Decimal("0"))
        self.assertEqual(len(gastos_mod.listar(a, "gasto")), 1)

    def test_filtros_y_por_categoria(self):
        eid = self._empresa()
        gastos_mod.crear(eid, "gasto", "2026-10-01", "alquiler", "Renta",
                         "1000")
        gastos_mod.crear(eid, "gasto", "2026-10-02", "alquiler", "Renta 2",
                         "2000")
        gastos_mod.crear(eid, "gasto", "2026-10-02", "nómina", "Sueldos",
                         "5000")
        pc = gastos_mod.por_categoria(eid, "gasto", "2026-10-01",
                                      "2026-10-01")
        self.assertEqual(len(pc), 1)
        self.assertEqual(pc[0]["categoria"], "alquiler")
        self.assertEqual(Decimal(pc[0]["total"]), Decimal("1000"))
        solo_nom = gastos_mod.listar(eid, "gasto", categoria="nómina")
        self.assertEqual(len(solo_nom), 1)


class TestProveedores(BaseFactura):
    def test_crud(self):
        eid = self._empresa()
        p = prov_mod.crear(eid, "Distribuidora Este", "130123456",
                           "809-555-0101", "contacto@este.do",
                           "Av. 1", balance="12000")
        self.assertEqual(p["rnc"], "130123456")
        self.assertEqual(str(p["balance"]), "12000")
        p2 = prov_mod.actualizar(eid, p["id"], "Distribuidora Este",
                                 "130123456", balance="8000")
        self.assertEqual(str(p2["balance"]), "8000")
        self.assertEqual(len(prov_mod.listar(eid)), 1)
        prov_mod.eliminar(eid, p["id"])  # baja lógica
        self.assertEqual(len(prov_mod.listar(eid)), 0)
        self.assertEqual(len(prov_mod.listar(eid, solo_activos=False)), 1)

    def test_validaciones(self):
        eid = self._empresa()
        with self.assertRaises(ValueError):
            prov_mod.crear(eid, "")
        with self.assertRaises(ValueError):
            prov_mod.crear(eid, "X", rnc="ABC")
        with self.assertRaises(ValueError):
            prov_mod.crear(eid, "X", email="sin-arroba")
        # rnc opcional: vacío permitido
        p = prov_mod.crear(eid, "Sin RNC")
        self.assertEqual(p["rnc"], "")

    def test_aislamiento(self):
        a = self._empresa("A", "111111111")
        b = self._empresa("B", "222222222")
        prov_mod.crear(a, "Prov A")
        self.assertEqual(len(prov_mod.listar(b)), 0)
        self.assertIsNone(prov_mod.obtener(b, 1))


class TestServicios(BaseFactura):
    def _items_mixtos(self, eid):
        prod = inventario.crear(eid, "SKU-P", "Arroz", 100, 60, stock=10)
        serv = inventario.crear(eid, "SRV-1", "Instalación", 500, 0,
                                tipo="servicio")
        return prod, serv

    def test_servicio_no_tiene_stock(self):
        eid = self._empresa()
        s = inventario.crear(eid, "SRV-1", "Instalación", 500, 0,
                             tipo="servicio", stock=99)
        self.assertEqual(str(s["stock"]), "0")
        self.assertEqual(s["tipo"], "servicio")
        with self.assertRaises(ValueError):
            inventario.crear(eid, "X", "Malo", 10, tipo="paquete")

    def test_venta_mixta_solo_descuenta_producto(self):
        eid = self._empresa()
        prod, serv = self._items_mixtos(eid)
        v = ventas.crear_venta(
            eid, "factura", None,
            [{"producto_id": prod["id"], "nombre": "Arroz", "cantidad": 2,
              "precio": 100, "itbis_exento": True},
             {"producto_id": serv["id"], "nombre": "Instalación",
              "cantidad": 1, "precio": 500, "itbis_exento": True}],
            creada_por=None)
        self.assertEqual(str(inventario.obtener(eid, prod["id"])["stock"]),
                         "8")
        self.assertEqual(str(inventario.obtener(eid, serv["id"])["stock"]),
                         "0")
        # la venta sí cobra el servicio
        self.assertEqual(Decimal(v["total"]), Decimal("700"))
        # anular devuelve solo el producto
        ventas.anular(eid, v["id"])
        self.assertEqual(str(inventario.obtener(eid, prod["id"])["stock"]),
                         "10")
        self.assertEqual(str(inventario.obtener(eid, serv["id"])["stock"]),
                         "0")

    def test_devolucion_de_servicio_no_mueve_stock(self):
        from factura_core import devoluciones
        eid = self._empresa()
        prod, serv = self._items_mixtos(eid)
        v = ventas.crear_venta(
            eid, "factura", None,
            [{"producto_id": serv["id"], "nombre": "Instalación",
              "cantidad": 1, "precio": 500, "itbis_exento": True}],
            creada_por=None, metodo_pago="efectivo")
        d = devoluciones.crear_devolucion(
            eid, v["id"], [{"producto_id": serv["id"], "cantidad": 1}],
            motivo="no se realizó", creada_por=None)
        self.assertEqual(str(d["total"]), "500")
        self.assertEqual(str(inventario.obtener(eid, serv["id"])["stock"]),
                         "0")

    def test_stock_bajo_excluye_servicios(self):
        eid = self._empresa()
        inventario.crear(eid, "SRV-1", "Instalación", 500, 0,
                         tipo="servicio")
        self.assertEqual(inventario.stock_bajo(eid), [])


class TestAlertasInventario(BaseFactura):
    def test_minimo_por_producto(self):
        eid = self._empresa()
        a = inventario.crear(eid, "A", "Prod A", 10, 5, stock=8,
                             stock_minimo=10)   # 8 <= 10 -> alerta
        inventario.crear(eid, "B", "Prod B", 10, 5, stock=8,
                         stock_minimo=5)        # 8 > 5 -> sin alerta
        alertas = inventario.stock_bajo(eid)
        self.assertEqual(len(alertas), 1)
        self.assertEqual(alertas[0]["id"], a["id"])
        self.assertEqual(alertas[0]["estado"], "bajo")

    def test_agotado(self):
        eid = self._empresa()
        p = inventario.crear(eid, "A", "Prod A", 10, 5, stock=0)
        alertas = inventario.stock_bajo(eid)
        self.assertEqual(len(alertas), 1)
        self.assertEqual(alertas[0]["id"], p["id"])
        self.assertEqual(alertas[0]["estado"], "agotado")

    def test_umbral_global_opcional(self):
        eid = self._empresa()
        inventario.crear(eid, "A", "Prod A", 10, 5, stock=4,
                         stock_minimo=100)
        # con umbral explícito 5: 4 <= 5 -> alerta
        self.assertEqual(len(inventario.stock_bajo(eid, umbral=5)), 1)
        # sin umbral: usa el mínimo del producto (100) -> alerta igual
        self.assertEqual(len(inventario.stock_bajo(eid)), 1)

    def test_actualizar_minimo(self):
        eid = self._empresa()
        p = inventario.crear(eid, "A", "Prod A", 10, 5, stock=8,
                             stock_minimo=5)
        self.assertEqual(inventario.stock_bajo(eid), [])
        inventario.actualizar(eid, p["id"], "A", "Prod A", 10, 5,
                              stock=8, stock_minimo=10)
        self.assertEqual(len(inventario.stock_bajo(eid)), 1)


class TestCuentasVencidas(BaseFactura):
    _n = 0

    def _venta_credito(self, eid, vencimiento=None, cliente_id=None):
        TestCuentasVencidas._n += 1
        n = TestCuentasVencidas._n
        p = inventario.crear(eid, f"SKU-V{n}", f"Prod V{n}", 100, 50,
                             stock=50)
        return ventas.crear_venta(
            eid, "factura", cliente_id,
            [{"producto_id": p["id"], "nombre": f"Prod V{n}", "cantidad": 1,
              "precio": 100, "itbis_exento": True}],
            creada_por=None, vencimiento=vencimiento)

    def test_vencida_aparece_y_pagada_no(self):
        eid = self._empresa()
        ayer = (date.today() - timedelta(days=1)).isoformat()
        manana = (date.today() + timedelta(days=1)).isoformat()
        v1 = self._venta_credito(eid, vencimiento=ayer)
        self._venta_credito(eid, vencimiento=manana)
        self._venta_credito(eid)  # sin vencimiento
        v4 = self._venta_credito(eid, vencimiento=ayer)
        pagos.registrar_pago(eid, v4["id"], 100, metodo="efectivo")
        venc = pagos.cxc_vencidas(eid)
        self.assertEqual(len(venc), 1)
        self.assertEqual(venc[0]["id"], v1["id"])
        self.assertGreaterEqual(venc[0]["dias_vencidos"], 1)
        self.assertEqual(pagos.total_vencido(eid), Decimal("100"))

    def test_resumen_por_cliente(self):
        eid = self._empresa()
        ayer = (date.today() - timedelta(days=1)).isoformat()
        c1 = clientes.crear(eid, "Cliente Uno", "111111111")
        c2 = clientes.crear(eid, "Cliente Dos", "222222222")
        self._venta_credito(eid, vencimiento=ayer, cliente_id=c1["id"])
        self._venta_credito(eid, vencimiento=ayer, cliente_id=c1["id"])
        self._venta_credito(eid, vencimiento=ayer, cliente_id=c2["id"])
        res = pagos.resumen_vencidas_por_cliente(eid)
        self.assertEqual(len(res), 2)
        self.assertEqual(res[0]["cliente_nombre"], "Cliente Uno")
        self.assertEqual(res[0]["cantidad"], 2)
        self.assertEqual(Decimal(res[0]["total"]), Decimal("200"))

    def test_establecer_vencimiento(self):
        eid = self._empresa()
        v = self._venta_credito(eid)
        manana = (date.today() + timedelta(days=1)).isoformat()
        ventas.establecer_vencimiento(eid, v["id"], manana)
        self.assertEqual(
            ventas.obtener(eid, v["id"])["vencimiento"], manana)
        ventas.establecer_vencimiento(eid, v["id"], "")
        self.assertEqual(ventas.obtener(eid, v["id"])["vencimiento"], "")
        with self.assertRaises(ValueError):
            ventas.establecer_vencimiento(eid, v["id"], "no-fecha")
        # pagada ya no acepta vencimiento
        pagos.registrar_pago(eid, v["id"], 100)
        with self.assertRaises(ValueError):
            ventas.establecer_vencimiento(eid, v["id"], manana)

    def test_aislamiento(self):
        a = self._empresa("A", "111111111")
        b = self._empresa("B", "222222222")
        ayer = (date.today() - timedelta(days=1)).isoformat()
        self._venta_credito(a, vencimiento=ayer)
        self.assertEqual(pagos.cxc_vencidas(b), [])
        self.assertEqual(pagos.total_vencido(b), Decimal("0"))


class TestReportesFase2(BaseFactura):
    def test_gastos_por_categoria(self):
        eid = self._empresa()
        gastos_mod.crear(eid, "gasto", "2026-10-01", "alquiler", "Renta",
                         "1000")
        gastos_mod.crear(eid, "gasto", "2026-10-02", "alquiler", "Renta 2",
                         "2000")
        gastos_mod.crear(eid, "gasto", "2026-10-03", "nómina", "Sueldos",
                         "5000")
        gastos_mod.crear(eid, "ingreso", "2026-10-03", "otros ingresos",
                         "Extra", "700")
        r = reportes.gastos_por_categoria(eid)
        self.assertEqual(Decimal(r["total_gastos"]), Decimal("8000"))
        self.assertEqual(Decimal(r["total_ingresos_no_venta"]),
                         Decimal("700"))
        self.assertEqual(len(r["gastos"]), 2)
        # ordenado por total descendente: nómina primero
        self.assertEqual(r["gastos"][0]["categoria"], "nómina")
        # con período
        r2 = reportes.gastos_por_categoria(eid, "2026-10-01", "2026-10-02")
        self.assertEqual(Decimal(r2["total_gastos"]), Decimal("3000"))

    def test_estado_resultados(self):
        eid = self._empresa()
        p = inventario.crear(eid, "SKU-E", "Prod E", 100, 40, stock=50)
        ventas.crear_venta(
            eid, "factura", None,
            [{"producto_id": p["id"], "nombre": "Prod E", "cantidad": 2,
              "precio": 100, "itbis_exento": True}],
            creada_por=None, metodo_pago="efectivo")
        gastos_mod.crear(eid, "gasto", date.today().isoformat(), "alquiler",
                         "Renta", "50")
        gastos_mod.crear(eid, "ingreso", date.today().isoformat(),
                         "otros ingresos", "Extra", "30")
        er = reportes.estado_resultados(eid)
        self.assertEqual(Decimal(er["ventas"]), Decimal("200"))
        self.assertEqual(Decimal(er["costo_ventas"]), Decimal("80"))
        self.assertEqual(Decimal(er["utilidad_bruta"]), Decimal("120"))
        self.assertEqual(Decimal(er["otros_ingresos"]), Decimal("30"))
        self.assertEqual(Decimal(er["gastos"]), Decimal("50"))
        self.assertEqual(Decimal(er["utilidad_neta"]), Decimal("100"))
        self.assertEqual(er["cantidad_ventas"], 1)

    def test_export_filas(self):
        from factura_web import export as ex
        eid = self._empresa()
        g = gastos_mod.crear(eid, "gasto", "2026-10-05", "alquiler",
                             "Renta", "1000")
        cols, filas = ex.filas_gastos([g])
        self.assertEqual(len(cols), 7)
        self.assertEqual(filas[0][1], "Gasto")
        er = reportes.estado_resultados(eid)
        cols2, filas2 = ex.filas_estado_resultados(er)
        self.assertEqual(len(cols2), 2)
        self.assertTrue(any(f[0] == "= Utilidad neta" for f in filas2))
        csv_b = ex.a_csv(cols, filas)
        self.assertIn("Renta".encode("utf-8"), csv_b)


class TestPermisosFase2(BaseFactura):
    def test_matriz(self):
        self.assertTrue(config.tiene_permiso("admin", "gastos_gestion"))
        self.assertTrue(config.tiene_permiso("contador", "gastos_ver"))
        self.assertFalse(config.tiene_permiso("contador", "gastos_gestion"))
        self.assertFalse(config.tiene_permiso("cajero", "gastos_ver"))
        self.assertTrue(config.tiene_permiso("admin", "proveedores_gestion"))
        self.assertTrue(config.tiene_permiso("cajero", "proveedores_ver"))
        self.assertFalse(config.tiene_permiso("cajero",
                                              "proveedores_gestion"))
        self.assertTrue(config.tiene_permiso("contador", "proveedores_ver"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
