"""BLOQUE CONTABILIDAD (2026-10-13) — Registro de rutas `contab/`.

CONTRATO (para el cableado en webapp, que lo hará otra fase):
- `rutas()` devuelve {subruta: spec} con claves RELATIVAS (el cableado
  antepone el prefijo `contab/`).
- spec = {"metodo": "GET"|"POST", "permiso": "os.read"|"os.write",
          "descripcion": str, "handler": fn}.
- `fn(bundle, ctx, payload) -> dict` JSON-serializable. `payload` es la
  query parseada (dict de listas, como en os_routes) en GET y el body
  JSON (dict) en POST.
- Errores: `ErrorContab`/`ValueError` -> 400; `NoEncontrado` -> 404;
  `PermisoDenegado` -> 403. El cableado los traduce; los handlers NO
  envían HTTP.

company_id SIEMPRE del ctx (anti-IDOR). Mutaciones: auditoría en
os_eventos en la MISMA transacción vía os_persist.auditoria.auditar
(patrón mutar_os; en memoria cae en bundle.eventos_memoria).
GET: solo lectura, sin lock.

Estado del bloque: registros en memoria ligados al bundle
(`_os_contab_bloque`), igual que os_crm/http.py. EXCEPCIÓN (P1b,
2026-10-13): los NCF SÍ persisten en PostgreSQL (migración 019) cuando
`ZAYVERO_OS_BACKEND=postgres` (ver os_contab/ncf_pg.py). EXCEPCIÓN (P1a,
2026-10-13): presupuestos y conciliación/extractos SÍ persisten en
PostgreSQL (migración 018, ver os_contab/almacen_pg.py, patrón tx_ambiente
re-entrante con auditoría en la misma transacción) cuando
`ZAYVERO_OS_BACKEND=postgres`; en modo memoria el comportamiento anterior
se conserva byte por byte. El resto del bloque sigue en memoria hasta el
wiring del panel (documentado en docs/bloque-contabilidad.md).
"""
from __future__ import annotations

import sys
import os
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .errores import (  # noqa: E402
    ErrorContab, NoEncontrado, PermisoDenegado)
from . import hunter_api as mod_hunter  # noqa: E402
from .presupuestos import (  # noqa: E402
    RegistroPresupuestos, Presupuesto, ErrorPresupuesto)
from . import rentabilidad_vistas as mod_rent  # noqa: E402
from .conciliacion import (  # noqa: E402
    RegistroConciliacion, movimientos_desde_libro)
from .almacen_pg import (  # noqa: E402
    RegistroPresupuestosPG, RegistroConciliacionPG)
from . import cxc_cxp as mod_cartera  # noqa: E402
from . import cashflow as mod_cashflow  # noqa: E402
from .itbis import (  # noqa: E402
    calcular_itbis, RegistroNCF, reporte_contador, TASA_REFERENCIA)
from .ncf_pg import (  # noqa: E402
    AlmacenNCF, tx_ncf, lectura_ncf)
from os_finanzas.expense_hunter import cazar_desperdicios  # noqa: E402


# ---- estado del bloque por bundle ---------------------------------------
_BLOQUE_ATTR = "_os_contab_bloque"
_bloque_lock = threading.RLock()


def _bloque(bundle) -> dict:
    with _bloque_lock:
        b = getattr(bundle, _BLOQUE_ATTR, None)
        if b is None:
            b = {"presupuestos": RegistroPresupuestos(bundle.company_id),
                 "conciliacion": RegistroConciliacion(bundle.company_id),
                 "ncf": RegistroNCF(bundle.company_id)}
            setattr(bundle, _BLOQUE_ATTR, b)
        if _es_pg() and "pg" not in b:
            # Registros PG (P1a): migran de forma idempotente lo que
            # hubiera en memoria (compatibilidad de lectura con datos
            # viejos, sin dañarlos).
            b["pg"] = {
                "presupuestos": RegistroPresupuestosPG(
                    bundle.company_id, mem=b["presupuestos"]),
                "conciliacion": RegistroConciliacionPG(
                    bundle.company_id, mem=b["conciliacion"]),
            }
        return b


