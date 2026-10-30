"""BLOQUE IA · 5 — Temporada (modelos estacionales de demanda).

Descomposición estacional de la demanda: efecto mes del año y día de
la semana por producto (o por categoría), calculada de históricos
reales del bundle. Determinista, explicable, verificable a mano.

Auditoría previa (2026-10-13): `os_ia/predict.py` pronostica con
media móvil de 28 días y declara explícitamente que su banda "no
captura estacionalidad". Este módulo COMPLETA ese hueco: no
reconstruye el pronóstico base, lo REUTILIZA
(`predict.predecir_demanda`) y lo ajusta con factores estacionales
medidos del histórico. El pronóstico base de `predict.py` NO se
toca.

Método:
- Serie: unidades vendidas por día calendario (ventas confirmadas;
  días sin venta = 0) desde la primera venta del producto (o de la
  categoría) hasta hoy, con un tope de SERIE_DIAS_TEMPORADA días.
- Factor mes: promedio diario del mes / promedio diario global.
- Factor día de semana: promedio diario del día / promedio global.
- Ambos son multiplicativos y se combinan: pronóstico(t) =
  pronóstico_base × factor_mes(mes(t)) × factor_dia(dia_semana(t)).
- Suficiencia (configurable, documentada en docs/bloque-temporada.md):
  MIN_DIAS_TEMPORADA = 180 días calendario de historia desde la
  primera venta. Con menos, estado "datos_insuficientes": nunca se
  inventa un factor. Los meses nunca observados usan factor neutro
  1.0 y SE DECLARAN (no se finge evidencia).
- Significancia: un factor se nombra como "efecto" solo si se desvía
  ≥ UMBRAL_SIGNIFICATIVO (15 %) del promedio; si no, es "débil".
- Confianza: cada factor trae su banda ±K×MAD de los días del
  cubo; el pronóstico ajustado escala la banda base. Es variabilidad
  histórica, NO un intervalo de confianza formal: se advierte
  explícitamente que esto no promete exactitud.

Todo es solo lectura: nunca muta el bundle. Sin persistencia nueva
(los factores se recalculan bajo demanda; ver § "Persistencia" en
docs/bloque-temporada.md).
"""
from __future__ import annotations

import math
import os as _os
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402

from .base import dia, hoy as _hoy_fn, mad, q1, verificar_aislamiento  # noqa: E402
from .predict import _historia_dias, _pronostico, predecir_demanda  # noqa: E402

# ---------------------------------------------------------------------
# Parámetros (configurables; documentados en docs/bloque-temporada.md)
# ---------------------------------------------------------------------
SERIE_DIAS_TEMPORADA = 400   # tope de días de serie construida
MIN_DIAS_TEMPORADA = 180     # N mínimo de días calendario de historia
MIN_OBS_MES = 14             # días observados mínimos para factor de un mes
MIN_OBS_DIA_SEMANA = 8       # observaciones mínimas para factor de un día
UMBRAL_SIGNIFICATIVO = 0.15  # |factor-1| >= 15 % -> efecto nombrado
K_BANDA = 2.0                # ±K × MAD escalada (igual que predict.py)
MAD_ESCALA = 1.4826
MAX_DIAS_PRONOSTICO = 90

MESES_ES = ["", "enero", "febrero", "marzo", "abril", "mayo", "junio",
            "julio", "agosto", "septiembre", "octubre", "noviembre",
            "diciembre"]
DIAS_ES = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado",
           "domingo"]

ADVERTENCIA_ES = (
    "Esto no promete exactitud: los factores miden patrones del "
    "pasado y la demanda real puede desviarse por promociones, "
    "quiebres de tendencia, cambios de catálogo o causas no "
    "estacionales. Úselo como orden de magnitud, no como garantía.")
ADVERTENCIA_EN = (
    "This does not promise accuracy: the factors measure past "
    "patterns and real demand can deviate due to promotions, trend "
    "breaks, catalog changes, or non-seasonal causes. Use it as an "
    "order of magnitude, not a guarantee.")


