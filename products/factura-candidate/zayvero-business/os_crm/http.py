"""BLOQUE CRM/VENTAS (2026-10-13) — API HTTP del bloque os_crm.

CONTRATO:
    rutas() -> {ruta: {"metodo": "GET"|"POST",
                       "permiso": "os.read"|"os.write",
                       "fn": fn}}
    fn(bundle, ctx, payload) -> dict JSON-serializable.

- Prefijo de todas las rutas: `crm/`.
- `bundle`: OSBundle de la empresa (webapp/os_api.py).
- `ctx`: {"company_id": <siempre del contexto autenticado, anti-IDOR>,
           "actor": <usuario que ejecuta>}. Cada fn verifica que
  ctx["company_id"] == bundle.company_id.
- `payload`: query (GET) o body (POST), dict plano.
- Errores: `NoEncontrado` (la capa de wiring lo traduce a 404),
  `ErrorCRM`/`ValueError` (→ 400). Este módulo no conoce HTTP.
- Auditoría: las escrituras llaman a `os_persist.auditoria.auditar`.
  En modo memoria el evento cae en `bundle.eventos_memoria`; con PG
  fuera de `mutar_os` es no-op seguro (el wiring del panel debe
  ejecutar estas fns dentro de `mutar_os`, como el resto de POSTs).

Este módulo NO toca webapp/os_routes.py ni webapp/static/* (el panel
se integra después); expone el contrato para ese wiring.

REGLA DURA: los recordatorios de cobro generan BORRADORES que requieren
aprobación humana explícita. El sistema NUNCA envía mensajes solo:
no existe ninguna función de envío en este bloque.
"""
from __future__ import annotations

import sys
import os
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .clientes import ErrorCRM  # noqa: E402
from .inteligencia import (  # noqa: E402
    ficha360, probabilidad_churn, ltv_cliente, segmento_rfm)
from .oportunidades import Pipeline, ErrorOportunidad  # noqa: E402
from .descuentos import RegistroDescuentos, ErrorDescuento  # noqa: E402
from .devoluciones import RegistroDevoluciones, ErrorDevolucion  # noqa: E402
from .cobranzas import alertas_inactividad  # noqa: E402


class NoEncontrado(ErrorCRM):
    """Recurso inexistente (el wiring lo traduce a 404)."""


class ErrorHTTP(ErrorCRM):
    """Error de contrato (el wiring lo traduce a 400)."""


# ---- servicios del bloque por bundle --------------------------------------
# Registros nuevos del bloque (oportunidades, descuentos, devoluciones,
# aprobaciones de borradores). Viven en memoria por proceso, ligados al
# bundle de la empresa. LIMITACIÓN: hasta el wiring del panel no hay
# persistencia en snapshots/PG (documentado en docs/bloque-crm.md).
_BLOQUE_ATTR = "_os_crm_bloque"
_bloque_lock = threading.RLock()


def _bloque(bundle) -> dict:
    with _bloque_lock:
        b = getattr(bundle, _BLOQUE_ATTR, None)
        if b is None:
            b = {
                "oportunidades": Pipeline(bundle.company_id),
                "descuentos": RegistroDescuentos(bundle.company_id,
                                                bundle.ventas),
                "devoluciones": RegistroDevoluciones(
                    bundle.company_id, bundle.inventario, bundle.libro,
                    bundle.ventas),
                "borradores": {},   # borrador_id -> {estado, ...}
            }
            # Las devoluciones parciales bloquean la cancelación total
            # (evita doble reversión; ver ventas.py).
            bundle.ventas._devoluciones = b["devoluciones"]
            setattr(bundle, _BLOQUE_ATTR, b)
        return b


# ---- utilidades -------------------------------------------------------------
def _q1(payload, name, default=""):
    v = (payload or {}).get(name, default)
    if isinstance(v, (list, tuple)):
        return v[0] if v else default
    return v if v is not None else default


