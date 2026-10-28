"""BLOQUE IA · 3 — Shield extendido.

Extiende las 8 reglas de `shield/reglas.py` con 2 reglas nuevas de
riesgo verificable, en el mismo formato `Alerta` (ES/EN, evidencia,
acción sugerida):

  R9  devoluciones_anormales  tasa de devoluciones de un producto
                              anómala frente a la distribución de la
                              empresa (z robusto). Difiere de R6
                              (devoluciones_frecuentes: ratio global):
                              R9 detecta EL producto problemático.
  R10 problemas_proveedor      incumplimientos (recepciones que llegan
                              tarde vs. fecha de entrega esperada) y
                              subidas de precio del proveedor
                              (precio reciente vs. histórico).

Anti-falsos-positivos: cada regla exige muestra mínima; sin datos
suficientes guarda silencio. `evaluar_todo()` devuelve las 10 reglas
ordenadas por severidad.
"""
from __future__ import annotations

import os as _os
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal

sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from oschema.contract import TipoMovimiento  # noqa: E402
from oschema.models import _dec  # noqa: E402
from shield.config import ConfigShield  # noqa: E402
from shield.reglas import (Alerta, REGLAS, SEVERIDADES, evaluar,  # noqa: E402
                           _dia, _dinero as dinero, _hoy)

from .base import q1, verificar_aislamiento, z_robusto  # noqa: E402

REGLAS_IA = ("devoluciones_anormales", "problemas_proveedor")
REGLAS_TODAS = REGLAS + REGLAS_IA

VENTANA_DEV_DIAS = 90
VENTANA_PROV_DIAS = 365


@dataclass
class ConfigShieldIA(ConfigShield):
    """Umbrales de shield + los 2 de las reglas nuevas."""
    # -- R9: devoluciones anormales por producto ----------------------
    dev_min_productos: int = 5        # productos con datos para comparar
    dev_min_uds_producto: int = 10    # uds vendidas mínimas por producto
    dev_z_alerta: float = 2.0
    dev_z_critico: float = 3.0
    # -- R10: problemas con proveedores --------------------------------
    prov_gracia_dias: int = 3         # tolerancia sobre fecha esperada
    prov_min_recepciones: int = 3
    prov_tasa_incumplimiento_alerta: float = 0.30
    prov_tasa_incumplimiento_critico: float = 0.60
    prov_ventana_reciente_dias: int = 60
    prov_ventana_base_dias: int = 180
    prov_min_compras: int = 3         # compras por período para comparar
    prov_subida_alerta: float = 0.10
    prov_subida_critico: float = 0.25


# ---------------------------------------------------------------------
# R9 — Devoluciones anormales por producto
# ---------------------------------------------------------------------
def _uds_vendidas(bundle, h, ventana: int) -> dict[str, Decimal]:
    ini = h - timedelta(days=ventana - 1)
    uds: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for reg in bundle.ventas._ventas.values():  # coord. persistencia
        if reg["estado"] != "confirmada":
            continue
        d = _dia(reg["solicitud"].fecha)
        if d is None or d < ini or d > h:
            continue
        for lin in reg["solicitud"].lineas or []:
            uds[lin.get("producto_id")] += abs(_dec(lin.get("cantidad",
                                                            "0")))
    return dict(uds)


def _uds_devueltas(bundle, h, ventana: int) -> dict[str, Decimal]:
    ini = h - timedelta(days=ventana - 1)
    dev: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for m in bundle.inventario.movimientos():
        if m.tipo != TipoMovimiento.DEVOLUCION_IN:
            continue
        d = _dia(m.fecha)
        if d is None or d < ini or d > h:
            continue
        dev[m.producto_id] += abs(_dec(m.cantidad))
    return dict(dev)


