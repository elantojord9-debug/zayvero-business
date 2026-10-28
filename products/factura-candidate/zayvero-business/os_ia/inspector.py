"""BLOQUE IA · 1 — Inspector.

Detección de anomalías y pérdidas sobre datos del núcleo OS (ventas,
compras, inventario, gastos), comparaciones de precios y compras entre
proveedores y entre períodos. Cada hallazgo trae EVIDENCIA (números
concretos) e impacto estimado; los hallazgos se priorizan por impacto.

Anti-alucinación (regla dura del bloque): sin datos suficientes para
una detección, se emite un hallazgo tipo "datos_insuficientes"
(severidad info, suficiencia="insuficiente") o la detección guarda
silencio. Nunca se inventan números.

Detecciones:
  ventas_anomalas          pico/caída de unidades vendidas por día y
                           producto (z robusto con MAD; exige >= 16
                           días con venta previa, misma filosofía que
                           shield R5)
  gastos_anomalos          gasto por categoría del mes en curso vs.
                           promedio de los 3 meses anteriores
                           (ratio >= 1.5; exige los 3 meses con dato)
  comparacion_precios_compra  mismo producto comprado a 2+ proveedores
                           con precios unitarios que difieren >= 10%
                           en 180 días: evidencia + ahorro potencial
  comparacion_periodos     gastos y ventas del mes en curso vs. mes
                           anterior (informativo, severidad info)
  perdidas_inventario      ajustes negativos de stock (merma) en 90
                           días, valorizados a costo promedio
"""
from __future__ import annotations

import os as _os
import sys
from collections import defaultdict
from datetime import date, timedelta

sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from oschema.contract import TipoMovimiento  # noqa: E402
from oschema.models import _dec  # noqa: E402

from .base import (Hallazgo, SEVERIDADES, dia, dinero, hoy as _hoy_fn,  # noqa: E402
                   impacto, ordenar_por_impacto, verificar_aislamiento,  # noqa: E402
                   q1, z_robusto)  # noqa: E402

VENTANA_VENTAS_DIAS = 120
MIN_HISTORIA_VENTAS = 16
Z_ALERTA = 3.0
Z_CRITICO = 5.0
MATERIALIDAD_FACTOR = 2.0

MESES_BASE_GASTO = 3
RATIO_GASTO_ANORMAL = 1.5

VENTANA_PRECIOS_DIAS = 180
DIF_PRECIO_MIN = 0.10

VENTANA_MERMA_DIAS = 90


def _serie_ventas(bundle, producto_id: str, h) -> dict:
    """{date: unidades vendidas} de ventas confirmadas (120 días)."""
    ini = h - timedelta(days=VENTANA_VENTAS_DIAS - 1)
    por_dia = defaultdict(lambda: __import__("decimal").Decimal("0"))
    for reg in bundle.ventas._ventas.values():  # coord. persistencia
        if reg["estado"] != "confirmada":
            continue
        d = dia(reg["solicitud"].fecha)
        if d is None or d < ini or d > h:
            continue
        for lin in reg["solicitud"].lineas or []:
            if lin.get("producto_id") == producto_id:
                por_dia[d] += abs(_dec(lin.get("cantidad", "0")))
    return dict(por_dia)


def _productos_con_ventas(bundle, h) -> list[str]:
    ini = h - timedelta(days=VENTANA_VENTAS_DIAS - 1)
    prods = set()
    for reg in bundle.ventas._ventas.values():
        if reg["estado"] != "confirmada":
            continue
        d = dia(reg["solicitud"].fecha)
        if d is None or d < ini or d > h:
            continue
        for lin in reg["solicitud"].lineas or []:
            prods.add(lin.get("producto_id"))
    return sorted(p for p in prods if p)


