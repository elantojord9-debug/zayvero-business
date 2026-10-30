"""BLOQUE A SEGURIDAD (2026-10-13) — Registro de rutas `seguridad/`.

CONTRATO (igual que os_contab/rutas.py; el cableado lo hará otra fase en
webapp/os_routes.py):
- `rutas()` devuelve {subruta: spec} con claves RELATIVAS (el cableado
  antepone el prefijo `seguridad/`).
- spec = {"metodo": "GET"|"POST", "permiso": str, "descripcion": str,
          "handler": fn}.
- `fn(bundle, ctx, payload) -> dict` JSON-serializable. `payload` es la
  query parseada (dict de listas, como en os_routes) en GET y el body
  JSON (dict) en POST.
- Errores: el despachador traduce ValueError -> 400, "NoEncontrado" ->
  404, "PermisoDenegado" -> 403. Los handlers NO envían HTTP: los
  errores del bloque se traducen aquí (ErrorSeguridad -> ValueError) y
  PermisoDenegado/NoEncontrado propagan con su nombre.

`ctx` es un TenantContext real (o dict en tests): se lee con
`_ctx_val()` (getattr primero, fallback a dict) — NUNCA se asume dict.

company_id SIEMPRE del ctx (anti-IDOR). El registro del bloque se crea
por bundle y se cachea en el atributo `_os_seguridad_reg` del bundle
(patrón de os_contab/_bloque). Con DSN usa PostgreSQL (migración 014);
sin DSN, JSON en el data_dir del store.

INTEGRACIÓN HTTP (para el coordinador — detalle en
docs/bloque-seguridad.md § "Integración"):
1. POST /api/login debe llamar a `mfa.login_con_mfa` en vez de
   `tenant.auth.login` y devolver `mfa_requerido` en la respuesta.
2. `seguridad/mfa/verificar` necesita un carve-out ANTES de
   `_auth_ctx()`: la sesión `mfa_pending` es rechazada por
   `get_tenant_context` por diseño (fail-closed); el carve-out resuelve
   (sesion, user) con `mfa.sesion_pendiente(store, token)` y construye el
   ctx con `build_tenant_context`.
3. Agregar `014_os_seguridad.sql` al final de
   `os_persist/schema/_orden.txt`.
"""

from __future__ import annotations

import os
import sys
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .errores import (  # noqa: E402
    ErrorSeguridad, NoEncontrado, PermisoDenegado)
from . import mfa as mod_mfa  # noqa: E402
from .almacen import crear_registro  # noqa: E402


# ---- estado del bloque por bundle ---------------------------------------
_BLOQUE_ATTR = "_os_seguridad_reg"
_bloque_lock = threading.RLock()


def _reg(bundle):
    """RegistroSeguridad del bundle (cacheado; patrón os_contab)."""
    with _bloque_lock:
        r = getattr(bundle, _BLOQUE_ATTR, None)
        if r is None:
            store = getattr(bundle, "store", None)
            r = crear_registro(store=store)
            setattr(bundle, _BLOQUE_ATTR, r)
        return r


def _store(bundle):
    store = getattr(bundle, "store", None)
    if store is None:
        raise ValueError("el bundle no expone .store")
    return store


# ---- utilidades -----------------------------------------------------------
def _q1(payload, name, default=""):
    v = (payload or {}).get(name, default)
    if isinstance(v, (list, tuple)):
        return v[0] if v else default
    return v if v is not None else default


def _ctx_val(ctx, nombre: str) -> str:
    """Lee un valor del ctx sea un dict (tests) o un TenantContext real."""
    if ctx is None:
        return ""
    v = getattr(ctx, nombre, None)
    if v:
        return str(v)
    if isinstance(ctx, dict):
        return str(ctx.get(nombre, "") or "")
    return ""


def _ctx_company(bundle, ctx) -> str:
    cid = _ctx_val(ctx, "company_id")
    if not cid or cid != bundle.company_id:
        raise ValueError("company_id del contexto no coincide con la empresa")
    return cid


def _traducir(fn):
    """Traduce errores del bloque al contrato del dispatcher."""
    def env(bundle, ctx, payload):
        try:
            return fn(bundle, ctx, payload)
        except (NoEncontrado, PermisoDenegado):
            raise  # el dispatcher los mapea por nombre (404/403)
        except ErrorSeguridad as e:
            raise ValueError(str(e))  # -> 400
    return env


# =====================================================================
# Handlers
# =====================================================================
@_traducir
def h_mfa_inscribir(bundle, ctx, payload):
    """POST seguridad/mfa/inscribir {password} -> {otpauth_uri (única vez)}."""
    store = _store(bundle)
    _ctx_company(bundle, ctx)
    password = (payload or {}).get("password", "")
    return mod_mfa.inscribir(store, _reg(bundle), _ctx_val(ctx, "user_id"),
                             password)


