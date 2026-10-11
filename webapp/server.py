"""FASE 6B — Servidor web de la aplicación ZAYVERO Business (solo stdlib).

Flujo: LOGIN → MI EMPRESA → RESUMEN EJECUTIVO → HALLAZGOS →
OPORTUNIDADES → PREDICCIONES → ADVISOR ("Pregúntale a ZAYVERO").

REGLA FUNDAMENTAL: el frontend solo presenta información. Permisos, tenant
isolation, cálculos y acceso a datos viven en este backend, sobre las
fases 1-6A existentes.

Endpoints:
  POST /api/login            {email, password} → cookie de sesión (auth 6A)
  POST /api/logout           → revoca la sesión
  GET  /api/me               → usuario, empresa, rol, permisos
  GET  /api/summary          → resumen ejecutivo (permiso: dashboard.read)
  GET  /api/findings         → lista con filtros (permiso: findings.read)
  GET  /api/findings/<id>    → detalle (permiso: findings.read)
  GET  /api/opportunities    → oportunidades 5A (permiso: dashboard.read)
  GET  /api/predictions      → 4A+4B+4C (permiso: predictions.read)
  POST /api/advisor/ask      → 5B+5C (permiso: advisor.use)
  GET  /api/audit            → auditoría (permiso: audit.read)
  --- FASE 7B: diagnóstico ejecutivo ---
  GET  /api/diagnostic       → Diagnóstico Ejecutivo (permiso: dashboard.read)
  --- FASE 8: experiencia de producto (primera entrada / onboarding) ---
  GET  /api/product/overview → estado, onboarding, demo, planes, beneficios
                              (permiso: dashboard.read; company_id siempre
                              del TenantContext, nunca del frontend)
  --- FASE 7A: workspace y datasets ---
  GET  /api/workspace        → Mi empresa (permiso: dashboard.read)
  GET  /api/datasets         → lista de datasets (permiso: dashboard.read)
  POST /api/datasets/upload  → multipart CSV/XLSX (permiso: data.admin)
  GET  /api/datasets/<id>              → detalle (permiso: dashboard.read)
  GET  /api/datasets/<id>/review       → Revisión de datos (permiso: dashboard.read)
  GET  /api/datasets/<id>/preview      → primeras 10 filas (permiso: dashboard.read)
  GET  /api/datasets/<id>/mapping      → sugerencias de mapeo (permiso: dashboard.read)
  POST /api/datasets/<id>/mapping      → confirmar mapeo (permiso: data.admin)
  POST /api/datasets/<id>/process       → iniciar análisis (permiso: data.admin)
  POST /api/datasets/<id>/activate      → activar dataset (permiso: data.admin)

Todo endpoint empresarial:
  1. get_tenant_context() — sesión válida
  2. require_permission() — permiso del rol
  3. TenantData(store, ctx) — check_data_access: el dataset demo-retail
     solo pertenece a la empresa demo. Otro company_id → 403.

El company_id NUNCA se acepta desde parámetros del usuario.
"""

from __future__ import annotations

import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from tenant import (
    TenantStore, TenantContext, AuthError, PermissionDenied,
    login, logout, get_tenant_context, require_permission, require_csrf,
    get_csrf_token, enter_demo, exit_demo,
)
from tenant import audit as audit_mod
from tenant import security as sec_mod
from tenant import rate_limit as rate_limit_mod

from intl import config as intl_config_mod

from .data import TenantData, WebappConfig

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
COOKIE_NAME = "zayvero_session"

STORE: TenantStore | None = None
CONFIG = WebappConfig()
# Cache de TenantData por company_id (solo empresas con acceso verificado).
_DATA_CACHE: dict[str, TenantData] = {}


def get_store() -> TenantStore:
    global STORE
    if STORE is None:
        STORE = TenantStore()
        STORE.ensure_demo_company()
    return STORE


def get_tenant_data(store: TenantStore, ctx: TenantContext) -> TenantData:
    """Devuelve TenantData verificado para la empresa del contexto.

    FASE 7A: las rutas de datos se resuelven desde el dataset activo de la
    empresa (config_for_company). El company_id siempre viene de la sesión.
    """
    from webapp.data import config_for_company

    cid = ctx.company_id
    if cid not in _DATA_CACHE:
        config, _state = config_for_company(cid)
        _DATA_CACHE[cid] = TenantData(store, ctx, config)
    return _DATA_CACHE[cid]


def get_dataset_checked(ctx: TenantContext, dataset_id: str):
    """Devuelve el dataset de la empresa del contexto o lanza la excepción
    adecuada (KeyError → 404, PermissionError → 403)."""
    from datasets import DatasetStore, check_dataset_access

    ds_store = DatasetStore(ctx.company_id)
    ds = ds_store.get_dataset(dataset_id)
    check_dataset_access(ctx.company_id, ds)  # None o ajeno → PermissionError
    return ds_store, ds