def _i1_ventas(bundle, h) -> list[Hallazgo]:
    out = []
    productos = _productos_con_ventas(bundle, h)
    if not productos:
        return [Hallazgo(
            id=f"datos_insuficientes:ventas:{h.isoformat()}",
            tipo="datos_insuficientes", severidad="info",
            suficiencia="insuficiente", fecha=h.isoformat(),
            titulo_es="Sin ventas registradas para analizar",
            titulo_en="No sales recorded to analyze",
            explicacion_es=("No hay ventas confirmadas en los últimos "
                            f"{VENTANA_VENTAS_DIAS} días: no se puede "
                            "detectar anomalías de ventas."),
            explicacion_en=(f"No confirmed sales in the last "
                            f"{VENTANA_VENTAS_DIAS} days: sales anomalies "
                            "cannot be detected."),
            evidencia={"ventana_dias": VENTANA_VENTAS_DIAS})]
    for prod in productos:
        serie = _serie_ventas(bundle, prod, h)
        dias = sorted(serie)
        if len(dias) < MIN_HISTORIA_VENTAS + 1:
            continue  # sin historia suficiente: silencio
        reciente = dias[-1]
        base = [float(serie[d]) for d in dias[:-1]]
        x = float(serie[reciente])
        z, mediana = z_robusto(x, base)
        if abs(z) < Z_ALERTA:
            continue
        if x > 0 and x < MATERIALIDAD_FACTOR * max(mediana, 1e-9):
            continue  # micro-variación en serie constante: ruido
        sev = "critico" if abs(z) >= Z_CRITICO else "alerta"
        direccion = "pico" if x > mediana else "caída"
        out.append(Hallazgo(
            id=f"ventas_anomalas:{prod}:{reciente.isoformat()}",
            tipo="ventas_anomalas", severidad=sev, fecha=h.isoformat(),
            titulo_es=(f"{direccion.capitalize()} de ventas en {prod}: "
                       f"{x:.0f} uds el {reciente.isoformat()}"),
            titulo_en=(f"Sales {'spike' if x > mediana else 'drop'} in "
                       f"{prod}: {x:.0f} units on {reciente.isoformat()}"),
            explicacion_es=(
                f"El {reciente.isoformat()} se vendieron {x:.0f} unidades "
                f"de {prod}, frente a una mediana de {mediana:.1f}/día "
                f"(z={z:.1f} con {len(dias) - 1} días de historia)."),
            explicacion_en=(
                f"On {reciente.isoformat()}, {x:.0f} units of {prod} "
                f"were sold vs. a median of {mediana:.1f}/day "
                f"(z={z:.1f}, {len(dias) - 1} days of history)."),
            evidencia={"producto_id": prod,
                       "fecha": reciente.isoformat(),
                       "unidades": f"{x:.2f}",
                       "mediana_diaria": f"{mediana:.2f}",
                       "z_robusto": f"{z:.2f}",
                       "dias_historia": len(dias) - 1}))
    return out


def _meses_gasto(bundle, h):
    """{(año, mes): {categoria: {moneda: Decimal}}} con fechas válidas."""
    por_mes = defaultdict(lambda: defaultdict(
        lambda: defaultdict(lambda: __import__("decimal").Decimal("0"))))
    for g in bundle.gastos.todos():
        d = dia(g.gasto.fecha)
        if d is None or d > h:
            continue
        por_mes[(d.year, d.month)][g.gasto.categoria][g.gasto.moneda] += \
            _dec(g.gasto.monto)
    return por_mes


def _mes_anterior(h):
    return (h.year - 1, 12) if h.month == 1 else (h.year, h.month - 1)


def _i2_gastos(bundle, h) -> list[Hallazgo]:
    por_mes = _meses_gasto(bundle, h)
    if not por_mes:
        return [Hallazgo(
            id=f"datos_insuficientes:gastos:{h.isoformat()}",
            tipo="datos_insuficientes", severidad="info",
            suficiencia="insuficiente", fecha=h.isoformat(),
            titulo_es="Sin gastos registrados para analizar",
            titulo_en="No expenses recorded to analyze",
            explicacion_es=("No hay gastos con fecha válida: no se puede "
                            "detectar anomalías de gasto."),
            explicacion_en=("No dated expenses found: expense anomalies "
                            "cannot be detected."),
            evidencia={})]
    actual = (h.year, h.month)
    bases = []
    m = actual
    for _ in range(MESES_BASE_GASTO):
        m = (m[0] - 1, 12) if m[1] == 1 else (m[0], m[1] - 1)
        bases.append(m)
    out = []
    categorias = set()
    for mm in [actual] + bases:
        for cat in por_mes.get(mm, {}):
            categorias.add(cat)
    for cat in sorted(categorias):
        for moneda in sorted({mon for mm in [actual] + bases
                              for mon in por_mes.get(mm, {})
                              .get(cat, {})}):
            vals_base = [por_mes.get(mm, {}).get(cat, {}).get(moneda)
                         for mm in bases]
            if any(v is None or v <= 0 for v in vals_base):
                continue  # exige los 3 meses con dato: si no, silencio
            promedio = sum(vals_base, _dec("0")) / MESES_BASE_GASTO
            actual_v = por_mes.get(actual, {}).get(cat, {}).get(moneda,
                                                                _dec("0"))
            if promedio <= 0:
                continue
            ratio = actual_v / promedio
            if ratio < RATIO_GASTO_ANORMAL:
                continue
            exceso = actual_v - promedio
            out.append(Hallazgo(
                id=f"gastos_anomalos:{cat}:{moneda}:{h.isoformat()}",
                tipo="gastos_anomalos", severidad="alerta",
                fecha=h.isoformat(),
                titulo_es=(f"Gasto anormal en '{cat}': "
                           f"{dinero(actual_v, moneda)} este mes"),
                titulo_en=(f"Abnormal spending on '{cat}': "
                           f"{dinero(actual_v, moneda)} this month"),
                explicacion_es=(
                    f"En '{cat}' se gastaron {dinero(actual_v, moneda)} en "
                    f"el mes en curso, frente a un promedio de "
                    f"{dinero(promedio, moneda)} en los "
                    f"{MESES_BASE_GASTO} meses anteriores "
                    f"(ratio {ratio:.1f}×)."),
                explicacion_en=(
                    f"'{cat}' spending is {dinero(actual_v, moneda)} this "
                    f"month vs. an average of {dinero(promedio, moneda)} "
                    f"over the previous {MESES_BASE_GASTO} months "
                    f"(ratio {ratio:.1f}×)."),
                evidencia={"categoria": cat, "moneda": moneda,
                           "mes_actual": str(actual_v),
                           "promedio_3m": f"{promedio:.2f}",
                           "ratio": f"{ratio:.2f}"},
                impacto_estimado=impacto(
                    exceso, moneda,
                    "exceso del mes actual sobre el promedio de 3 meses")))
    return out