@_traducir
def h_mfa_confirmar(bundle, ctx, payload):
    """POST seguridad/mfa/confirmar {password, codigo} -> {codigos (1 vez)}."""
    store = _store(bundle)
    _ctx_company(bundle, ctx)
    p = payload or {}
    return mod_mfa.confirmar(store, _reg(bundle), _ctx_val(ctx, "user_id"),
                             p.get("password", ""), p.get("codigo", ""))


@_traducir
def h_mfa_estado(bundle, ctx, payload):
    """GET seguridad/mfa/estado -> {inscrito, confirmado, codigos_restantes}."""
    store = _store(bundle)
    _ctx_company(bundle, ctx)
    return mod_mfa.estado(store, _reg(bundle), _ctx_val(ctx, "user_id"))


@_traducir
def h_mfa_revocar(bundle, ctx, payload):
    """POST seguridad/mfa/revocar {password} -> desactiva el MFA propio."""
    store = _store(bundle)
    _ctx_company(bundle, ctx)
    return mod_mfa.revocar(store, _reg(bundle), _ctx_val(ctx, "user_id"),
                           (payload or {}).get("password", ""))


@_traducir
def h_mfa_codigos_regenerar(bundle, ctx, payload):
    """POST seguridad/mfa/codigos/regenerar {password} -> 10 nuevos (1 vez)."""
    store = _store(bundle)
    _ctx_company(bundle, ctx)
    return mod_mfa.regenerar_codigos(store, _reg(bundle),
                                    _ctx_val(ctx, "user_id"),
                                    (payload or {}).get("password", ""))


@_traducir
def h_mfa_verificar(bundle, ctx, payload):
    """POST seguridad/mfa/verificar {password, codigo} -> completa el login.

    REQUIERE el carve-out documentado en el docstring del módulo: el ctx
    aquí viene de `mfa.sesion_pendiente` + `build_tenant_context`, no de
    `get_tenant_context` (que rechaza la sesión pendiente por diseño).
    Acepta código TOTP o código de recuperación (un solo uso).
    """
    store = _store(bundle)
    _ctx_company(bundle, ctx)
    p = payload or {}
    # El token de la sesión pendiente viaja en la cookie; el dispatcher
    # HTTP lo entrega en el payload bajo "session_token" SOLO para este
    # carve-out (nunca se loguea: ver _auditar/scrub de tenant/audit).
    token = str(p.get("session_token", "") or "")
    return mod_mfa.verificar_mfa(store, _reg(bundle), token,
                                 p.get("password", ""), p.get("codigo", ""))


@_traducir
def h_recuperacion_solicitar(bundle, ctx, payload):
    """POST seguridad/recuperacion/solicitar {usuario_objetivo_id, motivo,
    canal_verificacion} — owner/admin. Paso 1 (auditado)."""
    store = _store(bundle)
    _ctx_company(bundle, ctx)
    p = payload or {}
    return mod_mfa.recuperacion_solicitar(
        store, _reg(bundle), ctx, p.get("usuario_objetivo_id", ""),
        p.get("motivo", ""), p.get("canal_verificacion", ""))


@_traducir
def h_recuperacion_ejecutar(bundle, ctx, payload):
    """POST seguridad/recuperacion/ejecutar {solicitud_id, confirmacion} —
    owner/admin. Paso 2: desactiva el MFA del objetivo (auditado)."""
    store = _store(bundle)
    _ctx_company(bundle, ctx)
    p = payload or {}
    return mod_mfa.recuperacion_ejecutar(
        store, _reg(bundle), ctx, p.get("solicitud_id", ""),
        bool(p.get("confirmacion")))


@_traducir
def h_exportar(bundle, ctx, payload):
    """GET seguridad/exportar -> JSON con los datos de la empresa.
    Solo owner (403 si no). Todo el acceso queda auditado."""
    store = _store(bundle)
    _ctx_company(bundle, ctx)
    return mod_mfa.exportar_datos(store, _reg(bundle), ctx)


@_traducir
def h_borrado_solicitar(bundle, ctx, payload):
    """POST seguridad/cuenta/borrado/solicitar {motivo} — solo owner.
    Crea la solicitud en el módulo aprobaciones (requiere PG)."""
    store = _store(bundle)
    _ctx_company(bundle, ctx)
    return mod_mfa.borrado_solicitar(store, _reg(bundle), ctx,
                                    (payload or {}).get("motivo", ""))


