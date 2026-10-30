"""BLOQUE PLATAFORMA SAAS (2026-10-13) — Despachador de rutas `saas/*`.

CONTRATO:
    def rutas(): return {...}   # {"saas/...": fn}
    # cada fn: (bundle, ctx, query|body) -> dict JSON-serializable

`manejar(bundle, ctx, method, sub, payload)` -> (status, dict):
despacha `sub` ("saas/..."), verifica permisos y mapea ErrorSaas a
(status, {"ok": False, "error":..., "codigo":...}).

Seguridad:
- `saas/registro`, `saas/ayuda`, `saas/planes` y `saas/usuarios/aceptar`
  son PÚBLICAS (ctx puede ser None). Todo lo demás exige ctx autenticado.
- company_id SIEMPRE de ctx (anti-IDOR): ningún parámetro del usuario
  elige la empresa.
- Permisos por ruta sobre la matriz de tenant/roles.py (ctx.has(...)).
- Registro con anti-enumeración: duplicados de fiscal/nombre devuelven
  el MISMO 409 genérico sin decir qué campo colisionó.
- Los tokens de invitación se guardan como SHA-256; el crudo se muestra
  UNA vez y el sistema NO envía correos.

Integración (nota para el coordinador): este despachador NO toca
webapp/os_routes.py. Para exponerlo, montar en el servidor:
  - POST público  /api/v1/saas/registro
  - GET  público  /api/v1/saas/planes y /api/v1/saas/ayuda
  - POST público  /api/v1/saas/usuarios/aceptar
  - resto bajo /api/v1/os/saas/* con el gate ZAYVERO_OS_ENABLED, rate
    limiting, permiso os.read/os.write y CSRF ya existentes.
El CSRF de los POST autenticados lo aplica la capa de montaje.
"""
from __future__ import annotations

import os as _os
import sys
import uuid

sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from os_saas import almacen as _alm  # noqa: E402
from os_saas import ayuda as _ayuda_mod  # noqa: E402
from os_saas.modelo import (  # noqa: E402
    PLANES, SIN_FACTURACION, ErrorSaas, limite_de, normalizar_email,
    normalizar_fiscal, normalizar_nombre, periodo_actual, plan_de,
    validar_email, validar_estado_paso, validar_fiscal, validar_moneda,
    validar_nombre, validar_paso, validar_periodo, validar_sucursales,
    PASOS_ONBOARDING,
)

__all__ = ["rutas", "manejar"]


# ---------------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------------

def _ctx_requerido(ctx):
    if ctx is None or not getattr(ctx, "company_id", None):
        raise ErrorSaas(401, "no_autenticado",
                        "Se requiere autenticación.")


def _permiso(ctx, permiso: str):
    _ctx_requerido(ctx)
    tiene = ctx.has(permiso) if hasattr(ctx, "has") else False
    if not tiene:
        raise ErrorSaas(403, "permiso_denegado",
                        f"Se requiere el permiso '{permiso}'.")


def _tenant_store():
    from tenant import create_tenant_store
    return create_tenant_store()


def _plan_cuenta(cur, empresa: dict) -> dict:
    """Plan vigente para una empresa: el de la cuenta creadora."""
    cuenta = _alm.obtener_cuenta(cur, empresa["creado_por_email"])
    plan_id = cuenta["plan_id"] if cuenta else empresa["plan_id"]
    return plan_de(plan_id)


def _mensaje_limite(plan: dict, metrica: str, limite: int,
                    uso: int) -> str:
    nombres = {
        "max_usuarios": "usuarios activos",
        "max_sucursales": "sucursales/almacenes",
        "max_ia_mes": "ejecuciones de IA por mes",
        "max_almacenamiento_mb": "MB de almacenamiento",
        "max_llamadas_api_mes": "llamadas a la API por mes",
        "max_empresas_por_cuenta": "empresas por cuenta",
    }
    return (
        f"Límite del plan {plan['nombre']} alcanzado: "
        f"{nombres.get(metrica, metrica)} (máximo {limite}, uso actual "
        f"{uso}). Para continuar, suba de plan. {SIN_FACTURACION}.")


def _verificar_cupo_usuarios(cur, company_id: str, plan: dict,
                             extra: int = 1) -> None:
    activos = _alm.contar_usuarios_activos(cur, company_id)
    pendientes = _alm.contar_invitaciones_pendientes(cur, company_id)
    limite = limite_de(plan, "max_usuarios")
    if activos + pendientes + extra > limite:
        raise ErrorSaas(403, "limite_plan",
                        _mensaje_limite(plan, "max_usuarios", limite,
                                        activos + pendientes))


_JERARQUIA_ROL = {"owner": 3, "admin": 2, "analyst": 1, "viewer": 0}


def _puede_asignar_rol(rol_ctx: str, rol_nuevo: str) -> bool:
    from tenant import roles as _roles
    if not _roles.is_valid_role(rol_nuevo):
        raise ErrorSaas(400, "rol_invalido",
                        f"Rol '{rol_nuevo}' no válido.")
    if rol_ctx == "owner":
        return True
    return _JERARQUIA_ROL.get(rol_ctx, 0) > _JERARQUIA_ROL.get(rol_nuevo, 0)


# ---------------------------------------------------------------------------
# Rutas públicas
# ---------------------------------------------------------------------------

def r_planes(bundle, ctx, query):
    """GET saas/planes — catálogo público de planes (precios de referencia,
    sin facturación real)."""
    return {
        "ok": True,
        "planes": [
            {
                "id": p["id"], "nombre": p["nombre"],
                "precio_usd": p["precio_usd"],
                "limites": dict(p["limites"]),
                "modulos": list(p["modulos"]),
                "facturacion": p["facturacion"],
                **({"nota_precio": p["nota_precio"]}
                   if "nota_precio" in p else {}),
            }
            for p in (PLANES[k] for k in
                      ("starter", "business", "professional", "enterprise"))
        ],
        "nota_facturacion": SIN_FACTURACION,
    }


