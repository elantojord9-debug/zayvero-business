"""Contador IA — asistente informativo SOLO LECTURA (TALENTO).

REGLA DURA: este módulo jamás escribe en la base de datos (solo SELECT),
jamás llama a APIs externas ni a LLMs, y sus recomendaciones no modifican
obligaciones fiscales ni ejecutan operaciones financieras: son texto
informativo generado con reglas deterministas a partir de los datos
reales de la empresa.

Todos los montos usan Decimal exacto; toda consulta filtra por empresa_id.
"""
from __future__ import annotations

from factura_core import clock

import json
from collections import defaultdict
from datetime import date
from decimal import Decimal

from factura_persist import db
from factura_core import clientes as cli_mod
from factura_core import gastos as gastos_mod
from factura_core import inventario as inv_mod
from factura_core import pagos as pagos_mod
from factura_core import reportes


DISCLAIMER = (
    "Recomendación informativa generada automáticamente a partir de los "
    "datos de tu empresa. No constituye asesoría fiscal ni financiera, no "
    "modifica obligaciones fiscales y no ejecuta operaciones: es solo "
    "lectura."
)

_CERO = Decimal("0")


# --- helpers -----------------------------------------------------------------

def _rd(d) -> str:
    """Monto con formato RD$ 5,400.00 (Decimal, sin mutar el valor)."""
    try:
        q = Decimal(str(d)).quantize(Decimal("0.01"))
    except Exception:
        q = _CERO
    return f"RD$ {q:,.2f}"


def _ventanas(referencia: date | None = None) -> dict:
    """Ventanas de mes actual y mes anterior (ISO) desde una referencia."""
    ref = referencia or clock.today_business()
    mes_ini = ref.replace(day=1)
    # primer día del mes siguiente
    if mes_ini.month == 12:
        mes_sig = mes_ini.replace(year=mes_ini.year + 1, month=1)
    else:
        mes_sig = mes_ini.replace(month=mes_ini.month + 1)
    # primer día del mes anterior
    if mes_ini.month == 1:
        prev_ini = mes_ini.replace(year=mes_ini.year - 1, month=12)
    else:
        prev_ini = mes_ini.replace(month=mes_ini.month - 1)
    return {
        "referencia": ref.isoformat(),
        "mes_ini": mes_ini.isoformat(),
        "mes_fin": (date.fromordinal(mes_sig.toordinal() - 1)
                    .isoformat()),  # último día del mes
        "prev_ini": prev_ini.isoformat(),
        "prev_fin": (date.fromordinal(mes_ini.toordinal() - 1)
                     .isoformat()),  # último día del mes anterior
        "mes_nombre": _mes_nombre(mes_ini.month, mes_ini.year),
        "prev_nombre": _mes_nombre(prev_ini.month, prev_ini.year),
    }


def _mes_nombre(m: int, y: int) -> str:
    nombres = ("enero", "febrero", "marzo", "abril", "mayo", "junio",
               "julio", "agosto", "septiembre", "octubre", "noviembre",
               "diciembre")
    return f"{nombres[m - 1]} {y}"