# ---------------------------------------------------------------------
# Serie diaria (reutiliza el esquema de predict._serie_diaria, con
# filtro por conjunto de productos o por categoría y ventana larga)
# ---------------------------------------------------------------------
def _serie(bundle, h: date, productos: set | None,
           categoria: str | None, almacen_id: str,
           dias: int = SERIE_DIAS_TEMPORADA
           ) -> tuple[list[tuple[date, Decimal]], date | None]:
    """[(fecha, uds)] día calendario desde primera venta (o h-dias+1).

    productos: conjunto de producto_id a incluir (None = todos si hay
    categoria). categoria: agrupa por lin["categoria"] de la línea de
    venta. Una de las dos debe venir dada.
    """
    ini = h - timedelta(days=dias - 1)
    por_dia: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    primera: date | None = None
    for reg in bundle.ventas._ventas.values():  # coord. persistencia
        if reg["estado"] != "confirmada":
            continue
        d = dia(reg["solicitud"].fecha)
        if d is None:
            continue
        for lin in reg["solicitud"].lineas or []:
            if productos is not None and \
                    lin.get("producto_id") not in productos:
                continue
            if categoria is not None and \
                    lin.get("categoria") != categoria:
                continue
            if (lin.get("almacen_id") or "principal") != almacen_id:
                continue
            if primera is None or d < primera:
                primera = d
            if d < ini or d > h:
                continue
            por_dia[d] += abs(_dec(lin.get("cantidad", "0")))
    inicio = max(ini, primera) if primera else ini
    serie = [(inicio + timedelta(days=i),
              por_dia.get(inicio + timedelta(days=i), Decimal("0")))
             for i in range((h - inicio).days + 1)] if primera else []
    return serie, primera


def _suficiencia_temporada(bundle, h, productos, categoria,
                           almacen_id) -> tuple[dict | None,
                                                list, date | None]:
    """(falta_dict | None, serie, primera). None = hay datos."""
    serie, primera = _serie(bundle, h, productos, categoria, almacen_id)
    n = _historia_dias(primera, h)
    if n < MIN_DIAS_TEMPORADA:
        etiqueta = (f"producto {sorted(productos)[0]}"
                    if productos and len(productos) == 1
                    else (f"categoría {categoria}" if categoria
                          else "selección"))
        return ({
            "estado": "datos_insuficientes",
            "dias_historia": n,
            "minimo_requerido": MIN_DIAS_TEMPORADA,
            "explicacion_es": (
                f"La {etiqueta} solo tiene {n} días calendario de "
                f"historia de ventas; la descomposición estacional "
                f"requiere al menos {MIN_DIAS_TEMPORADA} días para no "
                f"confundir ruido con temporada. No se emite análisis."),
            "explicacion_en": (
                f"The {etiqueta} has only {n} days of sales history; "
                f"seasonal decomposition needs at least "
                f"{MIN_DIAS_TEMPORADA} days to avoid mistaking noise "
                f"for seasonality. No analysis issued.")},
            [], primera)
    total = sum(float(v) for _, v in serie)
    if total <= 0:
        return ({
            "estado": "datos_insuficientes",
            "dias_historia": n,
            "minimo_requerido": MIN_DIAS_TEMPORADA,
            "explicacion_es": (
                "Hay historia suficiente pero no hay ventas "
                "registradas en el período: no hay patrón estacional "
                "que medir."),
            "explicacion_en": (
                "History is long enough but no sales were recorded "
                "in the period: there is no seasonal pattern to "
                "measure.")},
            [], primera)
    return None, serie, primera


# ---------------------------------------------------------------------
# Descomposición
# ---------------------------------------------------------------------
def _descomponer(serie: list[tuple[date, Decimal]]) -> dict:
    """Factores mes (1-12) y día de semana (0=lunes..6=domingo)."""
    vals = [float(v) for _, v in serie]
    media = sum(vals) / len(vals)
    cubo_mes: dict[int, list[float]] = defaultdict(list)
    cubo_dow: dict[int, list[float]] = defaultdict(list)
    for d, v in serie:
        cubo_mes[d.month].append(float(v))
        cubo_dow[d.weekday()].append(float(v))

    def _factor(bucket: list[float], minimo: int):
        if len(bucket) < minimo or media <= 0:
            return None
        f = (sum(bucket) / len(bucket)) / media
        banda = K_BANDA * MAD_ESCALA * mad(bucket) / media
        return {"factor": f, "n": len(bucket),
                "banda": [max(0.0, f - banda), f + banda],
                "significativo": abs(f - 1.0) >= UMBRAL_SIGNIFICATIVO}

    meses = {m: _factor(cubo_mes.get(m, []), MIN_OBS_MES)
             for m in range(1, 13)}
    dias_sem = {dw: _factor(cubo_dow.get(dw, []), MIN_OBS_DIA_SEMANA)
                for dw in range(7)}
    return {"media_diaria": media, "n_dias": len(serie),
            "meses": meses, "dias_semana": dias_sem}


