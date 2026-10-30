"""BLOQUE 6 (sprint 3, 2026-10-12) — API HTTP de aprobaciones humanas.

`handle_aprobaciones`: despachador de `/api/v1/os/aprobaciones/*`
(misma firma que `handle_os`).

    GET  /api/v1/os/aprobaciones?estado=&limite=
    POST /api/v1/os/aprobaciones/aprobar   {aprobacion_id, motivo?}
    POST /api/v1/os/aprobaciones/rechazar  {aprobacion_id, motivo?}

Seguridad: igual que el resto de /api/v1/os/* (rate limiting antes
de todo; GET -> os.read; POST -> os.write + CSRF). Aprobar/rechazar
exige ADEMÁS el permiso `aprobaciones.decidir` (owner/admin).

Diseño de ejecución (documentado, anti-deadlock):
- Este despachador NO corre dentro de `mutar_os`. La decisión
  (`decidir()`) usa solo un row-lock sobre la solicitud (ver
  aprobaciones/api.py), así que nunca compite con `os_company_lock`.
- La ejecución es secuencial: decidir -> ejecutar -> marcar_ejecutada.
  - cambio_precio -> Catalogo.fijar_precio (su propio lock).
  - devolucion_venta / devolucion_compra -> mutar_os con los
    ejecutores de webapp.os_routes (bundle + snapshots + auditoría).
  - aprobacion_financiera -> sin ejecutor automático (se aprueba y
    queda en 'aprobada' para ejecución manual; documentado).
- P1d (issue #6): decidir() y marcar_ejecutada() auditan en os_eventos
  DENTRO de la misma transacción (ya no hay INSERT best-effort
  separado). Si la ejecución falla, se registra con
  marcar_ejecucion_fallida() para habilitar el reintento; sin fallo
  registrado, una segunda aprobación concurrente recibe 409 y la
  operación se ejecuta UNA sola vez.
"""
from __future__ import annotations

import os as _os
import sys

sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from aprobaciones.api import Aprobaciones  # noqa: E402
from aprobaciones.modelo import ErrorAprobacion  # noqa: E402


def _q1(query, name, default=""):
    v = query.get(name)
    if not v:
        return default
    return v[0] if isinstance(v, (list, tuple)) else v


def _map_error(_err, handler, e):
    from catalogo.modelo import ErrorCatalogo
    msg = str(e)
    if isinstance(e, ErrorAprobacion):
        if "inexistente" in msg:
            return _err(handler, 404, msg)
        # P1d: doble decisión concurrente / reintento sin fallo
        # registrado / conflicto de clave de operación -> 409.
        if ("ya fue ejecutada" in msg or "ya está decidida" in msg
                or "ya está aprobada" in msg
                or "clave de operación" in msg):
            return _err(handler, 409, msg)
        if "no es cancelable" in msg:
            return _err(handler, 409, msg)
        return _err(handler, 400, msg)
    if isinstance(e, ErrorCatalogo):
        if "ZAYVERO_OS_DATABASE_URL" in msg:
            return _err(handler, 503,
                        "aprobaciones no disponibles (sin PostgreSQL)")
        return _err(handler, 400, msg)
    return None




def _ejecutar_aprobada(company_id: str, ctx, reg: dict):
    """Ejecuta la operación de una solicitud aprobada.

    Devuelve (status, detalle). No lanza ante fallos de negocio:
    los devuelve como (status>=400, {...}).
    """
    tipo = reg["tipo"]
    payload = reg.get("payload") or {}
    aid = reg["aprobacion_id"]

    if tipo == "cambio_precio":
        from catalogo.api import Catalogo
        from catalogo.modelo import (ProductoNoExiste, ValidacionCatalogo)
        try:
            res = Catalogo(company_id).fijar_precio(
                str(payload["producto_id"]),
                str(payload["precio_nuevo"]),
                moneda=str(payload.get("moneda", "DOP") or "DOP"),
                motivo=(f"aprobación {aid}: "
                        f"{payload.get('motivo', '')}").strip())
            return 200, {"tipo": tipo, "resultado": res}
        except ProductoNoExiste as e:
            return 404, {"tipo": tipo, "error": str(e)}
        except (ValidacionCatalogo, KeyError, ValueError) as e:
            return 400, {"tipo": tipo, "error": str(e)}

    if tipo in ("devolucion_venta", "devolucion_compra"):
        # Importación perezosa: webapp.os_routes no importa este módulo.
        from webapp.os_routes import (_ejecutar_venta_cancelar,
                                      _ejecutar_devolucion_compra)
        from os_persist.tx import mutar_os
        ejecutor = (_ejecutar_venta_cancelar
                    if tipo == "devolucion_venta"
                    else _ejecutar_devolucion_compra)
        status, body = mutar_os(
            company_id,
            lambda b: ejecutor(b, company_id, ctx, payload))
        ok = body.get("ok", False) if isinstance(body, dict) else False
        detalle = {"tipo": tipo, "respuesta": body}
        return (200 if ok and status < 400 else status, detalle)

    # aprobacion_financiera (y futuros tipos sin ejecutor): la
    # aprobación humana queda registrada; la ejecución es manual.
    return 200, {"tipo": tipo,
                 "nota": "sin ejecutor automático: la operación debe "
                         "ejecutarse manualmente; la aprobación queda "
                         "registrada",
                 "ejecucion_automatica": False}


