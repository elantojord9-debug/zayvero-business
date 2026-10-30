"""BLOQUE IA · 7 — Advisor.

Integra los asesores existentes del núcleo OS (price_advisor y
simulator —los únicos conectados al OSBundle; el advisor legacy de
`advisor/` trabaja sobre parquet y no está integrado al OS, ver
docs/bloque-ia.md) en recomendaciones unificadas con:

- evidencia: los números concretos del motor que la respalda;
- impacto_posible: monto estimado + base del cálculo;
- prioridad: alta / media / baja según el impacto (umbrales
  documentados abajo);
- SEGUIMIENTO: ¿se ejecutó la recomendación? ¿qué pasó después?
  (`marcar_ejecutada` + `registrar_resultado` compara antes/después
  con datos reales del sistema; sin datos, el resultado dice
  "evidencia_insuficiente", nunca inventa un efecto).

El Advisor no ejecuta nada: solo recomienda y da seguimiento.

Umbrales de prioridad (DOP; documentados, no mágicos):
  alta:  impacto >= 5000   (mueve la aguja del mes de una PYME)
  media: impacto >= 1000
  baja:  impacto < 1000
"""
from __future__ import annotations

import os as _os
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402

from .base import dia, hoy as _hoy_fn, q1, verificar_aislamiento  # noqa: E402

TIPOS = ("precio", "quiebre", "rentabilidad")
PRIORIDADES = ("alta", "media", "baja")
ESTADOS = ("pendiente", "ejecutada", "descartada")

UMBRAL_ALTA = Decimal("5000")
UMBRAL_MEDIA = Decimal("1000")


class ErrorAdvisorIA(Exception):
    """Error de dominio del Advisor del bloque IA."""


def _prioridad(impacto: Decimal) -> str:
    if impacto >= UMBRAL_ALTA:
        return "alta"
    if impacto >= UMBRAL_MEDIA:
        return "media"
    return "baja"


@dataclass
class Recomendacion:
    id: str
    tipo: str
    titulo_es: str
    titulo_en: str
    causa_probable_es: str = ""
    causa_probable_en: str = ""
    evidencia: dict = field(default_factory=dict)
    impacto_posible: dict | None = None   # {monto, moneda, base}
    prioridad: str = "baja"
    accion_recomendada_es: str = ""
    accion_recomendada_en: str = ""
    estado: str = "pendiente"
    creada_at: str = ""
    ejecutada_at: str = ""
    ejecutada_por: str = ""
    resultado: dict | None = None  # seguimiento: qué pasó después

    def to_dict(self) -> dict:
        return {"id": self.id, "tipo": self.tipo,
                "titulo_es": self.titulo_es, "titulo_en": self.titulo_en,
                "causa_probable_es": self.causa_probable_es,
                "causa_probable_en": self.causa_probable_en,
                "evidencia": self.evidencia,
                "impacto_posible": self.impacto_posible,
                "prioridad": self.prioridad,
                "accion_recomendada_es": self.accion_recomendada_es,
                "accion_recomendada_en": self.accion_recomendada_en,
                "estado": self.estado, "creada_at": self.creada_at,
                "ejecutada_at": self.ejecutada_at,
                "ejecutada_por": self.ejecutada_por,
                "resultado": self.resultado}


_LOCK = threading.RLock()
_STORE: dict[str, dict[str, Recomendacion]] = {}


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _store(company_id: str) -> dict[str, Recomendacion]:
    with _LOCK:
        return _STORE.setdefault(company_id, {})


def _guardar(company_id: str, r: Recomendacion) -> Recomendacion:
    with _LOCK:
        store = _store(company_id)
        if r.id in store:
            return store[r.id]
        store[r.id] = r
        return r