def _regla_devoluciones_anormales(bundle, cfg: ConfigShieldIA,
                                  h) -> list[Alerta]:
    vendidas = _uds_vendidas(bundle, h, VENTANA_DEV_DIAS)
    devueltas = _uds_devueltas(bundle, h, VENTANA_DEV_DIAS)
    elegibles = {p: u for p, u in vendidas.items()
                 if u >= cfg.dev_min_uds_producto}
    if len(elegibles) < cfg.dev_min_productos:
        return []  # sin base de comparación: silencio
    ratios = {p: float(devueltas.get(p, Decimal("0")) / u)
              for p, u in elegibles.items()}
    out = []
    for prod in sorted(elegibles):
        base = [r for p, r in ratios.items() if p != prod]
        z, mediana = z_robusto(ratios[prod], base)
        if z < cfg.dev_z_alerta:
            continue
        sev = "critico" if z >= cfg.dev_z_critico else "alerta"
        r = ratios[prod]
        out.append(Alerta(
            regla="devoluciones_anormales", severidad=sev,
            fecha=h.isoformat(),
            titulo_es=(f"Devoluciones anormales en {prod}: "
                       f"{r * 100:.1f}% de lo vendido"),
            titulo_en=(f"Abnormal returns for {prod}: "
                       f"{r * 100:.1f}% of units sold"),
            explicacion_es=(
                f"En los últimos {VENTANA_DEV_DIAS} días se devolvieron "
                f"{devueltas.get(prod, Decimal('0'))} de {elegibles[prod]} "
                f"uds vendidas de {prod} ({r * 100:.1f}%), muy por encima "
                f"de la mediana de la empresa ({mediana * 100:.1f}%, "
                f"z={z:.1f})."),
            explicacion_en=(
                f"In the last {VENTANA_DEV_DIAS} days, "
                f"{devueltas.get(prod, Decimal('0'))} of {elegibles[prod]} "
                f"units sold of {prod} were returned ({r * 100:.1f}%), "
                f"far above the company median ({mediana * 100:.1f}%, "
                f"z={z:.1f})."),
            dato={"producto_id": prod,
                  "uds_vendidas": str(elegibles[prod]),
                  "uds_devueltas": str(devueltas.get(prod,
                                                     Decimal("0"))),
                  "ratio_producto": f"{r:.3f}",
                  "mediana_empresa": f"{mediana:.3f}",
                  "z_robusto": f"{z:.2f}",
                  "ventana_dias": VENTANA_DEV_DIAS},
            accion_es=("Investiga la causa en este producto (calidad, "
                       "descripción, empaque) antes de seguir vendiéndolo "
                       "igual."),
            accion_en=("Investigate the cause for this product (quality, "
                       "listing, packaging) before continuing to sell it "
                       "as-is.")))
    return out


# ---------------------------------------------------------------------
# R10 — Problemas con proveedores
# ---------------------------------------------------------------------
def _recepciones(bundle):
    """[(proveedor_id, fecha_recepcion, fecha_esperada)] con fechas."""
    rows = []
    for r in bundle.compras._recepciones.values():  # coord. persistencia
        oc = bundle.compras._ordenes.get(r.compra_id)
        if oc is None:
            continue
        f_rec = _dia(r.fecha)
        f_esp = _dia(oc.fecha_entrega_esperada)
        if f_rec is None or f_esp is None:
            continue
        rows.append((oc.proveedor_id, f_rec, f_esp, r.recepcion_id))
    return rows