def _reg(bundle, nombre: str):
    """Registro del bloque según el backend activo (P1a).

    En modo memoria devuelve el registro en memoria de `_bloque`;
    con `ZAYVERO_OS_BACKEND=postgres`, el registro PG equivalente
    (misma API pública).
    """
    b = _bloque(bundle)
    if _es_pg():
        return b["pg"][nombre]
    return b[nombre]


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
        raise ErrorContab("company_id del contexto no coincide con la empresa")
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


def _auditar(bundle, ctx, tipo: str, ref_id: str, detalle: dict):
    from os_persist.auditoria import auditar
    auditar(_ctx_company(bundle, ctx), tipo, _actor(ctx), ref_id, detalle)


def _es_pg() -> bool:
    """Backend de persistencia activo (patrón os_empleados/rutas.py)."""
    return os.environ.get("ZAYVERO_OS_BACKEND") == "postgres"


# =====================================================================
# Handlers: Expense Hunter (solo lectura)
# =====================================================================
def h_hunter(bundle, ctx, payload):
    query = payload
    _permiso(ctx, "os.read", "gastos/hunter")
    _ctx_company(bundle, ctx)
    gastos = bundle.gastos.todos()
    cfg = {}
    if _q1(query, "pico_factor"):
        cfg["factor"] = _q1(query, "pico_factor")
    umbral = _q1(query, "redondo_umbral")
    multiplo = _q1(query, "redondo_multiplo")
    hi = _q1(query, "hora_inicio")
    hf = _q1(query, "hora_fin")
    hallazgos = (
        mod_hunter.detectar_duplicados(gastos)
        + mod_hunter.detectar_picos(
            gastos, **({"factor": cfg["factor"]} if "factor" in cfg else {}))
        + mod_hunter.detectar_montos_redondos(
            gastos,
            **({"umbral": umbral} if umbral else {}),
            **({"multiplo": multiplo} if multiplo else {}))
        + mod_hunter.detectar_fuera_horario(
            gastos,
            **({"hora_inicio": int(hi)} if hi else {}),
            **({"hora_fin": int(hf)} if hf else {})))
    if _qbool(query, "desperdicios", True):
        for h in cazar_desperdicios(
                gastos, str(_q1(query, "hoy") or "") or None):
            hallazgos.append(mod_hunter.Hallazgo(
                tipo="pico_vs_historico", severidad=h.severidad,
                umbral=("total del último mes > 1.5× promedio mensual "
                        "histórico (≥3 meses de historia)"),
                evidencia={"detalle_origen": h.detalle},
                gasto_ids=list(h.gasto_ids),
                recomendacion=("Revisar si el aumento es estacional o un "
                               "desperdicio recurrente.")))
    por_tipo = {}
    for h in hallazgos:
        por_tipo[h.tipo] = por_tipo.get(h.tipo, 0) + 1
    return {"ok": True,
            "fuente": "RegistroGastos (solo lectura; no se modificó nada)",
            "gastos_analizados": len(gastos),
            "hallazgos": [h.to_dict() for h in hallazgos],
            "resumen_por_tipo": por_tipo,
            "nota_etica": mod_hunter.NOTA_ETICA}