# ---- multipart/form-data (streaming, para cargas CSV/XLSX) ---------------
def _parse_multipart(handler: BaseHTTPRequestHandler,
                     max_bytes: int = 100 * 1024 * 1024):
    """Parsea multipart y guarda los archivos en disco (streaming).

    Devuelve (fields: dict[str,str], files: dict[str,dict]) donde cada file
    es {"filename", "path", "size"}. Los archivos se guardan en /tmp y el
    llamador los mueve al destino final.
    """
    import re as _re

    ctype = handler.headers.get("Content-Type", "")
    m = _re.search(r'boundary=([^;]+)', ctype)
    if not m:
        raise ValueError("contenido inválido")
    boundary = b"--" + m.group(1).strip().strip('"').encode("latin-1")
    try:
        total = int(handler.headers.get("Content-Length", 0) or 0)
    except ValueError:
        total = 0
    if total <= 0 or total > max_bytes + 1024 * 1024:
        raise ValueError("tamaño inválido")

    fields: dict[str, str] = {}
    files: dict[str, dict] = {}
    buf = b""
    tmp_fh = None
    tmp_path = None
    file_name = None
    file_field = None
    field_name = ""
    file_size = 0
    state = "boundary"  # boundary | headers | field | content
    headers: dict[str, str] = {}
    remaining = total
    closing = boundary + b"--"

    def _finish_file():
        nonlocal tmp_fh, tmp_path, file_size
        if tmp_fh is not None:
            tmp_fh.close()
            files[file_field] = {
                "filename": file_name, "path": tmp_path, "size": file_size}
            tmp_fh = None
            tmp_path = None
            file_size = 0

    import tempfile as _tf
    while remaining > 0:
        chunk = handler.rfile.read(min(65536, remaining))
        if not chunk:
            break
        remaining -= len(chunk)
        buf += chunk
        while True:
            if state == "boundary":
                # Buscar línea de delimitador.
                idx = buf.find(b"\r\n")
                if idx < 0:
                    break
                line = buf[:idx]
                buf = buf[idx + 2:]
                if line in (boundary, closing) or line == b"":
                    if line == closing:
                        _finish_file()
                        return fields, files
                    state = "headers"
                    headers = {}
                # primera línea puede ser el boundary sin \r\n previo
                elif line.startswith(boundary):
                    state = "headers"
                    headers = {}
            elif state == "headers":
                idx = buf.find(b"\r\n")
                if idx < 0:
                    break
                line = buf[:idx].decode("latin-1")
                buf = buf[idx + 2:]
                if line == "":
                    # Fin de cabeceras: ¿campo o archivo?
                    disp = headers.get("content-disposition", "")
                    nm = _re.search(r'name="([^"]*)"', disp)
                    fn = _re.search(r'filename="([^"]*)"', disp)
                    field_name = nm.group(1) if nm else ""
                    if fn and fn.group(1):
                        file_field = field_name
                        file_name = os.path.basename(fn.group(1))
                        fd, tmp_path = _tf.mkstemp(prefix="zayvero_up_")
                        os.close(fd)
                        tmp_fh = open(tmp_path, "wb")
                        state = "content"
                    else:
                        fields[field_name] = ""
                        state = "field"
                else:
                    if ":" in line:
                        k, v = line.split(":", 1)
                        headers[k.strip().lower()] = v.strip()
            elif state == "field":
                idx = buf.find(b"\r\n" + boundary)
                if idx < 0:
                    # conservar cola por si el boundary está partido
                    keep = len(boundary) + 6
                    if len(buf) > keep:
                        fields[field_name] += \
                            buf[:-keep].decode("utf-8", "replace")
                        buf = buf[-keep:]
                    break
                fields[field_name] += buf[:idx].decode("utf-8", "replace")
                buf = buf[idx + 2:]
                state = "boundary"
            elif state == "content":
                idx = buf.find(b"\r\n" + boundary)
                if idx < 0:
                    # Escribir todo menos una cola de seguridad.
                    keep = len(boundary) + 6
                    if len(buf) > keep:
                        tmp_fh.write(buf[:-keep])
                        file_size += len(buf) - keep
                        buf = buf[-keep:]
                    break
                tmp_fh.write(buf[:idx])
                file_size += idx
                buf = buf[idx + 2:]
                _finish_file()
                state = "boundary"
    _finish_file()
    return fields, files


def _parse_cookies(handler: BaseHTTPRequestHandler) -> dict:
    raw = handler.headers.get("Cookie", "")
    cookies = {}
    for part in raw.split(";"):
        if "=" in part:
            k, v = part.strip().split("=", 1)
            cookies[k] = v
    return cookies


def _send_json(handler: BaseHTTPRequestHandler, status: int, payload: dict,
               set_cookie: str | None = None, clear_cookie: bool = False,
               extra_headers: dict | None = None):
    body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
    # SEG-01/02: decisiones INDEPENDIENTES y explícitas del operador.
    #  - cookie Secure: ZAYVERO_COOKIE_SECURE=1
    #  - HSTS: ZAYVERO_HSTS=1 (solo con origen público HTTPS confirmado)
    # Nunca se infieren de cabeceras de la petición (ver tenant/security.py).
    secure_cookie = sec_mod.cookie_secure_enabled()
    hsts = sec_mod.hsts_enabled()
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    for k, v in sec_mod.security_headers(hsts=hsts).items():
        handler.send_header(k, v)
    for k, v in (extra_headers or {}).items():
        handler.send_header(k, v)
    if set_cookie:
        handler.send_header(
            "Set-Cookie",
            sec_mod.build_set_cookie(COOKIE_NAME, set_cookie, secure=secure_cookie))
    if clear_cookie:
        handler.send_header(
            "Set-Cookie",
            sec_mod.build_clear_cookie(COOKIE_NAME, secure=secure_cookie))
    handler.end_headers()
    handler.wfile.write(body)


def _no_data_payload(data) -> dict:
    """FASE 7A: respuesta empresarial cuando no hay dataset READY activo.

    Nunca sirve datos de otra empresa ni inventa resultados.
    """
    from datasets.models import STATUS_MESSAGES
    state = data.dataset_state()
    return {
        "dataset_state": state,
        "dataset_state_message": STATUS_MESSAGES.get(state, ""),
        "ready": False,
    }