def _ctx_val(ctx, nombre: str) -> str:
    """Lee un valor del ctx sea un dict (tests) o un TenantContext real.

    El cableado HTTP pasa tenant.models.TenantContext (objeto con
    atributos); los tests de los bloques usaban dicts. Ambos funcionan.
    """
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
        raise ErrorHTTP("company_id del contexto no coincide con la empresa")
    return cid


def _actor(ctx) -> str:
    return str(_ctx_val(ctx, "user_id") or _ctx_val(ctx, "actor") or "")


def _auditar(bundle, ctx, tipo: str, ref_id: str, detalle: dict):
    from os_persist.auditoria import auditar
    auditar(_ctx_company(bundle, ctx), tipo, _actor(ctx), ref_id, detalle)


def _cliente_exigido(bundle, cliente_id: str):
    if not cliente_id:
        raise ErrorHTTP("cliente_id es obligatorio")
    try:
        bundle.crm._exigir(cliente_id)
    except ErrorCRM:
        raise NoEncontrado(f"cliente inexistente: {cliente_id}")


# ---- lecturas ----------------------------------------------------------------
def _ficha360(bundle, ctx, query):
    _ctx_company(bundle, ctx)
    cid = _q1(query, "cliente_id")
    _cliente_exigido(bundle, cid)
    return {"ok": True, "data": ficha360(bundle.crm, cid,
                                        hoy=_q1(query, "hoy") or None)}


def _churn(bundle, ctx, query):
    _ctx_company(bundle, ctx)
    hoy = _q1(query, "hoy") or None
    cid = _q1(query, "cliente_id")
    cids = ([cid] if cid else sorted(bundle.crm._clientes))
    if cid:
        _cliente_exigido(bundle, cid)
    return {"ok": True, "data": {
        "clientes": [probabilidad_churn(bundle.crm, c, hoy) for c in cids],
        "nota": ("probabilidad heurística explicada; sin datos suficientes "
                 "el estado es 'datos_insuficientes', nunca un número "
                 "inventado")}}


def _ltv(bundle, ctx, query):
    _ctx_company(bundle, ctx)
    hoy = _q1(query, "hoy") or None
    cid = _q1(query, "cliente_id")
    cids = ([cid] if cid else sorted(bundle.crm._clientes))
    if cid:
        _cliente_exigido(bundle, cid)
    return {"ok": True,
            "data": [ltv_cliente(bundle.crm, c, hoy) for c in cids]}


def _segmentacion(bundle, ctx, query):
    _ctx_company(bundle, ctx)
    hoy = _q1(query, "hoy") or None
    try:
        ventana = int(_q1(query, "ventana_dias", "365"))
    except (TypeError, ValueError):
        raise ErrorHTTP("ventana_dias debe ser entero")
    if ventana <= 0:
        raise ErrorHTTP("ventana_dias debe ser > 0")
    return {"ok": True, "data": segmento_rfm(bundle.crm, hoy,
                                            ventana_dias=ventana)}


def _oportunidades_listar(bundle, ctx, query):
    _ctx_company(bundle, ctx)
    try:
        items = _bloque(bundle)["oportunidades"].listar(
            etapa=_q1(query, "etapa") or None,
            responsable=_q1(query, "responsable") or None)
    except ErrorOportunidad as e:
        raise ErrorHTTP(str(e))
    return {"ok": True, "data": items}


def _oportunidades_resumen(bundle, ctx, query):
    _ctx_company(bundle, ctx)
    return {"ok": True,
            "data": _bloque(bundle)["oportunidades"].resumen()}


def _promesas_seguimiento(bundle, ctx, query):
    _ctx_company(bundle, ctx)
    return {"ok": True, "data": bundle.cobranzas.seguimiento_promesas(
        hoy=_q1(query, "hoy") or None)}


