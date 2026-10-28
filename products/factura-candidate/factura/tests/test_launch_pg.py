"""Launch fault/concurrency cases against an explicitly isolated PG test DB."""
import multiprocessing as mp
import os
import signal
from decimal import Decimal
from unittest.mock import patch

from test_pg_real import PGBase
from factura_persist import db
from factura_core import inventario, ventas, caja, pagos


def _worker(dsn, eid, pid, key, queue):
    os.environ["FACTURA_DATABASE_URL"] = dsn
    db.reset_backend_cache()
    try:
        result = ventas.emitir_administrativa(eid, "pos", None,
            [{"producto_id": pid, "cantidad": "1"}], metodo_pago="efectivo",
            idempotencia=key)
        queue.put(("ok", result["id"]))
    except ValueError:
        queue.put(("rejected", None))
    except Exception as exc:
        queue.put(("error", type(exc).__name__))


def _crash(dsn, eid, pid):
    os.environ["FACTURA_DATABASE_URL"] = dsn
    db.reset_backend_cache()
    from factura_persist.uow import company_transaction
    with company_transaction(eid):
        ventas.crear_venta(eid, "factura", None,
            [{"producto_id": pid, "nombre": "Synthetic", "cantidad": 1, "precio": "100"}])
        os.kill(os.getpid(), signal.SIGKILL)


class TestLaunchPostgres(PGBase):
    def _run_four(self, stock, same_key):
        eid, uid, _ = self._empresa()
        product = self._producto(eid, stock=stock, precio=100)
        turn = caja.abrir(eid, uid, "0")
        ctx = mp.get_context("spawn")
        queue = ctx.Queue()
        workers = [ctx.Process(target=_worker,
            args=(os.environ["FACTURA_DATABASE_URL"], eid, product["id"],
                  "pg-same-key" if same_key else "pg-different-key-" + str(i), queue)) for i in range(4)]
        for worker in workers: worker.start()
        for worker in workers:
            worker.join(40)
            self.assertEqual(worker.exitcode, 0)
        results = [queue.get(timeout=3) for _ in workers]
        queue.close()
        queue.join_thread()
        return eid, product, turn, results

    def test_four_processes_same_key_have_one_sale_and_cash_entry(self):
        eid, product, turn, results = self._run_four(5, True)
        self.assertTrue(all(r[0] == "ok" for r in results), results)
        self.assertEqual(len({r[1] for r in results}), 1)
        self.assertEqual(len(ventas.listar(eid)), 1)
        self.assertEqual(Decimal(inventario.obtener(eid, product["id"])["stock"]), 4)
        self.assertEqual(caja.esperado(turn["id"], eid), Decimal("118"))

    def test_four_processes_cannot_oversell(self):
        eid, product, turn, results = self._run_four(2, False)
        self.assertEqual(sum(r[0] == "ok" for r in results), 2, results)
        self.assertEqual(sum(r[0] == "rejected" for r in results), 2, results)
        self.assertEqual(Decimal(inventario.obtener(eid, product["id"])["stock"]), 0)
        self.assertEqual(caja.esperado(turn["id"], eid), Decimal("236"))

    def test_pg_sigkill_rolls_back_all_effects(self):
        eid, uid, _ = self._empresa()
        product = self._producto(eid, stock=2, precio=100)
        worker = mp.get_context("spawn").Process(target=_crash,
            args=(os.environ["FACTURA_DATABASE_URL"], eid, product["id"]))
        worker.start()
        worker.join(40)
        self.assertEqual(worker.exitcode, -signal.SIGKILL)
        self.assertEqual(ventas.listar(eid), [])
        self.assertEqual(Decimal(inventario.obtener(eid, product["id"])["stock"]), 2)

    def test_pg_cash_failure_rolls_back_sale_payment_and_stock(self):
        eid, uid, _ = self._empresa()
        product = self._producto(eid, stock=2, precio=100)
        caja.abrir(eid, uid, 0)
        with patch.object(caja, "registrar_movimiento", side_effect=RuntimeError("injected")):
            with self.assertRaises(RuntimeError):
                ventas.emitir_administrativa(eid, "pos", None,
                    [{"producto_id": product["id"], "cantidad": "1"}],
                    metodo_pago="efectivo", idempotencia="pg-fault-key")
        self.assertEqual(ventas.listar(eid), [])
        self.assertEqual(Decimal(inventario.obtener(eid, product["id"])["stock"]), 2)
