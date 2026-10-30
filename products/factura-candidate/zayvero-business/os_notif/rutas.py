"""BLOQUE B (2026-10-13) — Registro de rutas `notif/`.

CONTRATO (igual que os_contab/rutas.py; el cableado en
webapp/os_routes.py antepone el prefijo `notif/`):
- `rutas()` devuelve {subruta: spec} con claves RELATIVAS.
- spec = {"metodo": "GET"|"POST", "permiso": "os.read"|"os.write",
          "descripcion": str, "handler": fn}.
- `fn(bundle, ctx, payload) -> dict` JSON-serializable. `payload` es la
  query parseada (dict de listas, como en os_routes) en GET y el body
  JSON (dict) en POST.
- Errores: `ErrorNotif`/`ValueError` -> 400; `NoEncontrado` -> 404;
  `PermisoDenegado` -> 403. El cableado los traduce; los handlers NO
  envían HTTP.

company_id SIEMPRE del ctx (anti-IDOR). Mutaciones: auditoría en
os_eventos (tipo 'notificacion', migración 015) vía
os_persist.auditoria.auditar, delegada en el almacen.

REGLA DURA: ninguna ruta envía mensajes reales. La bandeja de salida
es SIMULADA; el envío externo real requiere aprobación humana en
aprobaciones/ y su despacho es una implementación futura.
"""
from __future__ import annotations

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .errores import (  # noqa: E402
    ErrorNotif, NoEncontrado, PermisoDenegado)
from . import almacen as mod_almacen  # noqa: E402
from . import reglas as mod_reglas  # noqa: E402
from . import programadas as mod_prog  # noqa: E402
from .modelo import (  # noqa: E402
    PRIORIDADES, CANALES, MODULOS_SENSIBLES,
    validar_tipo_alerta, validar_canal)


# ---- utilidades -----------------------------------------------------------
def _q1(payload, name, default=""):
    v = (payload or {}).get(name, default)
    if isinstance(v, (list, tuple)):
        return v[0] if v else default
    return v if v is not None else default


def _qbool(payload, name, default=False):
    v = str(_q1(payload, name, "")).strip().lower()
    if not v:
        return default
    return v in ("1", "true", "si", "sí", "yes")


def _ctx_val(ctx, nombre: str) -> str:
    """Lee un valor del ctx sea un TenantContext real o un dict."""
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
        raise ErrorNotif("company_id del contexto no coincide con la empresa")
    return cid


def _actor(ctx) -> str:
    return str(_ctx_val(ctx, "user_id") or _ctx_val(ctx, "actor") or "")


def _permiso(ctx, permiso: str, recurso: str) -> None:
    tiene = False
    if hasattr(ctx, "has"):
        tiene = bool(ctx.has(permiso))
    elif isinstance(ctx, dict):
        tiene = permiso in (ctx.get("permisos") or [])
    if not tiene:
        raise PermisoDenegado(
            f"permiso insuficiente: {permiso} (recurso {recurso})")


def _centro(bundle, ctx) -> mod_almacen.CentroNotificaciones:
    return mod_almacen.CentroNotificaciones(_ctx_company(bundle, ctx))


# =====================================================================
# Handlers: inbox (centro de notificaciones)
# =====================================================================
def h_inbox(bundle, ctx, payload):
    query = payload
    _permiso(ctx, "os.read", "notif/inbox")
    c = _centro(bundle, ctx)
    leida = None
    if str(_q1(query, "leida", "")).strip():
        leida = _qbool(query, "leida")
    pri = str(_q1(query, "prioridad", "")).strip() or None
    if pri and pri not in PRIORIDADES:
        raise ErrorNotif(f"prioridad inválida: {pri!r}")
    tipo = str(_q1(query, "tipo", "")).strip() or None
    if tipo:
        validar_tipo_alerta(tipo)
    return {"ok": True,
            "no_leidas": c.contar_no_leidas(),
            "notificaciones": c.inbox(leida=leida, prioridad=pri,
                                      tipo=tipo)}