def _r10_incumplimientos(bundle, cfg: ConfigShieldIA, h) -> list[Alerta]:
    ini = h - timedelta(days=VENTANA_PROV_DIAS)
    por_prov: dict[str, list] = defaultdict(list)
    for prov, f_rec, f_esp, rid in _recepciones(bundle):
        if f_rec < ini or f_rec > h:
            continue
        por_prov[prov].append((f_rec, f_esp, rid))
    out = []
    for prov in sorted(por_prov):
        recs = por_prov[prov]
        if len(recs) < cfg.prov_min_recepciones:
            continue
        tardias = [(rid, (f_rec - f_esp).days)
                   for f_rec, f_esp, rid in recs
                   if (f_rec - f_esp).days > cfg.prov_gracia_dias]
        tasa = len(tardias) / len(recs)
        if tasa >= cfg.prov_tasa_incumplimiento_critico:
            sev = "critico"
        elif tasa >= cfg.prov_tasa_incumplimiento_alerta:
            sev = "alerta"
        else:
            continue
        retraso_prom = (sum(d for _, d in tardias) / len(tardias))
        out.append(Alerta(
            regla="problemas_proveedor", severidad=sev,
            fecha=h.isoformat(),
            titulo_es=(f"Proveedor incumplido: {prov} entregó tarde el "
                       f"{tasa * 100:.0f}% de las recepciones"),
            titulo_en=(f"Unreliable supplier: {prov} delivered late "
                       f"{tasa * 100:.0f}% of receipts"),
            explicacion_es=(
                f"En los últimos {VENTANA_PROV_DIAS} días, {len(tardias)} "
                f"de {len(recs)} recepciones de {prov} llegaron más de "
                f"{cfg.prov_gracia_dias} días después de la fecha "
                f"esperada (retraso promedio {retraso_prom:.1f} días)."),
            explicacion_en=(
                f"In the last {VENTANA_PROV_DIAS} days, {len(tardias)} of "
                f"{len(recs)} receipts from {prov} arrived more than "
                f"{cfg.prov_gracia_dias} days after the expected date "
                f"(average delay {retraso_prom:.1f} days)."),
            dato={"proveedor_id": prov, "subtipo": "incumplimiento",
                  "n_recepciones": len(recs), "n_tardias": len(tardias),
                  "tasa_incumplimiento": f"{tasa:.3f}",
                  "retraso_promedio_dias": f"{retraso_prom:.1f}",
                  "recepciones_tardias": [
                      {"recepcion_id": rid, "retraso_dias": d}
                      for rid, d in sorted(tardias,
                                           key=lambda x: -x[1])[:10]]},
            accion_es=("Renegocia plazos con el proveedor o busca un "
                       "alternativo; ajusta las fechas esperadas de las "
                       "órdenes abiertas."),
            accion_en=("Renegotiate lead times with the supplier or find "
                       "an alternative; adjust expected dates on open "
                       "orders.")))
    return out


def _r10_subidas_precio(bundle, cfg: ConfigShieldIA, h) -> list[Alerta]:
    ini_rec = h - timedelta(days=cfg.prov_ventana_reciente_dias)
    ini_base = h - timedelta(
        days=cfg.prov_ventana_reciente_dias + cfg.prov_ventana_base_dias)
    # {(prov, prod, moneda): ([precios_rec], [precios_base])}
    datos: dict[tuple, list] = defaultdict(lambda: [[], []])
    for oc in bundle.compras._ordenes.values():  # coord. persistencia
        if oc.estado == "cancelada":
            continue
        d = _dia(oc.fecha_emision)
        if d is None or d < ini_base or d > h:
            continue
        for lin in oc.lineas or []:
            key = (oc.proveedor_id, lin.producto_id, oc.moneda)
            pu = float(_dec(lin.precio_unitario))
            if d >= ini_rec:
                datos[key][0].append(pu)
            else:
                datos[key][1].append(pu)
    out = []
    for (prov, prod, moneda) in sorted(datos):
        rec, base = datos[(prov, prod, moneda)]
        if len(rec) < cfg.prov_min_compras or len(base) < cfg.prov_min_compras:
            continue
        p_rec = sum(rec) / len(rec)
        p_base = sum(base) / len(base)
        if p_base <= 0:
            continue
        subida = (p_rec - p_base) / p_base
        if subida >= cfg.prov_subida_critico:
            sev = "critico"
        elif subida >= cfg.prov_subida_alerta:
            sev = "alerta"
        else:
            continue
        out.append(Alerta(
            regla="problemas_proveedor", severidad=sev,
            fecha=h.isoformat(),
            titulo_es=(f"{prov} subió el precio de {prod} "
                       f"{subida * 100:.0f}%"),
            titulo_en=(f"{prov} raised the price of {prod} by "
                       f"{subida * 100:.0f}%"),
            explicacion_es=(
                f"El precio unitario promedio de {prod} con {prov} pasó "
                f"de {dinero(p_base, moneda)} a {dinero(p_rec, moneda)} "
                f"(subida {subida * 100:.1f}%, {len(rec)} compras "
                f"recientes vs. {len(base)} anteriores)."),
            explicacion_en=(
                f"The average unit price of {prod} from {prov} went "
                f"from {dinero(p_base, moneda)} to "
                f"{dinero(p_rec, moneda)} (up {subida * 100:.1f}%, "
                f"{len(rec)} recent vs. {len(base)} earlier purchases)."),
            dato={"proveedor_id": prov, "producto_id": prod,
                  "moneda": moneda, "subtipo": "subida_precio",
                  "precio_base": f"{p_base:.2f}",
                  "precio_reciente": f"{p_rec:.2f}",
                  "subida_pct": f"{subida * 100:.1f}",
                  "n_compras_recientes": len(rec),
                  "n_compras_base": len(base)},
            accion_es=("Cotiza el producto con otros proveedores y "
                       "renegocia antes de la próxima orden."),
            accion_en=("Get quotes from other suppliers and renegotiate "
                       "before the next order.")))
    return out