def _efectos(des: dict) -> list[dict]:
    """Efectos significativos nombrados en lenguaje claro."""
    out = []
    for m in range(1, 13):
        f = des["meses"][m]
        if f and f["significativo"]:
            out.append({"tipo": "mes", "nombre": MESES_ES[m],
                        "factor": f"{f['factor']:.2f}",
                        "direccion": "alza" if f["factor"] > 1 else "baja"})
    for dw in range(7):
        f = des["dias_semana"][dw]
        if f and f["significativo"]:
            out.append({"tipo": "dia_semana", "nombre": DIAS_ES[dw],
                        "factor": f"{f['factor']:.2f}",
                        "direccion": "alza" if f["factor"] > 1 else "baja"})
    return out


def _meses_sin_dato(des: dict) -> list[str]:
    return [MESES_ES[m] for m in range(1, 13) if des["meses"][m] is None]


def _etiqueta(productos, categoria) -> str:
    if productos and len(productos) == 1:
        return f"el producto {sorted(productos)[0]}"
    if categoria:
        return f"la categoría {categoria}"
    return "la selección"


def _explicacion_analisis(etiqueta, n, media, efectos, sin_dato) -> tuple[str, str]:
    if efectos:
        partes = [f"{e['nombre']} {e['factor']}× el promedio"
                  for e in efectos]
        fx_es = "Efectos estacionales: " + "; ".join(partes) + "."
        fx_en = ("Seasonal effects: " +
                 "; ".join(f"{e['nombre']} {e['factor']}x average"
                           for e in efectos)) + "."
    else:
        fx_es = ("No se detectaron efectos estacionales fuertes: la "
                 "demanda se comporta como el promedio todo el año.")
        fx_en = ("No strong seasonal effects detected: demand behaves "
                 "like the average all year.")
    sd_es = (f" Sin histórico en: {', '.join(sin_dato)} (se usa el "
             f"promedio, factor 1.0)." if sin_dato else "")
    sd_en = (f" No history for: {', '.join(sin_dato)} (average used, "
             f"factor 1.0)." if sin_dato else "")
    es = (f"{etiqueta.capitalize()} vende en promedio {media:.1f} "
          f"uds/día ({n} días de historia). {fx_es}{sd_es}")
    en = (f"{etiqueta.capitalize()} sells an average of {media:.1f} "
          f"units/day ({n} days of history). {fx_en}{sd_en}")
    return es, en


def analizar_temporada(bundle, producto_id: str | None = None,
                       categoria: str | None = None,
                       almacen_id: str = "principal",
                       fecha=None) -> dict:
    """Descomposición estacional: factores mes y día de semana."""
    if not producto_id and not categoria:
        raise ValueError("producto_id o categoria es obligatorio")
    h = fecha if isinstance(fecha, date) else _hoy_fn(fecha)
    productos = {producto_id} if producto_id else None
    falta, serie, primera = _suficiencia_temporada(
        bundle, h, productos, categoria, almacen_id)
    if falta:
        return falta
    des = _descomponer(serie)
    efectos = _efectos(des)
    sin_dato = _meses_sin_dato(des)
    etiqueta = _etiqueta(productos, categoria)
    n = _historia_dias(primera, h)
    es, en = _explicacion_analisis(etiqueta, n, des["media_diaria"],
                                   efectos, sin_dato)

    def _ser(f):
        if f is None:
            return None
        return {"factor": f"{f['factor']:.4f}",
                "n_observaciones": f["n"],
                "banda_factor": [f"{f['banda'][0]:.4f}",
                                 f"{f['banda'][1]:.4f}"],
                "significativo": f["significativo"]}

    return {
        "estado": "ok",
        "producto_id": producto_id, "categoria": categoria,
        "almacen_id": almacen_id, "hoy": h.isoformat(),
        "dias_historia": n,
        "minimo_requerido": MIN_DIAS_TEMPORADA,
        "metodo": ("factores multiplicativos mes/día de semana = "
                   "promedio del cubo / promedio global, sobre días "
                   "calendario desde la primera venta"),
        "media_diaria_global": f"{des['media_diaria']:.4f}",
        "factores_mes": {MESES_ES[m]: _ser(des["meses"][m])
                         for m in range(1, 13)},
        "factores_dia_semana": {DIAS_ES[dw]: _ser(des["dias_semana"][dw])
                                for dw in range(7)},
        "efectos_significativos": efectos,
        "meses_sin_historico": sin_dato,
        "incertidumbre": {
            "metodo_banda": (f"cada factor trae ±{K_BANDA:g} × MAD "
                             f"escalada de los días de su cubo: es "
                             f"variabilidad histórica, no un intervalo "
                             f"de confianza formal"),
            "advertencia_es": ADVERTENCIA_ES,
            "advertencia_en": ADVERTENCIA_EN,
        },
        "explicacion_es": es,
        "explicacion_en": en,
    }


