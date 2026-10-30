"""Financial launch regressions: real transactions, subprocesses and faults."""
import os
import multiprocessing as mp
import sqlite3
import signal
import tempfile
import unittest
from decimal import Decimal
from pathlib import Path
from unittest.mock import patch

from test_factura import BaseFactura
from factura_persist import db
from factura_core import ventas, inventario, pagos, caja, devoluciones
from factura_web import paginas_nuevas as pages
import config


def _sell_worker(database, empresa, producto, kind, queue):
    config.SQLITE_PATH = database
    db.reset_backend_cache()
    try:
        v = ventas.crear_venta(empresa, kind, None,
                              [{"producto_id": producto, "nombre": "P",
                                "cantidad": 1, "precio": "100"}])
        queue.put(("ok", v["numero"]))
    except ValueError as exc:
        queue.put(("rejected", str(exc)))
    except Exception as exc:
        queue.put(("error", type(exc).__name__))


def _crash_worker(database, empresa, producto):
    config.SQLITE_PATH = database
    db.reset_backend_cache()
    from factura_persist.uow import company_transaction
    with company_transaction(empresa):
        ventas.crear_venta(empresa, "factura", None,
                          [{"producto_id": producto, "nombre": "P",
                            "cantidad": 1, "precio": "100"}])
        os.kill(os.getpid(), signal.SIGKILL)