# =====================================================================
# Handlers: presupuestos
# =====================================================================
def h_presupuesto_crear(bundle, ctx, payload):
    body = payload  # alias: POST recibe el body JSON
    _permiso(ctx, "os.write", "presupuestos/crear")
    cid = _ctx_company(bundle, ctx)
    body = body or {}
    p = Presupuesto(
        presupuesto_id=str(body.get("presupuesto_id") or "").strip(),
        company_id=cid,
        categoria=str(body.get("categoria") or "").strip(),
        periodo=str(body.get("periodo") or "").strip(),
        limite=str(body.get("limite") or "0"),
        moneda=str(body.get("moneda") or "").strip().upper())
    reg = _reg(bundle, "presupuestos")
    if _es_pg():
        # La auditoría viaja en la misma transacción (almacen_pg).
        creado = reg.crear(p, actor=_actor(ctx))
    else:
        creado = reg.crear(p)
        _auditar(bundle, ctx, "presupuesto", creado.presupuesto_id,
                 {"categoria": creado.categoria, "periodo": creado.periodo,
                  "limite": creado.to_dict()["limite"],
                  "moneda": creado.moneda})
    return {"ok": True, "data": creado.to_dict()}


def h_presupuesto_ejecucion(bundle, ctx, payload):
    query = payload  # alias: GET recibe la query parseada
    _permiso(ctx, "os.read", "presupuestos/ejecucion")
    _ctx_company(bundle, ctx)
    cat = _q1(query, "categoria")
    per = _q1(query, "periodo")
    if not cat or not per:
        raise ErrorContab("categoria y periodo son obligatorios")
    return {"ok": True, "data": _reg(bundle, "presupuestos").ejecucion(
        cat, per, bundle.gastos.todos())}


def h_presupuesto_proyeccion(bundle, ctx, payload):
    query = payload  # alias: GET recibe la query parseada
    _permiso(ctx, "os.read", "presupuestos/proyeccion")
    _ctx_company(bundle, ctx)
    cat = _q1(query, "categoria")
    per = _q1(query, "periodo")
    if not cat or not per:
        raise ErrorContab("categoria y periodo son obligatorios")
    return {"ok": True, "data": _reg(bundle, "presupuestos").proyeccion_cierre(
            cat, per, bundle.gastos.todos(),
            str(_q1(query, "hoy") or "") or None)}


# =====================================================================
# Handlers: rentabilidad (vistas honestas)
# =====================================================================
def h_rent_producto(bundle, ctx, payload):
    query = payload  # alias: GET recibe la query parseada
    _permiso(ctx, "os.read", "rentabilidad/producto")
    _ctx_company(bundle, ctx)
    pid = _q1(query, "producto_id")
    return mod_rent.vista_producto(
        bundle.libro, pid,
        str(_q1(query, "desde") or "") or None,
        str(_q1(query, "hasta") or "") or None)


def h_rent_sucursal(bundle, ctx, payload):
    query = payload  # alias: GET recibe la query parseada
    _permiso(ctx, "os.read", "rentabilidad/sucursal")
    _ctx_company(bundle, ctx)
    sid = _q1(query, "sucursal_id")
    return mod_rent.vista_sucursal(
        bundle.libro, sid,
        str(_q1(query, "desde") or "") or None,
        str(_q1(query, "hasta") or "") or None)


# =====================================================================
# Handlers: conciliación SIMULADA
# =====================================================================
def h_conciliacion_cargar(bundle, ctx, payload):
    body = payload  # alias: POST recibe el body JSON
    _permiso(ctx, "os.write", "conciliacion/cargar-extracto")
    _ctx_company(bundle, ctx)
    body = body or {}
    reg = _reg(bundle, "conciliacion")
    if _es_pg():
        # La auditoría viaja en la misma transacción (almacen_pg).
        res = reg.cargar_csv(
            str(body.get("csv") or ""), actor=_actor(ctx),
            extracto_id=str(body.get("extracto_id") or "") or None)
    else:
        res = reg.cargar_csv(str(body.get("csv") or ""))
        _auditar(bundle, ctx, "conciliacion", "extracto",
                 {"accion": "cargar_csv_simulado",
                  "lineas": res["lineas_cargadas"]})
    return {"ok": True, "data": res}


def h_conciliacion_estado(bundle, ctx, payload):
    _permiso(ctx, "os.read", "conciliacion/estado")
    _ctx_company(bundle, ctx)
    movs = movimientos_desde_libro(bundle.libro)
    return {"ok": True, "data": _reg(bundle, "conciliacion").conciliar(
        movs)}


