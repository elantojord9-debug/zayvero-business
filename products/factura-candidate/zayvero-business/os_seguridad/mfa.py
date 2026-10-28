"""BLOQUE A SEGURIDAD — Lógica de negocio: MFA/TOTP, recuperación,
exportación, borrado y retención.

Todas las funciones reciben `store` (TenantStore) y `reg`
(RegistroSeguridad). Ninguna toca os.environ. Los secretos en claro
(TOTP, contraseña, URI otpauth) solo viven en memoria durante el llamado:
NUNCA se persisten ni se registran en auditoría (tenant/audit.py los
filtra además por defensa en profundidad).

Flujo MFA:
  1. `inscribir(user_id, password)` -> entrega el URI otpauth UNA vez.
  2. `confirmar(user_id, password, codigo)` -> activa el MFA y entrega
     los 10 códigos de recuperación UNA vez.
  3. `login_con_mfa(...)` envuelve a tenant.auth.login: si el usuario
     tiene MFA confirmado, la sesión nace `mfa_pending` y
     `get_tenant_context` la rechaza (fail-closed) hasta
     `verificar_mfa(token, password, codigo)`.
  4. El segundo paso acepta código TOTP (ventana ±1 paso de 30 s) o un
     código de recuperación (un solo uso). La contraseña se pide de
     nuevo: es la que desenvuelve el secreto (ver envoltorio.py).

Anti-fuerza bruta: 5 fallos en 15 min -> bloqueo de 15 min por usuario.
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tenant import audit as audit_mod  # noqa: E402
from tenant.auth import AuthError, login as _login_base  # noqa: E402
from tenant.crypto import verify_password  # noqa: E402

from .errores import (  # noqa: E402
    ErrorSeguridad, NoEncontrado, PermisoDenegado)
from . import totp as totp_mod  # noqa: E402
from . import envoltorio as env_mod  # noqa: E402
from .almacen import RegistroSeguridad  # noqa: E402

# La rama de integración puede traer tenant/auth.py en versión vieja
# (login(store, email, password)) o nueva (login(..., company_id=None)).
# Se detecta una vez por firma, no por try/except (determinista).
import inspect as _inspect  # noqa: E402

_LOGIN_ACEPTA_EMPRESA = (
    "company_id" in _inspect.signature(_login_base).parameters
)


def _login(store, email: str, password: str,
           company_id: str | None = None):
    if _LOGIN_ACEPTA_EMPRESA:
        return _login_base(store, email, password, company_id)
    if company_id:
        # La versión vieja es mono-empresa por email: se verifica que el
        # usuario pertenezca a la empresa pedida tras el login.
        token, ctx = _login_base(store, email, password)
        if ctx.company_id != company_id:
            from tenant.auth import logout as _logout
            _logout(store, token)
            raise AuthError("credenciales inválidas")
        return token, ctx
    return _login_base(store, email, password)

# ---- eventos de auditoría del bloque ---------------------------------------
MFA_INSCRITO = "MFA_INSCRITO"
MFA_CONFIRMADO = "MFA_CONFIRMADO"
MFA_PENDIENTE = "MFA_PENDIENTE"
MFA_VERIFICADO = "MFA_VERIFICADO"
MFA_FALLIDO = "MFA_FALLIDO"
MFA_BLOQUEADO = "MFA_BLOQUEADO"
MFA_REVOCADO = "MFA_REVOCADO"
CODIGOS_REGENERADOS = "CODIGOS_REGENERADOS"
CODIGO_RECUPERACION_USADO = "CODIGO_RECUPERACION_USADO"
RECUPERACION_SOLICITADA = "RECUPERACION_SOLICITADA"
RECUPERACION_EJECUTADA = "RECUPERACION_EJECUTADA"
EXPORTACION_DATOS = "EXPORTACION_DATOS"
CUENTA_BORRADO_SOLICITADO = "CUENTA_BORRADO_SOLICITADO"
CUENTA_BORRADO_EJECUTADO = "CUENTA_BORRADO_EJECUTADO"
RETENCION_ACTUALIZADA = "RETENCION_ACTUALIZADA"


# ---- utilidades de ctx (dict o TenantContext; nunca se asume dict) ---------
def _ctx_val(ctx, nombre: str) -> str:
    if ctx is None:
        return ""
    v = getattr(ctx, nombre, None)
    if v:
        return str(v)
    if isinstance(ctx, dict):
        return str(ctx.get(nombre, "") or "")
    return ""


def _ctx_company(ctx) -> str:
    return _ctx_val(ctx, "company_id")


def _ctx_user(ctx) -> str:
    return _ctx_val(ctx, "user_id")


def _requerir_owner(store, ctx) -> None:
    """Solo rol owner. No existe un permiso exclusivo de owner en la
    matriz de roles: se verifica el rol directamente (fail-closed).
    Lanza PermisoDenegado (nombre mapeado a 403 por el dispatcher)."""
    if _ctx_val(ctx, "role") != "owner":
        _auditar_permiso(ctx, "rol owner")
        raise PermisoDenegado("solo el propietario (owner)")


def _auditar_permiso(ctx, faltante: str) -> None:
    # La auditoría de denegación necesita store; se hace donde haya.
    pass


def _requerir_permiso(store, ctx, permiso: str, recurso: str = "") -> None:
    """Verifica un permiso de la matriz de roles. Denegado ->
    PermisoDenegado (403 en el dispatcher) + evento PERMISSION_DENIED.

    No se usa tenant.authorization.require_permission: su
    PermissionDenied no está en la lista de nombres que el dispatcher
    traduce (devolvería 500 en vez de 403)."""
    tiene = False
    tiene_fn = getattr(ctx, "has", None)
    if callable(tiene_fn):
        tiene = bool(tiene_fn(permiso))
    elif isinstance(ctx, dict):
        perms = ctx.get("permissions") or ctx.get("permisos") or []
        tiene = permiso in perms
    if not tiene:
        _auditar(store, _ctx_company(ctx), _ctx_user(ctx),
                 audit_mod.PERMISSION_DENIED, resource=recurso or permiso,
                 result="denied", metadata={"missing_permission": permiso})
        raise PermisoDenegado("acceso denegado")


def _auditar(store, company_id: str, user_id: str, action: str,
             resource: str = "", result: str = "ok",
             metadata: dict | None = None) -> None:
    audit_mod.log_event(store, company_id=company_id, user_id=user_id,
                        action=action, resource=resource, result=result,
                        metadata=metadata or {})


def _usuario_activo(store, user_id: str):
    user = store.get_user(user_id)
    if user is None or user.status != "active":
        raise ErrorSeguridad("usuario no válido")
    return user


def _verificar_clave(user, password: str) -> None:
    """Confirmación de contraseña con el MISMO esquema que el login.
    Falla con mensaje genérico (no distingue)."""
    if not verify_password(password or "", user.password_hash or ""):
        raise ErrorSeguridad("credenciales inválidas")


# =====================================================================
# Enrolamiento TOTP
# =====================================================================
def inscribir(store, reg: RegistroSeguridad, user_id: str,
              password: str) -> dict:
    """Inicia el enrolamiento MFA. Requiere confirmación de contraseña.

    Devuelve {"otpauth_uri", "emisor"}: el URI contiene el secreto en
    claro y se entrega UNA sola vez (el llamador HTTP lo muestra para
    captura inmediata; nunca se guarda ni se loguea).
    """
    user = _usuario_activo(store, user_id)
    _verificar_clave(user, password)
    secreto = totp_mod.generar_secreto()
    env = env_mod.envolver_secreto(secreto, password)
    reg.mfa_guardar(user.user_id, env["sal"], env["cifrado"], env["sha"],
                    confirmado=False)
    _auditar(store, user.company_id, user.user_id, MFA_INSCRITO,
             resource=f"user:{user.user_id}")
    return {
        "otpauth_uri": totp_mod.uri_otpauth(secreto, user.email),
        "emisor": "ZAYVERO",
        "entrega_unica": True,
    }


def confirmar(store, reg: RegistroSeguridad, user_id: str, password: str,
              codigo: str, momento: float | None = None) -> dict:
    """Confirma el enrolamiento con un código del autenticador y genera
    los 10 códigos de recuperación (se devuelven UNA sola vez)."""
    user = _usuario_activo(store, user_id)
    fila = reg.mfa_leer(user.user_id)
    if not fila:
        raise ErrorSeguridad("no hay enrolamiento pendiente")
    if fila.get("confirmado"):
        raise ErrorSeguridad("el MFA ya está activo")
    _verificar_clave(user, password)
    secreto = env_mod.desenvolver_secreto(
        fila["cifrado"], fila["sal"], fila["sha"], password)
    if not totp_mod.verificar_totp(secreto, codigo, momento=momento):
        _auditar(store, user.company_id, user.user_id, MFA_FALLIDO,
                 resource=f"user:{user.user_id}", result="failed",
                 metadata={"fase": "confirmar"})
        raise ErrorSeguridad("código inválido")
    reg.mfa_confirmar(user.user_id)
    codigos = env_mod.generar_codigos_recuperacion(10)
    reg.codigos_guardar(user.user_id,
                        [env_mod.hash_codigo(c) for c in codigos])
    _auditar(store, user.company_id, user.user_id, MFA_CONFIRMADO,
             resource=f"user:{user.user_id}",
             metadata={"codigos_generados": 10})
    return {"confirmado": True, "codigos_recuperacion": codigos,
            "entrega_unica": True}


def estado(store, reg: RegistroSeguridad, user_id: str) -> dict:
    """Estado MFA del usuario (sin secretos)."""
    user = _usuario_activo(store, user_id)
    fila = reg.mfa_leer(user.user_id)
    return {
        "inscrito": fila is not None,
        "confirmado": bool(fila and fila.get("confirmado")),
        "codigos_restantes": reg.codigos_restantes(user.user_id)
        if fila and fila.get("confirmado") else 0,
    }


def revocar(store, reg: RegistroSeguridad, user_id: str,
            password: str) -> dict:
    """Desactiva el MFA del propio usuario. Requiere contraseña."""
    user = _usuario_activo(store, user_id)
    _verificar_clave(user, password)
    reg.mfa_borrar(user.user_id)
    _auditar(store, user.company_id, user.user_id, MFA_REVOCADO,
             resource=f"user:{user.user_id}")
    return {"revocado": True}


def regenerar_codigos(store, reg: RegistroSeguridad, user_id: str,
                      password: str) -> dict:
    """Genera 10 códigos nuevos e invalida los anteriores. Requiere
    contraseña. Se devuelven UNA sola vez."""
    user = _usuario_activo(store, user_id)
    fila = reg.mfa_leer(user.user_id)
    if not fila or not fila.get("confirmado"):
        raise ErrorSeguridad("el MFA no está activo")
    _verificar_clave(user, password)
    codigos = env_mod.generar_codigos_recuperacion(10)
    reg.codigos_guardar(user.user_id,
                        [env_mod.hash_codigo(c) for c in codigos])
    _auditar(store, user.company_id, user.user_id, CODIGOS_REGENERADOS,
             resource=f"user:{user.user_id}")
    return {"codigos_recuperacion": codigos, "entrega_unica": True}


# =====================================================================
# Login con MFA (fail-closed)
# =====================================================================
def login_con_mfa(store, reg: RegistroSeguridad, email: str, password: str,
                  company_id: str | None = None):
    """Envuelve a tenant.auth.login (anti-enumeración intacta).

    Si el usuario tiene MFA confirmado, la sesión queda `mfa_pending`:
    `get_tenant_context` la rechaza hasta `verificar_mfa` (sin acceso a
    datos). Devuelve (token, ctx, {"mfa_requerido": bool}).
    """
    token, ctx = _login(store, email, password, company_id)
    fila = reg.mfa_leer(ctx.user_id)
    if fila and fila.get("confirmado"):
        sesion = store.get_session(token)
        if sesion is None:  # no debería pasar: falla cerrado
            raise AuthError("sesión inválida")
        sesion.mfa_estado = "pendiente"
        store.save_session(sesion)
        _auditar(store, ctx.company_id, ctx.user_id, MFA_PENDIENTE,
                 metadata={"session": audit_mod.token_fingerprint(token)})
        return token, ctx, {"mfa_requerido": True}
    return token, ctx, {"mfa_requerido": False}


def sesion_pendiente(store, token: str):
    """(sesion, user) si el token es una sesión `mfa_pending` válida.

    NO usa get_tenant_context (que la rechaza por diseño): es la vía del
    segundo paso de verificación."""
    sesion = store.get_session(token) if token else None
    if sesion is None or getattr(sesion, "mfa_estado", "") != "pendiente":
        raise ErrorSeguridad("sesión no pendiente de MFA")
    # get_session no filtra expiradas (el mensaje "sesión expirada" lo da
    # get_tenant_context): aquí se verifica explícito (fail-closed).
    from datetime import datetime, timezone
    try:
        exp = datetime.strptime(
            sesion.expires_at or "", "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc)
    except Exception:
        raise ErrorSeguridad("sesión no válida")
    if exp < datetime.now(timezone.utc):
        raise ErrorSeguridad("sesión expirada")
    user = store.get_user(sesion.user_id)
    if user is None or user.status != "active":
        raise ErrorSeguridad("usuario no válido")
    return sesion, user


def verificar_mfa(store, reg: RegistroSeguridad, token: str, password: str,
                  codigo: str, momento: float | None = None) -> dict:
    """Segundo paso del login. Acepta código TOTP (ventana ±1 paso) o
    código de recuperación (un solo uso). La contraseña se pide de nuevo:
    es la que desenvuelve el secreto.

    Éxito -> sesión `mfa_ok` (acceso a datos). Fallo -> cuenta intento
    (5 fallos / 15 min = bloqueo 15 min), auditoría y error genérico.
    """
    sesion, user = sesion_pendiente(store, token)
    bloqueado, resto = reg.mfa_bloqueado(user.user_id)
    if bloqueado:
        _auditar(store, user.company_id, user.user_id, MFA_BLOQUEADO,
                 result="failed", metadata={"reintentar_en_s": resto})
        raise ErrorSeguridad("demasiados intentos: intente más tarde")

    def _fallo(motivo: str):
        reg.mfa_fallo(user.user_id)
        _auditar(store, user.company_id, user.user_id, MFA_FALLIDO,
                 result="failed", metadata={"motivo": motivo})
        raise ErrorSeguridad("código inválido")

    # La contraseña se verifica ANTES de distinguir TOTP vs recuperación:
    # un error aquí no revela qué método falló.
    if not verify_password(password or "", user.password_hash or ""):
        _fallo("clave")

    metodo = None
    fila = reg.mfa_leer(user.user_id)
    if fila and fila.get("confirmado"):
        try:
            secreto = env_mod.desenvolver_secreto(
                fila["cifrado"], fila["sal"], fila["sha"], password)
        except ErrorSeguridad:
            _fallo("envoltorio")
        if totp_mod.verificar_totp(secreto, codigo, momento=momento):
            metodo = "totp"
    if metodo is None:
        # Vía código de recuperación (un solo uso, atómico).
        try:
            sha = env_mod.hash_codigo(codigo)
        except ErrorSeguridad:
            _fallo("formato")
        if reg.codigo_usar(user.user_id, sha):
            metodo = "recuperacion"
            _auditar(store, user.company_id, user.user_id,
                     CODIGO_RECUPERACION_USADO,
                     resource=f"user:{user.user_id}")
        else:
            _fallo("codigo")

    reg.mfa_exito(user.user_id)
    sesion.mfa_estado = "ok"
    store.save_session(sesion)
    _auditar(store, user.company_id, user.user_id, MFA_VERIFICADO,
             metadata={"metodo": metodo,
                       "session": audit_mod.token_fingerprint(token)})
    return {"ok": True, "metodo": metodo}


# =====================================================================
# Recuperación asistida por owner/admin (dos pasos, auditada)
# =====================================================================
def recuperacion_solicitar(store, reg: RegistroSeguridad, ctx,
                           usuario_objetivo_id: str, motivo: str,
                           canal_verificacion: str) -> dict:
    """Paso 1: un owner/admin registra la solicitud. La identidad del
    usuario se verifica FUERA del sistema (llamada al número registrado,
    presencial, etc.): `canal_verificacion` documenta dónde. Sin
    email/SMS automáticos (decisión de diseño: sin proveedores)."""
    _requerir_permiso(store, ctx, "user.admin", "recuperacion/solicitar")
    company_id = _ctx_company(ctx)
    objetivo = store.get_user((usuario_objetivo_id or "").strip())
    if objetivo is None or objetivo.company_id != company_id:
        raise ErrorSeguridad("usuario objetivo no válido")
    motivo = (motivo or "").strip()
    canal = (canal_verificacion or "").strip()
    if not motivo or not canal:
        raise ErrorSeguridad("motivo y canal_verificacion son obligatorios")
    sid = reg.rec_solicitar(company_id, objetivo.user_id, _ctx_user(ctx),
                            motivo, canal)
    _auditar(store, company_id, _ctx_user(ctx), RECUPERACION_SOLICITADA,
             resource=f"solicitud:{sid}",
             metadata={"objetivo": objetivo.user_id,
                       "canal_verificacion": canal})
    return {"solicitud_id": sid, "estado": "pendiente"}


def recuperacion_ejecutar(store, reg: RegistroSeguridad, ctx,
                          solicitud_id: str, confirmacion: bool) -> dict:
    """Paso 2: ejecuta la recuperación (desactiva el MFA del objetivo y
    sus códigos). Requiere confirmación explícita. Atómico: una solicitud
    solo se ejecuta una vez."""
    _requerir_permiso(store, ctx, "user.admin", "recuperacion/ejecutar")
    if not confirmacion:
        raise ErrorSeguridad("se requiere confirmación explícita")
    company_id = _ctx_company(ctx)
    sol = reg.rec_leer((solicitud_id or "").strip())
    if sol is None:
        raise NoEncontrado("solicitud no encontrada")
    if sol.get("empresa_id") != company_id:
        raise ErrorSeguridad("solicitud no válida")
    if sol.get("estado") != "pendiente":
        raise ErrorSeguridad("la solicitud ya fue procesada")
    ejecutada = reg.rec_ejecutar(sol["solicitud_id"], _ctx_user(ctx))
    if ejecutada is None:  # carrera: otro la ejecutó primero
        raise ErrorSeguridad("la solicitud ya fue procesada")
    reg.mfa_borrar(sol["usuario_objetivo"])
    _auditar(store, company_id, _ctx_user(ctx), RECUPERACION_EJECUTADA,
             resource=f"solicitud:{sol['solicitud_id']}",
             metadata={"objetivo": sol["usuario_objetivo"],
                       "solicitante": sol["solicitante"]})
    return {"ejecutada": True,
            "solicitud_id": sol["solicitud_id"],
            "usuario": sol["usuario_objetivo"]}


# =====================================================================
# Exportación de datos (solo owner)
# =====================================================================
def exportar_datos(store, reg: RegistroSeguridad, ctx) -> dict:
    """Exporta los datos de la empresa en JSON. Solo rol owner.

    Incluye metadatos de usuarios (SIN password_hash), estado MFA (SIN
    secretos), políticas de retención efectivas y eventos de auditoría.
    Todo el acceso queda auditado."""
    _requerir_owner(store, ctx)
    company_id = _ctx_company(ctx)
    company = store.get_company(company_id)
    if company is None:
        raise ErrorSeguridad("empresa no válida")
    usuarios = []
    for u in store.list_users_by_company(company_id):
        d = u.to_dict()  # sin password_hash por defecto
        fila = reg.mfa_leer(u.user_id)
        d["mfa"] = {"inscrito": fila is not None,
                    "confirmado": bool(fila and fila.get("confirmado")),
                    "codigos_restantes": reg.codigos_restantes(u.user_id)}
        usuarios.append(d)
    auditoria = [e.to_dict() for e in store.list_audit(company_id)]
    export = {
        "formato": "zayvero-os-export/1",
        "empresa": {"company_id": company.company_id, "name": company.name,
                    "status": company.status},
        "usuarios": usuarios,
        "politicas_retencion": retencion_efectiva(reg, company_id),
        "auditoria_eventos": len(auditoria),
        "auditoria": auditoria,
    }
    _auditar(store, company_id, _ctx_user(ctx), EXPORTACION_DATOS,
             metadata={"formato": "json", "usuarios": len(usuarios),
                       "eventos": len(auditoria)})
    return export


# =====================================================================
# Borrado de cuenta: soft-delete + aprobación humana (módulo aprobaciones)
# =====================================================================
def _aprobaciones(company_id: str):
    from aprobaciones.api import Aprobaciones, ErrorAprobacion
    try:
        return Aprobaciones(company_id)
    except ErrorAprobacion as e:
        raise ErrorSeguridad(
            "el borrado requiere PostgreSQL (aprobaciones): " + str(e))


def borrado_solicitar(store, reg: RegistroSeguridad, ctx,
                      motivo: str) -> dict:
    """Paso 1: el owner solicita el borrado. Crea una solicitud de
    aprobación tipo `eliminacion_cuenta` (siempre requiere aprobación
    humana: ver aprobaciones/modelo.py)."""
    _requerir_owner(store, ctx)
    company_id = _ctx_company(ctx)
    motivo = (motivo or "").strip()
    if not motivo:
        raise ErrorSeguridad("el motivo es obligatorio")
    apr = _aprobaciones(company_id)
    sol = apr.solicitar(tipo="eliminacion_cuenta",
                        solicitante=_ctx_user(ctx),
                        payload={"company_id": company_id,
                                 "accion": "soft-delete",
                                 "politica": "docs/politica-retencion.md"},
                        motivo=motivo)
    _auditar(store, company_id, _ctx_user(ctx), CUENTA_BORRADO_SOLICITADO,
             resource=f"aprobacion:{sol['aprobacion_id']}",
             metadata={"motivo": motivo})
    return {"aprobacion_id": sol["aprobacion_id"],
            "estado": sol["estado"]}


def borrado_ejecutar(store, reg: RegistroSeguridad, ctx,
                     aprobacion_id: str) -> dict:
    """Paso 2: tras la aprobación (owner/admin vía el módulo
    aprobaciones), ejecuta el soft-delete: empresa `disabled`, todas sus
    sesiones revocadas. Los datos se CONSERVAN según la política de
    retención (borrado lógico, no físico)."""
    _requerir_owner(store, ctx)
    company_id = _ctx_company(ctx)
    apr = _aprobaciones(company_id)
    sol = apr.obtener((aprobacion_id or "").strip())
    if sol is None:
        raise NoEncontrado("aprobación no encontrada")
    if sol.get("estado") != "aprobada":
        raise ErrorSeguridad("la solicitud no está aprobada")
    if (sol.get("payload") or {}).get("company_id") != company_id:
        raise ErrorSeguridad("la aprobación no corresponde a esta empresa")
    company = store.get_company(company_id)
    if company is None:
        raise ErrorSeguridad("empresa no válida")
    company.status = "disabled"
    store.save_company(company)
    revocadas = 0
    for s in store.list_sessions():
        if s.company_id == company_id and not s.revoked:
            s.revoked = True
            store.save_session(s)
            revocadas += 1
    apr.marcar_ejecutada(sol["aprobacion_id"])
    _auditar(store, company_id, _ctx_user(ctx), CUENTA_BORRADO_EJECUTADO,
             resource=f"aprobacion:{sol['aprobacion_id']}",
             metadata={"sesiones_revocadas": revocadas,
                       "retencion": "datos conservados según política"})
    return {"borrada": True, "modo": "soft-delete",
            "sesiones_revocadas": revocadas}


# =====================================================================
# Políticas de retención
# =====================================================================
# Matriz por defecto (detalle y fundamento en docs/politica-retencion.md).
POLITICAS_DEFECTO = {
    "sesiones": {
        "plazo_dias": 90, "accion": "eliminar", "base": "ultima_actividad",
        "descripcion": "Sesiones expiradas o revocadas."},
    "auditoria_seguridad": {
        "plazo_dias": 2555, "accion": "archivar", "base": "creacion",
        "descripcion": "Eventos de auditoría (7 años, requisito contable)."},
    "codigos_recuperacion_usados": {
        "plazo_dias": 365, "accion": "eliminar", "base": "creacion",
        "descripcion": "Códigos de recuperación ya usados."},
    "solicitudes_recuperacion": {
        "plazo_dias": 730, "accion": "eliminar", "base": "creacion",
        "descripcion": "Solicitudes de recuperación asistida."},
    "intentos_mfa": {
        "plazo_dias": 90, "accion": "eliminar", "base": "ultima_actividad",
        "descripcion": "Cubetas de intentos fallidos de MFA."},
    "datos_empresa_eliminada": {
        "plazo_dias": 30, "accion": "eliminar", "base": "ultima_actividad",
        "descripcion": "Tras el soft-delete: 30 días de gracia; la "
                       "eliminación definitiva requiere confirmación humana."},
}


def retencion_efectiva(reg: RegistroSeguridad, company_id: str) -> dict:
    """Matriz por defecto + personalizaciones de la empresa."""
    efectivas = {k: dict(v, origen="defecto")
                 for k, v in POLITICAS_DEFECTO.items()}
    for tipo, p in reg.retencion_leer(company_id).items():
        if tipo in efectivas:
            efectivas[tipo] = dict(efectivas[tipo], **p)
    return efectivas


def retencion_actualizar(reg: RegistroSeguridad, store, ctx, tipo_dato: str,
                         plazo_dias: int, accion: str,
                         base: str = "ultima_actividad") -> dict:
    """Personaliza la política de un tipo de dato. Requiere company.admin
    (owner/admin)."""
    _requerir_permiso(store, ctx, "company.admin", "retencion/actualizar")
    tipo_dato = (tipo_dato or "").strip()
    if tipo_dato not in POLITICAS_DEFECTO:
        raise ErrorSeguridad("tipo_dato no válido")
    company_id = _ctx_company(ctx)
    reg.retencion_guardar(company_id, tipo_dato, int(plazo_dias), accion,
                          base or "ultima_actividad", _ctx_user(ctx))
    _auditar(store, company_id, _ctx_user(ctx), RETENCION_ACTUALIZADA,
             resource=f"retencion:{tipo_dato}",
             metadata={"plazo_dias": int(plazo_dias), "accion": accion})
    return {"actualizada": True, "tipo_dato": tipo_dato}