# ---------------------------------------------------------------------
# Generación (solo lectura de los motores existentes)
# ---------------------------------------------------------------------
def _reco_precio(bundle, h) -> list[Recomendacion]:
    from price_advisor.engine import AsesorPrecios
    out = []
    try:
        data = AsesorPrecios(bundle).recomendar(moneda="DOP",
                                                hoy=h.isoformat(),
                                                lang="es")
    except Exception:
        return []
    for it in data.get("resultados", []):
        if it.get("estado") != "recomendado":
            continue
        if it.get("veredicto") not in ("subir", "bajar"):
            continue
        res = it.get("resultados", {})
        ins = it.get("insumos", {})
        pid, alm = it["producto_id"], it["almacen_id"]
        brecha = abs(_dec(res.get("brecha_precio") or "0"))
        demanda = _dec(ins.get("demanda_diaria_uds") or "0")
        impacto = brecha * demanda * 30
        verbo = "Subir" if it["veredicto"] == "subir" else "Bajar"
        out.append(Recomendacion(
            id=f"adv:precio:{pid}:{alm}", tipo="precio",
            titulo_es=(f"{verbo} el precio de {pid} a "
                       f"{res.get('precio_recomendado')} DOP"),
            titulo_en=(f"{'Raise' if it['veredicto'] == 'subir' else 'Lower'} "
                       f"the price of {pid} to "
                       f"{res.get('precio_recomendado')} DOP"),
            causa_probable_es=it.get("explicacion_es", ""),
            causa_probable_en=it.get("explicacion_en", ""),
            evidencia={
                "producto_id": pid, "almacen_id": alm, "moneda": "DOP",
                "precio_actual": ins.get("precio_actual_referencia"),
                "precio_recomendado": res.get("precio_recomendado"),
                "brecha": res.get("brecha_precio"),
                "margen_actual_pct": res.get("margen_actual_pct"),
                "costo_promedio": ins.get("costo_promedio_usado"),
                "demanda_diaria_uds": ins.get("demanda_diaria_uds"),
                "fuente_costo": ins.get("fuente_costo")},
            impacto_posible={
                "monto": f"{impacto:.2f}", "moneda": "DOP",
                "base": ("|brecha de precio| × demanda diaria × 30 días; "
                         "supone demanda constante (sin elasticidad), "
                         "igual que el Price Advisor")},
            prioridad=_prioridad(impacto),
            accion_recomendada_es=(
                "Aprobar el cambio de precio (si supera el 25% pasa por "
                "aprobación humana) y observar ventas 7 días."),
            accion_recomendada_en=(
                "Approve the price change (over 25% requires human "
                "approval) and watch sales for 7 days."),
            creada_at=_ahora_iso()))
    return out


def _reco_quiebre(bundle, h) -> list[Recomendacion]:
    from simulator.engine import Simulador
    out = []
    try:
        data = Simulador(bundle).simular_quiebre(
            moneda="DOP", hoy=h.isoformat())
    except Exception:
        return []
    for it in data.get("resultados", {}).get("productos", []):
        est = it.get("resultados", {}).get("estado")
        if est not in ("agotado", "en_riesgo"):
            continue
        pid, alm = it["producto_id"], it["almacen_id"]
        ins, res = it.get("insumos", {}), it.get("resultados", {})
        sug = res.get("compra_sugerida") or {}
        impacto = _dec(sug.get("inversion_estimada") or "0")
        out.append(Recomendacion(
            id=f"adv:quiebre:{pid}:{alm}", tipo="quiebre",
            titulo_es=(f"Comprar {sug.get('cantidad', '?')} uds de {pid} "
                       f"antes del quiebre"),
            titulo_en=(f"Buy {sug.get('cantidad', '?')} units of {pid} "
                       f"before the stockout"),
            causa_probable_es=(
                f"Al ritmo actual ({ins.get('demanda_diaria_uds')} "
                f"uds/día), el stock de {ins.get('stock_actual')} uds se "
                f"agota en {res.get('dias_para_quiebre')} días."),
            causa_probable_en=(
                f"At the current pace ({ins.get('demanda_diaria_uds')} "
                f"units/day), the stock of {ins.get('stock_actual')} "
                f"units runs out in {res.get('dias_para_quiebre')} days."),
            evidencia={
                "producto_id": pid, "almacen_id": alm,
                "stock_actual": ins.get("stock_actual"),
                "demanda_diaria_uds": ins.get("demanda_diaria_uds"),
                "dias_para_quiebre": res.get("dias_para_quiebre"),
                "estado": est,
                "compra_sugerida": sug},
            impacto_posible={
                "monto": f"{impacto:.2f}", "moneda": "DOP",
                "base": ("inversión estimada de la compra sugerida "
                         "(cantidad × costo promedio); evita el quiebre, "
                         "no es ganancia")},
            prioridad="alta" if est == "agotado" else "media",
            accion_recomendada_es=(
                "Generar la orden de compra sugerida y dar seguimiento "
                "a la fecha de entrega."),
            accion_recomendada_en=(
                "Create the suggested purchase order and track the "
                "delivery date."),
            creada_at=_ahora_iso()))
    return out


