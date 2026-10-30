"""Reportes: ventas por día, ITBIS débito (motor reutilizado) y top productos.

Reutiliza os_contab.itbis.calcular_itbis para el débito de ITBIS con
aritmética Decimal exacta (tasa como parámetro).
"""
from __future__ import annotations

from factura_core import clock

import json
from collections import Counter, defaultdict
from datetime import date, timedelta
from decimal import Decimal

from factura_persist import db

# Motor reutilizado del bloque contabilidad
from os_contab.itbis import calcular_itbis


def ventas_por_dia(empresa_id: int, dias: int = 30) -> list[dict]:
    desde = (clock.today_business() - timedelta(days=dias - 1)).isoformat()
    conn = db.connect()
    try:
        cur = conn.cursor()
        filas = db.fetchall(
            cur, "SELECT creada AS dia, total"
                 " FROM factura_ventas WHERE empresa_id=%s"
                 " AND creada>=%s AND estado<>'anulada'"
                 " ORDER BY dia",
            (empresa_id, clock.utc_start(desde)))
        por_dia: dict[str, dict] = {}
        for dia, total in filas:
            dia = clock.local_datetime(dia).date().isoformat()
            acc = por_dia.setdefault(dia, {"cantidad": 0,
                                            "total": Decimal("0")})
            acc["cantidad"] += 1
            acc["total"] += db.dec(total)
        credits = db.fetchall(cur, "SELECT creada_en, total FROM factura_devoluciones WHERE empresa_id=%s AND financiera=1 AND creada_en>=%s", (empresa_id, clock.utc_start(desde)))
        for day, amount in credits:
            day = clock.local_datetime(day).date().isoformat()
            acc = por_dia.setdefault(day, {"cantidad": 0, "total": Decimal("0")})
            acc["total"] -= db.dec(amount)
        return [{"dia": d, "cantidad": v["cantidad"], "total": str(v["total"])}
                for d, v in sorted(por_dia.items())]
    finally:
        conn.close()


def _ventas_rango(empresa_id: int, desde: str | None,
                  hasta: str | None) -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        sql = ("SELECT id, creada, tipo, items, subtotal, itbis, total, estado"
               " FROM factura_ventas WHERE empresa_id=%s AND estado<>'anulada'")
        params: list = [empresa_id]
        if desde:
            sql += " AND creada>=%s"
            params.append(clock.utc_start(desde))
        if hasta:
            sql += " AND creada<%s"
            params.append(clock.utc_after(hasta))
        sql += " ORDER BY creada"
        return [dict(f) for f in db.fetchall(cur, sql, params)]
    finally:
        conn.close()


def resumen_itbis(empresa_id: int, desde: str | None = None,
                  hasta: str | None = None, tasa: str = "18") -> dict:
    """Débito de ITBIS calculado con el motor os_contab.itbis.

    Se pasa la base gravada de cada venta (monto sin ITBIS) como venta no
    exenta; el motor aplica la tasa como parámetro. Sin compras registradas
    en este prototipo (compras=[]), el crédito es 0.
    """
    documents = _ventas_rango(empresa_id, desde, hasta)
    tax = sum((db.dec(v["itbis"]) for v in documents), Decimal("0"))
    exempt = Decimal("0")
    taxable = Decimal("0")
    for v in documents:
        for item in json.loads(v["items"] or "[]"):
            if item.get("itbis_exento"):
                exempt += db.dec(item["subtotal"])
            else:
                taxable += db.dec(item["subtotal"])
    conn = db.connect()
    try:
        sql = "SELECT total, itbis FROM factura_devoluciones WHERE empresa_id=%s AND financiera=1"
        params = [empresa_id]
        if desde:
            sql += " AND creada_en>=%s"
            params.append(clock.utc_start(desde))
        if hasta:
            sql += " AND creada_en<%s"
            params.append(clock.utc_after(hasta))
        credits = db.fetchall(conn.cursor(), sql, params)
        returned_tax = sum((db.dec(r[1]) for r in credits), Decimal("0"))
    finally:
        conn.close()
    net = tax - returned_tax
    return {"tasa": str(tasa), "base_gravada": str(taxable),
            "base_exenta": str(exempt), "debito": str(net), "credito": "0",
            "a_pagar": str(max(Decimal("0"), net)),
            "saldo_a_favor": str(max(Decimal("0"), -net)),
            "ventas": len(documents), "itbis_bruto": str(tax),
            "itbis_devoluciones": str(returned_tax),
            "nota": "Resumen administrativo de importes registrados. Bases brutas, impuesto neto de devoluciones. No es declaración fiscal ni determina obligaciones tributarias."}


