"""BLOQUE 6 (sprint 3, 2026-10-12) — API HTTP del catálogo maestro.

`handle_catalogo`: despachador de `/api/v1/os/catalogo/*` con la misma
firma que `webapp.os_routes::handle_os` y `simulator.api::
handle_simulator` (para que `webapp/server.py` lo use como alternativa).

Seguridad (heredada del patrón /api/v1/os/*):
- Módulo OS habilitado (503 si no).
- Rate limiting ANTES de cualquier trabajo: GET usa cubeta de
  lectura, POST la de escritura (429 + Retry-After).
- Auth (401) y aislamiento multiempresa: `company_id` SIEMPRE del ctx
  (anti-IDOR); el `Catalogo` se liga a esa empresa.
- GET -> permiso `os.read`; POST -> `os.write` + CSRF (403 si falta).
- Cuerpo > tope -> 413 sin leer el body.

DIFERENCIA con handle_os: el catálogo NO pasa por `mutar_os` (sus
tablas normalizadas son la fuente primaria y `catalogo.api` ya
serializa por empresa con `os_company_lock`). Las escrituras usan
`catalogo.api.Catalogo` directamente.

Aprobación humana (ver aprobaciones/): POST `catalogo/precios` con un
cambio mayor al umbral configurado NO fija el precio: crea una
solicitud `pendiente_aprobacion` y responde 202.

Idempotencia (ver catalogo/idempotencia.py): POST `catalogo/precios`
y `catalogo/costos` aceptan el header opt-in `Idempotency-Key`.

Respuestas: {"ok": True, "data": {...}} / {"ok": False, "error": ...}.
Las lecturas de precios/costos traen `explicacion_datos` (fuente,
vigencia, moneda).
"""
from __future__ import annotations

import os as _os
import sys
from decimal import Decimal

sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from catalogo.api import Catalogo  # noqa: E402
from catalogo.modelo import (  # noqa: E402
    AlmacenNoExiste, CategoriaConProductos, CategoriaNoExiste,
    ConflictoVersion, ErrorCatalogo, ProductoNoExiste, SKUduplicado,
    ValidacionCatalogo, advertencias_precio_costo, validar_moneda,
    validar_precio, validar_costo,
)
from catalogo.idempotencia import (  # noqa: E402
    ConflictoClave, ejecutar_idempotente,
)


def _q1(query, name, default=""):
    v = query.get(name)
    if not v:
        return default
    return v[0] if isinstance(v, (list, tuple)) else v


def _explicacion_datos(moneda: str, vigente_desde=None,
                       vigente_hasta=None) -> dict:
    return {"fuente": "catalogo",
            "moneda": moneda,
            "vigente_desde": vigente_desde,
            "vigente_hasta": vigente_hasta,
            "nota": ("precio/costo maestro con vigencia; el historial "
                     "completo está en catalogo/precios/historial y "
                     "catalogo/costos/historial")}


def _map_error(_err, handler, e):
    """Catalogo/aprobaciones -> (status, body). None si no la conoce."""
    from aprobaciones.modelo import ErrorAprobacion
    msg = str(e)
    if isinstance(e, ConflictoClave):
        return _err(handler, 422, msg)
    if isinstance(e, (ProductoNoExiste, CategoriaNoExiste,
                      AlmacenNoExiste)):
        return _err(handler, 404, msg)
    if isinstance(e, (SKUduplicado, ConflictoVersion,
                      CategoriaConProductos)):
        return _err(handler, 409, msg)
    if isinstance(e, ErrorAprobacion):
        if "inexistente" in msg:
            return _err(handler, 404, msg)
        if "ya fue ejecutada" in msg or "ya está decidida" in msg:
            return _err(handler, 409, msg)
        return _err(handler, 400, msg)
    if isinstance(e, ValidacionCatalogo):
        if "duplicad" in msg:
            return _err(handler, 409, msg)
        return _err(handler, 400, msg)
    if isinstance(e, ErrorCatalogo):
        if "ZAYVERO_OS_DATABASE_URL" in msg:
            return _err(handler, 503,
                        "catálogo no disponible (sin PostgreSQL local)")
        return _err(handler, 400, msg)
    return None