def _reco_rentabilidad(bundle, h) -> list[Recomendacion]:
    from simulator.engine import Simulador
    out = []
    try:
        data = Simulador(bundle).rentabilidad(moneda="DOP")
    except Exception:
        return []
    for it in data.get("resultados", {}).get("productos", []):
        res = it.get("resultados", {})
        margen = _dec(res.get("margen_bruto") or "0")
        if margen >= 0:
            continue
        pid = it["producto_id"]
        impacto = abs(margen)
        out.append(Recomendacion(
            id=f"adv:rentabilidad:{pid}", tipo="rentabilidad",
            titulo_es=(f"{pid} se vende con pérdida "
                       f"({margen} DOP de margen bruto)"),
            titulo_en=(f"{pid} sells at a loss "
                       f"({margen} DOP gross margin)"),
            causa_probable_es=(
                f"Ingresos {res.get('ingresos')} vs. COGS "
                f"{res.get('cogs')}: el costo supera lo facturado."),
            causa_probable_en=(
                f"Revenue {res.get('ingresos')} vs. COGS "
                f"{res.get('cogs')}: cost exceeds revenue."),
            evidencia={"producto_id": pid,
                        "ingresos": res.get("ingresos"),
                        "cogs": res.get("cogs"),
                        "margen_bruto": res.get("margen_bruto"),
                        "margen_bruto_pct": res.get("margen_bruto_pct")},
            impacto_posible={
                "monto": f"{impacto:.2f}", "moneda": "DOP",
                "base": ("pérdida bruta acumulada en el período; "
                         "corregir precio o costo la elimina")},
            prioridad=_prioridad(impacto),
            accion_recomendada_es=(
                "Revisar costo y precio con el Price Advisor; si no hay "
                "margen posible, descontinuar."),
            accion_recomendada_en=(
                "Review cost and price with the Price Advisor; if no "
                "margin is possible, discontinue."),
            creada_at=_ahora_iso()))
    return out


def recomendar(bundle, fecha=None) -> dict:
    """Recomendaciones unificadas (idempotentes por id)."""
    h = _hoy_fn(fecha)
    recos = []
    recos += _reco_precio(bundle, h)
    recos += _reco_quiebre(bundle, h)
    recos += _reco_rentabilidad(bundle, h)
    guardadas = [_guardar(bundle.company_id, r) for r in recos]
    peso = {"alta": 0, "media": 1, "baja": 2}
    guardadas.sort(key=lambda r: (peso.get(r.prioridad, 9),
                                  -float((r.impacto_posible or {})
                                         .get("monto", "0")),
                                  r.id))
    return {"company_id": bundle.company_id, "hoy": h.isoformat(),
            "n_recomendaciones": len(guardadas),
            "recomendaciones": [r.to_dict() for r in guardadas]}


# ---------------------------------------------------------------------
# Seguimiento: ¿se ejecutó? ¿qué pasó después?
# ---------------------------------------------------------------------
def listar(company_id: str, estado: str | None = None,
           tipo: str | None = None,
           prioridad: str | None = None) -> list[Recomendacion]:
    if estado is not None and estado not in ESTADOS:
        raise ErrorAdvisorIA(f"estado inválido: {estado}")
    if tipo is not None and tipo not in TIPOS:
        raise ErrorAdvisorIA(f"tipo inválido: {tipo}")
    if prioridad is not None and prioridad not in PRIORIDADES:
        raise ErrorAdvisorIA(f"prioridad inválida: {prioridad}")
    with _LOCK:
        rs = list(_store(company_id).values())
    if estado:
        rs = [r for r in rs if r.estado == estado]
    if tipo:
        rs = [r for r in rs if r.tipo == tipo]
    if prioridad:
        rs = [r for r in rs if r.prioridad == prioridad]
    return sorted(rs, key=lambda r: r.creada_at)