def r_ayuda(bundle, ctx, query):
    """GET saas/ayuda?idioma=&modulo= — centro de ayuda estático ES/EN."""
    res = _ayuda_mod.obtener_ayuda(_q(query, "idioma", "es"),
                                   _q(query, "modulo", "") or None)
    if "error" in res:
        raise ErrorSaas(400, "modulo_ayuda_desconocido",
                        "Módulo de ayuda desconocido. Módulos: "
                        + ", ".join(res["modulos"]))
    return {"ok": True, **res}


def r_registro(bundle, ctx, body):
    """POST saas/registro — alta segura de empresa (público).

    Crea: empresa en el tenant + usuario propietario + fila os_companies
    + saas_empresas + saas_cuentas + onboarding inicial + evento
    'saas_registro'.

    Diseño en fases (anti-deadlock documentado):
    - El tenant (PostgresTenantStore._company_tx) inserta en os_companies
      dentro de SU transacción. Si el SaaS insertara primero esa fila y
      mantuviera la transacción abierta durante los writes del tenant,
      el INSERT del tenant bloquearía para siempre (deadlock de
      aplicación: verificado en pruebas).
    - Fase 1: validaciones puras + chequeos de lectura en PG (duplicados
      -> 409 genérico anti-enumeración; cupo de empresas -> 403).
    - Fase 2: writes del tenant (save_company + create_user). Si falla,
      el SaaS aún no escribió nada: no hay nada que compensar.
    - Fase 3: writes del SaaS en UNA transacción (empresa + cuenta +
      auditoría). La unicidad real la garantizan los UNIQUE de PG: una
      violación (carrera entre fase 1 y 3) se mapea al MISMO 409
      genérico y se compensa el tenant (best-effort).
    """
    if not isinstance(body, dict):
        raise ErrorSaas(400, "cuerpo_invalido",
                        "El cuerpo debe ser un objeto JSON.")
    nombre = validar_nombre(body.get("nombre", ""))
    fiscal = validar_fiscal(body.get("identificador_fiscal", ""))
    moneda = validar_moneda(body.get("moneda_base", "USD"))
    plan = plan_de(body.get("plan_id") or "starter")
    sucursales = validar_sucursales(
        body.get("sucursales"),
        limite_de(plan, "max_sucursales"))
    email = validar_email(body.get("email", ""))
    nombre_owner = " ".join(str(body.get("nombre_propietario", "")).split())
    if len(nombre_owner) < 2 or len(nombre_owner) > 120:
        raise ErrorSaas(400, "nombre_propietario_invalido",
                        "El nombre del propietario debe tener entre 2 y "
                        "120 caracteres.")
    password = body.get("password", "")
    from tenant import password_policy as _pp
    errores_pwd = _pp.validate_new_password(password)
    if errores_pwd:
        raise ErrorSaas(400, "password_invalido", errores_pwd[0])

    nombre_norm = normalizar_nombre(nombre)
    company_id = "EMP-" + uuid.uuid4().hex[:12]

    # Fase 1: chequeos de lectura (transacción corta, solo SELECTs).
    conn = _alm.conectar()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        try:
            # Anti-enumeración: un solo booleano, un solo mensaje.
            if _alm.existe_fiscal_o_nombre(cur, fiscal, nombre_norm):
                raise ErrorSaas(
                    409, "registro_rechazado",
                    "No fue posible completar el registro con esos datos. "
                    "Revise la información e inténtelo de nuevo.")
            # Límite de empresas por cuenta (dato propio: mensaje claro).
            n_empresas = _alm.contar_empresas_cuenta(cur, email)
            max_emp = limite_de(plan, "max_empresas_por_cuenta")
            if n_empresas >= max_emp:
                raise ErrorSaas(
                    403, "limite_plan",
                    f"El plan {plan['nombre']} permite hasta {max_emp} "
                    f"empresas por cuenta y esta cuenta ya tiene "
                    f"{n_empresas}. Para registrar otra empresa, suba de "
                    f"plan. {SIN_FACTURACION}.")
        finally:
            cur.close()
    finally:
        conn.close()

    # Fase 2: writes del tenant (su propia transacción; crea la fila
    # os_companies que la auditoría SaaS referencia).
    from tenant import auth as _auth
    from tenant.models import Company as _Company
    store = _tenant_store()
    store.save_company(_Company(company_id=company_id, name=nombre,
                                status="active"))
    try:
        _auth.create_user(
            store, company_id=company_id, email=email,
            name=nombre_owner, password=password, role_id="owner")
    except Exception as e:
        _eliminar_empresa_tenant(store, company_id)
        raise _mapear_error_tenant(e)

    # Fase 3: writes del SaaS en una transacción (con auditoría).
    conn = _alm.conectar()
    try:
        conn.autocommit = False
        cur = conn.cursor()
        try:
            _alm.crear_empresa(
                cur, company_id=company_id, nombre=nombre,
                nombre_norm=nombre_norm, fiscal_norm=fiscal, moneda=moneda,
                plan_id=plan["id"], sucursales=sucursales,
                creado_por_email=email, actor=email)
            _alm.guardar_cuenta(cur, email, plan["id"])
            conn.commit()
        except Exception as e:
            try:
                conn.rollback()
            except Exception:
                pass
            # Compensar el tenant: el SaaS no quedó registrado.
            _eliminar_empresa_tenant(store, company_id)
            raise _mapear_error_saas(e)
        finally:
            cur.close()
    finally:
        conn.close()
    return {
        "ok": True,
        "company_id": company_id,
        "nombre": nombre,
        "plan_id": plan["id"],
        "moneda_base": moneda,
        "sucursales": sucursales,
        "siguiente_paso": ("Inicie sesión con su correo y contraseña; la "
                           "checklist de puesta en marcha lo guiará."),
    }