def h_inbox_no_leidas(bundle, ctx, payload):
    _permiso(ctx, "os.read", "notif/inbox/no-leidas")
    c = _centro(bundle, ctx)
    return {"ok": True, "no_leidas": c.contar_no_leidas()}


def h_inbox_marcar_leida(bundle, ctx, payload):
    body = payload or {}
    _permiso(ctx, "os.write", "notif/inbox/marcar-leida")
    c = _centro(bundle, ctx)
    nid = str(body.get("notif_id") or "").strip()
    if not nid:
        raise ErrorNotif("notif_id es obligatorio")
    return c.marcar_leida(nid)


def h_evaluar(bundle, ctx, payload):
    """Evalúa fuentes de hallazgos y publica notificaciones nuevas.

    Fuentes: shield.evaluar(bundle) (reutilizado, no reimplementado),
    decisiones pendientes de os_ia/decisiones y reglas locales de
    finanzas/inventario con datos provistos en el body (opcionales).
    Dedup por huella: no se re-notifica lo ya publicado.
    """
    body = payload or {}
    _permiso(ctx, "os.write", "notif/evaluar")
    cid = _ctx_company(bundle, ctx)
    c = _centro(bundle, ctx)
    hoy = str(body.get("hoy") or "") or None

    alertas = []
    try:
        from shield.reglas import evaluar as shield_evaluar
        alertas = shield_evaluar(bundle, hoy=hoy)
    except Exception as e:
        raise ErrorNotif(f"no se pudo evaluar Shield: {e}")

    decisiones = []
    try:
        from os_ia.decisiones import listar as decisiones_listar
        decisiones = decisiones_listar(cid, estado="pendiente")
    except Exception:
        decisiones = []  # sin centro de decisiones disponible: silencio

    notifs = mod_reglas.evaluar_fuentes(
        alertas_shield=[a.to_dict() if hasattr(a, "to_dict") else a
                        for a in alertas],
        decisiones=[d.to_dict() if hasattr(d, "to_dict") else d
                    for d in decisiones],
        ejecuciones_presupuesto=body.get("ejecuciones_presupuesto"),
        proyeccion_cashflow=body.get("proyeccion_cashflow"),
        stocks=body.get("stocks"),
        fecha=str(body.get("hoy") or ""))
    res = c.publicar(notifs)
    return {"ok": True, "fuentes": {
                "shield_alertas": len(alertas),
                "decisiones_pendientes": len(decisiones)},
            "publicadas": res}


# =====================================================================
# Handlers: preferencias y canales (consentimiento)
# =====================================================================
def h_preferencias(bundle, ctx, payload):
    _permiso(ctx, "os.read", "notif/preferencias")
    c = _centro(bundle, ctx)
    uid = str(_q1(payload, "user_id", "")).strip() or _actor(ctx)
    return {"ok": True, "user_id": uid,
            "preferencias": c.preferencias(uid),
            "defaults": {"nota": ("sin fila: suscrito a todos los tipos "
                                  "por canal 'inapp'; canales externos "
                                  "requieren consentimiento explícito")}}


def h_preferencias_suscribir(bundle, ctx, payload):
    body = payload or {}
    _permiso(ctx, "os.write", "notif/preferencias")
    c = _centro(bundle, ctx)
    uid = str(body.get("user_id") or "").strip() or _actor(ctx)
    tipo = str(body.get("tipo_alerta") or "*").strip()
    canal = str(body.get("canal") or "inapp").strip()
    validar_tipo_alerta(tipo)
    validar_canal(canal)
    return c.suscribir(uid, tipo, canal, bool(body.get("suscrito", True)))


def h_canales(bundle, ctx, payload):
    _permiso(ctx, "os.read", "notif/canales")
    c = _centro(bundle, ctx)
    uid = str(_q1(payload, "user_id", "")).strip() or None
    return {"ok": True, "canales": c.canales(uid)}