def marcar_ejecutada(company_id: str, reco_id: str,
                     usuario: str) -> Recomendacion:
    """Registra que un humano ejecutó la recomendación (no la ejecuta)."""
    if not usuario:
        raise ErrorAdvisorIA("usuario es obligatorio")
    with _LOCK:
        r = _store(company_id).get(reco_id)
        if r is None:
            raise ErrorAdvisorIA(f"recomendación inexistente: {reco_id}")
        if r.estado != "pendiente":
            raise ErrorAdvisorIA(f"la recomendación ya está {r.estado}")
        r.estado = "ejecutada"
        r.ejecutada_at = _ahora_iso()
        r.ejecutada_por = usuario
        return r


def descartar(company_id: str, reco_id: str, usuario: str,
              motivo: str = "") -> Recomendacion:
    with _LOCK:
        r = _store(company_id).get(reco_id)
        if r is None:
            raise ErrorAdvisorIA(f"recomendación inexistente: {reco_id}")
        if r.estado != "pendiente":
            raise ErrorAdvisorIA(f"la recomendación ya está {r.estado}")
        r.estado = "descartada"
        r.ejecutada_por = usuario
        r.resultado = {"conclusion": "descartada", "motivo": motivo}
        return r


def _ingreso_diario(bundle, producto_id: str, desde, hasta) -> \
        tuple[Decimal, int]:
    """(ingreso total, días con venta) del producto en el rango."""
    total, dias = Decimal("0"), set()
    for reg in bundle.ventas._ventas.values():  # coord. persistencia
        if reg["estado"] != "confirmada":
            continue
        d = dia(reg["solicitud"].fecha)
        if d is None or d < desde or d > hasta:
            continue
        for lin in reg["solicitud"].lineas or []:
            if lin.get("producto_id") == producto_id:
                total += (abs(_dec(lin.get("cantidad", "0"))) *
                          _dec(lin.get("precio_unitario", "0")))
                dias.add(d)
    return total, len(dias)


def registrar_resultado(company_id: str, reco_id: str, bundle,
                        fecha=None) -> Recomendacion:
    """Compara antes/después con datos reales del sistema.

    - precio: ingreso diario promedio 7d antes vs. 7d después de la
      ejecución. Sin 7 días posteriores con datos: evidencia_insuficiente.
    - quiebre: stock al recomendar vs. stock actual.
    - rentabilidad: margen bruto al recomendar vs. margen actual.
    Sin datos suficientes el resultado lo dice; nunca inventa efectos.
    """
    h = _hoy_fn(fecha)
    with _LOCK:
        r = _store(company_id).get(reco_id)
        if r is None:
            raise ErrorAdvisorIA(f"recomendación inexistente: {reco_id}")
        if r.estado != "ejecutada":
            raise ErrorAdvisorIA(
                "solo se mide resultado de recomendaciones ejecutadas")
        ev = r.evidencia
        if r.tipo == "precio":
            pid = ev.get("producto_id")
            ejec = dia(r.ejecutada_at)
            ini_a, fin_a = ejec - timedelta(days=7), ejec - timedelta(days=1)
            ini_d, fin_d = ejec, ejec + timedelta(days=6)
            tot_a, _ = _ingreso_diario(bundle, pid, ini_a, fin_a)
            tot_d, n_d = _ingreso_diario(bundle, pid, ini_d,
                                         min(fin_d, h))
            if n_d == 0 or fin_d > h:
                r.resultado = {
                    "conclusion": "evidencia_insuficiente",
                    "nota": ("aún no hay 7 días de ventas posteriores a "
                             "la ejecución; no se puede medir el efecto")}
            else:
                prom_a, prom_d = tot_a / 7, tot_d / 7
                var = ((prom_d - prom_a) / prom_a * 100
                       if prom_a > 0 else None)
                r.resultado = {
                    "metrica": "ingreso_diario_promedio",
                    "antes": f"{prom_a:.2f}", "despues": f"{prom_d:.2f}",
                    "moneda": ev.get("moneda", "DOP"),
                    "variacion_pct": (f"{var:.1f}" if var is not None
                                      else None),
                    "conclusion": ("mejora" if var is not None and var > 0
                                   else "sin_mejora" if var is not None
                                   else "sin_base_previa")}
        elif r.tipo == "quiebre":
            pid, alm = ev.get("producto_id"), ev.get("almacen_id")
            antes = _dec(ev.get("stock_actual") or "0")
            ahora = bundle.inventario.stock_empresa().get(
                (pid, alm), Decimal("0"))
            r.resultado = {
                "metrica": "stock_actual",
                "antes": str(antes), "despues": str(ahora),
                "conclusion": ("reabastecido" if ahora > antes
                               else "sin_cambio")}
        elif r.tipo == "rentabilidad":
            from simulator.engine import Simulador
            pid = ev.get("producto_id")
            data = Simulador(bundle).rentabilidad(moneda="DOP")
            actual = None
            for it in data.get("resultados", {}).get("productos", []):
                if it.get("producto_id") == pid:
                    actual = it.get("resultados", {}).get("margen_bruto")
            antes = _dec(ev.get("margen_bruto") or "0")
            desp = _dec(actual or "0")
            r.resultado = {
                "metrica": "margen_bruto",
                "antes": str(antes), "despues": str(desp),
                "moneda": "DOP",
                "conclusion": ("mejora" if desp > antes else "sin_mejora")}
        else:
            r.resultado = {"conclusion": "evidencia_insuficiente",
                           "nota": "tipo sin medición automática"}
        return r