@_traducir
def h_borrado_ejecutar(bundle, ctx, payload):
    """POST seguridad/cuenta/borrado/ejecutar {aprobacion_id} — solo owner.
    Soft-delete tras aprobación humana: empresa `disabled` + sesiones
    revocadas. Datos conservados según retención."""
    store = _store(bundle)
    _ctx_company(bundle, ctx)
    return mod_mfa.borrado_ejecutar(store, _reg(bundle), ctx,
                                   (payload or {}).get("aprobacion_id", ""))


@_traducir
def h_retencion(bundle, ctx, payload):
    """GET seguridad/retencion -> matriz efectiva (defecto + personalizada)."""
    store = _store(bundle)
    company_id = _ctx_company(bundle, ctx)
    return mod_mfa.retencion_efectiva(_reg(bundle), company_id)


@_traducir
def h_retencion_actualizar(bundle, ctx, payload):
    """POST seguridad/retencion/actualizar {tipo_dato, plazo_dias, accion,
    base} — owner/admin (company.admin)."""
    store = _store(bundle)
    _ctx_company(bundle, ctx)
    p = payload or {}
    return mod_mfa.retencion_actualizar(
        _reg(bundle), store, ctx, p.get("tipo_dato", ""),
        p.get("plazo_dias", 0), p.get("accion", ""),
        p.get("base", "ultima_actividad"))


def rutas() -> dict:
    """Registro de rutas del bloque. Prefijo: `seguridad/` (lo antepone el
    cableado).

    El cableado traduce errores: ErrorSeguridad/ValueError -> 400,
    NoEncontrado -> 404, PermisoDenegado -> 403.
    """
    def _r(metodo, permiso, descripcion, handler):
        return {"metodo": metodo, "permiso": permiso,
                "descripcion": descripcion, "handler": handler}

    return {
        # --- MFA autofservicio (cualquier rol autenticado: os.read) -------
        "mfa/inscribir": _r(
            "POST", "os.read",
            "Inicia el enrolamiento TOTP. Requiere contraseña; devuelve el "
            "URI otpauth UNA sola vez para captura con la app autenticadora.",
            h_mfa_inscribir),
        "mfa/confirmar": _r(
            "POST", "os.read",
            "Confirma el enrolamiento con un código del autenticador; "
            "genera los 10 códigos de recuperación (entrega única).",
            h_mfa_confirmar),
        "mfa/estado": _r(
            "GET", "os.read",
            "Estado del MFA propio (sin secretos).",
            h_mfa_estado),
        "mfa/revocar": _r(
            "POST", "os.read",
            "Desactiva el MFA propio. Requiere contraseña.",
            h_mfa_revocar),
        "mfa/codigos/regenerar": _r(
            "POST", "os.read",
            "Regenera los 10 códigos de recuperación (invalida los "
            "anteriores). Requiere contraseña; entrega única.",
            h_mfa_codigos_regenerar),
        "mfa/verificar": _r(
            "POST", "os.read",
            "Segundo paso del login (sesión mfa_pending). Acepta código "
            "TOTP o de recuperación. REQUIERE el carve-out documentado en "
            "el docstring del módulo.",
            h_mfa_verificar),
        # --- recuperación asistida (owner/admin: user.admin) ---------------
        "recuperacion/solicitar": _r(
            "POST", "user.admin",
            "Paso 1: registra la solicitud de recuperación asistida "
            "(motivo + canal de verificación fuera del sistema). Auditado.",
            h_recuperacion_solicitar),
        "recuperacion/ejecutar": _r(
            "POST", "user.admin",
            "Paso 2: ejecuta la recuperación (desactiva el MFA del "
            "objetivo). Requiere confirmación explícita. Auditado.",
            h_recuperacion_ejecutar),
        # --- exportación / borrado (solo owner; el handler lo verifica) ----
        "exportar": _r(
            "GET", "os.read",
            "Exporta los datos de la empresa en JSON (sin secretos). Solo "
            "owner. Acceso auditado.",
            h_exportar),
        "cuenta/borrado/solicitar": _r(
            "POST", "os.read",
            "Paso 1 del borrado: crea la solicitud en el módulo "
            "aprobaciones (tipo eliminacion_cuenta). Solo owner.",
            h_borrado_solicitar),
        "cuenta/borrado/ejecutar": _r(
            "POST", "os.read",
            "Paso 2 del borrado: soft-delete tras aprobación humana "
            "(empresa disabled + sesiones revocadas). Solo owner.",
            h_borrado_ejecutar),
        # --- retención ------------------------------------------------------
        "retencion": _r(
            "GET", "os.read",
            "Matriz de políticas de retención efectiva (defecto + "
            "personalizaciones).",
            h_retencion),
        "retencion/actualizar": _r(
            "POST", "company.admin",
            "Personaliza la política de un tipo de dato. Owner/admin.",
            h_retencion_actualizar),
    }