def h_canales_registrar(bundle, ctx, payload):
    body = payload or {}
    _permiso(ctx, "os.write", "notif/canales")
    c = _centro(bundle, ctx)
    uid = str(body.get("user_id") or "").strip() or _actor(ctx)
    canal = str(body.get("canal") or "").strip()
    destino = str(body.get("destino") or "").strip()
    if not canal:
        raise ErrorNotif("canal es obligatorio (email|whatsapp|telegram)")
    if not destino:
        raise ErrorNotif("destino es obligatorio")
    res = c.registrar_canal(uid, canal, destino)
    res["nota"] = ("canal registrado DESHABILITADO: el consentimiento "
                   "explícito se da aparte (canales/consentir)")
    return res


def h_canales_consentir(bundle, ctx, payload):
    body = payload or {}
    _permiso(ctx, "os.write", "notif/canales/consentir")
    c = _centro(bundle, ctx)
    cid = str(body.get("canal_id") or "").strip()
    if not cid:
        raise ErrorNotif("canal_id es obligatorio")
    res = c.consentir_canal(cid, _actor(ctx))
    res["nota"] = "consentimiento explícito registrado; canal habilitado"
    return res


def h_canales_revocar(bundle, ctx, payload):
    body = payload or {}
    _permiso(ctx, "os.write", "notif/canales/revocar")
    c = _centro(bundle, ctx)
    cid = str(body.get("canal_id") or "").strip()
    if not cid:
        raise ErrorNotif("canal_id es obligatorio")
    return c.revocar_canal(cid, _actor(ctx))


def h_canales_puede_recibir(bundle, ctx, payload):
    query = payload
    _permiso(ctx, "os.read", "notif/canales/puede-recibir")
    c = _centro(bundle, ctx)
    uid = str(_q1(query, "user_id", "")).strip() or _actor(ctx)
    tipo = str(_q1(query, "tipo_alerta", "")).strip() or "*"
    canal = str(_q1(query, "canal", "")).strip() or "inapp"
    validar_tipo_alerta(tipo)
    validar_canal(canal)
    puede, motivo = c.puede_recibir(uid, tipo, canal)
    return {"ok": True, "puede": puede, "motivo": motivo,
            "user_id": uid, "tipo_alerta": tipo, "canal": canal}


# =====================================================================
# Handlers: programaciones de reportes
# =====================================================================
def h_programaciones(bundle, ctx, payload):
    _permiso(ctx, "os.read", "notif/programaciones")
    c = _centro(bundle, ctx)
    return {"ok": True, "programaciones": c.programaciones(
        solo_activas=_qbool(payload, "solo_activas", False))}


def h_programaciones_crear(bundle, ctx, payload):
    body = payload or {}
    _permiso(ctx, "os.write", "notif/programaciones/crear")
    c = _centro(bundle, ctx)
    modulo = str(body.get("modulo") or "").strip()
    if modulo in MODULOS_SENSIBLES:
        # Misma regla que reportes.rutas.SENSIBLES: finanzas/cobranzas
        # exigen os.write (ya verificado arriba); se documenta el
        # control adicional en la respuesta.
        pass
    res = c.crear_programacion(
        modulo, str(body.get("formato") or "pdf").strip(),
        str(body.get("frecuencia") or "diaria").strip(),
        str(body.get("hora") or "07:00").strip(),
        body.get("roles_destino") or [])
    res["nota"] = ("la programación GENERA el reporte en la bandeja "
                   "como SIMULADO; el envío externo requiere aprobación "
                   "humana en aprobaciones/")
    return res


def h_programaciones_cambiar(bundle, ctx, payload):
    body = payload or {}
    _permiso(ctx, "os.write", "notif/programaciones/activar")
    c = _centro(bundle, ctx)
    pid = str(body.get("prog_id") or "").strip()
    if not pid:
        raise ErrorNotif("prog_id es obligatorio")
    return c.cambiar_estado_programacion(pid, bool(body.get("activa", True)))


