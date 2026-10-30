"""BLOQUE B (2026-10-13) — Reglas que convierten hallazgos en
notificaciones.

Fuentes reutilizadas (no se reconstruye lo que ya funciona):
- shield/reglas.py: `evaluar(bundle, ...)` -> list[Alerta]. Cada
  Alerta se convierte en notificación con tipo f"shield.{regla}",
  prioridad = severidad y evidencia sanitizada.
- os_ia/decisiones.py: decisiones en estado 'pendiente' -> tipo
  "decisiones.nueva".
- Reglas locales puras de finanzas/inventario (entradas en formato
  dict, sin acoplarse a los motores): presupuesto sobreejecutado y
  cashflow negativo.

Todas las funciones aquí son PURAS (sin E/S): devuelven dicts listos
para `almacen.CentroNotificaciones.publicar()`, que hace el dedup
por huella y la inserción. El contenido ya sale sanitizado de aquí.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .modelo import PRIORIDADES, tipo_shield  # noqa: E402
from .sanitizar import sanitizar  # noqa: E402

_MAX_RESUMEN = 280


def _huella(tipo: str, fecha: str, dato: dict) -> str:
    """Identidad del hallazgo: misma regla + mismos datos del mismo
    día = la misma notificación (no se repite en cada pasada)."""
    canon = json.dumps(dato or {}, sort_keys=True, ensure_ascii=False,
                       default=str)
    return hashlib.sha256(
        f"{tipo}|{fecha}|{canon}".encode("utf-8")).hexdigest()


def _resumen(texto: str) -> str:
    t = " ".join((texto or "").split())
    return t if len(t) <= _MAX_RESUMEN else t[:_MAX_RESUMEN - 1] + "…"


def notificacion_desde_dict(*, tipo: str, prioridad: str, titulo: str,
                            resumen: str, fuente: str, regla: str,
                            fecha: str, dato: dict | None = None) -> dict:
    """Constructor canónico: sanitiza y calcula la huella."""
    if prioridad not in PRIORIDADES:
        raise ValueError(f"prioridad inválida: {prioridad!r}")
    dato_s = sanitizar(dato or {})
    return {"tipo": tipo, "prioridad": prioridad, "titulo": titulo,
            "resumen": _resumen(resumen), "fuente": fuente, "regla": regla,
            "huella": _huella(tipo, fecha or "", dato_s),
            "payload": dato_s}


# ---------------------------------------------------------------------
# Fuente: Shield (audité shield/reglas.py — REGLAS y severidades)
# ---------------------------------------------------------------------
def desde_shield(alertas: list, fecha: str = "") -> list[dict]:
    """Convierte alertas de shield (objetos Alerta o dicts) a
    notificaciones. Acepta dicts con las mismas claves para no
    acoplarse a la clase."""
    out = []
    for a in alertas or []:
        if hasattr(a, "to_dict"):
            a = a.to_dict()
        regla = str(a.get("regla") or "")
        sev = str(a.get("severidad") or "info")
        if sev not in PRIORIDADES:
            sev = "info"
        out.append(notificacion_desde_dict(
            tipo=tipo_shield(regla),
            prioridad=sev,
            titulo=str(a.get("titulo_es") or ""),
            resumen=str(a.get("explicacion_es") or ""),
            fuente="shield",
            regla=regla,
            fecha=fecha or str(a.get("fecha") or ""),
            dato=a.get("dato") or {}))
    return out


# ---------------------------------------------------------------------
# Fuente: os_ia/decisiones.py (audité el Centro de Decisiones)
# ---------------------------------------------------------------------
def desde_decisiones(decisiones: list, fecha: str = "") -> list[dict]:
    """Decisiones en estado 'pendiente' -> notificaciones de prioridad
    'alerta'. La evidencia se sanitiza (puede traer datos del
    negocio)."""
    out = []
    for d in decisiones or []:
        if hasattr(d, "to_dict"):
            d = d.to_dict()
        if str(d.get("estado") or "") != "pendiente":
            continue
        did = str(d.get("id") or "")
        out.append(notificacion_desde_dict(
            tipo="decisiones.nueva",
            prioridad="alerta",
            titulo=f"Decisión pendiente: {d.get('problema') or did}",
            resumen=(f"Fuente: {d.get('fuente') or ''}. "
                     f"Acción sugerida: "
                     f"{d.get('accion_recomendada') or ''}"),
            fuente="decisiones",
            regla="decisiones.nueva",
            fecha=fecha,
            dato={"decision_id": did,
                  "fuente_origen": d.get("fuente") or "",
                  "responsable": d.get("responsable_sugerido") or "",
                  "evidencia": d.get("evidencia") or {}}))
    return out


# ---------------------------------------------------------------------
# Reglas locales puras: finanzas / inventario
# (entradas dict; no dependen de los motores)
# ---------------------------------------------------------------------
def regla_presupuesto(ejecuciones: list, fecha: str = "") -> list[dict]:
    """ejecuciones: [{categoria, periodo, limite, consumido, moneda}].

    >100 % del límite -> critico; >=90 % -> alerta; menos -> silencio.
    Montos se muestran en el título/resumen (son agregados de la
    empresa, no datos sensibles de terceros).
    """
    out = []
    for e in ejecuciones or []:
        try:
            limite = float(e.get("limite") or 0)
            consumido = float(e.get("consumido") or 0)
        except (TypeError, ValueError):
            continue
        if limite <= 0:
            continue
        pct = consumido / limite
        if pct < 0.90:
            continue
        sev = "critico" if pct > 1.0 else "alerta"
        cat = str(e.get("categoria") or "")
        per = str(e.get("periodo") or "")
        mon = str(e.get("moneda") or "")
        out.append(notificacion_desde_dict(
            tipo="finanzas.presupuesto_sobreejecutado",
            prioridad=sev,
            titulo=(f"Presupuesto {cat} {per}: "
                    f"{pct * 100:.0f}% consumido"),
            resumen=(f"La categoría {cat} del período {per} consumió "
                     f"{consumido:,.2f} {mon} de {limite:,.2f} {mon} "
                     f"presupuestados ({pct * 100:.0f}%)."),
            fuente="finanzas",
            regla="presupuesto_sobreejecutado",
            fecha=fecha,
            dato={"categoria": cat, "periodo": per, "moneda": mon,
                  "limite": str(e.get("limite")),
                  "consumido": str(e.get("consumido")),
                  "pct": f"{pct:.3f}"}))
    return out


def regla_cashflow(proyeccion: dict, fecha: str = "") -> list[dict]:
    """proyeccion: {saldo_minimo, moneda, dia_minimo, dias}.

    Saldo mínimo proyectado < 0 -> critico (posible iliquidez).
    """
    if not proyeccion:
        return []
    try:
        minimo = float(proyeccion.get("saldo_minimo"))
    except (TypeError, ValueError):
        return []
    if minimo >= 0:
        return []
    mon = str(proyeccion.get("moneda") or "")
    return [notificacion_desde_dict(
        tipo="finanzas.cashflow_negativo",
        prioridad="critico",
        titulo=(f"Cashflow: saldo mínimo proyectado "
                f"{minimo:,.2f} {mon}"),
        resumen=(f"La proyección de caja muestra un saldo mínimo de "
                 f"{minimo:,.2f} {mon} "
                 f"(día {proyeccion.get('dia_minimo') or '?'}). "
                 f"Riesgo de iliquidez: revise cobros y pagos."),
        fuente="finanzas",
        regla="cashflow_negativo",
        fecha=fecha,
        dato={"saldo_minimo": str(proyeccion.get("saldo_minimo")),
              "moneda": mon,
              "dia_minimo": str(proyeccion.get("dia_minimo") or ""),
              "dias_proyectados": str(
                  proyeccion.get("dias") or "")})]


def regla_stock_cero(stocks: list, fecha: str = "") -> list[dict]:
    """stocks: [{producto_id, almacen_id, stock}]. stock <= 0 ->
    critico (quiebre). Complementa a shield.stock_critico con datos
    directos del módulo de inventario."""
    out = []
    for s in stocks or []:
        try:
            st = float(s.get("stock"))
        except (TypeError, ValueError):
            continue
        if st > 0:
            continue
        prod = str(s.get("producto_id") or "")
        alm = str(s.get("almacen_id") or "")
        out.append(notificacion_desde_dict(
            tipo="inventario.stock_cero",
            prioridad="critico",
            titulo=f"Quiebre de stock: {prod} en {alm}",
            resumen=(f"El producto {prod} no tiene unidades disponibles "
                     f"en el almacén {alm} (stock {st:g})."),
            fuente="inventario",
            regla="stock_cero",
            fecha=fecha,
            dato={"producto_id": prod, "almacen_id": alm,
                  "stock": str(s.get("stock"))}))
    return out


# ---------------------------------------------------------------------
# Orquestación de fuentes
# ---------------------------------------------------------------------
def evaluar_fuentes(*, alertas_shield: list | None = None,
                    decisiones: list | None = None,
                    ejecuciones_presupuesto: list | None = None,
                    proyeccion_cashflow: dict | None = None,
                    stocks: list | None = None,
                    fecha: str = "") -> list[dict]:
    """Une todas las fuentes en una sola lista ordenada por
    prioridad (critico primero)."""
    notifs = []
    notifs += desde_shield(alertas_shield or [], fecha)
    notifs += desde_decisiones(decisiones or [], fecha)
    notifs += regla_presupuesto(ejecuciones_presupuesto or [], fecha)
    notifs += regla_cashflow(proyeccion_cashflow or {}, fecha)
    notifs += regla_stock_cero(stocks or [], fecha)
    peso = {"critico": 0, "alerta": 1, "info": 2}
    notifs.sort(key=lambda n: (peso.get(n["prioridad"], 9), n["tipo"]))
    return notifs