def h_conciliacion_marcar(bundle, ctx, payload):
    body = payload  # alias: POST recibe el body JSON
    _permiso(ctx, "os.write", "conciliacion/marcar")
    _ctx_company(bundle, ctx)
    body = body or {}
    lid = str(body.get("linea_id") or "").strip()
    estado = str(body.get("estado") or "").strip()
    if not lid:
        raise ErrorContab("linea_id es obligatorio")
    # En PG la auditoría viaja en la misma transacción (almacen_pg);
    # la firma de marcar() es idéntica en ambos backends.
    res = _reg(bundle, "conciliacion").marcar(
        lid, estado, _actor(ctx), str(body.get("nota") or ""))
    if not _es_pg():
        _auditar(bundle, ctx, "conciliacion", lid,
                 {"accion": "marca_manual", "estado": estado,
                  "nota": str(body.get("nota") or "")})
    return {"ok": True, "data": res}


# =====================================================================
# Handlers: CxC / CxP
# =====================================================================
def h_cxc_resumen(bundle, ctx, payload):
    query = payload  # alias: GET recibe la query parseada
    _permiso(ctx, "os.read", "cxc/resumen")
    _ctx_company(bundle, ctx)
    return mod_cartera.resumen_cxc(
        bundle.cobranzas, str(_q1(query, "hoy") or "") or None)


def h_cxp_resumen(bundle, ctx, payload):
    query = payload  # alias: GET recibe la query parseada
    _permiso(ctx, "os.read", "cxp/resumen")
    _ctx_company(bundle, ctx)
    return mod_cartera.resumen_cxp(
        bundle.tesoreria, str(_q1(query, "hoy") or "") or None)


# =====================================================================
# Handlers: cash flow determinista
# =====================================================================
def h_cashflow(bundle, ctx, payload):
    query = payload  # alias: GET recibe la query parseada
    _permiso(ctx, "os.read", "cashflow/proyeccion")
    _ctx_company(bundle, ctx)
    dias = _q1(query, "dias", "90")
    try:
        dias_i = int(dias)
    except (TypeError, ValueError):
        raise ErrorContab(f"dias inválido: {dias}")
    return mod_cashflow.proyectar(
        bundle.tesoreria, dias_i, str(_q1(query, "hoy") or "") or None)


def h_cashflow_historial(bundle, ctx, payload):
    body = payload  # alias: POST recibe el body JSON
    """POST de CÁLCULO (no muta estado): proyección con ritmos históricos.

    Requiere os.read (no os.write) porque no escribe nada.
    """
    _permiso(ctx, "os.read", "cashflow/con-historial")
    _ctx_company(bundle, ctx)
    body = body or {}
    try:
        dias_i = int(body.get("dias", 90))
    except (TypeError, ValueError):
        raise ErrorContab(f"dias inválido: {body.get('dias')}")
    return mod_cashflow.proyectar(
        bundle.tesoreria, dias_i,
        str(body.get("hoy") or "") or None,
        historial_cobros=body.get("historial_cobros"),
        historial_pagos=body.get("historial_pagos"))


# =====================================================================
# Handlers: tesorería (posiciones por moneda)
# =====================================================================
def h_tesoreria_posiciones(bundle, ctx, payload):
    _permiso(ctx, "os.read", "tesoreria/posiciones")
    _ctx_company(bundle, ctx)
    return {"ok": True, "data": bundle.tesoreria.posiciones_por_moneda()}