# ---------------------------------------------------------------------
# Pronóstico ajustado por temporada
# ---------------------------------------------------------------------
def _factor_para(des: dict, d: date) -> tuple[float, float, bool, bool]:
    """(factor_mes, factor_dow, mes_sin_dato, dow_sin_dato)."""
    fm = des["meses"][d.month]
    fd = des["dias_semana"][d.weekday()]
    return ((fm["factor"] if fm else 1.0),
            (fd["factor"] if fd else 1.0),
            fm is None, fd is None)


def predecir_demanda_temporal(bundle, producto_id: str,
                              almacen_id: str = "principal",
                              dias: int = 30, fecha=None) -> dict:
    """Pronóstico base (predict.predecir_demanda) × factores
    estacionales de cada día futuro, con banda escalada y explicación.
    """
    h = fecha if isinstance(fecha, date) else _hoy_fn(fecha)
    if not 1 <= dias <= MAX_DIAS_PRONOSTICO:
        raise ValueError(f"dias debe estar entre 1 y {MAX_DIAS_PRONOSTICO}")
    productos = {producto_id}
    falta, serie, _ = _suficiencia_temporada(
        bundle, h, productos, None, almacen_id)
    if falta:
        return falta
    des = _descomponer(serie)

    # Base oficial de predict.py (propaga su propio datos_insuficientes
    # si la historia es < 30 días, y su advertencia honesta).
    base = predecir_demanda(bundle, producto_id, almacen_id, fecha=h)
    if base.get("estado") != "ok":
        return base
    p0 = Decimal(base["pronostico_diario"])
    lo0, hi0 = (Decimal(x) for x in base["incertidumbre"]["banda"])

    detalle, total, tot_lo, tot_hi = [], Decimal("0"), Decimal("0"), Decimal("0")
    meses_neutros: set[str] = set()
    for i in range(1, dias + 1):
        t = h + timedelta(days=i)
        fm, fd, msd, dsd = _factor_para(des, t)
        f = fm * fd
        pd = p0 * Decimal(str(f))
        plo = lo0 * Decimal(str(f))
        phi = hi0 * Decimal(str(f))
        total += pd
        tot_lo += plo
        tot_hi += phi
        if msd:
            meses_neutros.add(MESES_ES[t.month])
        detalle.append({
            "fecha": t.isoformat(), "dia_semana": DIAS_ES[t.weekday()],
            "mes": MESES_ES[t.month],
            "factor_mes": f"{fm:.4f}", "factor_dia_semana": f"{fd:.4f}",
            "factor_combinado": f"{f:.4f}",
            "pronostico": f"{pd:.2f}",
            "banda": [f"{plo:.2f}", f"{phi:.2f}"],
            "mes_sin_historico": msd, "dia_sin_historico": dsd})

    efectos = _efectos(des)
    fx = ("; ".join(f"{e['nombre']} {e['factor']}×" for e in efectos)
          if efectos else "ningún efecto fuerte detectado")
    neutro = (f" Los meses {', '.join(sorted(meses_neutros))} no tienen "
              f"histórico: esos días usan el promedio (factor 1.0)."
              if meses_neutros else "")
    return {
        "estado": "ok",
        "producto_id": producto_id, "almacen_id": almacen_id,
        "hoy": h.isoformat(), "dias_pronosticados": dias,
        "metodo": ("pronóstico base de predict.predecir_demanda (media "
                   "móvil 28 días) multiplicado por factor_mes × "
                   "factor_dia de cada día futuro"),
        "pronostico_base_diario": f"{p0:.4f}",
        "pronostico_total": f"{total:.2f}",
        "banda_total": [f"{tot_lo:.2f}", f"{tot_hi:.2f}"],
        "detalle_diario": detalle,
        "explicacion_es": (
            f"Pronóstico de {dias} días para el producto {producto_id}: "
            f"parte de {p0:.1f} uds/día (promedio de 28 días) y lo "
            f"ajusta por temporada ({fx}). Total estimado: "
            f"{total:.1f} uds.{neutro}"),
        "explicacion_en": (
            f"{dias}-day forecast for product {producto_id}: starts "
            f"from {p0:.1f} units/day (28-day average) adjusted by "
            f"seasonality ({fx}). Estimated total: {total:.1f} "
            f"units.{neutro}"),
        "incertidumbre": {
            "metodo_banda": ("banda de cada día = banda base de "
                             "predict.py × factor combinado del día; "
                             "variabilidad histórica, no intervalo de "
                             "confianza formal"),
            "limites_de_validez_es": (
                "Válido solo mientras el patrón estacional se mantenga: "
                "sin promociones, sin quiebres de tendencia y sin "
                "cambios de catálogo. Días de meses sin histórico usan "
                "factor neutro 1.0 (equivalen al promedio)."),
            "advertencia_es": ADVERTENCIA_ES,
            "advertencia_en": ADVERTENCIA_EN,
        },
    }