def _en_rango(items, desde, hasta):
    if not desde and not hasta:
        return items
    out = []
    for it in items:
        f = (it.get("vigente_desde") or "")[:10]
        if desde and f < desde:
            continue
        if hasta and f > hasta:
            continue
        out.append(it)
    return out


# ---------------------------------------------------------------- GET
def _handle_get(_ok, _err, _paginar, ctx, sub, query):
    cat = Catalogo(ctx.company_id)

    if sub == "catalogo/resumen":
        data = cat.resumen()
        data["explicacion_datos"] = {
            "fuente": "catalogo",
            "nota": "conteos del catálogo maestro de la empresa"}
        return _ok(None, data)

    if sub == "catalogo/categorias":
        return _ok(None, _paginar(cat.listar_categorias(), query))

    if sub == "catalogo/almacenes":
        return _ok(None, _paginar(cat.listar_almacenes(), query))

    if sub == "catalogo/productos":
        items = cat.buscar_productos(
            texto=_q1(query, "q", "") or None,
            categoria_id=_q1(query, "categoria_id", "") or None,
            solo_activos=_q1(query, "solo_activos", "1") != "0",
            limite=int(_q1(query, "limite", "100") or 100))
        return _ok(None, _paginar(items, query))

    if sub == "catalogo/productos/detalle":
        pid = _q1(query, "id", "")
        if not pid:
            return _err(None, 400, "falta ?id=")
        prod = cat.obtener_producto(pid)
        if prod is None:
            return _err(None, 404, "producto no encontrado")
        moneda = validar_moneda(_q1(query, "moneda", "DOP") or "DOP")
        precio = cat.precio_vigente(pid, moneda)
        costo = cat.costo_vigente(pid, moneda)
        data = {"producto": prod,
                "precio_vigente": precio,
                "costo_vigente": costo,
                "explicacion_datos": {
                    "fuente": "catalogo",
                    "moneda": moneda,
                    "precio_vigente_desde": (
                        precio["vigente_desde"] if precio else None),
                    "costo_vigente_desde": (
                        costo["vigente_desde"] if costo else None),
                    "costo_fuente": costo["fuente"] if costo else None,
                    "nota": ("el precio/costo vigente es el de mayor "
                             "vigente_desde sin vigente_hasta")}}
        return _ok(None, data)

    if sub == "catalogo/precios":
        pid = _q1(query, "producto_id", "")
        if not pid:
            return _err(None, 400, "falta ?producto_id=")
        moneda = validar_moneda(_q1(query, "moneda", "DOP") or "DOP")
        p = cat.precio_vigente(pid, moneda)
        if p is None:
            return _err(None, 404, "sin precio vigente")
        return _ok(None, {**p,
                          "explicacion_datos": _explicacion_datos(
                              moneda, p["vigente_desde"])})

    if sub == "catalogo/precios/historial":
        pid = _q1(query, "producto_id", "")
        if not pid:
            return _err(None, 400, "falta ?producto_id=")
        moneda = validar_moneda(_q1(query, "moneda", "DOP") or "DOP")
        items = _en_rango(
            cat.historial_precios(
                pid, moneda,
                limite=int(_q1(query, "limite", "50") or 50)),
            _q1(query, "desde", ""), _q1(query, "hasta", ""))
        for it in items:
            it["explicacion_datos"] = _explicacion_datos(
                moneda, it["vigente_desde"], it["vigente_hasta"])
        return _ok(None, _paginar(items, query))

    if sub == "catalogo/costos":
        pid = _q1(query, "producto_id", "")
        if not pid:
            return _err(None, 400, "falta ?producto_id=")
        moneda = validar_moneda(_q1(query, "moneda", "DOP") or "DOP")
        c = cat.costo_vigente(pid, moneda)
        if c is None:
            return _err(None, 404, "sin costo vigente")
        return _ok(None, {**c,
                          "explicacion_datos": _explicacion_datos(
                              moneda, c["vigente_desde"])})

    if sub == "catalogo/costos/historial":
        pid = _q1(query, "producto_id", "")
        if not pid:
            return _err(None, 400, "falta ?producto_id=")
        moneda = validar_moneda(_q1(query, "moneda", "DOP") or "DOP")
        items = _en_rango(
            cat.historial_costos(
                pid, moneda,
                limite=int(_q1(query, "limite", "50") or 50)),
            _q1(query, "desde", ""), _q1(query, "hasta", ""))
        for it in items:
            it["explicacion_datos"] = _explicacion_datos(
                moneda, it["vigente_desde"], it["vigente_hasta"])
        return _ok(None, _paginar(items, query))

    if sub == "catalogo/validacion-costos":
        moneda = validar_moneda(_q1(query, "moneda", "DOP") or "DOP")
        alms = _q1(query, "almacenes", "")
        almacenes = ([a.strip() for a in alms.split(",") if a.strip()]
                     if alms else None)
        data = cat.validar_costos(moneda, almacenes)
        data["explicacion_datos"] = {
            "fuente": "catalogo",
            "moneda": moneda,
            "nota": ("costo nunca negativo (validación + CHECK en PG); "
                     "los hallazgos se corrigen fijando costos con "
                     "POST catalogo/costos")}
        return _ok(None, data)

    if sub == "catalogo/config":
        from catalogo.config import leer_config
        return _ok(None, leer_config(ctx.company_id))

    return _err(None, 404, "recurso no encontrado")