def evaluar_extendido(bundle, config: ConfigShieldIA | None = None,
                      fecha=None, eventos=None) -> list[Alerta]:
    """Solo las 2 reglas nuevas del bloque IA (mismo formato Alerta)."""
    cfg = config or ConfigShieldIA()
    h = _hoy(fecha)
    alertas = []
    alertas += _regla_devoluciones_anormales(bundle, cfg, h)
    alertas += _r10_incumplimientos(bundle, cfg, h)
    alertas += _r10_subidas_precio(bundle, cfg, h)
    peso = {"critico": 0, "alerta": 1, "info": 2}
    alertas.sort(key=lambda a: (peso.get(a.severidad, 9), a.regla))
    return alertas


def evaluar_todo(bundle, config: ConfigShieldIA | None = None,
                 fecha=None, eventos=None) -> list[Alerta]:
    """Las 10 reglas: las 8 de shield + las 2 nuevas del bloque IA."""
    cfg = config or ConfigShieldIA()
    h = _hoy(fecha)
    alertas = evaluar(bundle, cfg, hoy=h, eventos=eventos)
    alertas += evaluar_extendido(bundle, cfg, fecha=h)
    peso = {"critico": 0, "alerta": 1, "info": 2}
    alertas.sort(key=lambda a: (peso.get(a.severidad, 9), a.regla,
                                str(sorted(a.dato.items()))))
    return alertas


# ---------------------------------------------------------------------
# Contrato de integración: rutas ia/shield/*
# ---------------------------------------------------------------------
def _eventos_empresa(bundle):
    from webapp.os_api import backend_activo, _pg
    if backend_activo() == "postgres":
        from os_persist.auditoria import leer_eventos
        return leer_eventos(_pg(), bundle.company_id, limite=2000)
    return list(getattr(bundle, "eventos_memoria", []) or [])


def _alertas(bundle, ctx, query):
    company_id = verificar_aislamiento(bundle, ctx)
    h = q1(query, "hoy") or None
    sev = q1(query, "severidad")
    if sev and sev not in SEVERIDADES:
        raise ValueError("severidad inválida: use " +
                         ", ".join(SEVERIDADES))
    regla = q1(query, "regla")
    if regla and regla not in REGLAS_TODAS:
        raise ValueError("regla inválida: use " + ", ".join(REGLAS_TODAS))
    eventos = _eventos_empresa(bundle)
    alertas = evaluar_todo(bundle, ConfigShieldIA(), fecha=h,
                           eventos=eventos)
    if sev:
        alertas = [a for a in alertas if a.severidad == sev]
    if regla:
        alertas = [a for a in alertas if a.regla == regla]
    out = []
    for a in alertas:
        d = a.to_dict()
        d["dato"]["origen_datos"] = "sintetico_demo"
        out.append(d)
    return {"ok": True, "data": {"company_id": company_id,
                                 "n_alertas": len(out),
                                 "reglas": list(REGLAS_TODAS),
                                 "alertas": out}}


def _resumen(bundle, ctx, query):
    r = _alertas(bundle, ctx, query)["data"]
    por_sev: dict[str, int] = {}
    por_regla: dict[str, int] = {}
    for a in r["alertas"]:
        por_sev[a["severidad"]] = por_sev.get(a["severidad"], 0) + 1
        por_regla[a["regla"]] = por_regla.get(a["regla"], 0) + 1
    return {"ok": True, "data": {"company_id": r["company_id"],
                                 "n_alertas": r["n_alertas"],
                                 "por_severidad": por_sev,
                                 "por_regla": por_regla}}


def rutas():
    """Contrato ia/shield: handlers (bundle, ctx, query) -> dict."""
    return {"GET ia/shield/alertas": _alertas,
            "GET ia/shield/resumen": _resumen}