# ---------------------------------------------------------------------
# Contrato de integración: rutas ia/advisor/*
# ---------------------------------------------------------------------
def _recomendaciones(bundle, ctx, query):
    company_id = verificar_aislamiento(bundle, ctx)
    data = recomendar(bundle, fecha=q1(query, "hoy") or None)
    rs = data["recomendaciones"]
    prio = q1(query, "prioridad")
    tipo = q1(query, "tipo")
    estado = q1(query, "estado")
    if prio and prio not in PRIORIDADES:
        raise ValueError("prioridad inválida")
    if tipo and tipo not in TIPOS:
        raise ValueError("tipo inválido")
    if estado and estado not in ESTADOS:
        raise ValueError("estado inválido")
    if prio:
        rs = [r for r in rs if r["prioridad"] == prio]
    if tipo:
        rs = [r for r in rs if r["tipo"] == tipo]
    if estado:
        rs = [r for r in rs if r["estado"] == estado]
    data["recomendaciones"] = rs
    data["n_recomendaciones"] = len(rs)
    data["company_id"] = company_id
    return {"ok": True, "data": data}


def _ejecutada(bundle, ctx, body):
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    for campo in ("reco_id", "usuario"):
        if not body.get(campo):
            raise ValueError(f"{campo} es obligatorio")
    try:
        r = marcar_ejecutada(company_id, body["reco_id"], body["usuario"])
    except ErrorAdvisorIA as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"recomendacion": r.to_dict(),
                                 "nota": ("ejecución registrada como "
                                          "hecha por un humano; el "
                                          "Advisor no ejecuta")}}


def _descartar(bundle, ctx, body):
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    if not body.get("reco_id"):
        raise ValueError("reco_id es obligatorio")
    try:
        r = descartar(company_id, body["reco_id"],
                      body.get("usuario", ""), body.get("motivo", ""))
    except ErrorAdvisorIA as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"recomendacion": r.to_dict()}}


def _resultado(bundle, ctx, body):
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    if not body.get("reco_id"):
        raise ValueError("reco_id es obligatorio")
    try:
        r = registrar_resultado(company_id, body["reco_id"], bundle,
                                fecha=body.get("hoy"))
    except ErrorAdvisorIA as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"recomendacion": r.to_dict()}}


def rutas():
    """Contrato ia/advisor.

    GET  ia/advisor/recomendaciones  (bundle, ctx, query)
    POST ia/advisor/ejecutada        (bundle, ctx, body)
    POST ia/advisor/descartar        (bundle, ctx, body)
    POST ia/advisor/resultado        (bundle, ctx, body)
    """
    return {"GET ia/advisor/recomendaciones": _recomendaciones,
            "POST ia/advisor/ejecutada": _ejecutada,
            "POST ia/advisor/descartar": _descartar,
            "POST ia/advisor/resultado": _resultado}