def _eliminar_empresa_tenant(store, company_id: str) -> None:
    """Compensación best-effort si el registro SaaS falla a mitad: elimina
    la empresa y sus usuarios/sesiones del tenant store. Soporta backend
    PG (SQL directo) y JSON (reescritura de listas)."""
    try:
        from tenant.store_pg import PostgresTenantStore
        es_pg = isinstance(store, PostgresTenantStore)
    except Exception:
        es_pg = False
    try:
        if es_pg:
            conn = _alm.conectar()
            try:
                conn.autocommit = False
                cur = conn.cursor()
                try:
                    cur.execute("DELETE FROM os_sesiones_v2 "
                                "WHERE empresa_id=%s",
                                (company_id,))
                    cur.execute("DELETE FROM os_usuarios WHERE empresa_id=%s",
                                (company_id,))
                    cur.execute("DELETE FROM os_empresas WHERE id=%s",
                                (company_id,))
                    cur.execute("DELETE FROM os_company_lock "
                                "WHERE company_id=%s", (company_id,))
                    cur.execute("DELETE FROM os_companies "
                                "WHERE company_id=%s", (company_id,))
                    conn.commit()
                except Exception:
                    conn.rollback()
                finally:
                    cur.close()
            finally:
                conn.close()
        else:
            comps = [c for c in store._load_list(store._companies_file)
                     if c.get("company_id") != company_id]
            store._save_list(store._companies_file, comps)
            users = [u for u in store._load_list(store._users_file)
                     if u.get("company_id") != company_id]
            store._save_list(store._users_file, users)
            sess = [s for s in store._load_list(store._sessions_file)
                    if s.get("company_id") != company_id]
            store._save_list(store._sessions_file, sess)
    except Exception:
        pass


def _mapear_error_tenant(e: Exception) -> ErrorSaas:
    from tenant.auth import AuthError
    if isinstance(e, AuthError):
        # Anti-enumeración: no distinguir "email ya registrado".
        return ErrorSaas(
            409, "registro_rechazado",
            "No fue posible completar el registro con esos datos. Revise "
            "la información e inténtelo de nuevo.")
    return ErrorSaas(500, "error_interno",
                     "Error interno del módulo SaaS.")


def _mapear_error_saas(e: Exception) -> ErrorSaas:
    """Mapea errores de la fase 3 del registro. Una violación de UNIQUE
    (carrera entre el chequeo de lectura y el INSERT) se reporta con el
    MISMO 409 genérico anti-enumeración que el chequeo."""
    if isinstance(e, ErrorSaas):
        return e
    try:
        from psycopg import errors as _pg_errors
        if isinstance(e, _pg_errors.UniqueViolation):
            return ErrorSaas(
                409, "registro_rechazado",
                "No fue posible completar el registro con esos datos. "
                "Revise la información e inténtelo de nuevo.")
    except Exception:
        pass
    return ErrorSaas(500, "error_interno",
                     "Error interno del módulo SaaS.")


def r_aceptar_invitacion(bundle, ctx, body):
    """POST saas/usuarios/aceptar — aceptar invitación (público, con token).

    Token inválido, vencido o usado → MISMO 404 (anti-enumeración).

    Diseño en fases (anti-deadlock): la transacción SaaS NUNCA se mantiene
    abierta durante los writes del tenant (ni siquiera con solo SELECTs:
    el AccessShare bloquearía un ALTER de migración disparado por
    auto_migrate del tenant store). Fase 1: lectura; fase 2: create_user;
    fase 3: marcar usada + auditoría con re-validación bajo lock (carrera
    de doble aceptación → 409).
    """
    if not isinstance(body, dict):
        raise ErrorSaas(400, "cuerpo_invalido",
                        "El cuerpo debe ser un objeto JSON.")
    token = str(body.get("token", "") or "")
    nombre = " ".join(str(body.get("nombre", "")).split())
    password = body.get("password", "")
    if not token:
        raise ErrorSaas(404, "invitacion_invalida",
                        "La invitación no es válida o ha vencido.")
    if len(nombre) < 2 or len(nombre) > 120:
        raise ErrorSaas(400, "nombre_invalido",
                        "El nombre debe tener entre 2 y 120 caracteres.")
    from tenant import password_policy as _pp
    errores_pwd = _pp.validate_new_password(password)
    if errores_pwd:
        raise ErrorSaas(400, "password_invalido", errores_pwd[0])

    # Fase 1: lectura (autocommit; sin transacción abierta).
    conn = _alm.conectar()
    try:
        conn.autocommit = True
        cur = conn.cursor()
        try:
            inv = _alm.obtener_invitacion_valida(cur, token)
            if inv is None:
                raise ErrorSaas(404, "invitacion_invalida",
                                "La invitación no es válida o ha vencido.")
            empresa = _alm.obtener_empresa(cur, inv["company_id"])
            if empresa is None or empresa["estado"] != "activa":
                raise ErrorSaas(404, "invitacion_invalida",
                                "La invitación no es válida o ha vencido.")
            plan = _plan_cuenta(cur, empresa)
            _verificar_cupo_usuarios(cur, inv["company_id"], plan)
        finally:
            cur.close()
    finally:
        conn.close()

    # Fase 2: write del tenant (su propia transacción).
    from tenant import auth as _auth
    store = _tenant_store()
    try:
        user = _auth.create_user(
            store, company_id=inv["company_id"],
            email=inv["email"], name=nombre, password=password,
            role_id=inv["rol"])
    except Exception as e:
        raise _mapear_error_tenant(e)

    # Fase 3: marcar usada + auditoría (re-valida bajo lock: si otra
    # aceptación la usó primero → 409, y el usuario ya creado queda
    # como miembro válido de todos modos).
    conn = _alm.conectar()
    try:
        conn.autocommit = False
        cur = conn.cursor()
        try:
            cur.execute(
                "SELECT usado_at FROM saas_invitaciones "
                "WHERE invitacion_id=%s FOR UPDATE",
                (inv["invitacion_id"],))
            fila = cur.fetchone()
            if fila is None or fila[0] is not None:
                conn.rollback()
                raise ErrorSaas(409, "invitacion_reutilizada",
                                "La invitación ya fue utilizada.")
            _alm.marcar_invitacion_usada(cur, inv["invitacion_id"])
            _alm.auditar_saas(cur, inv["company_id"], "saas_usuario",
                              user.user_id, ref_id=user.user_id,
                              detalle={"accion": "invitacion_aceptada",
                                       "email": inv["email"],
                                       "rol": inv["rol"]})
            conn.commit()
        except ErrorSaas:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            cur.close()
    finally:
        conn.close()
    return {"ok": True, "user_id": user.user_id,
            "email": inv["email"], "rol": inv["rol"],
            "company_id": inv["company_id"],
            "siguiente_paso": "Inicie sesión con su correo y "
                              "contraseña."}


