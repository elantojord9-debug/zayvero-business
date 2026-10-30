"""LOTE 8 — Rutas /api/v1/os/* del Business OS.

Seguridad:
- company_id siempre de ctx (anti-IDOR).
- GET -> os.read ; POST -> os.write + CSRF.
- Archivos: PDF (%PDF, <=5MB), CSV (<=1MB, parseable).

BLOQUE 3 — Rate limiting (ver tenant/rate_limit.py, sección BLOQUE 3):
- Toda petición /api/v1/os/* pasa por el limitador ANTES de cualquier
  trabajo: GET usa el presupuesto de lectura, POST el de escritura.
- Cubetas por usuario autenticado Y por IP (independientes).
- Al exceder: 429 + cabecera Retry-After.
- Cuerpos no-multipart > ZAYVERO_OS_RL_MAX_BODY_BYTES (def. 2 MiB) → 413
  antes de leer el body (multipart conserva sus topes de archivo propios).
- Auth (401), permisos por rol (403), CSRF (403) y aislamiento
  multiempresa quedan INTACTOS y se verifican después del rate limiting.

BLOQUE 2 — Idempotencia (ver os_persist/idempotencia.py, 2026-10-12):
- Los endpoints financieros/operativos (_SUBS_IDEMPOTENTES) aceptan el
  header opt-in `Idempotency-Key`.
- Misma clave + mismo body → se repite la respuesta almacenada sin
  re-ejecutar (sin doble cobro/descuento/compra). Misma clave + body
  distinto → 422. Operación fallida → la clave queda 'fallido' y se
  puede reintentar.
- La clave se reclama DENTRO de la transacción de mutar_os (mismo
  cursor, pre-commit): atomicidad clave+operación+auditoría.
- El hash es sobre el body JSON ya parseado (canónico, orden estable):
  el mismo body con distinto formato da el mismo hash.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webapp.os_api import get_bundle, cargar_demo, resumen_estado  # noqa: E402
from os_persist.auditoria import auditar, leer_eventos  # noqa: E402


def _h():
    from webapp import server as srv
    return srv


def _q1(query, name, default=""):
    """Primer valor de un parámetro de query ('' si ausente)."""
    v = query.get(name)
    return v[0] if v else default


# BLOQUE 4 (sprint 12-oct-2026) — paginación y filtros del panel.
_PAGE_DEF = 20
_PAGE_MAX = 100


def _paginacion(query):
    """(page, page_size) o None si no se pidió paginar.

    Sin page/page_size la respuesta conserva el formato legado (lista
    plana) para no romper consumidores existentes.
    """
    if "page" not in query and "page_size" not in query:
        return None
    try:
        page = int(_q1(query, "page", "1"))
        page_size = int(_q1(query, "page_size", str(_PAGE_DEF)))
    except (TypeError, ValueError):
        raise ValueError("page y page_size deben ser enteros")
    if page < 1:
        raise ValueError("page debe ser >= 1")
    if not 1 <= page_size <= _PAGE_MAX:
        raise ValueError(f"page_size debe estar entre 1 y {_PAGE_MAX}")
    return page, page_size


def _envelope(items, total, page, page_size):
    pages = (total + page_size - 1) // page_size if total else 0
    return {"items": items, "total": total, "page": page,
            "page_size": page_size, "pages": pages}


def _paginar(items, query):
    """Aplica paginación si se pidió; si no, devuelve la lista tal cual."""
    pp = _paginacion(query)
    if pp is None:
        return items
    page, page_size = pp
    total = len(items)
    ini = (page - 1) * page_size
    return _envelope(items[ini:ini + page_size], total, page, page_size)


def _num_key(v):
    """Clave numérica tolerante para ordenamiento (Decimal; lo no
    numérico queda como 0). Los montos/stocks llegan como str."""
    try:
        from decimal import Decimal, InvalidOperation
        if v is None or v == "":
            return Decimal(0)
        return Decimal(str(v))
    except Exception:
        return 0


def _aplicar_orden(items, query, campos):
    """SPRINT 3 (12-oct-2026) — ordenamiento de listados del panel:
    ?sort=<campo>&dir=asc|desc. `campos` mapea el nombre público a la
    función clave. Sin sort se conserva el orden previo (compatibilidad).
    Campo/dir inválidos -> ValueError (el handler responde 400)."""
    sort = _q1(query, "sort")
    if not sort:
        return items
    if sort not in campos:
        raise ValueError(
            "sort inválido: use " + ", ".join(sorted(campos)))
    dir_ = _q1(query, "dir", "asc").lower()
    if dir_ not in ("asc", "desc"):
        raise ValueError("dir debe ser asc o desc")
    return sorted(items, key=campos[sort], reverse=(dir_ == "desc"))


def _dentro_rango(fecha_iso, desde, hasta):
    """Compara por fecha calendario (primeros 10 chars ISO)."""
    f = (fecha_iso or "")[:10]
    if desde and f < desde:
        return False
    if hasta and f > hasta:
        return False
    return True


def _ok(handler, payload, status=200):
    """LOTE 9B: devuelve (status, cuerpo); el envío lo hace _send."""
    return (status, {"ok": True, "data": payload})


def _err(handler, status, msg):
    """LOTE 9B: devuelve (status, cuerpo); el envío lo hace _send."""
    return (status, {"ok": False, "error": msg})


def _send(handler, result):
    status, body = result
    _h()._send_json(handler, status, body)
    return True


def _need(handler, store, ctx, perm, resource):
    from tenant.authorization import require_permission, PermissionDenied
    try:
        require_permission(store, ctx, perm, resource)
        return True
    except PermissionDenied:
        _send(handler, _err(handler, 403, "acceso denegado"))
        return False


# BLOQUE 2 (2026-10-12): endpoints con soporte de Idempotency-Key.
# Financieros y operativos: los que mueven dinero, inventario o crean
# obligaciones. Fuera de esta lista el header se ignora (opt-in).
_SUBS_IDEMPOTENTES = frozenset({
    "ventas",
    "tesoreria/cobros",
    "compras/ordenes",
    "compras/recepciones",
    "ventas/cancelar",
    "compras/devoluciones",
    "finanzas/gastos",
})

_MAX_IDEMPOTENCY_KEY = 255


def _body_json(handler):
    """Lee el body JSON UNA sola vez y lo cachea en el handler.

    BLOQUE 2: el hash de idempotencia necesita el body parseado antes
    de entrar a mutar_os, y _handle_post lo necesita después; rfile
    solo se puede leer una vez.
    """
    cache = getattr(handler, "_os_body_json_cache", None)
    if cache is None:
        cache = _h()._read_json_body(handler)
        handler._os_body_json_cache = cache
    return cache


def _idem_params(handler, sub):
    """(idem, error): idem=(clave, endpoint, request_hash) o None.

    - Sin header Idempotency-Key -> (None, None): sin idempotencia.
    - Clave inválida o body no-objeto -> (None, (status, body)) para
      rechazar ya con 400.
    """
    clave = (handler.headers.get("Idempotency-Key") or "").strip()
    if not clave:
        return None, None
    if len(clave) > _MAX_IDEMPOTENCY_KEY:
        return None, _err(handler, 400,
                          "Idempotency-Key demasiado larga (máx 255)")
    body = _body_json(handler)
    if not isinstance(body, dict):
        return None, _err(handler, 400,
                          "cuerpo inválido: se esperaba objeto JSON")
    from os_persist.idempotencia import hash_peticion
    return (clave, sub, hash_peticion(body)), None


def _send_429(handler, retry_after):
    """BLOQUE 3: 429 genérico con cabecera Retry-After (segundos)."""
    import math as _math
    try:
        secs = max(1, int(_math.ceil(float(retry_after or 0))))
    except (TypeError, ValueError):
        secs = 1
    _h()._send_json(handler, 429,
                    {"ok": False,
                     "error": "demasiadas peticiones; "
                              "inténtalo de nuevo en unos segundos"},
                    extra_headers={"Retry-After": str(secs)})
    return True


def _os_rate_limit_ok(handler, ctx, method):
    """BLOQUE 3: cubetas por usuario autenticado y por IP.

    Devuelve True si la petición puede continuar; si no, envía 429 con
    Retry-After y devuelve False. Cada petición permitida CUENTA contra
    ambas cubetas (sin "perdón por éxito": no es login).
    """
    from tenant import rate_limit as _rl
    read = method != "POST"
    limiter = (_rl.get_os_read_limiter() if read
               else _rl.get_os_write_limiter())
    ip = _rl.client_origin_ip(handler)
    user = (getattr(ctx, "user_id", "") or getattr(ctx, "email", "")
            or "unknown")
    allowed, retry_after, reservation = limiter.try_acquire(ip=ip,
                                                           account=user)
    if not allowed:
        return _send_429(handler, retry_after) and False
    limiter.settle(reservation, success=False)
    return True


def _os_body_ok(handler):
    """BLOQUE 3: cuerpos no-multipart > tope → 413 (se drena el cuerpo
    antes de responder para que el cliente reciba el 413 en vez de
    ECONNRESET/broken-pipe).

    Los endpoints multipart (subida de PDF/CSV) conservan sus topes
    propios de archivo (5 MB / 1 MB) aplicados por _parse_multipart.
    """
    ctype = ((handler.headers.get("Content-Type", "") or "")
             .split(";")[0].strip().lower())
    if ctype.startswith("multipart/"):
        return True
    try:
        length = int(handler.headers.get("Content-Length", 0) or 0)
    except (TypeError, ValueError):
        length = 0
    from tenant import rate_limit as _rl
    limit = _rl.os_max_body_bytes()
    if length > limit:
        # Drenar el cuerpo antes de responder: si cerramos sin leerlo, el
        # cliente ve ECONNRESET/broken-pipe en vez del 413 (race de TCP).
        # Se drena con tope para no convertirlo en vector de DoS.
        _drenar_body(handler, min(length, 4 * 1024 * 1024))
        _h()._send_json(handler, 413,
                        {"ok": False,
                         "error": "cuerpo demasiado grande "
                                  f"(máximo {limit} bytes)"})
        return False
    return True


def _drenar_body(handler, nbytes: int) -> None:
    """Lee y descarta hasta nbytes del cuerpo de la petición."""
    restante = nbytes
    try:
        while restante > 0:
            trozo = handler.rfile.read(min(65536, restante))
            if not trozo:
                break
            restante -= len(trozo)
    except (OSError, ValueError):
        pass


def handle_os(handler, store, ctx, token, method, path, query):
    """Devuelve True si la ruta fue manejada."""
    import os as _os
    srv = _h()
    if not path.startswith("/api/v1/os/"):
        return False
    # LOTE 9A: el módulo OS se activa explícitamente. Deshabilitado por
    # defecto en despliegues públicos (ZAYVERO_OS_ENABLED=1 para activar).
    if _os.environ.get("ZAYVERO_OS_ENABLED", "0") != "1":
        return _send(handler, _err(handler, 503,
                                   "módulo Business OS no habilitado"))
    sub = path[len("/api/v1/os/"):]

    # BLOQUE 3: rate limiting ANTES de cualquier trabajo (lecturas y
    # escrituras con presupuestos distintos). No toca auth (401),
    # permisos (403), CSRF (403) ni aislamiento: esos siguen intactos
    # más abajo.
    if not _os_rate_limit_ok(handler, ctx, method):
        return True

    if method == "GET":
        bundle = get_bundle(ctx.company_id)
        if not _need(handler, store, ctx, "os.read", sub):
            return True
        if sub == "reportes" or sub.startswith("reportes/"):
            # SPRINT 5 PRIORIDAD 4 (2026-10-12): reportes CSV/XLSX/PDF.
            # Permiso base os.read ya verificado; los módulos sensibles
            # (finanzas, cobranzas) exigen además os.write dentro del
            # manejador (ver reportes/rutas.py).
            from reportes import rutas as _reportes_rutas
            return _reportes_rutas.manejar(
                handler, store, bundle, ctx, sub, query)
        try:
            result = _handle_get(handler, store, bundle, ctx, sub, query)
        except ValueError as e:
            # Parámetros de paginación/filtro inválidos -> 400 legible.
            result = _err(handler, 400, str(e))
        return _send(handler, result)

    if method == "POST":
        if not _need(handler, store, ctx, "os.write", sub):
            return True
        if not srv._require_csrf(handler, store, ctx, token,
                                 resource=f"os:{sub}"):
            return True
        # BLOQUE 3: cuerpo > tope → 413 antes de leer/parsear el body.
        if not _os_body_ok(handler):
            return True
        # BLOQUE 2: Idempotency-Key opt-in en endpoints financieros/
        # operativos. Se pre-parsea el body para el hash; _handle_post
        # reutiliza el cache (una sola lectura de rfile).
        idem, idem_err = (None, None)
        if sub in _SUBS_IDEMPOTENTES:
            idem, idem_err = _idem_params(handler, sub)
        if idem_err is not None:
            return _send(handler, idem_err)
        # REFORZAMIENTO: la mutación corre en transacción PG serializada
        # por empresa. Si PG falla o el commit falla, 500 fail-closed y
        # la caché se invalida (ningún GET ve lo no guardado).
        from os_persist.tx import mutar_os
        try:
            result = mutar_os(
                ctx.company_id,
                lambda b: _handle_post(handler, store, b, ctx, sub, query),
                idem=idem)
        except Exception as e:
            handler.log_message("OS mutación falló: %s", e)
            return _send(handler, (500, {"ok": False,
                                         "error": "no se pudo persistir"}))
        return _send(handler, result)

    _send(handler, _err(handler, 405, "método no permitido"))
    return True


# ---------------------------------------------------------------------------
# INTEGRACIÓN DE MÓDULOS (Orden Maestra, 2026-10-13): montaje de los
# contratos `rutas()` de los bloques en el dispatcher HTTP.
#
# Cada bloque expone su contrato sin tocar este archivo; aquí se adaptan
# al pipeline existente (rate-limit, CSRF, idempotencia, mutar_os,
# permisos por rol). Clave del registro: (metodo, sub).
#
# Formatos de contrato soportados:
#  - spec {metodo, permiso, handler} con claves relativas (empleados/,
#    contab/) o con prefijo (crm/): handler(bundle, ctx, datos).
#  - "METODO prefijo/..." -> fn(bundle, ctx, query) (ia/).
#  - manejar(bundle, ctx, sub, query, body, method) -> (manejada, resp)
#    (import/, integraciones/); métodos declarados en sus docstrings.
#  - {ruta: fn(bundle, ctx, qob)} con convención _metodo (inventario/,
#    compras/): la misma ruta sirve GET (lectura) y POST (mutación).
#  - saas: manejar(bundle, ctx, method, sub, payload) -> (status, dict)
#    con permisos propios; las 4 rutas públicas se montan en server.py.
# ---------------------------------------------------------------------------
_REGISTRO_OS = None

# Rutas SaaS públicas (sin autenticación; montadas en webapp/server.py).
_RUTAS_SAAS_PUBLICAS = frozenset({
    "saas/registro", "saas/planes", "saas/ayuda", "saas/usuarios/aceptar",
})

# Métodos POST declarados en los docstrings de los contratos sin spec.
_POST_IMPORT = frozenset({
    "docs/importar/vista-previa", "docs/importar/aplicar", "docs/ocr/extraer",
})


def _registro_os():
    """Construye (una vez) el registro {(metodo, sub): (permiso, llamada)}.

    llamada(bundle, ctx, datos) -> dict JSON-serializable. permiso=None
    significa que el módulo aplica sus propios controles (saas).
    """
    global _REGISTRO_OS
    if _REGISTRO_OS is not None:
        return _REGISTRO_OS
    import importlib
    reg = {}

    def poner(metodo, sub, permiso, llamada):
        clave = (metodo.upper(), sub)
        if clave in reg:
            raise ValueError(f"ruta OS duplicada: {metodo} {sub}")
        reg[clave] = (permiso, llamada)

    # 1) Specs {metodo, permiso, handler}, claves relativas.
    for prefijo, modulo in (("empleados/", "os_empleados.rutas"),
                            ("contab/", "os_contab.rutas"),
                            ("sector/", "os_sector.rutas"),
                            ("notif/", "os_notif.rutas"),
                            ("garantias/", "os_garantias.rutas"),
                            ("seguridad/", "os_seguridad.rutas")):
        for clave, spec in importlib.import_module(modulo).rutas().items():
            h = spec["handler"]
            poner(spec["metodo"], prefijo + clave, spec["permiso"],
                  lambda b, c, d, _h=h: _h(b, c, d))
    # 2) CRM: {metodo, permiso, fn}, claves ya con prefijo crm/.
    for clave, spec in importlib.import_module("os_crm.http").rutas().items():
        fn = spec["fn"]
        poner(spec["metodo"], clave, spec["permiso"],
              lambda b, c, d, _fn=fn: _fn(b, c, d))
    # 3) IA: "METODO ia/..." -> fn(bundle, ctx, query). Solo lectura
    #    salvo las rutas POST declaradas (seguimiento de Advisor, Medidor
    #    de ahorro, Autopilot-solo-propuestas, Centro de Decisiones).
    for clave, fn in importlib.import_module("os_ia").rutas().items():
        metodo, sub = clave.split(" ", 1)
        poner(metodo, sub, "os.read",
              lambda b, c, d, _fn=fn: _fn(b, c, d))
    # 4) Import/Integraciones vía su manejar() adaptador.
    for modulo in ("os_import.rutas", "os_integraciones.rutas"):
        mod = importlib.import_module(modulo)
        manejar = mod.manejar
        for clave in mod.rutas():
            es_post = (clave in _POST_IMPORT
                       or clave.endswith("/sincronizar"))
            metodo = "POST" if es_post else "GET"
            permiso = "os.write" if es_post else "os.read"

            def llamada(b, c, d, _m=manejar, _k=clave, _met=metodo):
                ok, resp = _m(b, c, _k,
                              query=d if _met == "GET" else None,
                              body=d if _met == "POST" else None,
                              method=_met)
                if not ok:
                    raise ValueError(f"ruta no manejada: {_k}")
                return resp
            poner(metodo, clave, permiso, llamada)
    # 5) Inventario/Compras: {ruta: fn(bundle, ctx, qob)} con convención
    #    _metodo — la misma ruta sirve GET (lectura) y POST (mutación).
    for modulo in ("os_inventory", "os_compras"):
        for clave, fn in importlib.import_module(modulo).rutas().items():
            poner("GET", clave, "os.read",
                  lambda b, c, d, _fn=fn: _fn(b, c, d))

            def llamada_post(b, c, d, _fn=fn):
                q = dict(d or {})
                q["_metodo"] = "POST"
                return _fn(b, c, q)
            poner("POST", clave, "os.write", llamada_post)
    # 6) SaaS autenticado: manejar() aplica método y permisos propios.
    from os_saas.rutas import manejar as _manejar_saas
    from os_saas.rutas import rutas as _rutas_saas
    for clave in _rutas_saas():
        if clave in _RUTAS_SAAS_PUBLICAS:
            continue
        # Se registran ambos métodos; manejar() devuelve 405 si el
        # método no corresponde a la ruta.
        for metodo in ("GET", "POST"):
            def llamada_ms(b, c, d, _m=_manejar_saas, _k=clave,
                           _met=metodo):
                return _m(b, c, _met, _k, d)
            poner(metodo, clave, None, llamada_ms)

    _REGISTRO_OS = reg
    return reg


def _despachar_modulo_os(handler, store, bundle, ctx, method, sub, datos):
    """Intenta despachar (method, sub) a un módulo con contrato rutas().

    Devuelve (status, body) si la ruta existe, o None si no corresponde a
    ningún módulo (el llamador sigue con su 404 propio).
    """
    entrada = _registro_os().get((method.upper(), sub))
    if entrada is None:
        return None
    permiso, llamada = entrada
    # Los handlers de módulos nuevos (p. ej. os_seguridad/MFA) pueden
    # necesitar el tenant store vía `bundle.store`. Se adjunta de forma
    # perezosa si el bundle no lo expone (el dataclass lo permite).
    if getattr(bundle, "store", None) is None and store is not None:
        try:
            bundle.store = store
        except Exception:
            pass
    if permiso is not None:
        # Control fino por ruta (p. ej. reportes de empleados exigen
        # os.write aun en GET). No se usa _need() aquí porque ya envía
        # la respuesta; se devuelve el 403 para un solo envío.
        from tenant.authorization import require_permission, PermissionDenied
        try:
            require_permission(store, ctx, permiso, sub)
        except PermissionDenied:
            return _err(handler, 403, "acceso denegado")
    try:
        resp = llamada(bundle, ctx, datos)
    except ValueError as e:
        return _err(handler, 400, str(e))
    except KeyError as e:
        # Parámetro obligatorio faltante -> 400 legible (no 500).
        return _err(handler, 400, f"parámetro faltante: {e}")
    except PermissionError as e:
        return _err(handler, 403, str(e) or "acceso denegado")
    except Exception as e:
        nombre = type(e).__name__
        if nombre == "NoEncontrado":
            return _err(handler, 404, str(e) or "no encontrado")
        if nombre in ("PermisoDenegado", "PermissionDeniedEmpleados"):
            return _err(handler, 403, str(e) or "acceso denegado")
        handler.log_message("OS módulo %s falló: %r", sub, e)
        return _err(handler, 500, "error interno del módulo")
    if isinstance(resp, tuple) and len(resp) == 2:
        # Adaptadores estilo manejar() devuelven (status, dict).
        return resp
    return _ok(handler, resp)


def _handle_get(handler, store, bundle, ctx, sub, query):
    from os_inventory.rotacion import analizar_rotacion, detectar_dead_stock
    from os_docs.score import calcular_score

    if sub == "estado":
        return _ok(handler, resumen_estado(bundle))
    if sub == "inventario/almacenes":
        alms = sorted({a for (_, a) in bundle.inventario.stock_empresa()})
        return _ok(handler, alms)
    if sub == "inventario/stock":
        items = [
            {"producto_id": p, "almacen_id": a, "stock": str(s)}
            for (p, a), s in sorted(bundle.inventario.stock_empresa().items())]
        alm = _q1(query, "almacen")
        if alm:
            items = [x for x in items if x["almacen_id"] == alm]
        # SPRINT 3: ordenamiento opcional del listado del panel.
        items = _aplicar_orden(items, query, {
            "producto": lambda x: x["producto_id"].lower(),
            "almacen": lambda x: x["almacen_id"].lower(),
            "stock": lambda x: _num_key(x["stock"])})
        return _ok(handler, _paginar(items, query))
    if sub == "inventario/alertas":
        from os_inventory.alertas import evaluar_alertas
        return _ok(handler, [a.to_dict() for a in evaluar_alertas(bundle.inventario)])
    if sub == "inventario/deadstock":
        return _ok(handler, [h.to_dict() for h in
                             detectar_dead_stock(bundle.inventario)])
    if sub == "inventario/rotacion":
        return _ok(handler, [r.to_dict() for r in
                             analizar_rotacion(bundle.inventario)])
    if sub == "compras/ordenes":
        oid = _q1(query, "id")
        if oid:
            o = bundle.compras.get_orden(oid)
            if o is None:
                return _err(handler, 404, "orden no encontrada")
            return _ok(handler, o.to_dict())
        # BLOQUE 4: listado con filtros (estado, rango de fechas) y
        # paginación para el panel.
        from os_compras.ordenes import EstadoOC as _EO2
        estado = _q1(query, "estado")
        if estado and estado not in _EO2.TODOS:
            raise ValueError(
                "estado inválido: use " + ", ".join(sorted(_EO2.TODOS)))
        desde = _q1(query, "desde")
        hasta = _q1(query, "hasta")
        items = []
        for o in bundle.compras.listar_ordenes():
            d = o.to_dict()
            if estado and d["estado"] != estado:
                continue
            if not _dentro_rango(d.get("fecha_emision"), desde, hasta):
                continue
            d["total"] = str(o.total())
            items.append(d)
        # SPRINT 3: ordenamiento opcional del listado del panel.
        items = _aplicar_orden(items, query, {
            "orden": lambda x: x["compra_id"].lower(),
            "proveedor": lambda x: x["proveedor_id"].lower(),
            "estado": lambda x: x["estado"],
            "emision": lambda x: x.get("fecha_emision") or "",
            "total": lambda x: _num_key(x.get("total"))})
        return _ok(handler, _paginar(items, query))
    if sub == "finanzas/rentabilidad":
        return _ok(handler, bundle.libro.rentabilidad())
    if sub == "finanzas/cashflow":
        dias = int((query.get("dias") or ["30"])[0])
        if dias not in (30, 60, 90):
            return _err(handler, 400, "dias debe ser 30, 60 o 90")
        return _ok(handler, bundle.tesoreria.proyectar(dias))
    if sub == "tesoreria/resumen":
        r = bundle.tesoreria.resumen_cxc_cxp()
        r["saldos"] = {m: bundle.tesoreria.saldo(m) for m in ("DOP", "USD")}
        return _ok(handler, r)
    if sub == "crm/clientes":
        # BLOQUE 4: búsqueda por nombre (q) + paginación.
        q = _q1(query, "q").strip().lower()
        items = [bundle.crm.ficha(cid).to_dict()
                 for cid in bundle.crm._clientes]
        if q:
            items = [x for x in items
                     if q in (x["cliente"].get("nombre") or "").lower()]
        items.sort(key=lambda x: (x["cliente"].get("nombre") or "").lower())
        # SPRINT 3: ordenamiento opcional del listado del panel.
        items = _aplicar_orden(items, query, {
            "nombre": lambda x: (x["cliente"].get("nombre") or "").lower(),
            "segmento": lambda x: (x.get("segmento") or "").lower(),
            "ltv": lambda x: _num_key(x.get("ltv")),
            "ultima_compra": lambda x: x.get("ultima_compra") or ""})
        return _ok(handler, _paginar(items, query))
    if sub.startswith("crm/clientes/"):
        cid = sub[len("crm/clientes/"):]
        try:
            return _ok(handler, bundle.crm.ficha(cid).to_dict())
        except Exception:
            return _err(handler, 404, "cliente no encontrado")
    if sub == "cobranzas/aging":
        # BLOQUE 4: filtro por tramo de mora y rango de vencimiento +
        # paginación.
        tramos = {"0-30", "31-60", "61-90", "90+"}
        tramo = _q1(query, "tramo")
        if tramo and tramo not in tramos:
            raise ValueError(
                "tramo inválido: use " + ", ".join(sorted(tramos)))
        desde = _q1(query, "desde")
        hasta = _q1(query, "hasta")
        items = [i.to_dict() for i in bundle.cobranzas.aging()]
        if tramo:
            items = [x for x in items if x["tramo"] == tramo]
        if desde or hasta:
            items = [x for x in items
                     if _dentro_rango(x.get("vencimiento"), desde, hasta)]
        # SPRINT 3: ordenamiento opcional del listado del panel.
        items = _aplicar_orden(items, query, {
            "cuenta": lambda x: x["cuenta_id"].lower(),
            "cliente": lambda x: x["cliente_id"].lower(),
            "pendiente": lambda x: _num_key(x.get("pendiente")),
            "vencimiento": lambda x: x.get("vencimiento") or "",
            "dias_vencida": lambda x: _num_key(x.get("dias_vencida")),
            "tramo": lambda x: x.get("tramo") or ""})
        return _ok(handler, _paginar(items, query))
    if sub == "score":
        s = calcular_score(
            libro=bundle.libro, tesoreria=bundle.tesoreria,
            inventario=bundle.inventario, cobranzas=bundle.cobranzas,
            crm=bundle.crm, gastos=bundle.gastos)
        return _ok(handler, s.to_dict())
    if sub == "auditoria/eventos":
        # BLOQUE 1 (2026-10-12): lectura de la bitácora de auditoría.
        # Aislamiento: company_id siempre del ctx (anti-IDOR).
        tipo = (query.get("tipo") or [None])[0]
        ref = (query.get("ref_id") or [None])[0]
        try:
            limite = max(1, min(int((query.get("limite") or ["200"])[0]),
                                1000))
        except ValueError:
            return _err(handler, 400, "limite debe ser entero")
        from webapp.os_api import _pg, backend_activo
        if backend_activo() == "postgres":
            try:
                evs = leer_eventos(_pg(), ctx.company_id, tipo=tipo,
                                   ref_id=ref, limite=limite)
            except ValueError as e:
                return _err(handler, 400, str(e))
            for e in evs:
                if e.get("fecha") is not None:
                    e["fecha"] = e["fecha"].isoformat()
            return _ok(handler, evs)
        mem = getattr(bundle, "eventos_memoria", [])
        evs = [e for e in mem
               if (tipo is None or e["tipo"] == tipo)
               and (ref is None or e["ref_id"] == ref)][-limite:]
        return _ok(handler, evs)
    if sub == "shield/alertas":
        # EXTRA 1 (2026-10-12): motor de riesgos empresariales. Lectura
        # -> permiso "os.read" (verificado en handle_os); company_id del
        # ctx (anti-IDOR); paginación/filtros por query (?hoy=,
        # ?severidad=, ?regla=, ?page=&page_size=).
        from shield.api import alertas_para_ruta
        try:
            items = alertas_para_ruta(bundle, ctx.company_id, query)
        except ValueError as e:
            return _err(handler, 400, str(e))
        # SPRINT 3 (12-oct-2026): conteo por gravedad sobre el total
        # filtrado (no solo la página) para el resumen prominente del
        # panel. Clave extra; no altera la paginación existente.
        env = _paginar(items, query)
        if isinstance(env, dict):
            resumen = {"critico": 0, "alerta": 0, "info": 0}
            for a in items:
                sev = a.get("severidad")
                if sev in resumen:
                    resumen[sev] += 1
            env["resumen_severidad"] = resumen
        return _ok(handler, env)
    if sub == "price-advisor/recomendaciones":
        # EXTRA 3 (2026-10-12): recomendaciones de precios basadas en
        # costos y márgenes comprobables. Solo lectura -> permiso
        # "os.read" (verificado en handle_os); company_id del ctx
        # (anti-IDOR); parámetros por query (?moneda=, ?margen_objetivo=,
        # ?margen_minimo=, ?tolerancia=, ?almacen=, ?producto_ids=,
        # ?ventana_demanda_dias=, ?hoy=, ?lang=). Ver price_advisor/api.py.
        from price_advisor.api import recomendaciones_para_ruta
        try:
            data = recomendaciones_para_ruta(bundle, ctx.company_id, query)
        except ValueError as e:
            return _err(handler, 400, str(e))
        return _ok(handler, data)
    # INTEGRACIÓN DE MÓDULOS (2026-10-13): delegación a los contratos
    # rutas() de los bloques antes del 404.
    _mod = _despachar_modulo_os(handler, store, bundle, ctx, "GET", sub,
                                query)
    if _mod is not None:
        return _mod
    return _err(handler, 404, "recurso no encontrado")


def _handle_post(handler, store, bundle, ctx, sub, query):
    srv = _h()
    c = ctx.company_id

    if sub == "demo/cargar":
        return _ok(handler, cargar_demo(bundle))

    if sub == "compras/ordenes":
        from os_compras.ordenes import OrdenCompra, LineaOC
        from decimal import Decimal
        body = _body_json(handler)  # BLOQUE 2: cacheado (hash pre-parseado)
        try:
            oc = OrdenCompra(
                compra_id=str(body["compra_id"]), company_id=c,
                proveedor_id=str(body["proveedor_id"]),
                moneda=str(body.get("moneda", "DOP")),
                lineas=[LineaOC(l["producto_id"], str(l["cantidad"]),
                                str(l["precio_unitario"]))
                        for l in body["lineas"]],
                fecha_emision=str(body.get("fecha_emision", "")),
                fecha_entrega_esperada=str(body.get("fecha_entrega_esperada", "")))
            oc.validate()
            bundle.compras.crear_orden(oc)
            # BLOQUE 1: auditoría en la misma transacción (mutar_os la
            # inserta pre-commit; si esto fallara, hay rollback).
            auditar(c, "compra", ctx.user_id, oc.compra_id, {
                "proveedor_id": oc.proveedor_id,
                "moneda": oc.moneda,
                "lineas": [{"producto_id": l.producto_id,
                            "cantidad": str(l.cantidad),
                            "precio_unitario": str(l.precio_unitario)}
                           for l in oc.lineas],
                "total_ordenado": str(sum(
                    (Decimal(l.cantidad) * Decimal(l.precio_unitario)
                     for l in oc.lineas), Decimal("0"))),
            })
            return _ok(handler, oc.to_dict(), 201)
        except (KeyError, ValueError) as e:
            return _err(handler, 400, f"orden inválida: {e}")
        except Exception as e:
            return _err(handler, 409, str(e))

    if sub == "compras/recepciones":
        from os_compras.recepciones import (Recepcion, LineaRecepcion,
                                            ErrorCompras)
        body = _body_json(handler)  # BLOQUE 2: cacheado
        try:
            r = Recepcion(
                recepcion_id=str(body["recepcion_id"]), company_id=c,
                compra_id=str(body["compra_id"]),
                almacen_id=str(body.get("almacen_id", "principal")),
                lineas=[LineaRecepcion(l["producto_id"], str(l["cantidad"]))
                        for l in body["lineas"]],
                fecha=str(body.get("fecha", "")),
                factura_ref=str(body.get("factura_ref", "")))
            oc = bundle.compras.get_orden(r.compra_id)
            from os_compras.ordenes import EstadoOC as _EO
            if oc.estado == _EO.BORRADOR:
                # La recepción confirma el envío: transición automática.
                oc.cambiar_estado(_EO.ENVIADA)
            mids = bundle.compras.recibir(r)
            for lin in r.lineas:
                pu = oc.precio_ordenado(lin.producto_id)
                if pu is not None:
                    bundle.costo.aplicar_entrada(
                        lin.producto_id, r.almacen_id, str(lin.cantidad),
                        str(pu), oc.moneda)
            # BLOQUE 1: auditoría (misma transacción vía mutar_os).
            auditar(c, "recepcion", ctx.user_id, r.recepcion_id, {
                "compra_id": r.compra_id,
                "almacen_id": r.almacen_id,
                "factura_ref": r.factura_ref,
                "lineas": [{"producto_id": lin.producto_id,
                            "cantidad": str(lin.cantidad)}
                           for lin in r.lineas],
                "movimientos": mids,
                "estado_orden": oc.estado,
            })
            return _ok(handler, {"movimientos": mids,
                                 "estado_orden": oc.estado}, 201)
        except (KeyError, ValueError) as e:
            return _err(handler, 400, f"recepción inválida: {e}")
        except ErrorCompras as e:
            return _err(handler, 409, str(e))

    if sub == "ventas":
        from os_crm.ventas import SolicitudVenta, ErrorVentas
        body = _body_json(handler)  # BLOQUE 2: cacheado
        try:
            sol = SolicitudVenta(
                venta_id=str(body["venta_id"]), company_id=c,
                cliente_id=str(body["cliente_id"]),
                lineas=[{"producto_id": l["producto_id"],
                         "cantidad": str(l["cantidad"]),
                         "precio_unitario": str(l["precio_unitario"]),
                         "almacen_id": l.get("almacen_id", "principal"),
                         "descuento_pct": str(l.get("descuento_pct", "0"))}
                        for l in body["lineas"]],
                moneda=str(body.get("moneda", "DOP")),
                fecha=str(body.get("fecha", "")),
                a_credito=bool(body.get("a_credito", False)),
                dias_credito=int(body.get("dias_credito", 30)),
                sucursal_id=str(body.get("sucursal_id", "")))
            # BLOQUE 1: stock antes/después para trazabilidad fiel.
            afectados = sorted({(l["producto_id"],
                                 l.get("almacen_id", "principal"))
                                for l in sol.lineas})
            stock_antes = {f"{p}@{a}": str(bundle.inventario.stock(p, a))
                           for p, a in afectados}
            reg = bundle.ventas.vender(sol)
            stock_despues = {f"{p}@{a}": str(bundle.inventario.stock(p, a))
                             for p, a in afectados}
            auditar(c, "venta", ctx.user_id, sol.venta_id, {
                "cliente_id": sol.cliente_id,
                "moneda": sol.moneda,
                "a_credito": sol.a_credito,
                "total": str(reg["total"]),
                "estado": str(reg["estado"]),
                "lineas": [{"producto_id": l["producto_id"],
                            "cantidad": str(l["cantidad"]),
                            "precio_unitario": str(l["precio_unitario"]),
                            "almacen_id": l.get("almacen_id", "principal"),
                            "descuento_pct": str(l.get("descuento_pct",
                                                       "0"))}
                           for l in sol.lineas],
                "stock": {k: {"antes": stock_antes[k],
                              "despues": stock_despues[k]}
                          for k in stock_antes},
            })
            return _ok(handler, {"venta_id": sol.venta_id,
                                 "total": reg["total"],
                                 "estado": reg["estado"]}, 201)
        except (KeyError, ValueError) as e:
            return _err(handler, 400, f"venta inválida: {e}")
        except ErrorVentas as e:
            return _err(handler, 409, str(e))
        except Exception as e:
            # ErrorCRM (cliente inexistente) u otros del dominio: 422,
            # nunca colgar la conexión
            return _err(handler, 422, f"venta no procesable: {e}")

    if sub == "crm/clientes":
        from oschema.models import Cliente
        body = srv._read_json_body(handler)
        try:
            cli = Cliente(cliente_id=str(body["cliente_id"]), company_id=c,
                          nombre=str(body["nombre"]),
                          email=str(body.get("email", "")),
                          telefono=str(body.get("telefono", "")))
            bundle.crm.registrar_cliente(cli)
            return _ok(handler, cli.to_dict(), 201)
        except (KeyError, ValueError) as e:
            return _err(handler, 400, f"cliente inválido: {e}")
        except Exception as e:
            return _err(handler, 409, str(e))

    if sub == "tesoreria/cobros":
        from os_finanzas.tesoreria import ErrorTesoreria
        body = _body_json(handler)  # BLOQUE 2: cacheado
        try:
            cuenta_id = str(body["cuenta_id"])
            monto = str(body["monto"])
            # BLOQUE 1: estado de la CxC antes/después para el evento.
            cx = bundle.tesoreria._cxc.get(cuenta_id)
            cobrado_antes = str(cx.cobrado) if cx else None
            pendiente_antes = str(cx.pendiente()) if cx else None
            bundle.tesoreria.registrar_cobro(
                cuenta_id=cuenta_id,
                monto=monto,
                fecha=str(body.get("fecha", "")))
            cx2 = bundle.tesoreria._cxc.get(cuenta_id)
            auditar(c, "cobro", ctx.user_id, cuenta_id, {
                "monto": monto,
                "moneda": cx2.moneda if cx2 else "",
                "cliente_id": cx2.cliente_id if cx2 else "",
                "referencia": cx2.referencia if cx2 else "",
                "cobrado_antes": cobrado_antes,
                "cobrado_despues": str(cx2.cobrado) if cx2 else None,
                "pendiente_antes": pendiente_antes,
                "pendiente_despues": (str(cx2.pendiente())
                                      if cx2 else None),
                "fecha": str(body.get("fecha", "")),
            })
            return _ok(handler, {"cobrado": True,
                                 "cuenta_id": cuenta_id})
        except (KeyError, ValueError, ErrorTesoreria) as e:
            return _err(handler, 400, f"cobro inválido: {e}")

    if sub == "finanzas/gastos":
        # 2026-10-13 (integridad financiera): la ÚNICA vía HTTP para
        # registrar un gasto operativo. Pasa por el cableado
        # RegistroGastos -> LibroFinanzas (bundle.libro ya trae el
        # registro conectado): idempotencia por gasto_id, sin doble
        # conteo, y evento 'gasto' en os_eventos en la MISMA
        # transacción (auditar() dentro de mutar_os).
        from os_finanzas.gastos import GastoDetallado
        from oschema.models import Gasto
        body = _body_json(handler)  # BLOQUE 2: cacheado
        try:
            g = GastoDetallado(
                Gasto(gasto_id=str(body["gasto_id"]), company_id=c,
                      categoria=str(body["categoria"]),
                      descripcion=str(body.get("descripcion", "")),
                      monto=str(body["monto"]),
                      moneda=str(body.get("moneda", "DOP")),
                      fecha=str(body.get("fecha", "")),
                      proveedor_id=str(body.get("proveedor_id", "")),
                      comprobante=str(body.get("comprobante", ""))),
                sucursal_id=str(body.get("sucursal_id", "")),
                centro_costo=str(body.get("centro_costo", "")))
            nuevo = bundle.libro.registrar_gasto(g)
            auditar(c, "gasto", ctx.user_id, g.gasto.gasto_id, {
                "categoria": g.gasto.categoria,
                "moneda": g.gasto.moneda,
                "monto": str(g.gasto.monto),
                "sucursal_id": g.sucursal_id,
                "centro_costo": g.centro_costo,
                "proveedor_id": g.gasto.proveedor_id,
                "comprobante": g.gasto.comprobante,
                "registrado": nuevo,
            })
            return _ok(handler, {
                "gasto_id": g.gasto.gasto_id,
                "registrado": nuevo,
                "nota": ("gasto registrado"
                         if nuevo else
                         "gasto_id ya existía: sin cambios, sin "
                         "doble conteo"),
            }, 201 if nuevo else 200)
        except (KeyError, ValueError) as e:
            return _err(handler, 400, f"gasto inválido: {e}")
        except Exception as e:
            return _err(handler, 409, str(e))

    if sub == "documentos":
        return _subir_documento(handler, bundle, c)

    if sub == "conciliacion":
        return _subir_extracto(handler, bundle, c)

    if sub == "conciliacion/aprobar":
        body = srv._read_json_body(handler)
        try:
            propuesta_id = str(body["propuesta_id"])
            # BLOQUE 1: propuesta antes de aprobar (qué se autorizó).
            prop = bundle.mesa._props.get(propuesta_id)
            prop_antes = prop.to_dict() if prop is not None else None
            asiento = bundle.mesa.aprobar(propuesta_id,
                                          aprobado_por=ctx.user_id)
            auditar(c, "aprobacion", ctx.user_id, propuesta_id, {
                "aprobado_por": ctx.user_id,
                "propuesta": prop_antes,
                "asiento": asiento.to_dict(),
            })
            return _ok(handler, asiento.to_dict())
        except (KeyError, ValueError) as e:
            return _err(handler, 400, f"no aprobable: {e}")

    if sub == "ventas/cancelar":
        # BLOQUE 1 (2026-10-12): expone cancelar_venta (devolución total
        # al cliente) como operación auditable. Antes no tenía ruta.
        # BLOQUE 6 (sprint 3): si el total supera el umbral configurado
        # (solo con PG), no se ejecuta: se crea una solicitud
        # pendiente_aprobacion y se responde 202.
        from os_crm.ventas import ErrorVentas
        body = _body_json(handler)  # BLOQUE 2: cacheado
        try:
            venta_id = str(body.get("venta_id", ""))
            previa = (bundle.ventas.estado_venta(venta_id)
                      if venta_id else None)
            if previa and previa.get("total") is not None:
                sol = previa["solicitud"]
                apr = _aprobacion_requerida(
                    c, "devolucion_venta", ctx.user_id,
                    previa.get("total"), sol.moneda,
                    {"venta_id": venta_id,
                     "motivo": str(body.get("motivo", ""))})
                if apr is not None:
                    return apr  # 202: pendiente de aprobación humana
            return _ejecutar_venta_cancelar(bundle, c, ctx, body)
        except ErrorVentas as e:
            msg = str(e)
            codigo = 404 if "inexistente" in msg else 409
            return _err(handler, codigo, msg)

    if sub == "compras/devoluciones":
        # BLOQUE 1 (2026-10-12): expone devolucion_a_proveedor como
        # operación auditable. Antes no tenía ruta.
        # BLOQUE 6 (sprint 3): umbral de aprobación como en
        # ventas/cancelar.
        from os_compras.contratos import devolucion_a_proveedor
        body = _body_json(handler)  # BLOQUE 2: cacheado
        try:
            compra_id = str(body.get("compra_id", ""))
            producto_id = str(body.get("producto_id", ""))
            cantidad = str(body.get("cantidad", ""))
            oc = (bundle.compras.get_orden(compra_id)
                  if compra_id else None)
            pu = (oc.precio_ordenado(producto_id)
                  if oc is not None and producto_id else None)
            monto = None
            if pu is not None and cantidad:
                try:
                    from decimal import Decimal as _D
                    monto = _D(str(cantidad)) * pu
                except Exception:
                    monto = None  # cantidad inválida: el legado da 400
            if monto is not None:
                    apr = _aprobacion_requerida(
                        c, "devolucion_compra", ctx.user_id, monto,
                        oc.moneda,
                        {"compra_id": compra_id,
                         "producto_id": producto_id,
                         "almacen_id": str(body.get("almacen_id",
                                                    "principal")),
                         "cantidad": cantidad,
                         "motivo": str(body.get("motivo", ""))})
                    if apr is not None:
                        return apr  # 202
            return _ejecutar_devolucion_compra(bundle, c, ctx, body)
        except (KeyError, ValueError) as e:
            return _err(handler, 400, f"devolución inválida: {e}")
        except Exception as e:
            return _err(handler, 409, str(e))

    # INTEGRACIÓN DE MÓDULOS (2026-10-13): delegación a los contratos
    # rutas() de los bloques antes del 404. Las mutaciones corren dentro
    # del mutar_os de handle_os (transacción PG por empresa).
    _mod = _despachar_modulo_os(handler, store, bundle, ctx, "POST", sub,
                                _body_json(handler))
    if _mod is not None:
        return _mod
    return _err(handler, 404, "recurso no encontrado")


# ---------------------------------------------------------------------
# BLOQUE 6 (sprint 3, 2026-10-12) — Aprobación humana de devoluciones.
#
# `_aprobacion_requerida` evalúa el umbral SOLO con backend PG
# (os_catalogo_config). Sin DSN devuelve None: comportamiento legado
# (la devolución se ejecuta directo, como antes del sprint 3).
# Con DSN, si la evaluación o la solicitud fallan, la excepción
# sube a handle_os -> 500 fail-closed (no se ejecuta a ciegas).
# ---------------------------------------------------------------------
def _clave_operacion_aprobacion(tipo: str, payload: dict) -> str | None:
    """Clave de idempotencia de la solicitud de aprobación (P1d).

    Identidad estable de la OPERACIÓN (no del intento): repetir la
    misma solicitud (doble clic, reintento de red) devuelve la
    solicitud existente en vez de duplicarla. Sin identidad estable
    (p. ej. venta_id vacío) -> None (legado: sin idempotencia).
    """
    if tipo == "devolucion_venta":
        vid = str(payload.get("venta_id", "") or "").strip()
        return f"devolucion_venta:{vid}" if vid else None
    if tipo == "devolucion_compra":
        cid = str(payload.get("compra_id", "") or "").strip()
        if not cid:
            return None
        return (f"devolucion_compra:{cid}:"
                f"{payload.get('producto_id', '')}:"
                f"{payload.get('cantidad', '')}:"
                f"{payload.get('almacen_id', 'principal')}")
    return None


def _aprobacion_requerida(company_id: str, tipo: str, user_id: str,
                          monto, moneda: str | None,
                          payload: dict):
    """(status, body) con 202 si requiere aprobación; None si ejecuta
    directo (bajo umbral o sin PG)."""
    import os as _os
    if not _os.environ.get("ZAYVERO_OS_DATABASE_URL"):
        return None  # sin PG: legado
    from aprobaciones.api import Aprobaciones, umbrales_empresa
    from aprobaciones.modelo import requiere_aprobacion
    cfg = umbrales_empresa(company_id)
    req, motivo_apr = requiere_aprobacion(tipo, cfg, monto=monto)
    if not req:
        return None
    sol = Aprobaciones(company_id).solicitar(
        tipo, user_id, payload, monto=monto, moneda=moneda,
        motivo=motivo_apr,
        # P1d: idempotencia por clave de operación. La auditoría de la
        # solicitud la hace solicitar() en la MISMA transacción.
        clave_operacion=_clave_operacion_aprobacion(tipo, payload))
    return _ok(None, {
        "ok": True, "requiere_aprobacion": True,
        "aprobacion_id": sol["aprobacion_id"],
        "estado": "pendiente_aprobacion", "motivo": motivo_apr,
        "nota": ("la operación NO se ejecutó: un rol con permiso "
                 "'aprobaciones.decidir' debe aprobarla en "
                 "POST /api/v1/os/aprobaciones/aprobar")}, 202)


def _ejecutar_venta_cancelar(bundle, company_id: str, ctx, body: dict):
    """Ejecuta la cancelación de venta (extraído de _handle_post para
    reutilizarlo en la ejecución de aprobaciones)."""
    from os_crm.ventas import ErrorVentas
    try:
        venta_id = str(body["venta_id"])
        motivo = str(body.get("motivo", ""))
        previa = bundle.ventas.estado_venta(venta_id)
        lineas_previas = ([{"producto_id": l["producto_id"],
                            "cantidad": str(l["cantidad"]),
                            "almacen_id": l.get("almacen_id",
                                                "principal")}
                           for l in previa["solicitud"].lineas]
                          if previa and previa.get("solicitud")
                          else [])
        reg = bundle.ventas.cancelar_venta(venta_id, motivo)
        auditar(company_id, "devolucion", ctx.user_id, venta_id, {
            "tipo_devolucion": "cancelacion_venta",
            "motivo": motivo,
            "total_revertido": str(reg.get("total", "")),
            "nota_credito": reg.get("nota_credito"),
            "estado": str(reg.get("estado", "")),
            "lineas": lineas_previas,
        })
        return _ok(None, {"venta_id": venta_id,
                          "estado": str(reg.get("estado", "")),
                          "nota_credito": reg.get("nota_credito")})
    except (KeyError, ValueError) as e:
        return _err(None, 400, f"cancelación inválida: {e}")
    except ErrorVentas as e:
        msg = str(e)
        codigo = 404 if "inexistente" in msg else 409
        return _err(None, codigo, msg)
    except Exception as e:
        return _err(None, 409, str(e))


def _ejecutar_devolucion_compra(bundle, company_id: str, ctx, body: dict):
    """Ejecuta la devolución a proveedor (extraído de _handle_post)."""
    from os_compras.contratos import devolucion_a_proveedor
    try:
        compra_id = str(body["compra_id"])
        producto_id = str(body["producto_id"])
        almacen_id = str(body.get("almacen_id", "principal"))
        cantidad = str(body["cantidad"])
        motivo = str(body.get("motivo", ""))
        mid = devolucion_a_proveedor(bundle.compras, compra_id,
                                     producto_id, almacen_id, cantidad,
                                     motivo)
        auditar(company_id, "devolucion", ctx.user_id, compra_id, {
            "tipo_devolucion": "a_proveedor",
            "producto_id": producto_id,
            "almacen_id": almacen_id,
            "cantidad": cantidad,
            "motivo": motivo,
            "movimiento_id": mid,
        })
        return _ok(None, {"movimiento_id": mid,
                          "compra_id": compra_id}, 201)
    except (KeyError, ValueError) as e:
        return _err(None, 400, f"devolución inválida: {e}")
    except Exception as e:
        return _err(None, 409, str(e))


def _subir_documento(handler, bundle, company_id):
    srv = _h()
    try:
        fields, files = srv._parse_multipart(handler, max_bytes=5 * 1024 * 1024)
    except ValueError as e:
        return _err(handler, 400, f"archivo inválido: {e}")
    f = files.get("archivo")
    if not f:
        return _err(handler, 400, "falta el archivo (campo 'archivo')")
    try:
        with open(f["path"], "rb") as fh:
            magia = fh.read(5)
        if magia != b"%PDF-":
            return _err(handler, 400, "solo se aceptan PDF")
        from os_docs.documentos import (extraer_texto_pdf, parsear_factura,
                                        RegistroDocumentos, ErrorDocumento)
        import uuid
        texto = extraer_texto_pdf(f["path"])
        fac = parsear_factura(
            texto, documento_id=f"doc-{uuid.uuid4().hex[:10]}",
            company_id=company_id)
        fac.validar()
        bundle.documentos.registrar(fac)
        d = {"documento_id": fac.documento_id, "numero": fac.numero,
             "fecha": fac.fecha, "proveedor": fac.proveedor_nombre,
             "total": fac.total, "moneda": fac.moneda,
             "lineas": [{"descripcion": l.descripcion,
                         "cantidad": l.cantidad,
                         "precio_unitario": l.precio_unitario}
                        for l in fac.lineas],
             "formato": fac.formato, "advertencias": fac.advertencias,
             "requiere_revision": fac.formato == "revision_manual"}
        return _ok(handler, d, 201)
    except ErrorDocumento as e:
        return _err(handler, 422, f"documento no procesable: {e}")
    finally:
        try:
            os.unlink(f["path"])
        except OSError:
            pass


def _subir_extracto(handler, bundle, company_id):
    srv = _h()
    try:
        fields, files = srv._parse_multipart(handler, max_bytes=1024 * 1024)
    except ValueError as e:
        return _err(handler, 400, f"archivo inválido: {e}")
    f = files.get("archivo")
    if not f:
        return _err(handler, 400, "falta el archivo (campo 'archivo')")
    try:
        with open(f["path"], "rb") as fh:
            raw = fh.read()
        try:
            texto = raw.decode("utf-8")
        except UnicodeDecodeError:
            return _err(handler, 400, "el CSV debe ser UTF-8")
        from os_docs.contador import importar_extracto, conciliar
        movs = importar_extracto(texto)
        props = conciliar(movs, bundle.tesoreria)
        bundle.mesa.cargar(props)
        return _ok(handler, {"propuestas": [p.to_dict() for p in props],
                             "nota": "ninguna propuesta se ejecuta sola; "
                                     "requieren aprobación humana"}, 201)
    except ValueError as e:
        return _err(handler, 422, f"extracto no procesable: {e}")
    finally:
        try:
            os.unlink(f["path"])
        except OSError:
            pass