# ---------------------------------------------------------------- POST
def _handle_post(_ok, _err, ctx, sub, body, clave=""):
    cat = Catalogo(ctx.company_id)
    c = ctx.company_id

    def _req(*names):
        falt = [n for n in names
                if n not in body or body[n] is None
                or (isinstance(body[n], str) and not body[n].strip())]
        if falt:
            return _err(None, 400,
                        "faltan campos: " + ", ".join(falt))
        return None

    if sub == "catalogo/categorias":
        err = _req("categoria_id", "nombre")
        if err:
            return err
        return _ok(None, cat.crear_categoria(
            str(body["categoria_id"]), str(body["nombre"]),
            str(body["padre_id"]) if body.get("padre_id") else None), 201)

    if sub == "catalogo/categorias/eliminar":
        err = _req("categoria_id")
        if err:
            return err
        return _ok(None, cat.eliminar_categoria(str(body["categoria_id"])))

    if sub == "catalogo/almacenes":
        err = _req("almacen_id", "nombre")
        if err:
            return err
        return _ok(None, cat.crear_almacen(
            str(body["almacen_id"]), str(body["nombre"]),
            str(body.get("direccion", ""))), 201)

    if sub == "catalogo/almacenes/eliminar":
        err = _req("almacen_id")
        if err:
            return err
        return _ok(None, cat.eliminar_almacen(str(body["almacen_id"])))

    if sub == "catalogo/productos":
        err = _req("producto_id", "sku", "nombre")
        if err:
            return err
        try:
            res = cat.crear_producto(
                str(body["producto_id"]), str(body["sku"]),
                str(body["nombre"]),
                descripcion=str(body.get("descripcion", "")),
                categoria_id=(str(body["categoria_id"])
                              if body.get("categoria_id") else None),
                unidad=str(body.get("unidad", "und") or "und"),
                metadata=body.get("metadata") or {},
                precio=body.get("precio"),
                costo=body.get("costo"),
                moneda=str(body.get("moneda", "DOP") or "DOP"))
        except (KeyError, ValueError, TypeError) as e:
            return _err(None, 400, f"producto inválido: {e}")
        return _ok(None, res, 201)

    if sub == "catalogo/productos/actualizar":
        err = _req("producto_id", "version_esperada")
        if err:
            return err
        try:
            version = int(body["version_esperada"])
        except (ValueError, TypeError):
            return _err(None, 400, "version_esperada debe ser entero")
        campos = {k: body[k] for k in
                  ("nombre", "descripcion", "categoria_id", "unidad",
                   "metadata") if k in body}
        return _ok(None, cat.actualizar_producto(
            str(body["producto_id"]), version, **campos))

    if sub == "catalogo/productos/activar":
        err = _req("producto_id", "activo")
        if err:
            return err
        return _ok(None, cat.fijar_activo(str(body["producto_id"]),
                                          bool(body["activo"])))

    if sub == "catalogo/precios":
        return _post_precio(_ok, _err, ctx, cat, body, clave)

    if sub == "catalogo/costos":
        return _post_costo(_ok, _err, ctx, cat, body, clave)

    if sub == "catalogo/config":
        from catalogo.config import guardar_config
        try:
            data = guardar_config(
                c, **{k: body[k] for k in
                      ("margen_objetivo", "margen_minimo",
                       "umbral_cambio_precio", "umbral_devolucion_monto",
                       "moneda_defecto") if k in body})
        except (ValueError, TypeError) as e:
            return _err(None, 400, f"config inválida: {e}")
        return _ok(None, data)

    return _err(None, 404, "recurso no encontrado")