# ---------------------------------------------------------------------------
# Rutas autenticadas
# ---------------------------------------------------------------------------

def r_mi_plan(bundle, ctx, query):
    """GET saas/plan — plan vigente + uso resumido de mi empresa."""
    _permiso(ctx, "os.read")
    conn = _alm.conectar()
    try:
        cur = conn.cursor()
        try:
            empresa = _alm.obtener_empresa(cur, ctx.company_id)
            if empresa is None:
                raise ErrorSaas(404, "empresa_no_encontrada",
                                "La empresa no está registrada en el SaaS.")
            plan = _plan_cuenta(cur, empresa)
            uso = _alm.obtener_metrica(cur, ctx.company_id,
                                       periodo_actual())
            return {
                "ok": True,
                "plan": {"id": plan["id"], "nombre": plan["nombre"],
                         "precio_usd": plan["precio_usd"],
                         "limites": dict(plan["limites"]),
                         "modulos": list(plan["modulos"])},
                "facturacion": SIN_FACTURACION,
                "uso_mes_actual": {
                    "periodo": periodo_actual(),
                    "ejecuciones_ia": uso["ejecuciones_ia"],
                    "llamadas_api": uso["llamadas_api"],
                    "almacenamiento_mb": round(
                        uso["almacenamiento_bytes"] / (1024 * 1024), 2),
                    "usuarios_activos": _alm.contar_usuarios_activos(
                        cur, ctx.company_id),
                },
            }
        finally:
            cur.close()
    finally:
        conn.close()


def r_limites(bundle, ctx, query):
    """GET saas/limites — límites del plan vs uso actual (verificables)."""
    _permiso(ctx, "os.read")
    conn = _alm.conectar()
    try:
        cur = conn.cursor()
        try:
            empresa = _alm.obtener_empresa(cur, ctx.company_id)
            if empresa is None:
                raise ErrorSaas(404, "empresa_no_encontrada",
                                "La empresa no está registrada en el SaaS.")
            plan = _plan_cuenta(cur, empresa)
            uso = _alm.obtener_metrica(cur, ctx.company_id,
                                       periodo_actual())
            usuarios = _alm.contar_usuarios_activos(cur, ctx.company_id)
            pendientes = _alm.contar_invitaciones_pendientes(
                cur, ctx.company_id)
            almacenamiento_mb = uso["almacenamiento_bytes"] / (1024 * 1024)
            actual = {
                "max_usuarios": usuarios + pendientes,
                "max_sucursales": len(empresa["sucursales"]),
                "max_ia_mes": uso["ejecuciones_ia"],
                "max_almacenamiento_mb": round(almacenamiento_mb, 2),
                "max_llamadas_api_mes": uso["llamadas_api"],
            }
            detalle = {}
            for metrica, lim in plan["limites"].items():
                if metrica == "max_empresas_por_cuenta":
                    # Límite a nivel de CUENTA, no de empresa.
                    n_emp = _alm.contar_empresas_cuenta(
                        cur, empresa["creado_por_email"])
                    detalle[metrica] = {
                        "limite": lim, "uso": n_emp,
                        "porcentaje": round(100.0 * n_emp / lim, 1)
                        if lim else 0,
                        "alcanzado": n_emp >= lim,
                        "nivel": "cuenta",
                    }
                    continue
                u = actual[metrica]
                detalle[metrica] = {
                    "limite": lim, "uso": u,
                    "porcentaje": round(100.0 * u / lim, 1) if lim else 0,
                    "alcanzado": u >= lim,
                    "nivel": "empresa",
                }
            return {"ok": True, "plan_id": plan["id"],
                    "plan_nombre": plan["nombre"],
                    "periodo": periodo_actual(),
                    "limites": detalle,
                    "facturacion": SIN_FACTURACION}
        finally:
            cur.close()
    finally:
        conn.close()