def h_programaciones_ejecutar(bundle, ctx, payload):
    """Pasa del programador: genera reportes vencidos -> bandeja
    SIMULADA. No envía nada."""
    body = payload or {}
    _permiso(ctx, "os.write", "notif/programaciones/ejecutar")
    c = _centro(bundle, ctx)
    creadas = mod_prog.ejecutar_vencidas(
        c, ahora=None, generador=None, bundle=bundle)
    return {"ok": True, "salidas_creadas": len(creadas),
            "salidas": [{"salida_id": s["salida_id"],
                         "prog_id": s["prog_id"], "rol": s["rol"],
                         "estado": s["estado"], "simulado": s["simulado"]}
                        for s in creadas],
            "nota": "SIMULADO: ningún envío real se realizó"}


# =====================================================================
# Handlers: bandeja de salida
# =====================================================================
def h_bandeja(bundle, ctx, payload):
    _permiso(ctx, "os.read", "notif/bandeja")
    c = _centro(bundle, ctx)
    estado = str(_q1(payload, "estado", "")).strip() or None
    return {"ok": True, "bandeja": c.bandeja(estado=estado),
            "nota": ("todo lo que salga de aquí es SIMULADO hasta que "
                     "un humano lo apruebe en aprobaciones/")}


def h_bandeja_detalle(bundle, ctx, payload):
    _permiso(ctx, "os.read", "notif/bandeja/detalle")
    c = _centro(bundle, ctx)
    sid = str(_q1(payload, "salida_id", "")).strip()
    if not sid:
        raise ErrorNotif("salida_id es obligatorio")
    return {"ok": True, "salida": c.obtener_salida(sid)}


def h_bandeja_solicitar_aprobacion(bundle, ctx, payload):
    body = payload or {}
    _permiso(ctx, "os.write", "notif/bandeja/solicitar-aprobacion")
    c = _centro(bundle, ctx)
    sid = str(body.get("salida_id") or "").strip()
    if not sid:
        raise ErrorNotif("salida_id es obligatorio")
    res = c.solicitar_aprobacion_envio(sid, _actor(ctx))
    res["nota"] = ("solicitud creada en aprobaciones/ "
                   "(tipo aprobacion_financiera). Un humano debe "
                   "aprobarla; aun aprobada, el envío real queda "
                   "RETENIDO (implementación futura).")
    return res


def h_bandeja_sincronizar(bundle, ctx, payload):
    body = payload or {}
    _permiso(ctx, "os.write", "notif/bandeja/sincronizar")
    c = _centro(bundle, ctx)
    sid = str(body.get("salida_id") or "").strip()
    if not sid:
        raise ErrorNotif("salida_id es obligatorio")
    return c.sincronizar_aprobacion(sid)


def h_bandeja_enviar_real(bundle, ctx, payload):
    """REGLA DURA: siempre levanta ErrorNotif (400)."""
    body = payload or {}
    _permiso(ctx, "os.write", "notif/bandeja/enviar-real")
    c = _centro(bundle, ctx)
    sid = str(body.get("salida_id") or "").strip()
    if not sid:
        raise ErrorNotif("salida_id es obligatorio")
    return c.enviar_real(sid)  # levanta ErrorNotif siempre