def _precios_por_proveedor(bundle, h):
    """{(producto_id, moneda): {proveedor_id: [(precio, cantidad, fecha)]}}."""
    ini = h - timedelta(days=VENTANA_PRECIOS_DIAS)
    datos = defaultdict(lambda: defaultdict(list))
    for oc in bundle.compras._ordenes.values():  # coord. persistencia
        if oc.estado == "cancelada":
            continue
        d = dia(oc.fecha_emision)
        if d is None or d < ini or d > h:
            continue
        for lin in oc.lineas or []:
            datos[(lin.producto_id, oc.moneda)][oc.proveedor_id].append(
                (_dec(lin.precio_unitario), _dec(lin.cantidad), d))
    return datos


def _i3_precios(bundle, h) -> list[Hallazgo]:
    datos = _precios_por_proveedor(bundle, h)
    out = []
    for (prod, moneda), provs in sorted(datos.items()):
        if len(provs) < 2:
            continue  # un solo proveedor: no hay con qué comparar
        prom = {p: sum(pr * q for pr, q, _ in v) /
                sum(q for _, q, _ in v)
                for p, v in provs.items()
                if sum(q for _, q, _ in v) > 0}
        if len(prom) < 2:
            continue
        caro = max(prom, key=prom.get)
        barato = min(prom, key=prom.get)
        pc, pb = prom[caro], prom[barato]
        if pb <= 0:
            continue
        dif = (pc - pb) / pb
        if dif < DIF_PRECIO_MIN:
            continue
        cant_caro = sum(q for _, q, _ in provs[caro])
        ahorro = (pc - pb) * cant_caro
        out.append(Hallazgo(
            id=f"comparacion_precios_compra:{prod}:{moneda}",
            tipo="comparacion_precios_compra", severidad="alerta",
            fecha=h.isoformat(),
            titulo_es=(f"{prod}: {caro} cobra {dif * 100:.0f}% más que "
                       f"{barato}"),
            titulo_en=(f"{prod}: {caro} charges {dif * 100:.0f}% more "
                       f"than {barato}"),
            explicacion_es=(
                f"En los últimos {VENTANA_PRECIOS_DIAS} días, {prod} se "
                f"compró a {dinero(pc, moneda)}/ud con {caro} y a "
                f"{dinero(pb, moneda)}/ud con {barato} (promedios "
                f"ponderados por cantidad)."),
            explicacion_en=(
                f"In the last {VENTANA_PRECIOS_DIAS} days, {prod} was "
                f"bought at {dinero(pc, moneda)}/unit from {caro} vs. "
                f"{dinero(pb, moneda)}/unit from {barato} (quantity-"
                f"weighted averages)."),
            evidencia={"producto_id": prod, "moneda": moneda,
                       "proveedor_caro": caro,
                       "precio_caro": f"{pc:.2f}",
                       "proveedor_barato": barato,
                       "precio_barato": f"{pb:.2f}",
                       "diferencia_pct": f"{dif * 100:.1f}",
                       "cantidad_comprada_caro": f"{cant_caro:.2f}"},
            impacto_estimado=impacto(
                ahorro, moneda,
                "(precio_caro − precio_barato) × cantidad comprada al "
                "proveedor caro en la ventana")))
    return out


