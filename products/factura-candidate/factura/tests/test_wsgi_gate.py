"""HTTP security/replay checks through the production WSGI application."""
from io import BytesIO
import json
from unittest.mock import patch

from test_factura import BaseFactura
from factura_auth import auth
from factura_core import caja, inventario, ventas
from wsgi import application


class TestWsgiGate(BaseFactura):
    def setUp(self):
        super().setUp()
        self.eid = self._empresa()
        self.user = auth.crear_usuario(self.eid, "wsgi@test.local", "Synthetic-User-Password-123!", "admin")
        self.cookie = auth.crear_sesion(self.user)
        self.csrf = auth.csrf_token(auth.leer_sesion(self.cookie))
        self.product = inventario.crear(self.eid, "WSGI", "Producto", 100, stock=5)
        caja.abrir(self.eid, self.user["id"], 0)

    def request(self, path, body=None, cookie=True, csrf=True, key="wsgi-replay-key", extra=None):
        raw = json.dumps(body).encode() if body is not None else b""
        env = {"REQUEST_METHOD": "POST" if body is not None else "GET",
               "PATH_INFO": path, "QUERY_STRING": "", "REMOTE_ADDR": "127.0.0.1",
               "wsgi.input": BytesIO(raw), "CONTENT_LENGTH": str(len(raw)),
               "CONTENT_TYPE": "application/json"}
        if cookie:
            env["HTTP_COOKIE"] = "factura_sess=" + self.cookie
        if csrf:
            env["HTTP_X_CSRF_TOKEN"] = self.csrf
        if key:
            env["HTTP_IDEMPOTENCY_KEY"] = key
        env.update(extra or {})
        response = []
        data = b"".join(application(env, lambda status, headers: response.append((status, headers))))
        return int(response[0][0].split()[0]), data

    def payload(self, quantity=1):
        return {"items": [{"producto_id": self.product["id"], "cantidad": quantity}], "metodo_pago": "efectivo"}

    def test_unauthenticated_api_has_401(self):
        status, _ = self.request("/api/pos/cobrar", self.payload(), cookie=False)
        self.assertEqual(status, 401)

    def test_csrf_rejection_has_no_effect(self):
        status, _ = self.request("/api/pos/cobrar", self.payload(), csrf=False)
        self.assertEqual(status, 403)
        self.assertEqual(ventas.listar(self.eid), [])

    def test_accountant_cannot_sell(self):
        auth.cambiar_rol(self.eid, self.user["id"], "contador")
        status, _ = self.request("/api/pos/cobrar", self.payload())
        self.assertEqual(status, 403)
        self.assertEqual(ventas.listar(self.eid), [])

    def test_http_same_key_is_one_sale(self):
        first = self.request("/api/pos/cobrar", self.payload())
        second = self.request("/api/pos/cobrar", self.payload())
        self.assertEqual(first, second)
        self.assertEqual(first[0], 200)
        self.assertEqual(len(ventas.listar(self.eid)), 1)

    def test_http_same_key_with_other_payload_is_rejected(self):
        self.assertEqual(self.request("/api/pos/cobrar", self.payload())[0], 200)
        self.assertEqual(self.request("/api/pos/cobrar", self.payload(2))[0], 400)
        self.assertEqual(len(ventas.listar(self.eid)), 1)

    def test_http_financial_mutation_requires_key(self):
        status, _ = self.request("/api/pos/cobrar", self.payload(), key=None)
        self.assertEqual(status, 400)
        self.assertEqual(ventas.listar(self.eid), [])

    def test_oversized_body_rejected_before_read(self):
        status, _ = self.request("/api/pos/cobrar", {}, extra={"CONTENT_LENGTH": "3000000"})
        self.assertEqual(status, 413)

    def test_foreign_invoice_is_not_visible(self):
        other = self._empresa("Other", "222222222")
        sale = ventas.crear_venta(other, "factura", None,
                [{"nombre": "PRIVATE-COMPANY-B", "cantidad": 1, "precio": "100"}])
        status, html = self.request("/facturas/" + str(sale["id"]))
        self.assertEqual(status, 404)
        self.assertNotIn(b"PRIVATE-COMPANY-B", html)

    def test_backend_failure_does_not_leak_exception(self):
        with patch.object(ventas, "emitir_administrativa", side_effect=RuntimeError("synthetic-private-dsn")):
            status, body = self.request("/api/pos/cobrar", self.payload())
        self.assertEqual(status, 500)
        self.assertNotIn(b"synthetic-private-dsn", body)