def _cobranzas_alertas(bundle, ctx, query):
    _ctx_company(bundle, ctx)
    hoy = _q1(query, "hoy") or None
    inactivos = alertas_inactividad(bundle.cobranzas, bundle.crm, hoy)
    prom_vencidas = [p for p in bundle.cobranzas.seguimiento_promesas(hoy)
                     if p["estado"] == "vencida"]
    return {"ok": True, "data": {
        "clientes_inactivos": inactivos,
        "promesas_vencidas": prom_vencidas}}


def _descuentos_listar(bundle, ctx, query):
    _ctx_company(bundle, ctx)
    return {"ok": True, "data": _bloque(bundle)["descuentos"].listar(
        venta_id=_q1(query, "venta_id") or None)}


def _descuentos_impacto(bundle, ctx, query):
    _ctx_company(bundle, ctx)
    did = _q1(query, "descuento_id")
    if not did:
        raise ErrorHTTP("descuento_id es obligatorio")
    try:
        return {"ok": True,
                "data": _bloque(bundle)["descuentos"].impacto(did)}
    except ErrorDescuento as e:
        raise NoEncontrado(str(e))


def _devoluciones_listar(bundle, ctx, query):
    _ctx_company(bundle, ctx)
    return {"ok": True, "data": _bloque(bundle)["devoluciones"].listar(
        venta_id=_q1(query, "venta_id") or None)}


def _devoluciones_impacto(bundle, ctx, query):
    _ctx_company(bundle, ctx)
    vid = _q1(query, "venta_id")
    if not vid:
        raise ErrorHTTP("venta_id es obligatorio")
    try:
        return {"ok": True, "data": _bloque(bundle)[
            "devoluciones"].impacto_margen(vid)}
    except ErrorDevolucion as e:
        raise NoEncontrado(str(e))


def _recordatorios(bundle, ctx, query):
    """Borradores de cobro. Requieren aprobación humana; nunca se envían."""
    _ctx_company(bundle, ctx)
    hoy = _q1(query, "hoy") or None
    try:
        min_dias = int(_q1(query, "min_dias", "1"))
    except (TypeError, ValueError):
        raise ErrorHTTP("min_dias debe ser entero")
    borradores = _bloque(bundle)["borradores"]
    items = []
    for r in bundle.cobranzas.recordatorios(hoy, min_dias=min_dias):
        bid = f"rec-{r.cuenta_id}"
        ap = borradores.get(bid, {})
        d = r.to_dict()
        d["borrador_id"] = bid
        d["estado"] = ap.get("estado", "borrador")
        d["aprobado_por"] = ap.get("aprobado_por", "")
        items.append(d)
    return {"ok": True, "data": {
        "borradores": items,
        "advertencia": ("BORRADORES: requieren aprobación humana explícita "
                        "antes de enviarse. Este sistema NUNCA envía "
                        "mensajes solo.")}}


# ---- escrituras ---------------------------------------------------------------
def _oportunidad_crear(bundle, ctx, body):
    company_id = _ctx_company(bundle, ctx)
    body = body or {}
    try:
        data = _bloque(bundle)["oportunidades"].crear(
            oportunidad_id=str(body.get("oportunidad_id", "")),
            titulo=str(body.get("titulo", "")),
            monto=str(body.get("monto", "0")),
            moneda=str(body.get("moneda", "DOP")),
            cliente_id=str(body.get("cliente_id", "")),
            responsable=str(body.get("responsable", "")),
            fecha_estimada=str(body.get("fecha_estimada", "")),
            probabilidad=body.get("probabilidad"),
            etapa=str(body.get("etapa", "nueva")),
            hoy=body.get("hoy") or None)
    except (ErrorOportunidad, ValueError) as e:
        raise ErrorHTTP(str(e))
    _auditar(bundle, ctx, "oportunidad", data["oportunidad_id"],
             {"accion": "crear", "etapa": data["etapa"],
              "monto": data["monto"], "moneda": data["moneda"],
              "valor_esperado": data["valor_esperado"]})
    return {"ok": True, "data": data}