def _total_mes(serie_mensual, mes):
    tot = defaultdict(lambda: __import__("decimal").Decimal("0"))
    for cat, mons in serie_mensual.get(mes, {}).items():
        for mon, v in mons.items():
            tot[mon] += v
    return tot


def _i4_periodos(bundle, h) -> list[Hallazgo]:
    """Comparación informativa mes en curso vs. mes anterior."""
    por_mes = _meses_gasto(bundle, h)
    ant = _mes_anterior(h)
    actual = (h.year, h.month)
    if actual not in por_mes or ant not in por_mes:
        return []  # sin los dos meses: silencio, no se afirma nada
    tot_a = _total_mes(por_mes, actual)
    tot_n = _total_mes(por_mes, ant)
    out = []
    for moneda in sorted(set(tot_a) | set(tot_n)):
        va, vn = tot_a.get(moneda, _dec("0")), tot_n.get(moneda, _dec("0"))
        if vn <= 0:
            continue
        var = (va - vn) / vn
        out.append(Hallazgo(
            id=f"comparacion_periodos:gastos:{moneda}:{h.isoformat()}",
            tipo="comparacion_periodos", severidad="info",
            fecha=h.isoformat(),
            titulo_es=(f"Gastos {var * 100:+.0f}% vs. mes anterior "
                       f"({moneda})"),
            titulo_en=(f"Expenses {var * 100:+.0f}% vs. previous month "
                       f"({moneda})"),
            explicacion_es=(
                f"Gastos del mes en curso: {dinero(va, moneda)}; mes "
                f"anterior: {dinero(vn, moneda)} (variación "
                f"{var * 100:+.1f}%)."),
            explicacion_en=(
                f"Current month expenses: {dinero(va, moneda)}; previous "
                f"month: {dinero(vn, moneda)} (change "
                f"{var * 100:+.1f}%)."),
            evidencia={"moneda": moneda, "mes_actual": str(va),
                       "mes_anterior": str(vn),
                       "variacion_pct": f"{var * 100:.1f}"}))
    # Ventas: totales por mes desde ventas confirmadas.
    ventas_mes = defaultdict(lambda: defaultdict(
        lambda: __import__("decimal").Decimal("0")))
    for reg in bundle.ventas._ventas.values():
        if reg["estado"] != "confirmada":
            continue
        d = dia(reg["solicitud"].fecha)
        if d is None or d > h:
            continue
        ventas_mes[(d.year, d.month)][reg["solicitud"].moneda] += \
            _dec(reg["total"])
    if actual in ventas_mes and ant in ventas_mes:
        for moneda in sorted(set(ventas_mes[actual]) |
                             set(ventas_mes[ant])):
            va = ventas_mes[actual].get(moneda, _dec("0"))
            vn = ventas_mes[ant].get(moneda, _dec("0"))
            if vn <= 0:
                continue
            var = (va - vn) / vn
            out.append(Hallazgo(
                id=f"comparacion_periodos:ventas:{moneda}:{h.isoformat()}",
                tipo="comparacion_periodos", severidad="info",
                fecha=h.isoformat(),
                titulo_es=(f"Ventas {var * 100:+.0f}% vs. mes anterior "
                           f"({moneda})"),
                titulo_en=(f"Sales {var * 100:+.0f}% vs. previous month "
                           f"({moneda})"),
                explicacion_es=(
                    f"Ventas del mes en curso: {dinero(va, moneda)}; mes "
                    f"anterior: {dinero(vn, moneda)} (variación "
                    f"{var * 100:+.1f}%)."),
                explicacion_en=(
                    f"Current month sales: {dinero(va, moneda)}; previous "
                    f"month: {dinero(vn, moneda)} (change "
                    f"{var * 100:+.1f}%)."),
                evidencia={"moneda": moneda, "mes_actual": str(va),
                           "mes_anterior": str(vn),
                           "variacion_pct": f"{var * 100:.1f}"}))
    return out