# =====================================================================
# Handlers: ITBIS / NCF
#
# P1b (2026-10-13): con ZAYVERO_OS_BACKEND=postgres, los NCF viven en
# PostgreSQL (migración 019, os_contab/ncf_pg.py): numeración secuencial
# con bloqueo pesimista (sin duplicados), idempotencia y anulación
# auditada. En modo memoria se conserva el RegistroNCF del bundle.
# Cada salida lleva la marca "NO CERTIFICADO DGII" / "SINTÉTICO".
# =====================================================================
def _ventas_para_itbis(libro, desde, hasta):
    out = []
    for v in libro._ventas.values():
        if desde and (v.fecha or "") < desde:
            continue
        if hasta and (v.fecha or "") > hasta:
            continue
        out.append({"monto": str(v.total()), "moneda": v.moneda,
                    "fecha": (v.fecha or "")[:10], "exento": False,
                    "ref": v.venta_id})
    return out


def h_itbis_reporte(bundle, ctx, payload):
    query = payload  # alias: GET recibe la query parseada
    _permiso(ctx, "os.read", "itbis/reporte")
    _ctx_company(bundle, ctx)
    desde = str(_q1(query, "desde") or "") or None
    hasta = str(_q1(query, "hasta") or "") or None
    tasa = str(_q1(query, "tasa_pct") or "") or TASA_REFERENCIA
    calc = calcular_itbis(_ventas_para_itbis(bundle.libro, desde, hasta),
                          [], tasa_pct=tasa, desde=desde, hasta=hasta)
    calc["fuentes"] = {
        "ventas": "LibroFinanzas (todas no exentas en esta vista)",
        "compras": ("no provistas en GET: el crédito fiscal queda en 0; "
                    "use POST itbis/calcular con la lista de compras")}
    rep = reporte_contador(calc, _bloque(bundle)["ncf"])
    return rep


def h_itbis_calcular(bundle, ctx, payload):
    body = payload  # alias: POST recibe el body JSON
    """POST de CÁLCULO (no muta estado): ITBIS con listas explícitas."""
    _permiso(ctx, "os.read", "itbis/calcular")
    _ctx_company(bundle, ctx)
    body = body or {}
    ventas = body.get("ventas")
    if ventas is None:
        ventas = _ventas_para_itbis(
            bundle.libro,
            str(body.get("desde") or "") or None,
            str(body.get("hasta") or "") or None)
    return calcular_itbis(
        ventas, body.get("compras") or [],
        tasa_pct=str(body.get("tasa_pct") or "") or TASA_REFERENCIA,
        desde=str(body.get("desde") or "") or None,
        hasta=str(body.get("hasta") or "") or None)


def h_ncf_rango(bundle, ctx, payload):
    body = payload  # alias: POST recibe el body JSON
    _permiso(ctx, "os.write", "ncf/rango")
    cid = _ctx_company(bundle, ctx)
    body = body or {}
    tipo = str(body.get("tipo") or "")
    if _es_pg():
        # La auditoría la hace AlmacenNCF vía tx.auditar (misma tx).
        with tx_ncf(cid) as tx:
            return AlmacenNCF(tx.cur, cid, tx.auditar).crear_rango(
                tipo, body.get("desde"), body.get("hasta"), _actor(ctx))
    res = _bloque(bundle)["ncf"].crear_rango(
        tipo, body.get("desde"), body.get("hasta"))
    _auditar(bundle, ctx, "ncf", tipo.upper(),
             {"accion": "crear_rango_sintetico",
              "desde": body.get("desde"), "hasta": body.get("hasta")})
    return res


def h_ncf_emitir(bundle, ctx, payload):
    body = payload  # alias: POST recibe el body JSON
    _permiso(ctx, "os.write", "ncf/emitir")
    cid = _ctx_company(bundle, ctx)
    body = body or {}
    tipo = str(body.get("tipo") or "")
    concepto = str(body.get("concepto") or "")
    if _es_pg():
        clave = str(body.get("clave_idempotencia") or "") or None
        with tx_ncf(cid) as tx:
            return AlmacenNCF(tx.cur, cid, tx.auditar).emitir(
                tipo, concepto, _actor(ctx), clave)
    res = _bloque(bundle)["ncf"].emitir(tipo, concepto)
    _auditar(bundle, ctx, "ncf", res["ncf"],
             {"accion": "emitir_sintetico", "concepto": concepto})
    return res