def r_cambiar_plan(bundle, ctx, body):
    """POST saas/cuenta/plan — cambiar el plan de la cuenta.

    Solo la cuenta creadora (ctx.email == creado_por_email) con
    company.admin. Si alguna empresa de la cuenta supera los límites del
    plan nuevo, el cambio se BLOQUEA con el detalle.
    """
    _permiso(ctx, "os.write")
    _permiso(ctx, "company.admin")
    if not isinstance(body, dict):
        raise ErrorSaas(400, "cuerpo_invalido",
                        "El cuerpo debe ser un objeto JSON.")
    plan_nuevo = plan_de(body.get("plan_id", ""))
    email_cuenta = normalizar_email(getattr(ctx, "email", ""))
    conn = _alm.conectar()
    try:
        conn.autocommit = False
        cur = conn.cursor()
        try:
            empresa_actual = _alm.obtener_empresa(cur, ctx.company_id)
            if empresa_actual is None:
                raise ErrorSaas(404, "empresa_no_encontrada",
                                "La empresa no está registrada en el SaaS.")
            if empresa_actual["creado_por_email"] != email_cuenta:
                raise ErrorSaas(
                    403, "permiso_denegado",
                    "Solo la cuenta que creó la empresa puede cambiar el "
                    "plan.")
            empresas = _alm.empresas_de_cuenta(cur, email_cuenta)
            bloqueos = []
            for emp in empresas:
                cid = emp["company_id"]
                usuarios = (_alm.contar_usuarios_activos(cur, cid)
                            + _alm.contar_invitaciones_pendientes(cur, cid))
                uso = _alm.obtener_metrica(cur, cid, periodo_actual())
                suc = len((emp.get("sucursales") or []))
                cheques = (
                    ("max_usuarios", usuarios),
                    ("max_sucursales", suc),
                    ("max_ia_mes", uso["ejecuciones_ia"]),
                    ("max_almacenamiento_mb",
                     uso["almacenamiento_bytes"] / (1024 * 1024)),
                    ("max_llamadas_api_mes", uso["llamadas_api"]),
                )
                for metrica, valor in cheques:
                    lim = limite_de(plan_nuevo, metrica)
                    if valor > lim:
                        bloqueos.append({
                            "company_id": cid, "metrica": metrica,
                            "uso": valor, "limite": lim})
            if bloqueos:
                conn.rollback()
                raise ErrorSaas(
                    409, "limite_plan",
                    "No se puede bajar al plan "
                    f"{plan_nuevo['nombre']}: el uso actual supera sus "
                    f"límites en {len(bloqueos)} caso(s). Reduzca el uso o "
                    f"elija otro plan. Detalle en 'bloqueos'. "
                    f"{SIN_FACTURACION}.")
            _alm.guardar_cuenta(cur, email_cuenta, plan_nuevo["id"])
            for emp in empresas:
                cur.execute(
                    "UPDATE saas_empresas SET plan_id=%s, updated_at=now() "
                    "WHERE company_id=%s",
                    (plan_nuevo["id"], emp["company_id"]))
                _alm.auditar_saas(
                    cur, emp["company_id"], "saas_plan",
                    getattr(ctx, "user_id", ""),
                    ref_id=emp["company_id"],
                    detalle={"plan_anterior": emp["plan_id"],
                             "plan_nuevo": plan_nuevo["id"],
                             "precio_usd_referencia":
                                 plan_nuevo["precio_usd"]})
            conn.commit()
            return {"ok": True, "plan_id": plan_nuevo["id"],
                    "plan_nombre": plan_nuevo["nombre"],
                    "empresas_afectadas": len(empresas),
                    "facturacion": SIN_FACTURACION}
        except ErrorSaas:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            cur.close()
    finally:
        conn.close()


def r_config_empresa(bundle, ctx, body):
    """POST saas/empresa/config — moneda base y sucursales/almacenes
    (respeta max_sucursales del plan; audita 'saas_config')."""
    _permiso(ctx, "os.write")
    _permiso(ctx, "company.admin")
    if not isinstance(body, dict):
        raise ErrorSaas(400, "cuerpo_invalido",
                        "El cuerpo debe ser un objeto JSON.")
    conn = _alm.conectar()
    try:
        conn.autocommit = False
        cur = conn.cursor()
        try:
            empresa = _alm.obtener_empresa(cur, ctx.company_id)
            if empresa is None:
                raise ErrorSaas(404, "empresa_no_encontrada",
                                "La empresa no está registrada en el SaaS.")
            plan = _plan_cuenta(cur, empresa)
            moneda = (validar_moneda(body["moneda_base"])
                      if "moneda_base" in body else None)
            sucursales = (validar_sucursales(
                body["sucursales"], limite_de(plan, "max_sucursales"))
                if "sucursales" in body else None)
            cambios = {}
            if moneda is not None:
                cambios["moneda"] = moneda
            if sucursales is not None:
                cambios["sucursales"] = sucursales
            if not cambios:
                raise ErrorSaas(400, "sin_cambios",
                                "Indique 'moneda_base' y/o 'sucursales'.")
            actualizada = _alm.actualizar_empresa(
                cur, ctx.company_id, **cambios)
            _alm.auditar_saas(cur, ctx.company_id, "saas_config",
                              getattr(ctx, "user_id", ""),
                              ref_id=ctx.company_id, detalle=cambios)
            conn.commit()
            return {"ok": True, "empresa": actualizada}
        except ErrorSaas:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            cur.close()
    finally:
        conn.close()


def r_empresa(bundle, ctx, query):
    """GET saas/empresa — ficha de mi empresa."""
    _permiso(ctx, "os.read")
    conn = _alm.conectar()
    try:
        cur = conn.cursor()
        try:
            empresa = _alm.obtener_empresa(cur, ctx.company_id)
            if empresa is None:
                raise ErrorSaas(404, "empresa_no_encontrada",
                                "La empresa no está registrada en el SaaS.")
            plan = _plan_cuenta(cur, empresa)
            empresa["plan_nombre"] = plan["nombre"]
            empresa["usuarios_activos"] = _alm.contar_usuarios_activos(
                cur, ctx.company_id)
            return {"ok": True, "empresa": empresa}
        finally:
            cur.close()
    finally:
        conn.close()