def _read_json_body(handler: BaseHTTPRequestHandler, max_bytes: int = 65536) -> dict:
    try:
        length = int(handler.headers.get("Content-Length", 0) or 0)
    except ValueError:
        length = 0
    if length <= 0 or length > max_bytes:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


def _auth_ctx(handler: BaseHTTPRequestHandler):
    """Autentica la petición. Devuelve (store, ctx, token) o envía 401."""
    store = get_store()
    token = _parse_cookies(handler).get(COOKIE_NAME, "")
    try:
        ctx = get_tenant_context(store, token)
    except AuthError as e:
        msg = "sesión expirada" if "expirada" in str(e) else "no autenticado"
        _send_json(handler, 401, {"error": msg})
        return None
    return store, ctx, token


def _csrf_from_request(handler: BaseHTTPRequestHandler) -> str:
    """Lee el token CSRF de la cabecera X-CSRF-Token (nunca de la URL)."""
    return (handler.headers.get("X-CSRF-Token", "") or "").strip()


def _require_csrf(handler: BaseHTTPRequestHandler, store, ctx,
                  token: str, resource: str = "") -> bool:
    """SEG-03: exige token CSRF válido en operaciones con estado.

    Devuelve True si pasa; si falla envía 403 y devuelve False.
    """
    try:
        require_csrf(store, ctx, token, _csrf_from_request(handler),
                     resource=resource)
        return True
    except PermissionDenied:
        _send_json(handler, 403, {"error": "solicitud rechazada"})
        return False


def _login_request_ok(handler: BaseHTTPRequestHandler) -> bool:
    """SEG-03 (login-CSRF, endurecido): el inicio de sesión no tiene sesión
    previa que ancle un token, así que se defiende en dos capas:

    1. Solo se acepta Content-Type: application/json. Un formulario
       cross-site no puede fijar ese Content-Type (dispararía un preflight
       CORS que este servidor no atiende: no hay cabeceras CORS).
    2. Si el navegador envía Origin/Referer, el origen COMPLETO
       (esquema://host[:puerto]) debe coincidir EXACTAMENTE con:
         a) el origen público configurado (ZAYVERO_PUBLIC_ORIGIN), cuando
            existe; o
         b) http://<Host de la petición> en desarrollo local sin TLS.

    El esquema público NO se deduce de la petición: en producción Render
    termina TLS en su proxy y el servidor local solo ve HTTP, así que el
    origen canónico viene de configuración explícita del operador:

        ZAYVERO_PUBLIC_ORIGIN=https://zayvero-business.onrender.com

    Comparar solo el netloc contra el Host sería inseguro: el Host lo fija
    el cliente y el esquema quedaría sin validar (un http:// maligno con
    el mismo host pasaría). Sin Origin/Referer (curl, clientes
    no-navegador) no hay amenaza CSRF —un atacante con curl no tiene las
    cookies de la víctima— y se permite; la capa 1 sigue aplicando.
    """
    ctype = (handler.headers.get("Content-Type", "") or "").split(";")[0]
    if ctype.strip().lower() != "application/json":
        return False
    public = sec_mod.public_origin()
    host = (handler.headers.get("Host", "") or "").strip().lower()
    for hdr in ("Origin", "Referer"):
        val = (handler.headers.get(hdr, "") or "").strip()
        if not val:
            continue
        try:
            parsed = urllib.parse.urlparse(val)
        except Exception:
            return False
        scheme = (parsed.scheme or "").lower()
        netloc = (parsed.netloc or "").lower()
        if not scheme or not netloc:
            return False
        candidate = f"{scheme}://{netloc}"
        if public:
            # Producción: coincidencia exacta con el origen público
            # configurado (esquema + host + puerto). Nada de prefijos ni
            # sufijos: "https://app.onrender.com.evil.com" NO pasa.
            if candidate != public:
                return False
        else:
            # Desarrollo local: solo http:// contra el propio Host.
            if scheme != "http" or not host or netloc != host:
                return False
    return True