def h_ncf_disponibles(bundle, ctx, payload):
    _permiso(ctx, "os.read", "ncf/disponibles")
    cid = _ctx_company(bundle, ctx)
    if _es_pg():
        with lectura_ncf(cid) as (_, alm):
            rangos = alm.rangos()
    else:
        rangos = _bloque(bundle)["ncf"].rangos()
    return {"ok": True, "sintetico": True, "rangos": rangos}


def h_ncf_emitidos(bundle, ctx, payload):
    query = payload  # alias: GET recibe la query parseada
    _permiso(ctx, "os.read", "ncf/emitidos")
    cid = _ctx_company(bundle, ctx)
    tipo = str(_q1(query, "tipo") or "") or None
    estado = str(_q1(query, "estado") or "") or None
    if _es_pg():
        with lectura_ncf(cid) as (_, alm):
            emitidos = alm.emitidos(tipo, estado)
    else:
        emitidos = _bloque(bundle)["ncf"].emitidos(tipo)
        if estado:
            emitidos = [e for e in emitidos
                        if e.get("estado", "emitido") == estado]
    return {"ok": True, "sintetico": True, "emitidos": emitidos,
            "aviso": ("NCF SINTÉTICOS — secuencia de prueba interna, "
                      "NO válida ante la DGII")}


def h_ncf_anular(bundle, ctx, payload):
    body = payload  # alias: POST recibe el body JSON
    _permiso(ctx, "os.write", "ncf/anular")
    cid = _ctx_company(bundle, ctx)
    body = body or {}
    numero = str(body.get("numero") or "")
    motivo = str(body.get("motivo") or "")
    if _es_pg():
        with tx_ncf(cid) as tx:
            return AlmacenNCF(tx.cur, cid, tx.auditar).anular(
                numero, motivo, _actor(ctx))
    res = _bloque(bundle)["ncf"].anular(numero, motivo)
    _auditar(bundle, ctx, "ncf", res["ncf"],
             {"accion": "anular", "motivo": motivo,
              "nota": "el número queda reservado; no se reutiliza"})
    return res


def h_ncf_documento(bundle, ctx, payload):
    query = payload  # alias: GET recibe la query parseada
    _permiso(ctx, "os.read", "ncf/documento")
    cid = _ctx_company(bundle, ctx)
    numero = str(_q1(query, "numero") or "")
    if not numero:
        raise ErrorContab("numero es obligatorio")
    if _es_pg():
        with lectura_ncf(cid) as (_, alm):
            return alm.documento(numero)
    return _bloque(bundle)["ncf"].documento(numero)