def r_resumen_uso(bundle, ctx, query):
    """GET saas/uso?desde=YYYY-MM&hasta=YYYY-MM — métricas por período."""
    _permiso(ctx, "os.read")
    hasta = validar_periodo(_q(query, "hasta", "") or periodo_actual())
    desde = validar_periodo(_q(query, "desde", "") or _retroceder(hasta, 5))
    if desde > hasta:
        raise ErrorSaas(400, "rango_invalido",
                        "'desde' no puede ser posterior a 'hasta'.")
    conn = _alm.conectar()
    try:
        cur = conn.cursor()
        try:
            empresa = _alm.obtener_empresa(cur, ctx.company_id)
            if empresa is None:
                raise ErrorSaas(404, "empresa_no_encontrada",
                                "La empresa no está registrada en el SaaS.")
            plan = _plan_cuenta(cur, empresa)
            periodos = _alm.metricas_rango(cur, ctx.company_id, desde,
                                           hasta)
            for p in periodos:
                p["almacenamiento_mb"] = round(
                    p.pop("almacenamiento_bytes") / (1024 * 1024), 2)
            totales = {
                "ejecuciones_ia": sum(p["ejecuciones_ia"]
                                      for p in periodos),
                "llamadas_api": sum(p["llamadas_api"] for p in periodos),
                "usuarios_activos": _alm.contar_usuarios_activos(
                    cur, ctx.company_id),
            }
            return {
                "ok": True, "company_id": ctx.company_id,
                "plan_id": plan["id"], "desde": desde, "hasta": hasta,
                "periodos": periodos, "totales": totales,
                "limites_mensuales": dict(plan["limites"]),
                "nota": ("Base para futura facturación; en esta fase no "
                         "se genera ningún cargo."),
            }
        finally:
            cur.close()
    finally:
        conn.close()


def _retroceder(periodo: str, meses: int) -> str:
    y, m = int(periodo[:4]), int(periodo[5:7])
    m -= meses
    while m < 1:
        m += 12
        y -= 1
    return f"{y:04d}-{m:02d}"


def r_registrar_uso(bundle, ctx, body):
    """POST saas/uso/registrar — suma consumo al mes actual.

    HACE CUMPLIR los límites: si el consumo proyectado supera el límite
    del plan, se bloquea con 403 antes de registrar nada.
    """
    _permiso(ctx, "os.write")
    if not isinstance(body, dict):
        raise ErrorSaas(400, "cuerpo_invalido",
                        "El cuerpo debe ser un objeto JSON.")

    def _entero(nombre):
        v = body.get(nombre, 0)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise ErrorSaas(400, "consumo_invalido",
                            f"'{nombre}' debe ser un entero >= 0.")
        return v

    ia = _entero("ejecuciones_ia")
    api = _entero("llamadas_api")
    alm = _entero("almacenamiento_bytes")
    if ia == api == alm == 0:
        raise ErrorSaas(400, "consumo_invalido",
                        "Indique al menos un consumo mayor que cero.")
    periodo = periodo_actual()
    conn = _alm.conectar()
    try:
        conn.autocommit = False
        cur = conn.cursor()
        try:
            empresa = _alm.obtener_empresa(cur, ctx.company_id)
            if empresa is None:
                raise ErrorSaas(404, "empresa_no_encontrada",
                                "La empresa no está registrada en el SaaS.")
            plan = _plan_cuenta(cur, empresa)
            actual = _alm.obtener_metrica(cur, ctx.company_id, periodo)
            cheques = (
                ("max_ia_mes", actual["ejecuciones_ia"] + ia, "entero"),
                ("max_llamadas_api_mes",
                 actual["llamadas_api"] + api, "entero"),
                ("max_almacenamiento_mb",
                 (actual["almacenamiento_bytes"] + alm) / (1024 * 1024),
                 "mb"),
            )
            for metrica, proyectado, _ in cheques:
                lim = limite_de(plan, metrica)
                if proyectado > lim:
                    conn.rollback()
                    raise ErrorSaas(
                        403, "limite_plan",
                        _mensaje_limite(plan, metrica, lim,
                                        int(proyectado))
                        + " El consumo NO fue registrado.")
            fila = _alm.sumar_uso(cur, ctx.company_id, periodo,
                                  ejecuciones_ia=ia, llamadas_api=api,
                                  almacenamiento_bytes=alm)
            conn.commit()
            return {"ok": True, "periodo": periodo,
                    "acumulado": fila}
        except ErrorSaas:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            cur.close()
    finally:
        conn.close()


def r_onboarding(bundle, ctx, query):
    """GET saas/onboarding — checklist con estado por paso.

    Algunos pasos se detectan automáticamente (invitar_usuarios,
    cargar_catalogo, primera_venta); crear_empresa viene del registro;
    primer_reporte es manual.
    """
    _permiso(ctx, "os.read")
    conn = _alm.conectar()
    try:
        cur = conn.cursor()
        try:
            empresa = _alm.obtener_empresa(cur, ctx.company_id)
            if empresa is None:
                raise ErrorSaas(404, "empresa_no_encontrada",
                                "La empresa no está registrada en el SaaS.")
            guardados = _alm.obtener_onboarding(cur, ctx.company_id)
            auto = {}
            if _alm.contar_usuarios_activos(cur, ctx.company_id) >= 2:
                auto["invitar_usuarios"] = "auto"
            if _alm.hay_productos_catalogo(cur, ctx.company_id):
                auto["cargar_catalogo"] = "auto"
            if _alm.hay_ventas(cur, ctx.company_id):
                auto["primera_venta"] = "auto"
            pasos = []
            for pid, titulo, desc in PASOS_ONBOARDING:
                g = guardados.get(pid, {})
                estado = g.get("estado", "pendiente")
                origen = g.get("origen", "manual")
                if estado != "completado" and pid in auto:
                    estado, origen = "completado", auto[pid]
                pasos.append({"id": pid, "titulo": titulo,
                              "descripcion": desc, "estado": estado,
                              "origen": origen})
            hechos = sum(1 for p in pasos if p["estado"] == "completado")
            return {"ok": True, "pasos": pasos,
                    "completados": hechos, "total": len(pasos),
                    "porcentaje": round(100.0 * hechos / len(pasos), 1)}
        finally:
            cur.close()
    finally:
        conn.close()