def _oportunidad_etapa(bundle, ctx, body):
    _ctx_company(bundle, ctx)
    body = body or {}
    oid = str(body.get("oportunidad_id", ""))
    etapa = str(body.get("etapa", ""))
    if not oid or not etapa:
        raise ErrorHTTP("oportunidad_id y etapa son obligatorios")
    try:
        data = _bloque(bundle)["oportunidades"].cambiar_etapa(
            oid, etapa, nota=str(body.get("nota", "")),
            probabilidad=body.get("probabilidad"),
            hoy=body.get("hoy") or None)
    except ErrorOportunidad as e:
        msg = str(e)
        if "inexistente" in msg:
            raise NoEncontrado(msg)
        raise ErrorHTTP(msg)
    _auditar(bundle, ctx, "oportunidad", oid,
             {"accion": "cambiar_etapa", "etapa": etapa,
              "probabilidad": data["probabilidad"],
              "valor_esperado": data["valor_esperado"]})
    return {"ok": True, "data": data}


def _promesa_registrar(bundle, ctx, body):
    _ctx_company(bundle, ctx)
    body = body or {}
    cuenta_id = str(body.get("cuenta_id", ""))
    fecha = str(body.get("fecha", ""))
    monto = str(body.get("monto", "0"))
    if not cuenta_id or not fecha:
        raise ErrorHTTP("cuenta_id y fecha son obligatorios")
    try:
        bundle.cobranzas.promesa_pago(
            cuenta_id, fecha, monto,
            registrada_por=str(body.get("registrada_por", "")),
            notas=str(body.get("notas", "")),
            hoy=body.get("hoy") or None)
    except ValueError as e:
        msg = str(e)
        if "inexistente" in msg:
            raise NoEncontrado(msg)
        raise ErrorHTTP(msg)
    _auditar(bundle, ctx, "promesa_pago", cuenta_id,
             {"fecha": fecha, "monto": monto,
              "registrada_por": str(body.get("registrada_por", ""))})
    return {"ok": True, "data": {"cuenta_id": cuenta_id, "fecha": fecha,
                                "monto": str(monto)}}


def _descuento_registrar(bundle, ctx, body):
    _ctx_company(bundle, ctx)
    body = body or {}
    try:
        data = _bloque(bundle)["descuentos"].registrar(
            descuento_id=str(body.get("descuento_id", "")),
            venta_id=str(body.get("venta_id", "")),
            tipo=str(body.get("tipo", "")),
            valor=str(body.get("valor", "0")),
            motivo=str(body.get("motivo", "")),
            autorizado_por=str(body.get("autorizado_por", "")),
            fecha=str(body.get("fecha", "")))
    except ErrorDescuento as e:
        raise ErrorHTTP(str(e))
    impacto = _bloque(bundle)["descuentos"].impacto(data["descuento_id"])
    _auditar(bundle, ctx, "descuento", data["descuento_id"],
             {"venta_id": data["venta_id"], "tipo": data["tipo"],
              "valor": data["valor"], "motivo": data["motivo"],
              "autorizado_por": data["autorizado_por"],
              "margen_con_descuento_pct":
                  impacto["margen_bruto_con_descuento_pct"]})
    return {"ok": True, "data": {"descuento": data, "impacto": impacto}}


def _devolucion_registrar(bundle, ctx, body):
    _ctx_company(bundle, ctx)
    body = body or {}
    lineas = body.get("lineas")
    if not isinstance(lineas, list) or not lineas:
        raise ErrorHTTP("lineas debe ser una lista no vacía")
    try:
        data = _bloque(bundle)["devoluciones"].registrar(
            devolucion_id=str(body.get("devolucion_id", "")),
            venta_id=str(body.get("venta_id", "")),
            lineas=lineas,
            motivo=str(body.get("motivo", "")),
            fecha=str(body.get("fecha", "")))
    except ErrorDevolucion as e:
        msg = str(e)
        if "inexistente" in msg:
            raise NoEncontrado(msg)
        raise ErrorHTTP(msg)
    impacto = _bloque(bundle)["devoluciones"].impacto_margen(
        data["venta_id"])
    _auditar(bundle, ctx, "devolucion", data["devolucion_id"],
             {"venta_id": data["venta_id"], "motivo": data["motivo"],
              "monto_total": data["monto_total"],
              "cogs_revertido_total": data["cogs_revertido_total"],
              "margen_neto_pct": impacto["margen_neto_pct"]})
    return {"ok": True, "data": {"devolucion": data, "impacto": impacto}}