def top_productos(empresa_id: int, desde: str | None = None,
                  hasta: str | None = None, limite: int = 10) -> list[dict]:
    cant = Counter()
    ing = defaultdict(Decimal)
    for v in _ventas_rango(empresa_id, desde, hasta):
        for it in json.loads(v["items"] or "[]"):
            nombre = it.get("nombre", "?")
            q = Decimal(str(it.get("cantidad", 0)))
            cant[nombre] += q
            ing[nombre] += q * Decimal(str(it.get("precio", 0)))
    top = sorted(cant.items(), key=lambda kv: ing[kv[0]], reverse=True)
    return [{"nombre": n, "cantidad": str(q), "ingreso": str(ing[n])}
            for n, q in top[:limite]]


def resumen_general(empresa_id: int) -> dict:
    hoy = ventas_por_dia(empresa_id, dias=1)
    total_mes = ventas_por_dia(empresa_id, dias=30)
    return {
        "hoy": hoy[0] if hoy else {"cantidad": 0, "total": "0"},
        "ultimos_30d_total": str(sum((Decimal(d["total"])
                                      for d in total_mes), Decimal("0"))),
        "ultimos_30d_cantidad": sum(d["cantidad"] for d in total_mes),
    }


# --- FASE 2: gastos e ingresos ------------------------------------------------

def gastos_por_categoria(empresa_id: int, desde: str | None = None,
                         hasta: str | None = None) -> dict:
    """Reporte de gastos por categoría y período + ingresos no-venta."""
    from factura_core import gastos as gmod
    filas_g = gmod.por_categoria(empresa_id, "gasto", desde, hasta)
    filas_i = gmod.por_categoria(empresa_id, "ingreso", desde, hasta)
    return {
        "gastos": filas_g,
        "total_gastos": str(sum((Decimal(g["total"]) for g in filas_g),
                               Decimal("0"))),
        "ingresos_no_venta": filas_i,
        "total_ingresos_no_venta": str(
            sum((Decimal(g["total"]) for g in filas_i), Decimal("0"))),
        "desde": desde or "", "hasta": hasta or "",
    }


def estado_resultados(empresa_id: int, desde: str | None = None,
                      hasta: str | None = None) -> dict:
    """Estado de resultados simple del período:

    ventas - costo de ventas = utilidad bruta;
    utilidad bruta + otros ingresos - gastos = utilidad neta.

    El costo de ventas usa el costo ACTUAL del catálogo por ítem vendido
    (aproximación declarada en la UI).
    """
    from factura_core import gastos as gmod
    ventas_r = [v for v in _ventas_rango(empresa_id, desde, hasta)
                if v.get("tipo") in ("factura", "pos")]
    total_ventas = sum((Decimal(v["total"]) - Decimal(v["itbis"]) for v in ventas_r),
                       Decimal("0"))
    # Mapa producto_id -> costo actual del catálogo
    conn = db.connect()
    try:
        cur = conn.cursor()
        costos = {int(f[0]): db.dec(f[1]) for f in db.fetchall(
            cur, "SELECT id, costo FROM factura_productos"
                 " WHERE empresa_id=%s", (empresa_id,))}
    finally:
        conn.close()
    costo_ventas = Decimal("0")
    for v in ventas_r:
        for it in json.loads(v["items"] or "[]"):
            pid = it.get("producto_id")
            if pid and int(pid) in costos:
                costo_ventas += (costos[int(pid)] *
                                 Decimal(str(it.get("cantidad", 0))))
    conn = db.connect()
    try:
        sql = "SELECT total, itbis, items FROM factura_devoluciones WHERE empresa_id=%s AND financiera=1"
        params = [empresa_id]
        if desde:
            sql += " AND creada_en>=%s"
            params.append(clock.utc_start(desde))
        if hasta:
            sql += " AND creada_en<%s"
            params.append(clock.utc_after(hasta))
        returned = db.fetchall(conn.cursor(), sql, params)
        for amount, tax, items in returned:
            total_ventas -= db.dec(amount) - db.dec(tax)
            for item in json.loads(items or "[]"):
                costo_ventas -= costos.get(int(item.get("producto_id") or 0), Decimal("0")) * db.dec(item.get("cantidad"))
    finally:
        conn.close()
    utilidad_bruta = total_ventas - costo_ventas
    total_gastos = gmod.total(empresa_id, "gasto", desde, hasta)
    otros_ingresos = gmod.total(empresa_id, "ingreso", desde, hasta)
    utilidad_neta = utilidad_bruta + otros_ingresos - total_gastos
    return {
        "desde": desde or "", "hasta": hasta or "",
        "ventas": str(total_ventas),
        "cantidad_ventas": len(ventas_r),
        "costo_ventas": str(costo_ventas),
        "utilidad_bruta": str(utilidad_bruta),
        "otros_ingresos": str(otros_ingresos),
        "gastos": str(total_gastos),
        "utilidad_neta": str(utilidad_neta),
        "nota": ("El costo de ventas es una aproximación con el costo "
                 "actual del catálogo."),
    }