def r_marcar_paso(bundle, ctx, body):
    """POST saas/onboarding/paso {paso, estado} — marcado manual."""
    _permiso(ctx, "os.write")
    if not isinstance(body, dict):
        raise ErrorSaas(400, "cuerpo_invalido",
                        "El cuerpo debe ser un objeto JSON.")
    paso = validar_paso(body.get("paso", ""))
    estado = validar_estado_paso(body.get("estado", ""))
    conn = _alm.conectar()
    try:
        conn.autocommit = False
        cur = conn.cursor()
        try:
            empresa = _alm.obtener_empresa(cur, ctx.company_id)
            if empresa is None:
                raise ErrorSaas(404, "empresa_no_encontrada",
                                "La empresa no está registrada en el SaaS.")
            pasos = _alm.marcar_paso_onboarding(
                cur, ctx.company_id, paso, estado,
                getattr(ctx, "user_id", ""), origen="manual")
            conn.commit()
            return {"ok": True, "paso": paso, "estado": estado,
                    "pasos": pasos}
        except ErrorSaas:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            cur.close()
    finally:
        conn.close()


def r_usuarios(bundle, ctx, query):
    """GET saas/usuarios — usuarios de MI empresa (aislamiento por ctx)."""
    _permiso(ctx, "os.read")
    store = _tenant_store()
    usuarios = []
    for u in store.list_users_by_company(ctx.company_id):
        if not u.email:
            continue  # stubs internos, no son usuarios reales
        usuarios.append({
            "user_id": u.user_id, "email": u.email, "nombre": u.name,
            "rol": u.role_id, "estado": u.status,
            "created_at": u.created_at,
        })
    usuarios.sort(key=lambda x: x["email"])
    return {"ok": True, "usuarios": usuarios,
            "total": len(usuarios)}


def r_invitar(bundle, ctx, body):
    """POST saas/usuarios/invitar {email, rol} — genera token (NO envía
    correos). El token crudo se devuelve UNA sola vez."""
    _permiso(ctx, "user.admin")
    if not isinstance(body, dict):
        raise ErrorSaas(400, "cuerpo_invalido",
                        "El cuerpo debe ser un objeto JSON.")
    email = validar_email(body.get("email", ""))
    rol = str(body.get("rol", "viewer") or "viewer").strip().lower()
    if not _puede_asignar_rol(getattr(ctx, "role", ""), rol):
        raise ErrorSaas(
            403, "rol_no_permitido",
            f"Su rol '{getattr(ctx, 'role', '')}' no puede asignar el rol "
            f"'{rol}'.")
    conn = _alm.conectar()
    try:
        conn.autocommit = False
        cur = conn.cursor()
        try:
            empresa = _alm.obtener_empresa(cur, ctx.company_id)
            if empresa is None:
                raise ErrorSaas(404, "empresa_no_encontrada",
                                "La empresa no está registrada en el SaaS.")
            if empresa["estado"] != "activa":
                raise ErrorSaas(403, "empresa_suspendida",
                                "La empresa está suspendida.")
            plan = _plan_cuenta(cur, empresa)
            _verificar_cupo_usuarios(cur, ctx.company_id, plan)
            inv = _alm.crear_invitacion(
                cur, company_id=ctx.company_id, email_norm=email,
                rol=rol, creado_por=getattr(ctx, "user_id", ""))
            conn.commit()
            return {"ok": True, **inv}
        except ErrorSaas:
            try:
                conn.rollback()
            except Exception:
                pass
            raise
        finally:
            cur.close()
    finally:
        conn.close()


def _usuario_de_mi_empresa(store, ctx, user_id: str):
    """Obtiene el usuario solo si pertenece a la empresa del ctx;
    si no, 404 genérico (anti-IDOR y anti-enumeración)."""
    u = store.get_user(user_id or "")
    if u is None or u.company_id != ctx.company_id or not u.email:
        raise ErrorSaas(404, "usuario_no_encontrado",
                        "El usuario no existe.")
    return u


def _cambio_estado_usuario(ctx, body, nuevo_estado: str, accion: str):
    _permiso(ctx, "user.admin")
    if not isinstance(body, dict):
        raise ErrorSaas(400, "cuerpo_invalido",
                        "El cuerpo debe ser un objeto JSON.")
    store = _tenant_store()
    objetivo = _usuario_de_mi_empresa(store, ctx, body.get("user_id", ""))
    if objetivo.user_id == getattr(ctx, "user_id", ""):
        raise ErrorSaas(403, "operacion_no_permitida",
                        "No puede cambiar su propio estado.")
    if (nuevo_estado == "disabled" and objetivo.role_id == "owner"
            and objetivo.status == "active"):
        conn = _alm.conectar()
        try:
            cur = conn.cursor()
            try:
                if _alm.contar_owners_activos(cur, ctx.company_id) <= 1:
                    raise ErrorSaas(
                        403, "ultimo_propietario",
                        "No se puede desactivar al último propietario "
                        "activo de la empresa.")
            finally:
                cur.close()
        finally:
            conn.close()
    from tenant import auth as _auth
    from tenant.auth import AuthError
    try:
        user = _auth.update_user(store, objetivo.user_id,
                                 status=nuevo_estado, actor_ctx=ctx)
    except AuthError as e:
        raise ErrorSaas(400, "operacion_rechazada", str(e))
    _auditar_usuario(ctx, objetivo.user_id, accion,
                     {"estado": nuevo_estado})
    return {"ok": True, "user_id": user.user_id,
            "email": user.email, "estado": user.status}


def _auditar_usuario(ctx, user_id: str, accion: str,
                     detalle: dict) -> None:
    """Auditoría SaaS best-effort (el cambio en el tenant ya quedó)."""
    try:
        conn = _alm.conectar()
    except ErrorSaas:
        return
    try:
        cur = conn.cursor()
        try:
            conn.autocommit = True
            _alm._asegurar_os_company(cur, ctx.company_id, "")
            _alm.auditar_saas(cur, ctx.company_id, "saas_usuario",
                              getattr(ctx, "user_id", ""),
                              ref_id=user_id,
                              detalle={"accion": accion, **detalle})
        finally:
            cur.close()
    except Exception:
        pass
    finally:
        try:
            conn.close()
        except Exception:
            pass


def r_desactivar(bundle, ctx, body):
    """POST saas/usuarios/desactivar {user_id}."""
    return _cambio_estado_usuario(ctx, body, "disabled", "desactivar")