class TestLaunchGate(BaseFactura):
    def setUp(self):
        super().setUp()
        self.eid = self._empresa()
        self.product = inventario.crear(self.eid, "P", "Producto", 100, stock=10)
        self.items = [{"producto_id": self.product["id"], "nombre": "Producto",
                       "precio": "100", "cantidad": "1"}]

    def _query(self, sql, args=()):
        c = db.connect()
        try:
            return db.fetchall(c.cursor(), sql, args)
        finally:
            c.close()

    def _pos(self, key="pos-launch-key-1", items=None):
        return ventas.emitir_administrativa(self.eid, "pos", None,
                    items or self.items, metodo_pago="efectivo", idempotencia=key)

    def test_cash_failure_rolls_back_sale_payment_stock_and_audit(self):
        caja.abrir(self.eid, None, "0")
        count = len(self._query("SELECT * FROM factura_auditoria"))
        with patch.object(caja, "registrar_movimiento", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                self._pos()
        self.assertEqual(ventas.listar(self.eid), [])
        self.assertEqual(len(self._query("SELECT * FROM factura_pagos")), 0)
        self.assertEqual(Decimal(inventario.obtener(self.eid, self.product["id"])["stock"]), 10)
        self.assertEqual(len(self._query("SELECT * FROM factura_auditoria")), count)

    def test_critical_audit_failure_rolls_back_business_operation(self):
        with patch("factura_auditoria.registrar", side_effect=RuntimeError("audit down")):
            with self.assertRaises(RuntimeError):
                ventas.crear_venta(self.eid, "factura", None, self.items)
        self.assertEqual(ventas.listar(self.eid), [])
        self.assertEqual(Decimal(inventario.obtener(self.eid, self.product["id"])["stock"]), 10)

    def test_commit_then_retry_replays_all_pos_effects_once(self):
        turn = caja.abrir(self.eid, None, 0)
        first = self._pos()
        db.reset_backend_cache()  # Connections were closed, replay is DB-backed.
        second = self._pos()
        self.assertEqual(first, second)
        self.assertEqual(len(ventas.listar(self.eid)), 1)
        self.assertEqual(len(pagos.pagos_de_venta(self.eid, first["id"])), 1)
        self.assertEqual(caja.esperado(turn["id"], self.eid), Decimal("118"))

    def test_same_key_different_payload_is_rejected(self):
        caja.abrir(self.eid, None, 0)
        self._pos()
        with self.assertRaisesRegex(ValueError, "otros datos"):
            self._pos(items=[dict(self.items[0], cantidad="2")])
        self.assertEqual(len(ventas.listar(self.eid)), 1)

    def test_pos_uses_server_prices(self):
        caja.abrir(self.eid, None, 0)
        sale = self._pos(items=[dict(self.items[0], precio="0.01", itbis_exento=True)])
        self.assertEqual(Decimal(sale["total"]), Decimal("118"))

    def test_cross_company_product_is_rejected(self):
        other = self._empresa("B", "222222222")
        with self.assertRaises(ValueError):
            ventas.emitir_administrativa(other, "factura", None, self.items,
                                         idempotencia="other-company-key")
        self.assertEqual(ventas.listar(other), [])

    def test_nonfinite_numbers_rejected(self):
        for value in ("NaN", "Infinity", "-Infinity"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    ventas.crear_venta(self.eid, "factura", None,
                                       [dict(self.items[0], cantidad=value)])
        self.assertEqual(ventas.listar(self.eid), [])

    def test_duplicate_return_lines_cannot_exceed_sold_quantity(self):
        sale = ventas.crear_venta(self.eid, "factura", None, self.items)
        with self.assertRaises(ValueError):
            devoluciones.crear_devolucion(self.eid, sale["id"],
                [{"producto_id": self.product["id"], "cantidad": 1}] * 2,
                "prueba")
        self.assertEqual(Decimal(inventario.obtener(self.eid, self.product["id"])["stock"]), 9)

    def test_converting_failure_keeps_quote_and_stock_unchanged(self):
        quote = ventas.crear_cotizacion(self.eid, None, self.items)
        original = db.execute
        def fail_quote(cur, sql, params=()):
            if "UPDATE factura_cotizaciones SET estado='convertida'" in sql:
                raise RuntimeError("injected")
            return original(cur, sql, params)
        with patch.object(db, "execute", side_effect=fail_quote):
            with self.assertRaises(RuntimeError):
                ventas.convertir_cotizacion(self.eid, quote["id"])
        self.assertEqual(ventas.listar(self.eid), [])
        self.assertEqual(ventas.obtener_cotizacion(self.eid, quote["id"])["estado"], "vigente")

    def test_no_cancellation_after_return_prevents_double_stock_credit(self):
        sale = ventas.crear_venta(self.eid, "factura", None, self.items)
        devoluciones.crear_devolucion(self.eid, sale["id"],
            [{"producto_id": self.product["id"], "cantidad": 1}], "prueba")
        with self.assertRaises(ValueError):
            ventas.anular(self.eid, sale["id"])
        self.assertEqual(Decimal(inventario.obtener(self.eid, self.product["id"])["stock"]), 10)

    def test_four_processes_mixed_sale_types_cannot_oversell(self):
        devoluciones.ajuste_manual(self.eid, self.product["id"], 2, "test")
        ctx = mp.get_context("spawn")
        q = ctx.Queue()
        workers = [ctx.Process(target=_sell_worker,
            args=(config.SQLITE_PATH, self.eid, self.product["id"],
                  "pos" if i % 2 else "factura", q)) for i in range(4)]
        for w in workers: w.start()
        for w in workers:
            w.join(30)
            self.assertEqual(w.exitcode, 0)
        results = [q.get(timeout=3) for _ in workers]
        self.assertEqual(sum(r[0] == "ok" for r in results), 2, results)
        self.assertEqual(sum(r[0] == "rejected" for r in results), 2, results)
        self.assertEqual(Decimal(inventario.obtener(self.eid, self.product["id"])["stock"]), 0)
        q.close()
        q.join_thread()

    def test_sigkill_before_commit_leaves_no_partial_business_data(self):
        ctx = mp.get_context("spawn")
        worker = ctx.Process(target=_crash_worker,
            args=(config.SQLITE_PATH, self.eid, self.product["id"]))
        worker.start()
        worker.join(30)
        self.assertEqual(worker.exitcode, -signal.SIGKILL)
        self.assertEqual(ventas.listar(self.eid), [])
        self.assertEqual(Decimal(inventario.obtener(self.eid, self.product["id"])["stock"]), 10)

    def test_public_prices_not_rendered(self):
        from factura_web import paginas_nuevas2 as private_pages
        from factura_core import saas
        html = pages.pagina_planes() + private_pages.pagina_suscripcion({"rol": "admin", "csrf": "synthetic"}, saas.uso_actual(self.eid))
        for value in ("790", "1,490", "2,490", "1490", "2490"):
            self.assertNotIn(value, html)

    def test_partial_and_full_returns_reconcile_credit_and_refund(self):
        turn = caja.abrir(self.eid, None, 0)
        sale = self._pos()
        note = devoluciones.crear_devolucion(self.eid, sale["id"],
            [{"producto_id": self.product["id"], "cantidad": 1}], "defecto",
            idempotencia="return-launch-key")
        self.assertEqual(Decimal(note["total"]), Decimal("118"))
        self.assertEqual(pagos.balance(self.eid, sale["id"]), Decimal("-118"))
        refund = devoluciones.reembolsar(self.eid, note["id"], "118", idempotencia="refund-launch-key")
        replay = devoluciones.reembolsar(self.eid, note["id"], "118", idempotencia="refund-launch-key")
        self.assertEqual(refund, replay)
        self.assertEqual(pagos.balance(self.eid, sale["id"]), 0)
        from factura_core import reportes
        self.assertEqual(Decimal(reportes.estado_resultados(self.eid)["ventas"]), 0)
        self.assertEqual(Decimal(reportes.resumen_itbis(self.eid)["debito"]), 0)
        self.assertEqual(caja.esperado(turn["id"], self.eid), 0)
        with self.assertRaises(ValueError):
            devoluciones.reembolsar(self.eid, note["id"], "1", idempotencia="other-refund-key")

    def test_credit_sale_return_reduces_receivable(self):
        sale = ventas.crear_venta(self.eid, "factura", None, self.items)
        devoluciones.crear_devolucion(self.eid, sale["id"],
            [{"producto_id": self.product["id"], "cantidad": 1}], "defecto")
        self.assertEqual(pagos.balance(self.eid, sale["id"]), 0)
        with self.assertRaises(ValueError):
            pagos.registrar_pago(self.eid, sale["id"], "1")

    def test_backup_restore_in_separate_database_matches_business_data(self):
        from scripts import respaldo
        ventas.crear_venta(self.eid, "factura", None, self.items)
        with patch.object(respaldo, "RESPALDOS_DIR", os.path.join(self.tmp, "external")):
            result = respaldo.crear("gate")
            path = Path(respaldo.RESPALDOS_DIR) / result["archivo"]
            self.assertEqual(respaldo.verificar(str(path))["verificacion"], "OK")
            restored = sqlite3.connect(os.path.join(self.tmp, "restored.db"))
            original = sqlite3.connect(str(path))
            original.backup(restored)
            try:
                for table in ("factura_ventas", "factura_productos", "factura_auditoria"):
                    self.assertEqual(restored.execute("SELECT * FROM " + table).fetchall(),
                                     original.execute("SELECT * FROM " + table).fetchall())
            finally:
                original.close()
                restored.close()
            with open(path, "ab") as out:
                out.write(b"corruption")
            with self.assertRaises(ValueError):
                respaldo.verificar(str(path))

    def test_registration_failure_never_leaves_orphan_company(self):
        from factura_auth import registro, auth
        before = len(self._query("SELECT * FROM factura_empresas"))
        with patch.object(auth, "crear_usuario", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                registro.registrar_empresa("Synthetic", "555666777", "", "", "synthetic@test.local", "Valid-Test-Pass-123!")
        self.assertEqual(len(self._query("SELECT * FROM factura_empresas")), before)

    def test_csv_formula_injection_is_neutralized(self):
        from factura_web.export import a_csv
        result = a_csv(["nombre", "monto"], [["=HYPERLINK(\"evil\")", "-100"]]).decode()
        self.assertIn("'=HYPERLINK", result)
        self.assertIn(",-100", result)

    def test_cloud_fiscal_creation_is_blocked(self):
        from factura_ecf import service
        with patch.dict(os.environ, {"FACTURA_ENV": "production"}):
            with self.assertRaisesRegex(ValueError, "SIN VALOR FISCAL"):
                service.crear(self.eid, 1, "32", "test")
