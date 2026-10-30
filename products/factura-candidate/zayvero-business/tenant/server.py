"""FASE 6A — API mínima de autenticación (solo stdlib, para pruebas).

Endpoints:
  POST /api/login        {email, password} → cookie de sesión (HttpOnly)
  POST /api/logout       → revoca la sesión
  GET  /api/me          → usuario autenticado (sin secretos)
  GET  /api/company     → empresa actual
  GET  /api/permissions → permisos del rol actual
  GET  /                → página mínima de prueba (login / estado / logout)

NO expone endpoints de findings, predictions ni Advisor (fase 6A).
"""

from __future__ import annotations

import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import audit as audit_mod
from .auth import AuthError, get_tenant_context, login, logout
from .store import TenantStore
from . import security as sec_mod
from . import rate_limit as rate_limit_mod

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
COOKIE_NAME = "zayvero_session"

STORE = None


def get_store() -> TenantStore:
    global STORE
    if STORE is None:
        STORE = TenantStore()
        STORE.ensure_demo_company()
    return STORE


def _parse_cookies(handler: BaseHTTPRequestHandler) -> dict:
    raw = handler.headers.get("Cookie", "")
    cookies = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            cookies[k] = v
    return cookies


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict,
               set_cookie: str | None = None, clear_cookie: bool = False):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    # SEG-01/02: igual que la app principal (ver tenant/security.py).
    # Decisiones independientes y explícitas: cookie Secure (ZAYVERO_COOKIE_SECURE)
    # y HSTS (ZAYVERO_HSTS). Nunca se infieren de cabeceras de la petición.
    secure_cookie = sec_mod.cookie_secure_enabled()
    hsts = sec_mod.hsts_enabled()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    for k, v in sec_mod.security_headers(hsts=hsts).items():
        handler.send_header(k, v)
    if set_cookie:
        handler.send_header("Set-Cookie",
                            sec_mod.build_set_cookie(COOKIE_NAME, set_cookie,
                                                     secure=secure_cookie))
    if clear_cookie:
        handler.send_header("Set-Cookie",
                            sec_mod.build_clear_cookie(COOKIE_NAME,
                                                       secure=secure_cookie))
    handler.end_headers()
    handler.wfile.write(body)


def _read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    try:
        length = int(handler.headers.get("Content-Length", 0) or 0)
    except ValueError:
        length = 0
    if length <= 0 or length > 4096:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


class TenantHandler(BaseHTTPRequestHandler):
    server_version = "ZayveroTenant/6A"

    def log_message(self, *args):
        # Sin logs ruidosos; nunca se loguean cuerpos con credenciales.
        pass

    # ---- GET ---------------------------------------------------------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        store = get_store()

        if path == "/" or path == "/index.html":
            return self._serve_static("index.html", "text/html; charset=utf-8")
        if path == "/health":
            return _send_json(self, 200, {"status": "ok", "phase": "6A"})

        token = _parse_cookies(self).get(COOKIE_NAME, "")
        if path == "/api/me":
            try:
                ctx = get_tenant_context(store, token)
            except AuthError:
                return _send_json(self, 401, {"error": "no autenticado"})
            return _send_json(self, 200, {"user": {
                "user_id": ctx.user_id, "email": ctx.email,
                "name": ctx.user_name, "role": ctx.role,
                "company_id": ctx.company_id, "company_name": ctx.company_name,
            }})
        if path == "/api/company":
            try:
                ctx = get_tenant_context(store, token)
            except AuthError:
                return _send_json(self, 401, {"error": "no autenticado"})
            company = store.get_company(ctx.company_id)
            return _send_json(self, 200, {"company": {
                "company_id": company.company_id, "name": company.name,
                "status": company.status, "is_demo": company.is_demo,
            }})
        if path == "/api/permissions":
            try:
                ctx = get_tenant_context(store, token)
            except AuthError:
                return _send_json(self, 401, {"error": "no autenticado"})
            return _send_json(self, 200, {
                "role": ctx.role, "permissions": ctx.permissions})
        return _send_json(self, 404, {"error": "no encontrado"})

    # ---- POST --------------------------------------------------------
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        store = get_store()

        if path == "/api/login":
            body = _read_json_body(self)
            email = str(body.get("email", "") or "").strip().lower()
            # SEG-04: igual que la app principal (ver webapp/server.py).
            # Reserva atómica con token + liquidación try/finally.
            limiter = rate_limit_mod.get_limiter()
            origin_ip = rate_limit_mod.client_origin_ip(self)
            allowed, retry_after, reservation = limiter.try_acquire(
                ip=origin_ip, account=email)
            if not allowed:
                self.send_response(429)
                self.send_header("Retry-After", str(int(retry_after) + 1))
                self.send_header("Content-Type",
                                 "application/json; charset=utf-8")
                for k, v in sec_mod.security_headers(
                        hsts=sec_mod.hsts_enabled()).items():
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(b'{"error": "demasiados intentos"}')
                return
            try:
                token, ctx = login(store, email, str(body.get("password", "")))
            except AuthError:
                limiter.settle(reservation, success=False)
                return _send_json(self, 401, {"error": "credenciales inválidas"})
            except Exception:
                limiter.settle(reservation, success=False)
                raise
            limiter.settle(reservation, success=True)
            return _send_json(self, 200, {"ok": True,
                                          "role": ctx.role,
                                          "company_name": ctx.company_name},
                              set_cookie=token)
        if path == "/api/logout":
            token = _parse_cookies(self).get(COOKIE_NAME, "")
            logout(store, token)
            return _send_json(self, 200, {"ok": True}, clear_cookie=True)
        return _send_json(self, 404, {"error": "no encontrado"})

    def _serve_static(self, filename: str, content_type: str):
        path = os.path.join(STATIC_DIR, filename)
        if not os.path.exists(path):
            return _send_json(self, 404, {"error": "no encontrado"})
        with open(path, "rb") as f:
            body = f.read()
        # SEG-01/02: igual que la app principal (ver tenant/security.py).
        hsts = sec_mod.hsts_enabled()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in sec_mod.security_headers(hsts=hsts).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)


def run_server(port: int = 8601):
    sec_mod.warn_if_insecure()
    server = ThreadingHTTPServer(("127.0.0.1", port), TenantHandler)
    print(f"FASE 6A tenant API en http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    import sys
    run_server(int(sys.argv[1]) if len(sys.argv) > 1 else 8601)
