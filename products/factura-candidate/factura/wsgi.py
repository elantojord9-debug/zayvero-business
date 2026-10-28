"""WSGI bridge for the existing routes; no socket server in production.

The domain services and route permission checks remain shared with local
tests. Gunicorn owns HTTP parsing, concurrency, timeouts and request limits.
"""
from email.message import Message
from io import BytesIO
from types import SimpleNamespace
from http import HTTPStatus
import logging

from factura_web.server import Handler


class _Connection:
    def settimeout(self, seconds):
        pass  # The production WSGI server owns connection timeouts.


class Request(Handler):
    def __init__(self, environ):
        self.command = environ["REQUEST_METHOD"]
        self.path = environ.get("PATH_INFO", "/")
        if environ.get("QUERY_STRING"):
            self.path += "?" + environ["QUERY_STRING"]
        self.headers = Message()
        for key, value in environ.items():
            if key.startswith("HTTP_"):
                self.headers[key[5:].replace("_", "-")] = value
        for key in ("CONTENT_LENGTH", "CONTENT_TYPE"):
            if environ.get(key):
                self.headers[key.replace("_", "-")] = environ[key]
        self.rfile = environ["wsgi.input"]
        self.wfile = BytesIO()
        self.client_address = (environ.get("REMOTE_ADDR", "127.0.0.1"), 0)
        self.connection = _Connection()
        self.server = SimpleNamespace(server_name="factura", server_port=0)
        self.close_connection = True
        self.response_status = 200
        self.response_headers = []

    def send_response(self, code, message=None):
        self.response_status = code

    def send_header(self, name, value):
        # WSGI forbids hop-by-hop connection headers.
        if name.lower() not in ("connection", "transfer-encoding"):
            self.response_headers.append((str(name), str(value)))

    def end_headers(self):
        pass


def application(environ, start_response):
    req = Request(environ)
    try:
        if req.command == "GET":
            req.do_GET()
        elif req.command == "POST":
            req.do_POST()
        elif req.command == "HEAD":
            req.do_GET()
        else:
            req.send_response(405)
            req.send_header("Allow", "GET, HEAD, POST")
    except Exception as exc:
        # Deliberately avoid logging request bodies, DSNs and tokens.
        logging.getLogger(__name__).error("request_failed type=%s", type(exc).__name__)
        start_response("500 Internal Server Error", [("Content-Type", "application/json"), ("Cache-Control", "no-store")])
        return [b'{"ok":false,"error":"No se pudo confirmar la operacion. Reintente con la misma clave."}']
    body = req.wfile.getvalue()
    headers = req.response_headers
    present = {name.lower() for name, _ in headers}
    defaults = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin", "Cache-Control": "no-store"}
    for name, value in defaults.items():
        if name.lower() not in present:
            headers.append((name, value))
    if "content-length" not in present:
        headers.append(("Content-Length", str(len(body))))
    status = str(req.response_status) + " " + HTTPStatus(req.response_status).phrase
    start_response(status, headers)
    return [b"" if req.command == "HEAD" else body]
