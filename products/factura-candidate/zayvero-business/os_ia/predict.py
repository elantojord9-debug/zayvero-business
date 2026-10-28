"""BLOQUE IA · 2 — Predict.

Pronóstico de demanda y riesgo de faltantes sobre el núcleo OS.

Método (determinista, explicable, verificable a mano):
- Serie: unidades vendidas por día calendario (ventas confirmadas;
  días sin venta = 0) en los últimos SERIE_DIAS días.
- Suficiencia: el producto debe tener al menos MIN_PUNTOS (30) días
  calendario de historia desde su primera venta. Con menos historia
  NO se pronostica: estado "datos_insuficientes". Nunca un número
  inventado.
- Pronóstico diario: media aritmética simple de los últimos
  VENTANA_PRONOSTICO (28) días calendario.
- Incertidumbre EXPLICADA: banda ±2 × MAD escalada
  (MAD × 1.4826 ≈ σ bajo normalidad) de la misma ventana. No es un
  intervalo de confianza formal: con series no normales o con
  estacionalidad es solo una banda robusta de variabilidad, y así se
  documenta en cada respuesta.
- Backtesting: se entrena con la serie sin los últimos 7 días, se
  predicen esos 7 y se compara con lo real: MAPE, MAE y cobertura
  (fracción de los 7 reales dentro de la banda). Métricas honestas:
  si el backtest es malo, la respuesta lo dice.
- Riesgo de faltantes: días de cobertura = stock / pronóstico
  diario; si <= lead_time + colchón hay riesgo (crítico si ni al
  lead_time llega).

Todo es solo lectura: nunca muta el bundle.
"""
from __future__ import annotations

import os as _os
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402

from .base import (SEVERIDADES, dia, dinero, hoy as _hoy_fn, mad,  # noqa: E402
                   q1, verificar_aislamiento)

SERIE_DIAS = 60          # días calendario de serie construida
MIN_PUNTOS = 30          # días de historia mínimos para pronosticar
VENTANA_PRONOSTICO = 28  # días para la media móvil
DIAS_BACKTEST = 7
K_BANDA = 2.0            # ±K × MAD escalada
MAD_ESCALA = 1.4826      # MAD × 1.4826 ≈ σ bajo normalidad
COLCHON_DIAS = 2


def _serie_diaria(bundle, producto_id: str, almacen_id: str,
                  h: date) -> tuple[list[Decimal], date | None]:
    """(serie de SERIE_DIAS Decimals, fecha de primera venta).

    Índice 0 = día más antiguo (h - 59), índice -1 = hoy.
    """
    ini = h - timedelta(days=SERIE_DIAS - 1)
    por_dia: dict[date, Decimal] = defaultdict(lambda: Decimal("0"))
    primera: date | None = None
    for reg in bundle.ventas._ventas.values():  # coord. persistencia
        if reg["estado"] != "confirmada":
            continue
        d = dia(reg["solicitud"].fecha)
        if d is None:
            continue
        if primera is None or d < primera:
            primera = d
        if d < ini or d > h:
            continue
        for lin in reg["solicitud"].lineas or []:
            if lin.get("producto_id") != producto_id:
                continue
            if (lin.get("almacen_id") or "principal") != almacen_id:
                continue
            por_dia[d] += abs(_dec(lin.get("cantidad", "0")))
    serie = [por_dia.get(ini + timedelta(days=i), Decimal("0"))
             for i in range(SERIE_DIAS)]
    return serie, primera


def _historia_dias(primera: date | None, h: date) -> int:
    if primera is None:
        return 0
    return (h - primera).days + 1


def _suficiencia(bundle, producto_id, almacen_id, h,
                 extra: int = 0) -> dict | None:
    """None si hay datos suficientes; si no, dict datos_insuficientes."""
    _, primera = _serie_diaria(bundle, producto_id, almacen_id, h)
    n = _historia_dias(primera, h)
    minimo = MIN_PUNTOS + extra
    if n < minimo:
        return {"estado": "datos_insuficientes",
                "producto_id": producto_id, "almacen_id": almacen_id,
                "dias_historia": n, "minimo_requerido": minimo,
                "explicacion_es": (
                    f"El producto {producto_id} solo tiene {n} días de "
                    f"historia de ventas; se requieren al menos {minimo} "
                    f"para pronosticar. No se emite pronóstico."),
                "explicacion_en": (
                    f"Product {producto_id} has only {n} days of sales "
                    f"history; at least {minimo} are required to "
                    f"forecast. No forecast issued.")}
    return None