def _ventas_rango(eid: int, desde: str, hasta: str) -> list[dict]:
    """Ventas (factura/POS, no anuladas) en el rango. SOLO SELECT."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        filas = db.fetchall(
            cur, "SELECT id, cliente_id, items, total, descuento, creada"
                 " FROM factura_ventas WHERE empresa_id=%s"
                 " AND estado<>'anulada' AND tipo IN ('factura','pos')"
                 " AND creada>=%s AND creada<%s"
                 " ORDER BY creada",
            (eid, desde, hasta))
        return [dict(f) for f in filas]
    finally:
        conn.close()


def _pct_cambio(actual: Decimal, anterior: Decimal) -> Decimal | None:
    if anterior <= 0:
        return None
    return ((actual - anterior) / anterior * 100).quantize(Decimal("0.1"))


# --- 1. resumen del mes -------------------------------------------------------

def resumen_mes(eid: int, referencia: date | None = None) -> dict:
    """Ventas del mes actual vs mes anterior (SOLO LECTURA)."""
    v = _ventanas(referencia)
    actual = _ventas_rango(eid, v["mes_ini"], v["mes_fin"])
    anterior = _ventas_rango(eid, v["prev_ini"], v["prev_fin"])
    total = sum((db.dec(x["total"]) for x in actual), _CERO)
    total_ant = sum((db.dec(x["total"]) for x in anterior), _CERO)
    n = len(actual)
    ticket = (total / n).quantize(Decimal("0.01")) if n else _CERO
    cambio = _pct_cambio(total, total_ant)
    return {
        "mes": v["mes_nombre"],
        "mes_anterior_nombre": v["prev_nombre"],
        "desde": v["mes_ini"], "hasta": v["mes_fin"],
        "ventas_total": str(total),
        "n_facturas": n,
        "ticket_promedio": str(ticket),
        "ventas_mes_anterior": str(total_ant),
        "cambio_pct": str(cambio) if cambio is not None else None,
    }


# --- 2. margen -----------------------------------------------------------------

def margen_mes(eid: int, referencia: date | None = None) -> dict:
    """Estado de resultados del mes + margen neto. SOLO LECTURA.

    El costo de ventas es una aproximación con el costo ACTUAL del
    catálogo (declarado en la nota).
    """
    v = _ventanas(referencia)
    er = reportes.estado_resultados(eid, v["mes_ini"], v["mes_fin"])
    ventas = Decimal(er["ventas"])
    neta = Decimal(er["utilidad_neta"])
    margen = None
    if ventas > 0:
        margen = (neta / ventas * 100).quantize(Decimal("0.1"))
    er["margen_neto_pct"] = str(margen) if margen is not None else None
    er["mes"] = v["mes_nombre"]
    return er


# --- 3. tops -------------------------------------------------------------------

def top_productos(eid: int, desde: str | None = None,
                  hasta: str | None = None, limite: int = 5,
                  por: str = "ingreso") -> list[dict]:
    """Top productos del período, ordenados por ingreso o por unidades."""
    if desde is None or hasta is None:
        v = _ventanas()
        desde, hasta = v["mes_ini"], v["mes_fin"]
    cant: dict[str, Decimal] = defaultdict(Decimal)
    ing: dict[str, Decimal] = defaultdict(Decimal)
    for x in _ventas_rango(eid, desde, hasta):
        for it in json.loads(x["items"] or "[]"):
            nombre = it.get("nombre", "?")
            q = Decimal(str(it.get("cantidad", 0)))
            cant[nombre] += q
            ing[nombre] += q * Decimal(str(it.get("precio", 0)))
    clave = ing if por == "ingreso" else cant
    top = sorted(cant.items(), key=lambda kv: clave[kv[0]], reverse=True)
    return [{"nombre": n, "cantidad": str(q), "ingreso": str(ing[n])}
            for n, q in top[:limite]]


def top_clientes(eid: int, desde: str | None = None,
                 hasta: str | None = None, limite: int = 5) -> list[dict]:
    """Clientes que más compraron en el período. SOLO LECTURA."""
    if desde is None or hasta is None:
        v = _ventanas()
        desde, hasta = v["mes_ini"], v["mes_fin"]
    agg: dict[int | None, dict] = {}
    for x in _ventas_rango(eid, desde, hasta):
        cid = x.get("cliente_id")
        g = agg.setdefault(cid, {"cliente_id": cid, "facturas": 0,
                                 "total": _CERO, "nombre": ""})
        g["facturas"] += 1
        g["total"] += db.dec(x["total"])
    filas = sorted(agg.values(), key=lambda g: g["total"], reverse=True)
    for g in filas[:limite]:
        cid = g["cliente_id"]
        c = cli_mod.obtener(eid, cid) if cid else None
        g["nombre"] = c["nombre"] if c else "Consumidor final"
        g["total"] = str(g["total"])
    return [{"cliente_id": g["cliente_id"], "nombre": g["nombre"],
             "facturas": g["facturas"], "total": g["total"]}
            for g in filas[:limite]]


# --- 4. CxC --------------------------------------------------------------------

def analisis_cxc(eid: int, referencia: date | None = None) -> dict:
    """Cuentas por cobrar: pendiente total, vencido, antigüedad. SOLO LECTURA.

    'Vencida' = fecha límite anterior a la referencia y balance > 0
    (misma regla que pagos.cxc_vencidas, pero con fecha de referencia
    explícita para que el análisis sea determinista).
    """
    ref = referencia or clock.today_business()
    hoy = ref.isoformat()
    pend = pagos_mod.cxc_pendientes(eid)
    total_pend = sum((Decimal(p["balance"]) for p in pend), _CERO)
    venc = []
    nombres: dict[int | None, str] = {}
    for p in pend:
        ven = (p.get("vencimiento") or "").strip()
        if not ven or ven >= hoy or Decimal(p["balance"]) <= 0:
            continue
        cid = p.get("cliente_id")
        if cid not in nombres:
            c = cli_mod.obtener(eid, cid) if cid else None
            nombres[cid] = c["nombre"] if c else "Consumidor final"
        p["cliente_nombre"] = nombres[cid]
        try:
            p["dias_vencidos"] = (ref - date.fromisoformat(ven)).days
        except Exception:
            p["dias_vencidos"] = 0
        venc.append(p)
    total_venc = sum((Decimal(p["balance"]) for p in venc), _CERO)
    clientes_venc = {p.get("cliente_nombre") for p in venc}
    dias = [int(p.get("dias_vencidos", 0)) for p in venc]
    buckets = {"0-30": _CERO, "31-60": _CERO, "61+": _CERO}
    for p in venc:
        d = int(p.get("dias_vencidos", 0))
        clave = "0-30" if d <= 30 else "31-60" if d <= 60 else "61+"
        buckets[clave] += Decimal(p["balance"])
    return {
        "total_pendiente": str(total_pend),
        "n_pendientes": len(pend),
        "total_vencido": str(total_venc),
        "n_vencidas": len(venc),
        "n_clientes_vencidos": len(clientes_venc),
        "dias_promedio_vencidos": (round(sum(dias) / len(dias))
                                  if dias else 0),
        "antiguedad": {k: str(v) for k, v in buckets.items()},
    }


# --- 5. inventario --------------------------------------------------------------

def analisis_inventario(eid: int,
                        referencia: date | None = None) -> dict:
    """Stock bajo/agotado, valorización y días de stock. SOLO LECTURA.

    'días de stock' = stock actual / ritmo promedio de venta de los
    últimos 30 días (0 si el producto no se vendió).
    """
    ref = referencia or clock.today_business()
    desde_30 = (ref.fromordinal(ref.toordinal() - 29)).isoformat()
    hasta = ref.isoformat()
    unidades_30d: dict[int, Decimal] = defaultdict(Decimal)
    for x in _ventas_rango(eid, desde_30, hasta):
        for it in json.loads(x["items"] or "[]"):
            pid = it.get("producto_id")
            if pid:
                unidades_30d[int(pid)] += Decimal(
                    str(it.get("cantidad", 0)))
    productos = [p for p in inv_mod.listar(eid)
                 if str(p.get("tipo") or "producto").lower() != "servicio"
                 and int(p.get("activo", 1)) == 1]
    valorizacion = _CERO
    inmovilizado = _CERO
    alertas = []
    for p in productos:
        stock = db.dec(p.get("stock") or 0)
        costo = db.dec(p.get("costo") or 0)
        val = stock * costo
        valorizacion += val
        vendido = unidades_30d.get(int(p["id"]), _CERO)
        if vendido <= 0:
            inmovilizado += val
        minimo = db.dec(p.get("stock_minimo") or 5)
        if stock <= minimo:
            ritmo = vendido / 30
            dias = (stock / ritmo) if ritmo > 0 else None
            alertas.append({
                "id": p["id"], "nombre": p["nombre"], "sku": p.get("sku", ""),
                "stock": str(stock), "stock_minimo": str(minimo),
                "precio": str(db.dec(p.get("precio") or 0)),
                "vendidos_30d": str(vendido),
                "dias_stock": (round(float(dias), 1)
                               if dias is not None else None),
                "estado": "agotado" if stock <= 0 else "bajo",
            })
    alertas.sort(key=lambda a: (a["dias_stock"] if a["dias_stock"]
                                is not None else 9999,
                                str(a["nombre"])))
    return {
        "n_productos": len(productos),
        "valorizacion_total": str(valorizacion),
        "valorizacion_inmovilizada": str(inmovilizado),
        "n_alertas": len(alertas),
        "n_agotados": sum(1 for a in alertas if a["estado"] == "agotado"),
        "alertas": alertas,
    }


# --- 6. gastos mes vs anterior ---------------------------------------------------

def gastos_vs_mes_anterior(eid: int,
                           referencia: date | None = None) -> dict:
    """Gastos por categoría: mes actual vs mes anterior. SOLO LECTURA."""
    v = _ventanas(referencia)
    act = gastos_mod.por_categoria(eid, "gasto", v["mes_ini"], v["mes_fin"])
    ant = gastos_mod.por_categoria(eid, "gasto", v["prev_ini"], v["prev_fin"])
    prev_map = {g["categoria"]: Decimal(g["total"]) for g in ant}
    filas = []
    for g in act:
        total = Decimal(g["total"])
        previo = prev_map.get(g["categoria"], _CERO)
        filas.append({
            "categoria": g["categoria"],
            "cantidad": g["cantidad"],
            "total_mes": str(total),
            "total_mes_anterior": str(previo),
            "cambio_pct": (str(_pct_cambio(total, previo))
                           if previo > 0 else None),
        })
    total_act = sum((Decimal(g["total_mes"]) for g in filas), _CERO)
    total_ant = sum((Decimal(g["total_mes_anterior"]) for g in filas),
                    _CERO)
    return {
        "mes": v["mes_nombre"], "mes_anterior_nombre": v["prev_nombre"],
        "categorias": filas,
        "total_mes": str(total_act),
        "total_mes_anterior": str(total_ant),
        "cambio_pct": (str(_pct_cambio(total_act, total_ant))
                       if total_ant > 0 else None),
    }


# --- 7. recomendaciones (reglas deterministas, sin LLM ni APIs) ------------------

def generar_recomendaciones(eid: int,
                            referencia: date | None = None) -> list[dict]:
    """Produce hasta 8 recomendaciones accionables en español.

    Cada una cita datos reales calculados de la BD. Ordenadas por impacto.
    """
    v = _ventanas(referencia)
    resumen = resumen_mes(eid, referencia)
    margen = margen_mes(eid, referencia)
    tp = top_productos(eid, v["mes_ini"], v["mes_fin"], limite=5,
                       por="ingreso")
    tc = top_clientes(eid, v["mes_ini"], v["mes_fin"], limite=5)
    cxc = analisis_cxc(eid)
    inv = analisis_inventario(eid, referencia)
    gastos = gastos_vs_mes_anterior(eid, referencia)

    recs: list[dict] = []
    ventas_mes = Decimal(resumen["ventas_total"])
    ventas_ant = Decimal(resumen["ventas_mes_anterior"])

    # R1 — concentración de ventas en pocos productos
    ingreso_total = sum((Decimal(p["ingreso"]) for p in tp), _CERO)
    if ingreso_total > 0 and len(tp) >= 3:
        top3 = sum((Decimal(p["ingreso"]) for p in tp[:3]), _CERO)
        share = (top3 / ingreso_total * 100).quantize(Decimal("0.1"))
        if share >= 60:
            nombres = ", ".join(p["nombre"] for p in tp[:3])
            recs.append({
                "titulo": "Tus ventas dependen de pocos productos",
                "explicacion":
                    (f"{nombres} concentraron el {share}% "
                     f"({_rd(top3)}) de tus ingresos de {v['mes_nombre']} "
                     f"({_rd(ingreso_total)}). Si uno de ellos falla, tu "
                     "facturación se resiente."),
                "dato": f"{share}% de {v['mes_nombre']} = {_rd(top3)} "
                        f"en 3 productos",
                "accion":
                    "Promociona otros productos del catálogo con combos, "
                    "muestras o descuentos por volumen para diversificar.",
                "impacto": 4,
            })

    # R2 — CxC vencidas
    total_venc = Decimal(cxc["total_vencido"])
    if total_venc > 0:
        recs.append({
            "titulo": "Recupera tus cuentas vencidas",
            "explicacion":
                (f"Tienes {_rd(total_venc)} en cuentas por cobrar "
                 f"vencidas, repartidas en {cxc['n_vencidas']} facturas de "
                 f"{cxc['n_clientes_vencidos']} clientes, con un promedio "
                 f"de {cxc['dias_promedio_vencidos']} días de retraso."),
            "dato": f"{_rd(total_venc)} vencidos en "
                    f"{cxc['n_clientes_vencidos']} clientes",
            "accion":
                "Llama o escribe a cada cliente con el monto exacto de "
                "su factura y ofrécele fecha de pago concreta; anota el "
                "compromiso.",
            "impacto": 5,
        })

    # R3 — agotamiento inminente
    urgentes = [a for a in inv["alertas"]
                if a["dias_stock"] is not None and a["dias_stock"] <= 14]
    if urgentes:
        u = urgentes[0]
        impacto = 5 if u["dias_stock"] <= 7 else 3
        otros = (f" También: {', '.join(a['nombre'] for a in urgentes[1:3])}"
                 if len(urgentes) > 1 else "")
        recs.append({
            "titulo": "Repón inventario antes de quedarte sin vender",
            "explicacion":
                (f"{u['nombre']} (stock {u['stock']}) se agotará en "
                 f"~{u['dias_stock']} días al ritmo de venta de los "
                 f"últimos 30 días.{otros}"),
            "dato": f"{u['nombre']}: ~{u['dias_stock']} días de stock",
            "accion":
                "Haz tu pedido de reposición hoy; si el proveedor tarda, "
                "avisa a tus clientes habituales para evitar perder "
                "ventas.",
            "impacto": impacto,
        })

    # R4 — gasto que subió fuerte
    subidas = [g for g in gastos["categorias"]
               if g["cambio_pct"] is not None
               and Decimal(g["cambio_pct"]) >= 20
               and Decimal(g["total_mes"]) >= 100]
    subidas.sort(key=lambda g: Decimal(g["cambio_pct"]), reverse=True)
    if subidas:
        s = subidas[0]
        recs.append({
            "titulo": f"Revisa tus gastos de {s['categoria']}",
            "explicacion":
                (f"Gastaste {_rd(s['total_mes'])} en "
                 f"{s['categoria']} durante {v['mes_nombre']}, un "
                 f"{s['cambio_pct']}% más que en {v['prev_nombre']} "
                 f"({_rd(s['total_mes_anterior'])})."),
            "dato": f"{s['categoria']}: +{s['cambio_pct']}% vs "
                    f"{v['prev_nombre']}",
            "accion":
                "Revisa los comprobantes de esa categoría y fíjate si "
                "hay pagos duplicados, aumentos de tarifa o consumos "
                "que puedas negociar.",
            "impacto": 3,
        })

    # R5 — cliente concentrado
    if ventas_mes > 0 and tc:
        top1 = tc[0]
        share_cli = (Decimal(top1["total"]) / ventas_mes * 100
                     ).quantize(Decimal("0.1"))
        if share_cli >= 40:
            recs.append({
                "titulo": "Un solo cliente pesa mucho en tus ventas",
                "explicacion":
                    (f"{top1['nombre']} compró {_rd(top1['total'])} en "
                     f"{v['mes_nombre']}, el {share_cli}% de tus ventas "
                     f"({_rd(ventas_mes)}). Si ese cliente se detiene, "
                     "tu flujo se cae."),
                "dato": f"{top1['nombre']}: {share_cli}% de las ventas "
                        f"de {v['mes_nombre']}",
                "accion":
                    "Fidelízalo (trato preferencial, seguimiento) y en "
                    "paralelo busca 2-3 clientes nuevos del mismo "
                    "perfil para repartir el riesgo.",
                "impacto": 4,
            })

    # R6 — ticket promedio a la baja
    ticket = Decimal(resumen["ticket_promedio"])
    if ventas_ant > 0 and ventas_mes > 0:
        # ticket del mes anterior calculado de sus ventas reales
        ant_real = _ventas_rango(eid, v["prev_ini"], v["prev_fin"])
        if ant_real:
            ticket_ant = (ventas_ant / len(ant_real)
                          ).quantize(Decimal("0.01"))
            if ticket_ant > 0 and ticket < ticket_ant:
                caida = ((ticket_ant - ticket) / ticket_ant * 100
                         ).quantize(Decimal("0.1"))
                recs.append({
                    "titulo": "Tu ticket promedio está bajando",
                    "explicacion":
                        (f"El ticket promedio de {v['mes_nombre']} fue "
                         f"{_rd(ticket)}, un {caida}% menos que en "
                         f"{v['prev_nombre']} ({_rd(ticket_ant)}). "
                         "Vendes parecido o más, pero cada venta deja "
                         "menos."),
                    "dato": f"ticket {_rd(ticket)} vs {_rd(ticket_ant)} "
                            f"(-{caida}%)",
                    "accion":
                        "Crea combos o paquetes que eleven el monto por "
                        "compra, y entrena a quien vende a sugerir un "
                        "producto complementario en cada venta.",
                    "impacto": 3,
                })

    # R7 — margen neto
    if margen["margen_neto_pct"] is not None and ventas_mes > 0:
        m_pct = Decimal(margen["margen_neto_pct"])
        neta = Decimal(margen["utilidad_neta"])
        if m_pct <= 0:
            recs.append({
                "titulo": "Este mes operaste a pérdida",
                "explicacion":
                    (f"En {v['mes_nombre']} tu utilidad neta fue "
                     f"{_rd(neta)} con ventas de {_rd(ventas_mes)} "
                     f"(margen {m_pct}%). Salió más de lo que entró."),
                "dato": f"utilidad neta {_rd(neta)} en {v['mes_nombre']}",
                "accion":
                    "Revisa primero los gastos grandes del mes y los "
                    "productos con margen bajo (precio vs costo); "
                    "ajusta precios antes de recortar inversión.",
                "impacto": 5,
            })
        elif m_pct < 10:
            recs.append({
                "titulo": "Tu margen neto está muy ajustado",
                "explicacion":
                    (f"En {v['mes_nombre']} tu margen neto fue {m_pct}% "
                     f"(utilidad {_rd(neta)} sobre ventas de "
                     f"{_rd(ventas_mes)}). Un imprevisto pequeño te deja "
                     "en rojo."),
                "dato": f"margen neto {m_pct}% en {v['mes_nombre']}",
                "accion":
                    "Identifica los 3 productos con peor relación "
                    "precio/costo y súbeles el precio o negocia mejor "
                    "costo con el proveedor.",
                "impacto": 4,
            })

    # R8 — capital inmovilizado
    val_total = Decimal(inv["valorizacion_total"])
    inmov = Decimal(inv["valorizacion_inmovilizada"])
    if val_total > 0 and inmov >= val_total * Decimal("0.2"):
        share_inm = (inmov / val_total * 100).quantize(Decimal("0.1"))
        recs.append({
            "titulo": "Tienes capital quieto en inventario",
            "explicacion":
                (f"{_rd(inmov)} ({share_inm}% de tu inventario valorizado "
                 f"en {_rd(val_total)}) está en productos que no se "
                 "vendieron en los últimos 30 días. Ese dinero podría "
                 "estar trabajando."),
            "dato": f"{_rd(inmov)} inmovilizados ({share_inm}% del "
                    "inventario)",
            "accion":
                "Liquida ese stock con una oferta especial o úsalo en "
                "combos con tus productos más vendidos.",
            "impacto": 3,
        })

    # R9 — sin datos: invita a registrar
    if ventas_mes == 0 and Decimal(gastos["total_mes"]) == 0:
        recs.append({
            "titulo": "Registra tu actividad para recibir análisis",
            "explicacion":
                (f"Aún no hay ventas ni gastos registrados en "
                 f"{v['mes_nombre']}. El Contador IA analiza tus "
                 "facturas, gastos e inventario para darte "
                 "recomendaciones."),
            "dato": "sin movimientos en " + v["mes_nombre"],
            "accion":
                "Registra tu primera venta desde el POS o Facturas y "
                "tus gastos del mes; vuelve aquí y verás el análisis.",
            "impacto": 1,
        })

    for r in recs:
        r["disclaimer"] = DISCLAIMER
    recs.sort(key=lambda r: (-r["impacto"], r["titulo"]))
    return recs[:8]


# --- resumen global para la página ----------------------------------------------

def panel_contador(eid: int, referencia: date | None = None) -> dict:
    """Todo lo que necesita la página /contador-ia en un solo dict."""
    v = _ventanas(referencia)
    return {
        "resumen": resumen_mes(eid, referencia),
        "margen": margen_mes(eid, referencia),
        "top_ingreso": top_productos(eid, v["mes_ini"], v["mes_fin"],
                                    limite=5, por="ingreso"),
        "top_unidades": top_productos(eid, v["mes_ini"], v["mes_fin"],
                                      limite=5, por="unidades"),
        "top_clientes": top_clientes(eid, v["mes_ini"], v["mes_fin"],
                                     limite=5),
        "cxc": analisis_cxc(eid, referencia),
        "inventario": analisis_inventario(eid, referencia),
        "gastos": gastos_vs_mes_anterior(eid, referencia),
        "recomendaciones": generar_recomendaciones(eid, referencia),
    }