def _post_precio(_ok, _err, ctx, cat, body, clave=""):
    """Fijar precio: con umbral de aprobación + idempotencia."""
    c = ctx.company_id
    if not str(body.get("producto_id", "") or "").strip():
        return _err(None, 400, "falta campo: producto_id")
    if body.get("precio") is None:
        return _err(None, 400, "falta campo: precio")
    try:
        pid = str(body["producto_id"]).strip()
        precio = validar_precio(body["precio"])
        moneda = validar_moneda(str(body.get("moneda", "DOP") or "DOP"))
        motivo = str(body.get("motivo", "") or "")
    except ValidacionCatalogo as e:
        return _err(None, 400, str(e))

    # Umbral de aprobación (requiere PG; sin config legible se aplica
    # directo: el catálogo mismo exige PG, así que aquí siempre hay).
    from catalogo.config import leer_config
    from aprobaciones.modelo import (ConfigAprobaciones,
                                     requiere_aprobacion)
    from aprobaciones.api import Aprobaciones
    _fila_cfg = leer_config(c)
    cfg = ConfigAprobaciones.desde_fila({
        "umbral_cambio_precio": _fila_cfg["umbral_cambio_precio"],
        "umbral_devolucion_monto": _fila_cfg["umbral_devolucion_monto"],
        "moneda_defecto": _fila_cfg["moneda_defecto"],
        "personalizada": _fila_cfg["origen"] == "personalizado",
    })
    vigente = cat.precio_vigente(pid, moneda)
    req, motivo_apr = requiere_aprobacion(
        "cambio_precio", cfg,
        precio_vigente=(vigente["precio"] if vigente else None),
        precio_nuevo=precio)

    if req:
        # No se fija: se crea la solicitud y se responde 202. Con
        # Idempotency-Key la solicitud no se duplica (se repite la
        # respuesta almacenada).
        def _op_apr(cur):
            apr = Aprobaciones(c)
            sol = apr.solicitar(
                "cambio_precio", ctx.user_id,
                {"producto_id": pid, "moneda": moneda,
                 "precio_nuevo": str(precio),
                 "precio_vigente": (vigente["precio"] if vigente else None),
                 "motivo": motivo},
                monto=precio, moneda=moneda,
                motivo=(motivo_apr +
                        (f"; solicitado: {motivo}" if motivo else "")),
                # P1d: idempotencia por clave de operación (la
                # Idempotency-Key HTTP ya evita el reintento a este
                # nivel; esta es la segunda capa, a nivel solicitud).
                clave_operacion=(f"cambio_precio:{pid}:{moneda}:"
                                 f"{precio}"))
            payload = {"ok": True, "requiere_aprobacion": True,
                       "aprobacion_id": sol["aprobacion_id"],
                       "estado": sol["estado"],
                       "motivo": motivo_apr,
                       "nota": ("el precio NO se fijó: un rol con "
                                "permiso aprobaciones.decidir debe "
                                "aprobarlo en POST aprobaciones/aprobar")}
            return 202, payload
        return _con_idem(_ok, _err, c, "catalogo/precios", body,
                         clave, _op_apr)

    def _op(cur):
        from catalogo.api import (_existe_producto, _precio_vigente,
                                  _costo_vigente, _insertar_precio)
        _existe_producto(cur, c, pid)
        costo = _costo_vigente(cur, c, pid, moneda)
        adv = advertencias_precio_costo(
            precio, Decimal(str(costo["costo"])) if costo else None)
        _insertar_precio(cur, c, pid, moneda, precio, motivo)
        payload = {"ok": True, "producto_id": pid, "moneda": moneda,
                   "precio": str(precio), "advertencias": adv,
                   "explicacion_datos": _explicacion_datos(
                       moneda, None)}
        return 200, payload
    return _con_idem(_ok, _err, c, "catalogo/precios", body, clave, _op)