# ---------------------------------------------------------------------
# Integración con Stock Predictor (os_inventory/predictor.py)
# ---------------------------------------------------------------------
def factor_estacional_ventana(bundle, producto_id: str,
                              almacen_id: str = "principal",
                              fecha=None, dias: int = 14) -> dict:
    """Factor estacional promedio sobre una ventana futura de `dias`.

    Pensado para escalar el punto de reposición: el reabastecimiento
    cubre lead_time + seguridad días, y si esos días caen en temporada
    alta/baja el punto debe moverse en la misma proporción.
    """
    h = fecha if isinstance(fecha, date) else _hoy_fn(fecha)
    productos = {producto_id}
    falta, serie, _ = _suficiencia_temporada(
        bundle, h, productos, None, almacen_id)
    if falta:
        return {"estado": "datos_insuficientes",
                "factor_promedio": None,
                "nota_es": falta["explicacion_es"]}
    des = _descomponer(serie)
    fs = [_factor_para(des, h + timedelta(days=i))[0] *
          _factor_para(des, h + timedelta(days=i))[1]
          for i in range(1, dias + 1)]
    return {"estado": "ok", "dias_ventana": dias,
            "factor_promedio": f"{sum(fs) / len(fs):.4f}"}


def punto_reposicion_temporal(bundle, producto_id: str,
                              almacen_id: str = "principal",
                              lead_time_dias: int = 7,
                              dias_seguridad: int = 7,
                              fecha=None) -> dict:
    """Punto de reposición del Stock Predictor ajustado por temporada.

    Cableado real: llama a `os_inventory.predictor.predecir` (su API
    pública) y escala el punto de reposición con el factor estacional
    promedio de la ventana lead_time + seguridad.
    """
    from os_inventory import predictor as inv_predictor  # noqa: E402
    h = fecha if isinstance(fecha, date) else _hoy_fn(fecha)
    hoy_s = h.strftime("%Y-%m-%dT00:00:00Z")
    prs = inv_predictor.predecir(
        bundle.inventario, dias=90, min_dias=30,
        lead_time_dias=lead_time_dias, dias_seguridad=dias_seguridad,
        hoy=hoy_s, producto_id=producto_id, almacen_id=almacen_id)
    pr = next((p for p in prs
               if (p.producto_id, p.almacen_id) == (producto_id, almacen_id)),
              None)
    base = pr.to_dict() if pr else None
    if pr is None or pr.suficiencia != "suficiente":
        return {"estado": "ok", "pronostico_inventario": base,
                "factor_estacional": None,
                "punto_reposicion_ajustado": None,
                "explicacion_es": (
                    "El Stock Predictor no tiene tasa confiable para "
                    f"({producto_id}, {almacen_id}): no se aplica ajuste "
                    "estacional. Ver 'pronostico_inventario'."),
                "explicacion_en": (
                    "Stock Predictor has no reliable rate for "
                    f"({producto_id}, {almacen_id}): no seasonal "
                    "adjustment applied. See 'pronostico_inventario'.")}
    ventana = lead_time_dias + dias_seguridad
    fest = factor_estacional_ventana(bundle, producto_id, almacen_id,
                                     fecha=h, dias=ventana)
    if fest["estado"] != "ok":
        return {"estado": "ok", "pronostico_inventario": base,
                "factor_estacional": None,
                "punto_reposicion_ajustado": None,
                "explicacion_es": (
                    "Hay tasa del Stock Predictor pero no hay "
                    "estacionalidad medible: el punto de reposición se "
                    "deja sin ajuste. " + fest["nota_es"]),
                "explicacion_en": (
                    "Stock Predictor has a rate but no measurable "
                    "seasonality: reorder point left unadjusted. " +
                    fest["nota_es"])}
    f = Decimal(fest["factor_promedio"])
    tasa = Decimal(pr.tasa_diaria)
    ajustado = math.ceil(float(tasa * ventana * f))
    direccion = ("temporada alta" if f > 1 else
                 "temporada baja" if f < 1 else "temporada neutra")
    return {
        "estado": "ok",
        "producto_id": producto_id, "almacen_id": almacen_id,
        "hoy": h.isoformat(),
        "pronostico_inventario": base,
        "factor_estacional": fest["factor_promedio"],
        "ventana_dias": ventana,
        "punto_reposicion_base": pr.punto_reposicion,
        "punto_reposicion_ajustado": str(ajustado),
        "explicacion_es": (
            f"Punto de reposición base {pr.punto_reposicion} uds "
            f"(tasa {pr.tasa_diaria}/día × {ventana} días) ajustado por "
            f"temporada: factor {f} ({direccion}) en los próximos "
            f"{ventana} días -> {ajustado} uds. La tasa base viene de "
            f"SALIDAs de inventario; el factor, de ventas confirmadas: "
            f"si hay salidas no ligadas a ventas (mermas, traslados), "
            f"el ajuste puede desviarse."),
        "explicacion_en": (
            f"Base reorder point {pr.punto_reposicion} units (rate "
            f"{pr.tasa_diaria}/day × {ventana} days) adjusted by "
            f"seasonality: factor {f} ({direccion}) over the next "
            f"{ventana} days -> {ajustado} units. Base rate comes from "
            f"inventory outflows; the factor, from confirmed sales."),
        "incertidumbre": {
            "advertencia_es": ADVERTENCIA_ES,
            "advertencia_en": ADVERTENCIA_EN,
        },
    }