def _recordatorio_aprobar(bundle, ctx, body):
    """Aprueba un borrador. NO lo envía: el envío es manual y externo.

    La aprobación solo cambia el estado borrador -> aprobado y deja el
    evento de auditoría. No existe código de envío en este bloque.
    """
    _ctx_company(bundle, ctx)
    body = body or {}
    bid = str(body.get("borrador_id", ""))
    aprobado_por = str(body.get("aprobado_por", "")).strip()
    if not bid:
        raise ErrorHTTP("borrador_id es obligatorio")
    if not aprobado_por:
        raise ErrorHTTP(
            "aprobado_por es obligatorio: la aprobación debe identificar "
            "al humano que aprueba")
    borradores = _bloque(bundle)["borradores"]
    # El borrador debe existir entre los generados actualmente.
    validos = {f"rec-{r.cuenta_id}"
               for r in bundle.cobranzas.recordatorios(
                   str(body.get("hoy") or "") or None)}
    if bid not in validos:
        raise NoEncontrado(f"borrador inexistente o ya no vigente: {bid}")
    if borradores.get(bid, {}).get("estado") == "aprobado":
        raise ErrorHTTP(f"el borrador {bid} ya fue aprobado")
    borradores[bid] = {"estado": "aprobado", "aprobado_por": aprobado_por,
                       "fecha": str(body.get("hoy") or "")}
    _auditar(bundle, ctx, "recordatorio", bid,
             {"accion": "aprobar", "aprobado_por": aprobado_por,
              "enviado": False,
              "nota": "aprobación humana; el envío es manual y externo"})
    return {"ok": True, "data": {
        "borrador_id": bid, "estado": "aprobado",
        "aprobado_por": aprobado_por,
        "enviado": False,
        "nota": ("el sistema nunca envía solo; el envío del mensaje "
                 "aprobado se hace manualmente fuera del sistema")}}


# ---- contrato -----------------------------------------------------------------
def rutas():
    """{ruta: {metodo, permiso, fn}} — prefijo `crm/` en todas."""
    g = ("GET", "os.read")
    p = ("POST", "os.write")
    defs = {
        "crm/ficha360": (g, _ficha360),
        "crm/churn": (g, _churn),
        "crm/ltv": (g, _ltv),
        "crm/segmentacion": (g, _segmentacion),
        "crm/oportunidades": (g, _oportunidades_listar),
        "crm/oportunidades/resumen": (g, _oportunidades_resumen),
        "crm/oportunidades/crear": (p, _oportunidad_crear),
        "crm/oportunidades/etapa": (p, _oportunidad_etapa),
        "crm/cobranzas/promesas": (g, _promesas_seguimiento),
        "crm/cobranzas/promesa": (p, _promesa_registrar),
        "crm/cobranzas/alertas": (g, _cobranzas_alertas),
        "crm/descuentos": (g, _descuentos_listar),
        "crm/descuentos/registrar": (p, _descuento_registrar),
        "crm/descuentos/impacto": (g, _descuentos_impacto),
        "crm/devoluciones": (g, _devoluciones_listar),
        "crm/devoluciones/registrar": (p, _devolucion_registrar),
        "crm/devoluciones/impacto": (g, _devoluciones_impacto),
        "crm/recordatorios": (g, _recordatorios),
        "crm/recordatorios/aprobar": (p, _recordatorio_aprobar),
    }
    out = {}
    for ruta, ((metodo, permiso), fn) in defs.items():
        out[ruta] = {"metodo": metodo, "permiso": permiso, "fn": fn}
    return out