def r_activar(bundle, ctx, body):
    """POST saas/usuarios/activar {user_id}."""
    return _cambio_estado_usuario(ctx, body, "active", "activar")


def r_cambiar_rol(bundle, ctx, body):
    """POST saas/usuarios/rol {user_id, rol}."""
    _permiso(ctx, "user.admin")
    if not isinstance(body, dict):
        raise ErrorSaas(400, "cuerpo_invalido",
                        "El cuerpo debe ser un objeto JSON.")
    rol = str(body.get("rol", "") or "").strip().lower()
    if not _puede_asignar_rol(getattr(ctx, "role", ""), rol):
        raise ErrorSaas(
            403, "rol_no_permitido",
            f"Su rol '{getattr(ctx, 'role', '')}' no puede asignar el rol "
            f"'{rol}'.")
    store = _tenant_store()
    objetivo = _usuario_de_mi_empresa(store, ctx, body.get("user_id", ""))
    if (objetivo.role_id == "owner" and rol != "owner"
            and objetivo.status == "active"):
        conn = _alm.conectar()
        try:
            cur = conn.cursor()
            try:
                if _alm.contar_owners_activos(cur, ctx.company_id) <= 1:
                    raise ErrorSaas(
                        403, "ultimo_propietario",
                        "No se puede quitar el rol de propietario al "
                        "último propietario activo.")
            finally:
                cur.close()
        finally:
            conn.close()
    from tenant import auth as _auth
    from tenant.auth import AuthError
    try:
        user = _auth.update_user(store, objetivo.user_id, role_id=rol,
                                 actor_ctx=ctx)
    except AuthError as e:
        raise ErrorSaas(400, "operacion_rechazada", str(e))
    _auditar_usuario(ctx, objetivo.user_id, "cambio_rol",
                     {"rol_anterior": objetivo.role_id, "rol_nuevo": rol})
    return {"ok": True, "user_id": user.user_id, "email": user.email,
            "rol": user.role_id}


# ---------------------------------------------------------------------------
# Tabla de rutas: nombre -> (fn, métodos, permiso)
# ---------------------------------------------------------------------------

_TABLA = {
    # públicas
    "saas/planes": (r_planes, {"GET"}, None),
    "saas/ayuda": (r_ayuda, {"GET"}, None),
    "saas/registro": (r_registro, {"POST"}, None),
    "saas/usuarios/aceptar": (r_aceptar_invitacion, {"POST"}, None),
    # autenticadas
    "saas/plan": (r_mi_plan, {"GET"}, "os.read"),
    "saas/limites": (r_limites, {"GET"}, "os.read"),
    "saas/cuenta/plan": (r_cambiar_plan, {"POST"}, "company.admin"),
    "saas/empresa": (r_empresa, {"GET"}, "os.read"),
    "saas/empresa/config": (r_config_empresa, {"POST"},
                            "company.admin"),
    "saas/uso": (r_resumen_uso, {"GET"}, "os.read"),
    "saas/uso/registrar": (r_registrar_uso, {"POST"}, "os.write"),
    "saas/onboarding": (r_onboarding, {"GET"}, "os.read"),
    "saas/onboarding/paso": (r_marcar_paso, {"POST"}, "os.write"),
    "saas/usuarios": (r_usuarios, {"GET"}, "os.read"),
    "saas/usuarios/invitar": (r_invitar, {"POST"}, "user.admin"),
    "saas/usuarios/desactivar": (r_desactivar, {"POST"}, "user.admin"),
    "saas/usuarios/activar": (r_activar, {"POST"}, "user.admin"),
    "saas/usuarios/rol": (r_cambiar_rol, {"POST"}, "user.admin"),
}


def rutas():
    """Mapa público de rutas: {"saas/...": fn(bundle, ctx, query|body)}."""
    return {nombre: fn for nombre, (fn, _m, _p) in _TABLA.items()}


def manejar(bundle, ctx, method: str, sub: str,
            payload: dict | None = None):
    """Despacha una ruta saas/*. Devuelve (status, dict JSON-serializable).

    - sub: p. ej. "saas/planes".
    - payload: query (GET) o body (POST).
    - Nunca filtra detalles internos en el 500.
    """
    entrada = _TABLA.get(sub or "")
    if entrada is None:
        return (404, {"ok": False, "codigo": "ruta_no_encontrada",
                      "error": f"Ruta SaaS '{sub}' no encontrada."})
    fn, metodos, permiso = entrada
    metodo = (method or "").upper()
    if metodo not in metodos:
        return (405, {"ok": False, "codigo": "metodo_no_permitido",
                      "error": f"Método {metodo} no permitido; use "
                               f"{'/'.join(sorted(metodos))}."})
    try:
        if permiso is not None:
            # os.write implica lectura; los POST de gestión exigen además
            # su permiso específico (company.admin / user.admin).
            if permiso in ("company.admin", "user.admin"):
                _permiso(ctx, "os.write")
            _permiso(ctx, permiso)
        resultado = fn(bundle, ctx, payload or {})
        if not isinstance(resultado, dict):
            raise ErrorSaas(500, "respuesta_invalida",
                            "Respuesta interna inválida.")
        status = 201 if sub in ("saas/registro",
                                "saas/usuarios/invitar",
                                "saas/usuarios/aceptar") else 200
        return (status, resultado)
    except ErrorSaas as e:
        return (e.status, {"ok": False, "codigo": e.codigo,
                           "error": e.mensaje})
    except Exception:
        # Nunca exponer trazas ni detalles internos al cliente.
        return (500, {"ok": False, "codigo": "error_interno",
                      "error": "Error interno del módulo SaaS."})


def _q(data: dict, name: str, default=""):
    if not isinstance(data, dict):
        return default
    v = data.get(name, default)
    if isinstance(v, (list, tuple)):
        return v[0] if v else default
    return v