# =====================================================================
# Registro
# =====================================================================
def rutas() -> dict:
    """Registro de rutas del bloque. Prefijo: `notif/` (lo antepone el
    cableado).

    El cableado traduce errores: ErrorNotif/ValueError -> 400,
    NoEncontrado -> 404, PermisoDenegado -> 403.
    """
    def _r(metodo, permiso, descripcion, handler):
        return {"metodo": metodo, "permiso": permiso,
                "descripcion": descripcion, "handler": handler}

    return {
        # --- inbox -----------------------------------------------------
        "inbox": _r(
            "GET", "os.read",
            "Inbox de notificaciones: filtros leida/prioridad/tipo; "
            "aislado por company_id del ctx.", h_inbox),
        "inbox/no-leidas": _r(
            "GET", "os.read", "Conteo de notificaciones no leídas.",
            h_inbox_no_leidas),
        "inbox/marcar-leida": _r(
            "POST", "os.write",
            "Marca una notificación como leída.", h_inbox_marcar_leida),
        "evaluar": _r(
            "POST", "os.write",
            "Evalúa Shield + decisiones + reglas locales y publica "
            "notificaciones nuevas (dedup por huella).", h_evaluar),
        # --- preferencias y consentimiento ------------------------------
        "preferencias": _r(
            "GET", "os.read",
            "Suscripciones a tipos de alerta por canal (con defaults).",
            h_preferencias),
        "preferencias/suscribir": _r(
            "POST", "os.write",
            "Suscribe/des-suscribe un usuario a un tipo de alerta y "
            "canal.", h_preferencias_suscribir),
        "canales": _r(
            "GET", "os.read",
            "Canales externos registrados (destino enmascarado).",
            h_canales),
        "canales/registrar": _r(
            "POST", "os.write",
            "Registra un canal externo (email/WhatsApp/Telegram). Nace "
            "DESHABILITADO, sin consentimiento; el destino se guarda "
            "enmascarado + hash.", h_canales_registrar),
        "canales/consentir": _r(
            "POST", "os.write",
            "Consentimiento EXPLÍCITO del usuario: habilita el canal.",
            h_canales_consentir),
        "canales/revocar": _r(
            "POST", "os.write",
            "Revoca el consentimiento: el canal vuelve a deshabilitado.",
            h_canales_revocar),
        "canales/puede-recibir": _r(
            "GET", "os.read",
            "Verifica si un usuario puede recibir un tipo de alerta por "
            "un canal (suscripción + consentimiento).",
            h_canales_puede_recibir),
        # --- reportes programados ----------------------------------------
        "programaciones": _r(
            "GET", "os.read", "Lista programaciones de reportes.",
            h_programaciones),
        "programaciones/crear": _r(
            "POST", "os.write",
            "Crea una programación (diaria/semanal/mensual, hora RD, "
            "formato pdf/xlsx, roles destinatarios). Genera en bandeja "
            "SIMULADA; no envía nada.", h_programaciones_crear),
        "programaciones/activar": _r(
            "POST", "os.write", "Activa/desactiva una programación.",
            h_programaciones_cambiar),
        "programaciones/ejecutar": _r(
            "POST", "os.write",
            "Pasa del programador: genera reportes vencidos en la "
            "bandeja como SIMULADO (no envía nada).",
            h_programaciones_ejecutar),
        # --- bandeja de salida --------------------------------------------
        "bandeja": _r(
            "GET", "os.read",
            "Bandeja de salida SIMULADA (filtro por estado).",
            h_bandeja),
        "bandeja/detalle": _r(
            "GET", "os.read", "Detalle de una salida (sin contenido).",
            h_bandeja_detalle),
        "bandeja/solicitar-aprobacion": _r(
            "POST", "os.write",
            "Crea la solicitud de aprobación humana en aprobaciones/ "
            "(tipo aprobacion_financiera). No envía nada.",
            h_bandeja_solicitar_aprobacion),
        "bandeja/sincronizar": _r(
            "POST", "os.write",
            "Sincroniza la decisión de aprobaciones/ a la bandeja. "
            "Aprobada = lista pero RETENIDA (sin despacho real).",
            h_bandeja_sincronizar),
        "bandeja/enviar-real": _r(
            "POST", "os.write",
            "REGLA DURA: siempre responde 400. El envío real no existe "
            "todavía (implementación futura aprobada por el "
            "propietario).", h_bandeja_enviar_real),
    }