# =====================================================================
# Registro
# =====================================================================
def rutas() -> dict:
    """Registro de rutas del bloque. Prefijo: `contab/` (lo antepone el
    cableado).

    El cableado traduce errores: ErrorContab/ValueError -> 400,
    NoEncontrado -> 404, PermisoDenegado -> 403.
    """
    def _r(metodo, permiso, descripcion, handler):
        return {"metodo": metodo, "permiso": permiso,
                "descripcion": descripcion, "handler": handler}

    return {
        # --- Expense Hunter (solo lectura) -------------------------------
        "gastos/hunter": _r(
            "GET", "os.read",
            "Análisis de gastos: duplicados, picos, montos redondos y "
            "fuera de horario. Solo lectura; tono 'revisar', no acusa.",
            h_hunter),
        # --- presupuestos --------------------------------------------------
        "presupuestos/crear": _r(
            "POST", "os.write", "Crea un presupuesto por categoría/período.",
            h_presupuesto_crear),
        "presupuestos/ejecucion": _r(
            "GET", "os.read", "Ejecución vs presupuesto: % consumido y "
            "alertas de sobreejecución.", h_presupuesto_ejecucion),
        "presupuestos/proyeccion": _r(
            "GET", "os.read", "Proyección lineal de cierre de mes con "
            "supuestos explícitos.", h_presupuesto_proyeccion),
        # --- rentabilidad honesta ------------------------------------------
        "rentabilidad/producto": _r(
            "GET", "os.read",
            "Contribución por producto (gastos indirectos NO prorrateados).",
            h_rent_producto),
        "rentabilidad/sucursal": _r(
            "GET", "os.read",
            "P&L por sucursal (gastos directos atribuidos).",
            h_rent_sucursal),
        # --- conciliación SIMULADA ------------------------------------------
        "conciliacion/cargar-extracto": _r(
            "POST", "os.write",
            "Carga líneas de extracto desde CSV (SIMULADO: carga manual, "
            "sin conexión bancaria).", h_conciliacion_cargar),
        "conciliacion/estado": _r(
            "GET", "os.read",
            "Cotejo extracto vs libro: conciliado/pendiente/discrepancia "
            "(SIMULADO).", h_conciliacion_estado),
        "conciliacion/marcar": _r(
            "POST", "os.write",
            "Marca manual de una línea (SIMULADO; queda registrada).",
            h_conciliacion_marcar),
        # --- CxC / CxP -------------------------------------------------------
        "cxc/resumen": _r(
            "GET", "os.read",
            "CxC: saldos, aging por tramos y vencidos (reutiliza "
            "os_crm.cobranzas).", h_cxc_resumen),
        "cxp/resumen": _r(
            "GET", "os.read",
            "CxP: saldos, aging por tramos y vencidos.", h_cxp_resumen),
        # --- cash flow --------------------------------------------------------
        "cashflow/proyeccion": _r(
            "GET", "os.read",
            "Proyección determinista desde CxC/CxP programados "
            "(sin historial: ritmo en datos insuficientes).",
            h_cashflow),
        "cashflow/con-historial": _r(
            "POST", "os.read",
            "Proyección determinista + ritmos históricos provistos "
            "(cálculo, no muta estado).", h_cashflow_historial),
        # --- tesorería ----------------------------------------------------------
        "tesoreria/posiciones": _r(
            "GET", "os.read",
            "Posiciones de caja por moneda: caja + CxC − CxP.",
            h_tesoreria_posiciones),
        # --- ITBIS / NCF (preparación local) --------------------------------------
        "itbis/reporte": _r(
            "GET", "os.read",
            "Reporte para el contador: ITBIS de ventas del libro + NCF. "
            "PREPARACIÓN LOCAL, no certificado DGII.", h_itbis_reporte),
        "itbis/calcular": _r(
            "POST", "os.read",
            "Cálculo ITBIS con listas explícitas de ventas/compras "
            "(cálculo, no muta estado).", h_itbis_calcular),
        "ncf/rango": _r(
            "POST", "os.write",
            "Crea un rango NCF SINTÉTICO por tipo (control de rangos).",
            h_ncf_rango),
        "ncf/emitir": _r(
            "POST", "os.write",
            "Emite el siguiente NCF del rango (PG: secuencial con bloqueo "
            "pesimista, idempotente con clave_idempotencia; memoria: "
            "sintético, no válido DGII).", h_ncf_emitir),
        "ncf/disponibles": _r(
            "GET", "os.read", "Rangos NCF y disponibles.",
            h_ncf_disponibles),
        "ncf/emitidos": _r(
            "GET", "os.read",
            "Lista NCF emitidos (filtros tipo, estado). SINTÉTICOS, no "
            "válidos DGII.", h_ncf_emitidos),
        "ncf/anular": _r(
            "POST", "os.write",
            "Anula un NCF emitido (el número queda reservado, no se "
            "reutiliza). Anulación auditada en os_eventos.",
            h_ncf_anular),
        "ncf/documento": _r(
            "GET", "os.read",
            "Documento fiscal del NCF, claramente marcado NO CERTIFICADO "
            "DGII (documento de prueba interna).", h_ncf_documento),
    }