def _pronostico(serie: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    """(media, banda_inf, banda_sup) sobre la ventana de pronóstico."""
    ventana = [float(x) for x in serie[-VENTANA_PRONOSTICO:]]
    media = sum(ventana) / len(ventana)
    banda = K_BANDA * MAD_ESCALA * mad(ventana)
    return (Decimal(str(media)),
            Decimal(str(max(0.0, media - banda))),
            Decimal(str(media + banda)))


def predecir_demanda(bundle, producto_id: str, almacen_id: str = "principal",
                     fecha=None) -> dict:
    """Pronóstico de demanda diaria con incertidumbre explicada."""
    h = fecha if isinstance(fecha, date) else _hoy_fn(fecha)
    falta = _suficiencia(bundle, producto_id, almacen_id, h)
    if falta:
        return falta
    serie, _ = _serie_diaria(bundle, producto_id, almacen_id, h)
    media, lo, hi = _pronostico(serie)
    return {
        "estado": "ok",
        "producto_id": producto_id, "almacen_id": almacen_id,
        "hoy": h.isoformat(),
        "metodo": (f"media móvil simple de los últimos "
                   f"{VENTANA_PRONOSTICO} días calendario"),
        "pronostico_diario": f"{media:.4f}",
        "pronostico_7d": f"{media * 7:.2f}",
        "pronostico_30d": f"{media * 30:.2f}",
        "incertidumbre": {
            "banda": [f"{lo:.4f}", f"{hi:.4f}"],
            "metodo_banda": (f"±{K_BANDA:g} × MAD escalada "
                             f"(×{MAD_ESCALA}) de la ventana"),
            "advertencia_es": (
                "La banda mide variabilidad histórica, no es un "
                "intervalo de confianza formal: no captura "
                "estacionalidad, promociones ni quiebres de tendencia. "
                "Úsela como orden de magnitud, no como garantía."),
            "advertencia_en": (
                "The band measures historical variability, not a formal "
                "confidence interval: it captures no seasonality, "
                "promotions, or trend breaks. Use it as an order of "
                "magnitude, not a guarantee."),
        },
        "dias_historia_usados": SERIE_DIAS,
    }


def backtest(bundle, producto_id: str, almacen_id: str = "principal",
             fecha=None) -> dict:
    """Entrena sin los últimos 7 días, predice 7, compara con lo real."""
    h = fecha if isinstance(fecha, date) else _hoy_fn(fecha)
    falta = _suficiencia(bundle, producto_id, almacen_id, h,
                         extra=DIAS_BACKTEST)
    if falta:
        return falta
    serie, _ = _serie_diaria(bundle, producto_id, almacen_id, h)
    train, real = serie[:-DIAS_BACKTEST], serie[-DIAS_BACKTEST:]
    media, lo, hi = _pronostico(train)
    pf = float(media)
    errores, abs_pct = [], []
    dentro = 0
    detalle = []
    for r in real:
        rf = float(r)
        err = abs(rf - pf)
        errores.append(err)
        # MAPE con denominador protegido: si real=0 se usa |err|/max(pf,1).
        denom = rf if rf > 0 else max(pf, 1.0)
        abs_pct.append(err / denom)
        ok = float(lo) <= rf <= float(hi)
        dentro += 1 if ok else 0
        detalle.append({"real": f"{rf:.2f}",
                        "pronosticado": f"{pf:.4f}",
                        "dentro_de_banda": ok})
    mae = sum(errores) / len(errores)
    mape = sum(abs_pct) / len(abs_pct)
    cobertura = dentro / len(real)
    return {
        "estado": "ok",
        "producto_id": producto_id, "almacen_id": almacen_id,
        "hoy": h.isoformat(),
        "metodo": (f"entrenado con {len(train)} días, pronóstico "
                   f"constante = media móvil {VENTANA_PRONOSTICO}d, "
                   f"evaluado en los últimos {DIAS_BACKTEST} días"),
        "metricas": {
            "mae": f"{mae:.4f}",
            "mape": f"{mape:.4f}",
            "cobertura_banda": f"{cobertura:.3f}",
        },
        "lectura_es": (
            f"En los últimos {DIAS_BACKTEST} días el pronóstico se "
            f"equivocó en promedio {mae:.2f} uds/día (MAPE "
            f"{mape * 100:.1f}%) y {dentro} de {len(real)} valores "
            f"reales cayeron dentro de la banda. " +
            ("Cobertura aceptable." if cobertura >= 0.7
             else "Cobertura baja: la demanda es más volátil de lo que "
                  "la banda captura; no confíe el reabastecimiento solo "
                  "a este pronóstico.")),
        "detalle": detalle,
    }


def riesgo_faltante(bundle, producto_id: str, almacen_id: str = "principal",
                    lead_time_dias: int = 7, fecha=None) -> dict:
    """¿El stock alcanza hasta que llegue la próxima compra?"""
    h = fecha if isinstance(fecha, date) else _hoy_fn(fecha)
    pron = predecir_demanda(bundle, producto_id, almacen_id, fecha=h)
    if pron.get("estado") != "ok":
        return pron  # propaga datos_insuficientes
    p = Decimal(pron["pronostico_diario"])
    stock = bundle.inventario.stock_empresa().get(
        (producto_id, almacen_id), Decimal("0"))
    if p <= 0:
        return {"estado": "datos_insuficientes",
                "producto_id": producto_id, "almacen_id": almacen_id,
                "explicacion_es": (
                    "El pronóstico de demanda es 0: no hay ritmo de "
                    "venta medible para estimar cobertura."),
                "explicacion_en": (
                    "Forecasted demand is 0: no measurable sales pace "
                    "to estimate coverage.")}
    dias = stock / p
    umbral = lead_time_dias + COLCHON_DIAS
    hay_riesgo = dias <= umbral
    if not hay_riesgo:
        severidad = None
    elif dias <= lead_time_dias:
        severidad = "critico"
    else:
        severidad = "alerta"
    quiebre = h + timedelta(days=float(dias))
    return {
        "estado": "ok",
        "producto_id": producto_id, "almacen_id": almacen_id,
        "hoy": h.isoformat(),
        "stock_actual": str(stock),
        "pronostico_diario": f"{p:.4f}",
        "dias_cobertura": f"{dias:.1f}",
        "lead_time_dias": lead_time_dias,
        "colchon_dias": COLCHON_DIAS,
        "fecha_quiebre_proyectada": quiebre.isoformat(),
        "riesgo": hay_riesgo,
        "severidad": severidad,
        "explicacion_es": (
            f"Con {stock} uds y demanda pronosticada de {p:.1f}/día, "
            f"el stock cubre {dias:.1f} días (quiebre proyectado: "
            f"{quiebre.isoformat()}). " +
            (f"Riesgo {severidad}: no alcanza el lead time de "
             f"{lead_time_dias} días + {COLCHON_DIAS} de colchón."
             if hay_riesgo else
             f"Suficiente para el lead time de {lead_time_dias} días.")),
    }


def predecir_todos(bundle, fecha=None) -> dict:
    """Resumen de pronóstico para cada producto con historia suficiente."""
    h = fecha if isinstance(fecha, date) else _hoy_fn(fecha)
    productos = sorted({(m.producto_id, m.almacen_id)
                        for m in bundle.inventario.movimientos()})
    items, sin_datos = [], 0
    for prod, alm in productos:
        r = predecir_demanda(bundle, prod, alm, fecha=h)
        if r.get("estado") == "ok":
            items.append({
                "producto_id": prod, "almacen_id": alm,
                "pronostico_diario": r["pronostico_diario"],
                "banda": r["incertidumbre"]["banda"]})
        else:
            sin_datos += 1
    return {"estado": "ok", "hoy": h.isoformat(),
            "productos_pronosticados": len(items),
            "productos_sin_datos": sin_datos,
            "items": items}


# ---------------------------------------------------------------------
# Contrato de integración: rutas ia/predict/*
# ---------------------------------------------------------------------
def _demanda(bundle, ctx, query):
    verificar_aislamiento(bundle, ctx)
    pid = q1(query, "producto_id")
    if not pid:
        raise ValueError("producto_id es obligatorio")
    return {"ok": True, "data": predecir_demanda(
        bundle, pid, q1(query, "almacen", "principal"),
        fecha=q1(query, "hoy") or None)}


def _backtest(bundle, ctx, query):
    verificar_aislamiento(bundle, ctx)
    pid = q1(query, "producto_id")
    if not pid:
        raise ValueError("producto_id es obligatorio")
    return {"ok": True, "data": backtest(
        bundle, pid, q1(query, "almacen", "principal"),
        fecha=q1(query, "hoy") or None)}


def _riesgo(bundle, ctx, query):
    verificar_aislamiento(bundle, ctx)
    pid = q1(query, "producto_id")
    if not pid:
        raise ValueError("producto_id es obligatorio")
    try:
        lead = int(q1(query, "lead_time_dias", "7"))
    except ValueError:
        raise ValueError("lead_time_dias debe ser entero")
    return {"ok": True, "data": riesgo_faltante(
        bundle, pid, q1(query, "almacen", "principal"),
        lead_time_dias=lead, fecha=q1(query, "hoy") or None)}


def _resumen(bundle, ctx, query):
    verificar_aislamiento(bundle, ctx)
    return {"ok": True,
            "data": predecir_todos(bundle, fecha=q1(query, "hoy") or None)}


def rutas():
    """Contrato ia/predict: handlers (bundle, ctx, query) -> dict."""
    return {"GET ia/predict/demanda": _demanda,
            "GET ia/predict/backtest": _backtest,
            "GET ia/predict/riesgo-faltantes": _riesgo,
            "GET ia/predict/resumen": _resumen}