def _post_costo(_ok, _err, ctx, cat, body, clave=""):
    """Fijar costo: validación (nunca negativo) + idempotencia."""
    c = ctx.company_id
    if not str(body.get("producto_id", "") or "").strip():
        return _err(None, 400, "falta campo: producto_id")
    if body.get("costo") is None:
        return _err(None, 400, "falta campo: costo")
    try:
        pid = str(body["producto_id"]).strip()
        costo = validar_costo(body["costo"])
        moneda = validar_moneda(str(body.get("moneda", "DOP") or "DOP"))
        fuente = str(body.get("fuente", "manual") or "manual")
        motivo = str(body.get("motivo", "") or "")
        if fuente not in ("manual", "compra", "importacion",
                          "migracion"):
            raise ValidacionCatalogo(
                f"fuente de costo inválida: {fuente!r}")
    except ValidacionCatalogo as e:
        return _err(None, 400, str(e))

    def _op(cur):
        from catalogo.api import (_existe_producto, _precio_vigente,
                                  _insertar_costo)
        _existe_producto(cur, c, pid)
        precio = _precio_vigente(cur, c, pid, moneda)
        adv = advertencias_precio_costo(
            Decimal(str(precio["precio"])) if precio else None, costo)
        _insertar_costo(cur, c, pid, moneda, costo, fuente, motivo)
        payload = {"ok": True, "producto_id": pid, "moneda": moneda,
                   "costo": str(costo), "fuente": fuente,
                   "advertencias": adv,
                   "explicacion_datos": _explicacion_datos(moneda, None)}
        return 200, payload
    return _con_idem(_ok, _err, c, "catalogo/costos", body, clave, _op)


def _con_idem(_ok, _err, company_id, endpoint, body, clave, operacion):
    """Envuelve la operación con Idempotency-Key opt-in.

    La clave viaja en el header (la lee el despachador y la pasa).
    """
    try:
        status, payload = ejecutar_idempotente(
            company_id, clave, endpoint, body, operacion)
    except ConflictoClave as e:
        return _err(None, 422, str(e))
    return _ok(None, payload, status)


# ---------------------------------------------------------------- despachador
def handle_catalogo(handler, store, ctx, token, method, path, query):
    """Despachador de /api/v1/os/catalogo/*. Firma idéntica a handle_os."""
    from webapp.os_routes import (_h, _need, _ok, _err, _send,
                                  _os_rate_limit_ok, _os_body_ok,
                                  _body_json, _paginar)
    srv = _h()
    if _os.environ.get("ZAYVERO_OS_ENABLED", "0") != "1":
        return _send(handler, _err(handler, 503,
                                   "módulo Business OS no habilitado"))
    sub = path[len("/api/v1/os/"):]
    # BLOQUE 3: rate limiting antes de cualquier trabajo.
    if not _os_rate_limit_ok(handler, ctx, method):
        return True

    if method == "GET":
        if not _need(handler, store, ctx, "os.read", sub):
            return True
        try:
            result = _handle_get(_ok, _err, _paginar, ctx, sub, query)
        except (ErrorCatalogo, ConflictoClave) as e:
            result = _map_error(_err, handler, e) or _err(
                handler, 400, str(e))
        except (ValueError, TypeError) as e:
            result = _err(handler, 400, f"parámetro inválido: {e}")
        except Exception as e:  # fail-closed
            handler.log_message("catalogo GET falló: %s", e)
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
        body = _body_json(handler)
        if not isinstance(body, dict):
            return _send(handler, _err(
                handler, 400, "cuerpo inválido: se esperaba objeto JSON"))
        # Idempotency-Key opt-in (solo precios/costos).
        clave = (handler.headers.get("Idempotency-Key") or "").strip()
        if sub in ("catalogo/precios", "catalogo/costos") and clave:
            if len(clave) > 255:
                return _send(handler, _err(
                    handler, 400,
                    "Idempotency-Key demasiado larga (máx 255)"))
        try:
            result = _handle_post(_ok, _err, ctx, sub, body, clave)
        except (ErrorCatalogo, ConflictoClave) as e:
            result = _map_error(_err, handler, e) or _err(
                handler, 400, str(e))
        except (ValueError, TypeError) as e:
            result = _err(handler, 400, f"parámetro inválido: {e}")
        except Exception as e:  # fail-closed
            handler.log_message("catalogo POST falló: %s", e)
            result = _err(handler, 500, "error interno")
        return _send(handler, result)

    return _send(handler, _err(handler, 405, "método no permitido"))