def _i5_mermas(bundle, h) -> list[Hallazgo]:
    ini = h - timedelta(days=VENTANA_MERMA_DIAS)
    merma = defaultdict(lambda: __import__("decimal").Decimal("0"))
    for m in bundle.inventario.movimientos():
        if m.tipo != TipoMovimiento.AJUSTE:
            continue
        d = dia(m.fecha)
        if d is None or d < ini or d > h:
            continue
        cant = _dec(m.cantidad)
        if cant < 0:
            merma[(m.producto_id, m.almacen_id, m.moneda or "")] += -cant
    out = []
    for (prod, alm, moneda), uds in sorted(merma.items()):
        try:
            costo_u = _dec(bundle.costo.costo_promedio(prod, alm))
        except Exception:
            costo_u = _dec("0")
        perdida = uds * costo_u
        out.append(Hallazgo(
            id=f"perdidas_inventario:{prod}:{alm}",
            tipo="perdidas_inventario",
            severidad="alerta" if uds >= 10 else "info",
            fecha=h.isoformat(),
            titulo_es=(f"Merma de {uds} uds de {prod} en {alm} "
                       f"({VENTANA_MERMA_DIAS}d)"),
            titulo_en=(f"Shrinkage of {uds} units of {prod} at {alm} "
                       f"({VENTANA_MERMA_DIAS}d)"),
            explicacion_es=(
                f"En los últimos {VENTANA_MERMA_DIAS} días se ajustaron "
                f"a la baja {uds} unidades de {prod} en {alm} "
                f"(merma/daño/pérdida registrada como ajuste negativo)."),
            explicacion_en=(
                f"In the last {VENTANA_MERMA_DIAS} days, {uds} units of "
                f"{prod} at {alm} were adjusted down (shrinkage/damage/"
                f"loss recorded as a negative adjustment)."),
            evidencia={"producto_id": prod, "almacen_id": alm,
                       "unidades_merma": str(uds),
                       "costo_unitario_promedio": f"{costo_u:.2f}",
                       "ventana_dias": VENTANA_MERMA_DIAS},
            impacto_estimado=(
                impacto(perdida, moneda or "DOP",
                        "unidades de merma × costo promedio") if costo_u > 0
                else None)))
    return out


def inspeccionar(bundle, fecha=None) -> list[Hallazgo]:
    """Todos los hallazgos del Inspector para UNA empresa.

    Devuelve hallazgos priorizados por impacto estimado (mayor
    primero). Sin datos para una detección: hallazgo
    "datos_insuficientes" o silencio, nunca números inventados.
    """
    h = fecha if isinstance(fecha, date) else _hoy_fn(fecha)
    hallazgos = []
    hallazgos += _i1_ventas(bundle, h)
    hallazgos += _i2_gastos(bundle, h)
    hallazgos += _i3_precios(bundle, h)
    hallazgos += _i4_periodos(bundle, h)
    hallazgos += _i5_mermas(bundle, h)
    return ordenar_por_impacto(hallazgos)


# ---------------------------------------------------------------------
# Contrato de integración: rutas ia/inspector/*
# ---------------------------------------------------------------------
def _hallazgos(bundle, ctx, query):
    verificar_aislamiento(bundle, ctx)
    h = q1(query, "hoy") or None
    tipo = q1(query, "tipo")
    sev = q1(query, "severidad")
    if sev and sev not in SEVERIDADES:
        raise ValueError("severidad inválida: use " + ", ".join(SEVERIDADES))
    halls = inspeccionar(bundle, fecha=h)
    if tipo:
        halls = [x for x in halls if x.tipo == tipo]
    if sev:
        halls = [x for x in halls if x.severidad == sev]
    return {"ok": True,
            "data": {"company_id": bundle.company_id,
                     "hoy": (dia(h) or _hoy_fn(None)).isoformat(),
                     "n_hallazgos": len(halls),
                     "hallazgos": [x.to_dict() for x in halls]}}


def _resumen(bundle, ctx, query):
    verificar_aislamiento(bundle, ctx)
    halls = inspeccionar(bundle, fecha=q1(query, "hoy") or None)
    por_tipo: dict[str, int] = {}
    por_sev: dict[str, int] = {}
    for x in halls:
        por_tipo[x.tipo] = por_tipo.get(x.tipo, 0) + 1
        por_sev[x.severidad] = por_sev.get(x.severidad, 0) + 1
    return {"ok": True,
            "data": {"company_id": bundle.company_id,
                     "n_hallazgos": len(halls),
                     "por_tipo": por_tipo, "por_severidad": por_sev}}


def rutas():
    """Contrato ia/inspector: handlers (bundle, ctx, query) -> dict."""
    return {"GET ia/inspector/hallazgos": _hallazgos,
            "GET ia/inspector/resumen": _resumen}