# ---------------------------------------------------------------------
# Contrato de integración: rutas ia/predict/temporada/*
# ---------------------------------------------------------------------
def _temporada(bundle, ctx, query):
    verificar_aislamiento(bundle, ctx)
    return {"ok": True, "data": analizar_temporada(
        bundle, producto_id=q1(query, "producto_id") or None,
        categoria=q1(query, "categoria") or None,
        almacen_id=q1(query, "almacen", "principal"),
        fecha=q1(query, "hoy") or None)}


def _pronostico(bundle, ctx, query):
    verificar_aislamiento(bundle, ctx)
    pid = q1(query, "producto_id")
    if not pid:
        raise ValueError("producto_id es obligatorio")
    try:
        dias = int(q1(query, "dias", "30"))
    except ValueError:
        raise ValueError("dias debe ser entero")
    return {"ok": True, "data": predecir_demanda_temporal(
        bundle, pid, q1(query, "almacen", "principal"),
        dias=dias, fecha=q1(query, "hoy") or None)}


def _reorden(bundle, ctx, query):
    verificar_aislamiento(bundle, ctx)
    pid = q1(query, "producto_id")
    if not pid:
        raise ValueError("producto_id es obligatorio")
    try:
        lead = int(q1(query, "lead_time_dias", "7"))
        seg = int(q1(query, "dias_seguridad", "7"))
    except ValueError:
        raise ValueError("lead_time_dias y dias_seguridad deben ser enteros")
    return {"ok": True, "data": punto_reposicion_temporal(
        bundle, pid, q1(query, "almacen", "principal"),
        lead_time_dias=lead, dias_seguridad=seg,
        fecha=q1(query, "hoy") or None)}


def rutas():
    """Contrato ia/predict/temporada-*: handlers (bundle, ctx, query)."""
    return {"GET ia/predict/temporada": _temporada,
            "GET ia/predict/temporada-pronostico": _pronostico,
            "GET ia/predict/temporada-reorden": _reorden}