def _handle_get(_ok, _err, _paginar, ctx, sub, query):
    apr = Aprobaciones(ctx.company_id)
    if sub == "aprobaciones":
        estado = _q1(query, "estado", "") or None
        items = apr.listar(
            estado=estado,
            limite=int(_q1(query, "limite", "100") or 100))
        return _ok(None, _paginar(items, query))
    return _err(None, 404, "recurso no encontrado")


def _handle_post(_ok, _err, ctx, sub, body):
    apr = Aprobaciones(ctx.company_id)
    c = ctx.company_id

    if sub in ("aprobaciones/aprobar", "aprobaciones/rechazar"):
        aid = str(body.get("aprobacion_id", "") or "").strip()
        if not aid:
            return _err(None, 400, "falta campo: aprobacion_id")
        motivo = str(body.get("motivo", "") or "")
        aprobar = sub.endswith("/aprobar")
        # P1d: decidir() audita en os_eventos DENTRO de la misma
        # transacción (ya no hay INSERT best-effort separado).
        reg = apr.decidir(aid, aprobar, ctx.user_id, motivo)
        if not aprobar:
            return _ok(None, {"ok": True, "aprobacion_id": aid,
                              "estado": "rechazada"})
        # P1d — EJECUCIÓN ÚNICA: solo quien ganó la decisión (pasó de
        # pendiente a aprobada) llega aquí; una decisión concurrente
        # recibe 409 en decidir() y nunca ejecuta dos veces.
        try:
            status, detalle = _ejecutar_aprobada(c, ctx, reg)
        except Exception as e:
            # Fallo inesperado: registrarlo para habilitar el reintento
            # y luego responder 500 (el handler exterior lo traduce).
            try:
                apr.marcar_ejecucion_fallida(
                    aid, f"{type(e).__name__}: {e}")
            except Exception:
                pass
            raise
        data = {"ok": status < 400, "aprobacion_id": aid,
                "tipo": reg["tipo"], "ejecucion": detalle}
        if status < 400 and detalle.get("ejecucion_automatica", True):
            marc = apr.marcar_ejecutada(aid, {"tipo": reg["tipo"]})
            data["estado"] = marc["estado"]
        else:
            data["estado"] = "aprobada"
            if status >= 400:
                # P1d: la ejecución falló -> queda registrada para que
                # aprobar de nuevo REINTENTE (una sola vez por intento).
                err = str(detalle.get("error") or detalle)[:500]
                try:
                    apr.marcar_ejecucion_fallida(aid, err)
                except Exception:
                    pass
                data["nota"] = ("la ejecución falló y quedó registrada: "
                                "la solicitud sigue 'aprobada'; aprobar "
                                "de nuevo reintenta la ejecución")
        if status >= 400:
            data["ok"] = False
            data["error"] = detalle.get("error") or detalle
            return status, data
        return _ok(None, data)

    return _err(None, 404, "recurso no encontrado")


def handle_aprobaciones(handler, store, ctx, token, method, path, query):
    """Despachador de /api/v1/os/aprobaciones/*. Firma idéntica a
    handle_os."""
    from webapp.os_routes import (_h, _need, _ok, _err, _send,
                                  _os_rate_limit_ok, _os_body_ok,
                                  _body_json, _paginar)
    srv = _h()
    if _os.environ.get("ZAYVERO_OS_ENABLED", "0") != "1":
        return _send(handler, _err(handler, 503,
                                   "módulo Business OS no habilitado"))
    sub = path[len("/api/v1/os/"):]
    if not _os_rate_limit_ok(handler, ctx, method):
        return True

    if method == "GET":
        if not _need(handler, store, ctx, "os.read", sub):
            return True
        try:
            result = _handle_get(_ok, _err, _paginar, ctx, sub, query)
        except (ErrorAprobacion,) as e:
            result = _map_error(_err, handler, e) or _err(
                handler, 400, str(e))
        except (ValueError, TypeError) as e:
            result = _err(handler, 400, f"parámetro inválido: {e}")
        except Exception as e:
            handler.log_message("aprobaciones GET falló: %s", e)
            result = _err(handler, 500, "error interno")
        return _send(handler, result)

    if method == "POST":
        if not _need(handler, store, ctx, "os.write", sub):
            return True
        if not srv._require_csrf(handler, store, ctx, token,
                                 resource=f"os:{sub}"):
            return True
        if not _os_body_ok(handler):
            return True
        # Decidir exige permiso explícito (owner/admin).
        if (sub in ("aprobaciones/aprobar", "aprobaciones/rechazar")
                and not _need(handler, store, ctx,
                              "aprobaciones.decidir", sub)):
            return True
        body = _body_json(handler)
        if not isinstance(body, dict):
            return _send(handler, _err(
                handler, 400, "cuerpo inválido: se esperaba objeto JSON"))
        try:
            result = _handle_post(_ok, _err, ctx, sub, body)
        except (ErrorAprobacion,) as e:
            result = _map_error(_err, handler, e) or _err(
                handler, 400, str(e))
        except (ValueError, TypeError) as e:
            result = _err(handler, 400, f"parámetro inválido: {e}")
        except Exception as e:
            handler.log_message("aprobaciones POST falló: %s", e)
            result = _err(handler, 500, "error interno")
        return _send(handler, result)

    return _send(handler, _err(handler, 405, "método no permitido"))