class WebappHandler(BaseHTTPRequestHandler):
    server_version = "ZayveroWebApp/6B"

    def log_message(self, *args):
        pass  # Sin logs ruidosos; nunca cuerpos con credenciales.

    # ------------------------------------------------------------------ GET
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/health":
            return _send_json(self, 200, {"status": "ok", "phase": "6B"})
        if path in ("/", "/index.html", "/app"):
            return self._serve_static("index.html", "text/html; charset=utf-8")
        if path == "/style.css":
            return self._serve_static("style.css", "text/css; charset=utf-8")
        if path == "/app.js":
            return self._serve_static("app.js", "application/javascript; charset=utf-8")
        if path == "/i18n.js":
            return self._serve_static("i18n.js", "application/javascript; charset=utf-8")

        auth = _auth_ctx(self)
        if auth is None:
            return
        store, ctx, _token = auth

        try:
            if path == "/api/me":
                company = store.get_company(ctx.company_id)
                return _send_json(self, 200, {
                    "user": {"user_id": ctx.user_id, "email": ctx.email,
                             "name": ctx.user_name, "role": ctx.role},
                    "company": {"company_id": ctx.company_id,
                                "name": ctx.company_name or
                                (company.name if company else ctx.company_id),
                                "is_demo": bool(company and company.is_demo),
                                # FASE 7C: configuración internacional
                                # (el company_id viene del TenantContext).
                                "config": intl_config_mod.config_view(company)
                                if company else None},
                    "permissions": ctx.permissions,
                    # SEG-03: token CSRF de la sesión actual (para
                    # recargas de página: el frontend lo re-sincroniza).
                    "csrf_token": get_csrf_token(store, _token),
                })
            # ---- FASE 7C: configuración internacional de la empresa ------
            if path == "/api/company/config":
                require_permission(store, ctx, "dashboard.read", "company_config")
                company = store.get_company(ctx.company_id)
                if company is None:
                    return _send_json(self, 404, {"error": "empresa no encontrada"})
                return _send_json(self, 200, {
                    "config": intl_config_mod.config_view(company),
                    "can_edit": "company.config" in (ctx.permissions or []),
                })
            if path == "/api/summary":
                require_permission(store, ctx, "dashboard.read", "summary")
                data = get_tenant_data(store, ctx)
                return _send_json(self, 200, data.executive_summary())
            if path == "/api/findings":
                require_permission(store, ctx, "findings.read", "findings")
                return self._findings_list(store, ctx, query)
            if path.startswith("/api/findings/"):
                require_permission(store, ctx, "findings.read", "findings")
                fid = path[len("/api/findings/"):]
                return self._finding_detail(store, ctx, fid)
            if path == "/api/opportunities":
                require_permission(store, ctx, "dashboard.read", "opportunities")
                data = get_tenant_data(store, ctx)
                if data.dataset_state() != "READY":
                    payload = _no_data_payload(data)
                    payload.update({"opportunities": []})
                    return _send_json(self, 200, payload)
                return _send_json(self, 200, {"opportunities": data.opportunities()})
            if path == "/api/predictions":
                require_permission(store, ctx, "predictions.read", "predictions")
                data = get_tenant_data(store, ctx)
                if data.dataset_state() != "READY":
                    return _send_json(self, 200, _no_data_payload(data))
                return _send_json(self, 200, data.predictions())
            if path == "/api/audit":
                require_permission(store, ctx, "audit.read", "audit")
                data = get_tenant_data(store, ctx)
                return _send_json(self, 200,
                                  {"events": data.audit_events(store, ctx)})
            # ---- FASE 7B: Diagnóstico Ejecutivo -------------------------
            if path == "/api/diagnostic":
                require_permission(store, ctx, "dashboard.read", "diagnostic")
                data = get_tenant_data(store, ctx)
                payload, fresh = data.diagnostic(store, ctx)
                diag_id = payload.get("diagnostic_id", "")
                if fresh:
                    audit_mod.log_event(
                        store, company_id=ctx.company_id, user_id=ctx.user_id,
                        action=audit_mod.DIAGNOSTIC_GENERATED,
                        resource=f"diagnostic:{diag_id}",
                    )
                audit_mod.log_event(
                    store, company_id=ctx.company_id, user_id=ctx.user_id,
                    action=audit_mod.DIAGNOSTIC_VIEWED,
                    resource=f"diagnostic:{diag_id}",
                )
                return _send_json(self, 200, {"diagnostic": payload})
            # ---- FASE 8: experiencia de producto ----------------------
            if path == "/api/product/overview":
                require_permission(store, ctx, "dashboard.read", "product")
                data = get_tenant_data(store, ctx)
                company = store.get_company(ctx.company_id)
                # El company_id NUNCA se lee de query params: cualquier
                # ?company_id= se ignora (siempre el del TenantContext).
                config_view = intl_config_mod.config_view(company) \
                    if company else None
                ws = data.workspace(store, ctx)
                # Estado del diagnóstico sin efectos colaterales extra: usa
                # el builder existente (7B); la auditoría de diagnóstico la
                # registra únicamente /api/diagnostic.
                diag_payload, _ = data.diagnostic(store, ctx)
                diag_status = (diag_payload or {}).get("diagnostic_status")
                from product import build_product_overview
                overview = build_product_overview(
                    {
                        "company_id": ctx.company_id,
                        "name": ctx.company_name or
                        (company.name if company else ctx.company_id),
                    },
                    config_view, ws, diag_status,
                    is_demo=bool(company and company.is_demo),
                )
                audit_mod.log_event(
                    store, company_id=ctx.company_id, user_id=ctx.user_id,
                    action=audit_mod.PRODUCT_OVERVIEW_VIEWED,
                    resource=f"product:{ctx.company_id}",
                )
                return _send_json(self, 200, {"overview": overview})
            # ---- FASE 7A: workspace y datasets -------------------------
            if path == "/api/workspace":
                require_permission(store, ctx, "dashboard.read", "workspace")
                data = get_tenant_data(store, ctx)
                return _send_json(self, 200, data.workspace(store, ctx))
            if path == "/api/datasets":
                require_permission(store, ctx, "dashboard.read", "datasets")
                data = get_tenant_data(store, ctx)
                ws = data.workspace(store, ctx)
                return _send_json(self, 200, {
                    "dataset_state": ws["dataset_state"],
                    "dataset_state_message": ws["dataset_state_message"],
                    "datasets": ws["datasets"],
                    "active_dataset": ws["active_dataset"],
                    "can_upload": ws["can_upload"],
                })
            if path.startswith("/api/datasets/"):
                return self._dataset_get(store, ctx, path)
        except AuthError:
            return _send_json(self, 401, {"error": "no autenticado"})
        except PermissionDenied:
            return _send_json(self, 403, {"error": "acceso denegado"})
        except Exception:
            # Error seguro: sin revelar información interna.
            return _send_json(self, 500, {"error": "error interno"})
        return _send_json(self, 404, {"error": "no encontrado"})

    # ---- FASE 7A: lectura de datasets -----------------------------------
    def _dataset_get(self, store, ctx, path):
        from datasets import build_preview, build_review, suggest_mapping

        parts = path[len("/api/datasets/"):].split("/")
        dataset_id = parts[0]
        action = parts[1] if len(parts) > 1 else ""
        try:
            ds_store, ds = get_dataset_checked(ctx, dataset_id)
        except (PermissionError, KeyError):
            # PermissionError (builtin): dataset ajeno o inexistente.
            return _send_json(self, 403, {"error": "acceso denegado"})

        try:
            if action == "":
                require_permission(store, ctx, "dashboard.read", "datasets")
                return _send_json(self, 200, {"dataset": ds.to_dict()})
            if action == "review":
                require_permission(store, ctx, "dashboard.read", "datasets")
                up = ds.upload or {}
                if not up.get("path") or not os.path.exists(up["path"]):
                    return _send_json(self, 404,
                                      {"error": "archivo no disponible"})
                review = build_review(up["path"])
                ds.review = review
                ds_store.save_dataset(ds)
                return _send_json(self, 200, {"review": review})
            if action == "preview":
                require_permission(store, ctx, "dashboard.read", "datasets")
                up = ds.upload or {}
                if not up.get("path") or not os.path.exists(up["path"]):
                    return _send_json(self, 404,
                                      {"error": "archivo no disponible"})
                return _send_json(self, 200,
                                  {"preview": build_preview(up["path"])})
            if action == "mapping":
                require_permission(store, ctx, "dashboard.read", "datasets")
                up = ds.upload or {}
                if not up.get("path") or not os.path.exists(up["path"]):
                    return _send_json(self, 404,
                                      {"error": "archivo no disponible"})
                review = ds.review or build_review(up["path"])
                suggestion = suggest_mapping(review.get("columns", []))
                return _send_json(self, 200, {
                    "mapping": ds.mapping,
                    "suggestion": suggestion,
                })
        except PermissionDenied:
            return _send_json(self, 403, {"error": "acceso denegado"})
        except Exception:
            return _send_json(self, 500, {"error": "error interno"})
        return _send_json(self, 404, {"error": "no encontrado"})

    # ----------------------------------------------------------------- POST
    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        store = get_store()

        if path == "/api/login":
            # SEG-03 (login-CSRF): sin sesión previa no hay token que
            # validar; se exige JSON + misma origen (ver _login_request_ok).
            if not _login_request_ok(self):
                return _send_json(self, 403,
                                  {"error": "solicitud rechazada"})
            body = _read_json_body(self, max_bytes=4096)
            email = str(body.get("email", "") or "").strip().lower()
            # SEG-04: rate limiting ANTES de buscar al usuario: un 429 no
            # revela si la cuenta existe (coord. SEG-06). La cubeta por
            # cuenta usa el email normalizado, igual que el login.
            # try_acquire() reserva atómicamente con token: los intentos
            # concurrentes cuentan (sin race TOCTOU). El candado solo
            # cubre contadores; PBKDF2 corre fuera de él.
            limiter = rate_limit_mod.get_limiter()
            origin_ip = rate_limit_mod.client_origin_ip(self)
            allowed, retry_after, reservation = limiter.try_acquire(
                ip=origin_ip, account=email)
            if not allowed:
                return _send_json(
                    self, 429,
                    {"error": "demasiados intentos. Inténtalo de nuevo "
                              "más tarde."},
                    extra_headers={"Retry-After": str(int(retry_after) + 1)})
            try:
                token, ctx = login(store, email, str(body.get("password", "")))
            except AuthError:
                # SEG-04: el fallo liquida la reserva como fallo.
                # SEG-06: respuesta EXTERNA homogénea (401 genérico) para
                # email inexistente, clave incorrecta y usuario
                # deshabilitado. El motivo queda en la auditoría interna.
                limiter.settle(reservation, success=False)
                return _send_json(self, 401,
                                  {"error": "credenciales incorrectas"})
            except Exception:
                # Cualquier excepción también liquida como fallo
                # (fail-closed): un error no permite evadir el límite.
                limiter.settle(reservation, success=False)
                raise
            # SEG-04: el éxito liquida la reserva y perdona la cubeta de
            # la cuenta (el usuario legítimo que recordó su clave no
            # queda bloqueado).
            limiter.settle(reservation, success=True)
            return _send_json(self, 200, {"ok": True, "role": ctx.role,
                                          "company_name": ctx.company_name,
                                          # SEG-03: token CSRF de la sesión.
                                          "csrf_token": get_csrf_token(
                                              store, token)},
                              set_cookie=token)
        if path == "/api/logout":
            token = _parse_cookies(self).get(COOKIE_NAME, "")
            # SEG-03: el logout también exige CSRF (evita cierre de sesión
            # forzado; el token viaja en la cabecera, no en la cookie).
            try:
                ctx_lo = get_tenant_context(store, token)
            except AuthError:
                return _send_json(self, 200, {"ok": True}, clear_cookie=True)
            if not _require_csrf(self, store, ctx_lo, token,
                                 resource="logout"):
                return
            logout(store, token)
            return _send_json(self, 200, {"ok": True}, clear_cookie=True)

        auth = _auth_ctx(self)
        if auth is None:
            return
        store, ctx, token = auth

        try:
            if path == "/api/demo/enter":
                # Entra a la demo sin destruir la sesión: crea una sesión
                # nueva ligada a demo-retail (rol viewer, TTL 1h). La sesión
                # origen queda intacta para poder volver.
                if not _require_csrf(self, store, ctx, token,
                                     resource="demo:enter"):
                    return
                origin_token = _parse_cookies(self).get(COOKIE_NAME, "")
                try:
                    demo_token, demo_ctx = enter_demo(store, ctx,
                                                     origin_token)
                except AuthError as e:
                    return _send_json(self, 400, {"error": str(e)})
                return _send_json(self, 200,
                                  {"ok": True,
                                   "company_name": demo_ctx.company_name,
                                   # SEG-03: la sesión demo trae su propio
                                   # token CSRF; el frontend lo actualiza.
                                   "csrf_token": get_csrf_token(
                                       store, demo_token)},
                                  set_cookie=demo_token)

            if path == "/api/demo/exit":
                # Sale de la demo: revoca la sesión demo y restaura la
                # cookie con la sesión origen si sigue válida.
                if not _require_csrf(self, store, ctx, token,
                                     resource="demo:exit"):
                    return
                demo_token = _parse_cookies(self).get(COOKIE_NAME, "")
                try:
                    origin_token = exit_demo(store, demo_token)
                except AuthError as e:
                    return _send_json(self, 400, {"error": str(e)})
                if origin_token:
                    return _send_json(self, 200,
                                      {"ok": True,
                                       # SEG-03: token de la sesión origen
                                       # restaurada.
                                       "csrf_token": get_csrf_token(
                                           store, origin_token)},
                                      set_cookie=origin_token)
                return _send_json(self, 200,
                                  {"ok": True, "login_required": True},
                                  clear_cookie=True)

            if path == "/api/advisor/ask":
                require_permission(store, ctx, "advisor.use", "advisor")
                if not _require_csrf(self, store, ctx, token,
                                     resource="advisor:ask"):
                    return
                body = _read_json_body(self)
                question = str(body.get("question", "") or "").strip()
                if not question or len(question) > 2000:
                    return _send_json(self, 400,
                                      {"error": "pregunta inválida"})
                data = get_tenant_data(store, ctx)
                # FASE 7A: el Advisor nunca inventa respuestas sin datos de
                # la empresa autenticada.
                if data.dataset_state() != "READY":
                    nodata = _no_data_payload(data)
                    nodata.update({
                        "answer": "Todavía no tengo datos de tu empresa para analizar. "
                                  "Sube un reporte en la sección \"Mi empresa\" y, "
                                  "cuando esté listo, podré responder con base en tu información.",
                        "key_points": [],
                        "confidence": None,
                        "fallback": True,
                    })
                    return _send_json(self, 200, nodata)
                pipeline = data.advisor_pipeline()
                result = pipeline.ask(question)
                # FASE 6C (integración visual mínima): enriquecer la respuesta
                # con la incertidumbre y las recomendaciones del motor
                # determinista (5B). No modifica 5B ni 5C.
                try:
                    engine_resp = pipeline.engine.ask(question).to_dict()
                    result["uncertainty"] = engine_resp.get("uncertainty", {})
                    result["advisor_recommendations"] = (
                        engine_resp.get("recommendations", []) or [])[:6]
                except Exception:
                    result.setdefault("uncertainty", {})
                    result.setdefault("advisor_recommendations", [])
                audit_mod.log_event(
                    store, company_id=ctx.company_id, user_id=ctx.user_id,
                    action=audit_mod.DATA_ACCESS, resource="advisor:ask",
                )
                return _send_json(self, 200, result)
            # ---- FASE 7A: datasets -------------------------------------
            if path == "/api/datasets/upload":
                return self._dataset_upload(store, ctx, token)
            if path.startswith("/api/datasets/"):
                return self._dataset_post(store, ctx, token, path)
        except AuthError:
            return _send_json(self, 401, {"error": "no autenticado"})
        except PermissionDenied:
            return _send_json(self, 403, {"error": "acceso denegado"})
        except Exception:
            return _send_json(self, 500, {"error": "error interno"})
        return _send_json(self, 404, {"error": "no encontrado"})

    # ---- FASE 7C: actualización de la configuración de la empresa -------
    def do_PUT(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        store = get_store()

        auth = _auth_ctx(self)
        if auth is None:
            return
        store, ctx, token = auth

        try:
            if path == "/api/company/config":
                require_permission(store, ctx, "company.config", "company_config")
                # SEG-03: la configuración cambia estado → token CSRF.
                if not _require_csrf(self, store, ctx, token,
                                     resource="company:config"):
                    return
                company = store.get_company(ctx.company_id)
                if company is None:
                    return _send_json(self, 404,
                                      {"error": "empresa no encontrada"})
                if company.is_demo:
                    # La empresa demo protege su contexto (dataset UCI/GBP).
                    return _send_json(self, 403,
                                      {"error": "la empresa demo no se puede modificar"})
                body = _read_json_body(self)
                config, errors = intl_config_mod.normalize_config(body)
                if errors:
                    return _send_json(self, 400, {
                        "error": "configuración inválida",
                        "fields": errors,
                    })
                # Solo se tocan los campos PRESENTES en el cuerpo (PATCH
                # parcial): los ausentes se conservan, nunca se borran.
                changed = []
                for field_name in ("country", "industry", "language",
                                   "currency", "timezone", "date_format",
                                   "number_format"):
                    if field_name not in body:
                        continue
                    new_val = config.get(field_name, "")
                    if getattr(company, field_name, "") != new_val:
                        setattr(company, field_name, new_val)
                        changed.append(field_name)
                if body.get("name") and str(body["name"]).strip():
                    new_name = str(body["name"]).strip()[:120]
                    if company.name != new_name:
                        company.name = new_name
                        changed.append("name")
                from tenant.store import utcnow_iso
                company.updated_at = utcnow_iso()
                store.save_company(company)
                audit_mod.log_event(
                    store, company_id=ctx.company_id, user_id=ctx.user_id,
                    action=audit_mod.COMPANY_CONFIG_UPDATED,
                    resource=f"company:{ctx.company_id}",
                    metadata={"fields": changed},
                )
                return _send_json(self, 200, {
                    "ok": True,
                    "changed": changed,
                    "config": intl_config_mod.config_view(company),
                })
        except AuthError:
            return _send_json(self, 401, {"error": "no autenticado"})
        except PermissionDenied:
            return _send_json(self, 403, {"error": "acceso denegado"})
        except Exception:
            return _send_json(self, 500, {"error": "error interno"})
        return _send_json(self, 404, {"error": "no encontrado"})
    def _dataset_upload(self, store, ctx, token):
        """POST /api/datasets/upload — recibe CSV/XLSX (multipart)."""
        from datasets import DatasetStore, receive_upload
        from datasets.upload import UploadError

        try:
            require_permission(store, ctx, "data.admin", "datasets:upload")
        except PermissionDenied:
            return _send_json(self, 403, {"error": "acceso denegado"})
        # SEG-03: la subida multipart también exige token CSRF (cabecera).
        if not _require_csrf(self, store, ctx, token,
                             resource="datasets:upload"):
            return

        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return _send_json(self, 400, {"error": "formato de envío inválido"})
        try:
            fields, files = _parse_multipart(self)
        except ValueError as e:
            return _send_json(self, 400, {"error": str(e)})

        up = files.get("file")
        if not up:
            return _send_json(self, 400,
                              {"error": "No se recibió ningún archivo."})
        nombre = (fields.get("nombre") or "").strip()[:160]
        ds_store = DatasetStore(ctx.company_id)

        audit_mod.log_event(
            store, company_id=ctx.company_id, user_id=ctx.user_id,
            action=audit_mod.DATASET_UPLOAD_STARTED,
            resource="datasets:upload",
            metadata={"filename": up["filename"]},
        )
        try:
            def _chunks():
                with open(up["path"], "rb") as fh:
                    while True:
                        chunk = fh.read(65536)
                        if not chunk:
                            break
                        yield chunk

            ds = receive_upload(
                ds_store, filename=up["filename"], size_bytes=up["size"],
                content_iter=_chunks(), user_id=ctx.user_id, nombre=nombre,
            )
        except UploadError as e:
            return _send_json(self, 400, {"error": str(e)})
        finally:
            try:
                os.remove(up["path"])
            except OSError:
                pass

        # Revisión de datos inmediata (solo lectura, sin modificar).
        from datasets import build_review
        try:
            review = build_review((ds.upload or {})["path"])
            ds.review = review
            ds_store.save_dataset(ds)
        except Exception:
            pass

        # Mapeo: si todo está claro, se marca confirmado automáticamente.
        from datasets import suggest_mapping
        try:
            suggestion = suggest_mapping((ds.review or {}).get("columns", []))
            if not suggestion.get("needs_confirmation"):
                from datasets.store import utcnow_iso
                ds.mapping = {
                    "confirmed": True,
                    "mapping": {s["canonical"]: s["source"]
                                for s in suggestion["suggestions"]},
                    "confirmed_at": utcnow_iso(),
                    "auto": True,
                }
                ds_store.save_dataset(ds)
        except Exception:
            pass

        audit_mod.log_event(
            store, company_id=ctx.company_id, user_id=ctx.user_id,
            action=audit_mod.DATASET_UPLOAD_COMPLETED,
            resource=f"dataset:{ds.dataset_id}",
            metadata={"filename": up["filename"], "size": up["size"]},
        )
        audit_mod.log_event(
            store, company_id=ctx.company_id, user_id=ctx.user_id,
            action=audit_mod.DATASET_CREATED,
            resource=f"dataset:{ds.dataset_id}",
        )
        # Invalidar caché de datos de la empresa (nuevo dataset).
        _DATA_CACHE.pop(ctx.company_id, None)
        return _send_json(self, 200, {"dataset": ds.to_dict()})

    def _dataset_post(self, store, ctx, token, path):
        from datasets import confirm_mapping, process_in_background

        parts = path[len("/api/datasets/"):].split("/")
        dataset_id = parts[0]
        action = parts[1] if len(parts) > 1 else ""
        # SEG-03: mapping/process/activate cambian estado → token CSRF.
        if not _require_csrf(self, store, ctx, token,
                             resource=f"datasets:{action or 'dataset'}"):
            return
        try:
            ds_store, ds = get_dataset_checked(ctx, dataset_id)
        except (PermissionError, KeyError):
            # PermissionError (builtin): dataset ajeno o inexistente.
            return _send_json(self, 403, {"error": "acceso denegado"})

        try:
            if action == "mapping":
                require_permission(store, ctx, "data.admin", "datasets:mapping")
                body = _read_json_body(self)
                review = ds.review or {}
                result = confirm_mapping(
                    review.get("columns", []), body.get("mapping") or {})
                if not result["ok"]:
                    return _send_json(self, 400, {
                        "error": "Revisa el mapeo de columnas.",
                        "errors": result["errors"],
                    })
                from datasets.store import utcnow_iso
                ds.mapping = {
                    "confirmed": True,
                    "mapping": result["mapping"],
                    "confirmed_at": utcnow_iso(),
                    "auto": False,
                }
                ds_store.save_dataset(ds)
                return _send_json(self, 200, {"mapping": ds.mapping})
            if action == "process":
                require_permission(store, ctx, "data.admin", "datasets:process")
                if ds.status == "PROCESSING":
                    return _send_json(self, 409,
                                      {"error": "El análisis ya está en curso."})
                if not (ds.mapping or {}).get("confirmed"):
                    return _send_json(self, 400, {
                        "error": "Primero confirma el mapeo de columnas."})
                if ds.status == "READY":
                    return _send_json(self, 200, {
                        "ok": True,
                        "message": "Los datos ya fueron analizados."})
                # Limpiar caché para que el dashboard vea el estado nuevo.
                _DATA_CACHE.pop(ctx.company_id, None)
                process_in_background(ds_store, dataset_id,
                                      tenant_store=store, user_id=ctx.user_id)
                return _send_json(self, 202, {
                    "ok": True,
                    "message": "Estamos procesando tus datos.",
                    "dataset_id": dataset_id,
                })
            if action == "activate":
                require_permission(store, ctx, "data.admin", "datasets:activate")
                try:
                    ds = ds_store.set_active(dataset_id)
                except (KeyError, ValueError) as e:
                    return _send_json(self, 400, {"error": str(e)})
                audit_mod.log_event(
                    store, company_id=ctx.company_id, user_id=ctx.user_id,
                    action=audit_mod.DATASET_ACTIVATED,
                    resource=f"dataset:{dataset_id}",
                )
                _DATA_CACHE.pop(ctx.company_id, None)
                return _send_json(self, 200, {"dataset": ds.to_dict()})
        except PermissionDenied:
            return _send_json(self, 403, {"error": "acceso denegado"})
        except Exception:
            return _send_json(self, 500, {"error": "error interno"})
        return _send_json(self, 404, {"error": "no encontrado"})

    # ---- findings (reutiliza FASE 3: adapter + service) --------------------
    def _findings_list(self, store, ctx, query):
        # Import diferido: FASE 3 no se modifica.
        from dashboard import service as svc

        def _one(name, default="all"):
            vals = query.get(name)
            return vals[0] if vals else default

        data = get_tenant_data(store, ctx)
        # FASE 7A: sin dataset READY, lista vacía + estado (nunca 500, nunca
        # datos de otra empresa).
        if data.dataset_state() != "READY":
            payload = _no_data_payload(data)
            payload.update({
                "dataset": None, "total": 0, "page": 1, "per_page": 20,
                "by_priority": {}, "by_type": {}, "items": [],
            })
            return _send_json(self, 200, payload)
        view = data.findings_view()
        findings = svc.sort_findings(view["findings"])
        filtered = svc.filter_findings(
            findings,
            priority=_one("priority"),
            ftype=_one("type"),
            period=_one("period"),
            query=_one("q", ""),
        )
        page = max(1, int(_one("page", "1") or 1))
        per_page = 20
        total = len(filtered)
        start = (page - 1) * per_page
        items = filtered[start:start + per_page]
        return _send_json(self, 200, {
            "dataset": view["dataset"],
            "total": total,
            "page": page,
            "per_page": per_page,
            "by_priority": svc.priority_counts(filtered),
            "by_type": svc.type_counts(filtered),
            "items": [self._finding_card(v) for v in items],
        })

    def _finding_card(self, v):
        return {
            "finding_id": v.get("finding_id"),
            "title": v.get("title"),
            "type": v.get("type"),
            "type_label": v.get("type_label"),
            "business_priority": v.get("business_priority"),
            "impact_score": v.get("impact_score"),
            "confidence_score": v.get("confidence_score"),
            "evidence_quality": v.get("evidence_quality"),
            "entity": v.get("entity"),
            "period": v.get("period"),
            "observed_value": v.get("observed_value"),
            "expected_value": v.get("expected_value"),
            "difference": v.get("difference"),
            "percentage_difference": v.get("percentage_difference"),
            "business_explanation": v.get("business_explanation"),
            "n_recommendations": v.get("n_recommendations"),
            "requires_review": v.get("requires_review"),
        }

    def _finding_detail(self, store, ctx, finding_id):
        from dashboard import service as svc
        data = get_tenant_data(store, ctx)
        if data.dataset_state() != "READY":
            return _send_json(self, 200, _no_data_payload(data))
        view = data.findings_view()
        detail = svc.get_detail(view["findings"], finding_id)
        if detail is None:
            return _send_json(self, 404, {"error": "hallazgo no encontrado"})
        return _send_json(self, 200, {"finding": detail})

    # ---- estáticos ---------------------------------------------------------
    def _serve_static(self, filename: str, content_type: str):
        path = os.path.join(STATIC_DIR, filename)
        if not os.path.exists(path):
            return _send_json(self, 404, {"error": "no encontrado"})
        with open(path, "rb") as f:
            body = f.read()
        # SEG-02: las cabeceras también aplican a estáticos (y a index.html,
        # puerta de entrada de la app). HSTS solo con ZAYVERO_HSTS=1.
        hsts = sec_mod.hsts_enabled()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        for k, v in sec_mod.security_headers(hsts=hsts).items():
            self.send_header(k, v)
        # Evita que el navegador conserve un app.js viejo tras un despliegue:
        # siempre revalida los archivos estáticos con el servidor.
        self.send_header("Cache-Control", "no-cache, must-revalidate")
        self.end_headers()
        self.wfile.write(body)


def run_server(port: int = 8701, host: str | None = None):
    # En despliegue (Render u otro host), usar HOST/PORT del entorno.
    # Local: 127.0.0.1:8701 como siempre.
    if host is None:
        host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", port))
    # Falla rápido si la configuración de autenticación es inválida
    # (p.ej. ZAYVERO_REQUIRE_SECRET_FILE=1 sin Secret File): mejor que
    # arrancar un servicio que no puede autenticar a nadie.
    get_store()
    # SEG-01: avisar si la cookie Secure no está activa (evita olvidos en
    # producción). Ver tenant/security.py para la configuración.
    sec_mod.warn_if_insecure()
    server = ThreadingHTTPServer((host, port), WebappHandler)
    print(f"FASE 6B web app en http://{host}:{port}")
    server.serve_forever()


if __name__ == "__main__":
    import sys
    run_server(int(sys.argv[1]) if len(sys.argv) > 1 else 8701)
