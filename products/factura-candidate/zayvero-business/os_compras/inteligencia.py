"""Supplier Intelligence vía API: lo que existe, expuesto.

Construye sobre `os_compras.proveedores.ficha_proveedor` (motor ya
operativo) y agrega:

- Precios históricos por (proveedor, producto): serie temporal de
  precio_unitario por orden.
- Plazos reales vs prometidos: por entrega, dias_retraso =
  ultima_recepcion - fecha_entrega_esperada (solo si ambos existen;
  sin fechas no se inventa).
- Incumplimientos: entregas tardías, órdenes incompletas (faltante
  recibido vs ordenado), productos facturados sin orden (vía cruce).
- Evaluación ponderada del proveedor: score 0-100 =
  50% tasa_cumplimiento + 30% puntualidad + 20% competitividad de precio.
  Solo con muestra suficiente (min_ordenes, default 3); si no,
  `evaluacion="datos_insuficientes"` — nunca se inventa un score.

Ponderaciones configurables por parámetro; documentadas en la salida.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from .ordenes import OrdenCompra, EstadoOC  # noqa: E402
from .recepciones import Recepcion  # noqa: E402
from .proveedores import ficha_proveedor  # noqa: E402


def _parse(f: str) -> datetime:
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc)


def listar_proveedores(ordenes: list[OrdenCompra],
                       company_id: str) -> list[str]:
    return sorted({o.proveedor_id for o in ordenes
                   if o.company_id == company_id})


def precios_historicos(ordenes: list[OrdenCompra], proveedor_id: str,
                       producto_id: str | None = None) -> list[dict]:
    """Serie de precios pactados por orden (fecha_emision, precio, OC)."""
    serie = []
    for o in sorted(ordenes, key=lambda x: (x.fecha_emision or "",
                                           x.compra_id)):
        if o.proveedor_id != proveedor_id:
            continue
        for lin in o.lineas:
            if producto_id is not None and lin.producto_id != producto_id:
                continue
            serie.append({
                "fecha": o.fecha_emision, "compra_id": o.compra_id,
                "producto_id": lin.producto_id,
                "precio_unitario": str(_dec(lin.precio_unitario)),
                "cantidad": str(_dec(lin.cantidad)),
            })
    return serie


def entregas_vs_prometido(ordenes: list[OrdenCompra],
                          recepciones: list[Recepcion],
                          proveedor_id: str) -> list[dict]:
    """Por orden: prometido vs real (días de retraso), con evidencia."""
    rec_por_orden: dict[str, list[Recepcion]] = {}
    for r in recepciones:
        rec_por_orden.setdefault(r.compra_id, []).append(r)
    out = []
    for o in sorted(ordenes, key=lambda x: (x.fecha_emision or "",
                                            x.compra_id)):
        if o.proveedor_id != proveedor_id:
            continue
        recs = rec_por_orden.get(o.compra_id, [])
        item = {"compra_id": o.compra_id,
                "fecha_entrega_esperada": o.fecha_entrega_esperada,
                "estado": o.estado, "recepciones": len(recs),
                "dias_retraso": None, "a_tiempo": None}
        if o.fecha_entrega_esperada and recs:
            fechas = [r.fecha for r in recs if r.fecha]
            if fechas:
                ultima = max(fechas)
                delta = (_parse(ultima) - _parse(
                    o.fecha_entrega_esperada)).total_seconds() / 86400
                item["dias_retraso"] = round(delta, 1)
                item["a_tiempo"] = delta <= 0
                item["ultima_recepcion"] = ultima
        out.append(item)
    return out


def incumplimientos(ordenes: list[OrdenCompra],
                    recepciones: list[Recepcion], proveedor_id: str,
                    hoy: str | None = None) -> list[dict]:
    """Entregas tardías, incompletas y órdenes vencidas sin cerrar."""
    ahora = _parse(hoy) if hoy else datetime.now(timezone.utc)
    rec_por_orden: dict[str, list[Recepcion]] = {}
    for r in recepciones:
        rec_por_orden.setdefault(r.compra_id, []).append(r)
    out = []
    for o in ordenes:
        if o.proveedor_id != proveedor_id:
            continue
        recs = rec_por_orden.get(o.compra_id, [])
        recibido: dict[str, Decimal] = {}
        for r in recs:
            for l in r.lineas:
                recibido[l.producto_id] = recibido.get(
                    l.producto_id, Decimal("0")) + _dec(l.cantidad)
        # Incompleta: algún producto con recibido < ordenado
        faltantes = [
            {"producto_id": lin.producto_id,
             "ordenado": str(_dec(lin.cantidad)),
             "recibido": str(recibido.get(lin.producto_id, Decimal("0")))}
            for lin in o.lineas
            if recibido.get(lin.producto_id, Decimal("0")) < _dec(lin.cantidad)
        ]
        if faltantes and o.estado not in (EstadoOC.CANCELADA,):
            out.append({"tipo": "entrega_incompleta", "severidad": "media",
                        "compra_id": o.compra_id, "estado": o.estado,
                        "faltantes": faltantes})
        # Tardía
        if o.fecha_entrega_esperada and recs:
            fechas = [r.fecha for r in recs if r.fecha]
            if fechas:
                delta = (_parse(max(fechas)) - _parse(
                    o.fecha_entrega_esperada)).total_seconds() / 86400
                if delta > 0:
                    out.append({"tipo": "entrega_tardia",
                                "severidad": "alta" if delta > 7 else "media",
                                "compra_id": o.compra_id,
                                "dias_retraso": round(delta, 1),
                                "esperada": o.fecha_entrega_esperada,
                                "ultima_recepcion": max(fechas)})
        # Vencida sin cerrar: esperada pasada y orden aún abierta
        if (o.fecha_entrega_esperada
                and o.estado in (EstadoOC.ENVIADA, EstadoOC.PARCIAL)
                and _parse(o.fecha_entrega_esperada) < ahora):
            dias = (ahora - _parse(
                o.fecha_entrega_esperada)).total_seconds() / 86400
            out.append({"tipo": "orden_vencida_abierta",
                        "severidad": "alta", "compra_id": o.compra_id,
                        "dias_vencida": round(dias, 1)})
    return out


def evaluar_proveedor(proveedor_id: str, company_id: str,
                      ordenes: list[OrdenCompra],
                      recepciones: list[Recepcion],
                      min_ordenes: int = 3,
                      pesos: dict | None = None) -> dict:
    """Evaluación ponderada 0-100 con muestra suficiente.

    pesos: {"cumplimiento": .5, "puntualidad": .3, "precio": .2}
    - cumplimiento: tasa_cumplimiento de la ficha (0-1).
    - puntualidad: 1 / (1 + retraso_promedio_dias).
    - precio: precio mínimo histórico del proveedor vs su promedio por
      producto, promediado (1 = siempre al mínimo).
    Sin muestra suficiente -> datos_insuficientes, sin score inventado.
    """
    pesos = pesos or {"cumplimiento": 0.5, "puntualidad": 0.3,
                      "precio": 0.2}
    ficha = ficha_proveedor(proveedor_id, company_id, ordenes, recepciones)
    if ficha.total_ordenes < min_ordenes:
        return {"proveedor_id": proveedor_id, "company_id": company_id,
                "evaluacion": "datos_insuficientes",
                "ordenes_observadas": ficha.total_ordenes,
                "min_ordenes": min_ordenes,
                "mensaje": f"solo {ficha.total_ordenes} órdenes; se "
                           f"requieren {min_ordenes} para evaluar",
                "ficha": ficha.to_dict()}
    cumplimiento = float(ficha.tasa_cumplimiento or 0)
    retraso = float(ficha.retraso_promedio_dias or 0)
    puntualidad = 1 / (1 + retraso)
    # Competitividad de precio por producto
    comps = []
    for p in ficha.productos:
        mn, prom = _dec(p["precio_min"]), _dec(p["precio_promedio"])
        comps.append(float(mn / prom) if prom > 0 else 0.0)
    precio = sum(comps) / len(comps) if comps else 0.0
    score = (pesos["cumplimiento"] * cumplimiento
             + pesos["puntualidad"] * puntualidad
             + pesos["precio"] * precio) * 100
    if score >= 80:
        veredicto = "preferido"
    elif score >= 60:
        veredicto = "aceptable"
    elif score >= 40:
        veredicto = "en_observacion"
    else:
        veredicto = "riesgoso"
    return {"proveedor_id": proveedor_id, "company_id": company_id,
            "evaluacion": "ok", "score": round(score, 1),
            "veredicto": veredicto,
            "componentes": {
                "cumplimiento": round(cumplimiento * 100, 1),
                "puntualidad": round(puntualidad * 100, 1),
                "precio": round(precio * 100, 1)},
            "pesos": pesos,
            "ordenes_observadas": ficha.total_ordenes,
            "ficha": ficha.to_dict()}


# ---- contrato de rutas -------------------------------------------------
def _ctx_company(ctx) -> str:
    c = getattr(ctx, "company_id", "") or ""
    if not c:
        raise ValueError("ctx sin company_id")
    return c


def _datos(bundle, ctx):
    c = _ctx_company(ctx)
    if bundle.compras.company_id != c:
        raise ValueError("empresa ajena")
    ords = bundle.compras.listar_ordenes()
    recs = []
    for o in ords:
        recs.extend(bundle.compras.recepciones_de(o.compra_id))
    return c, ords, recs


def _fn_proveedores(bundle, ctx, qob):
    c, ords, recs = _datos(bundle, ctx)
    q = qob or {}
    out = []
    for pid in listar_proveedores(ords, c):
        ev = evaluar_proveedor(pid, c, ords, recs,
                               min_ordenes=int(q.get("min_ordenes", 3)))
        out.append({k: v for k, v in ev.items() if k != "ficha"})
    return {"company_id": c, "proveedores": out}


def _fn_ficha(bundle, ctx, qob):
    c, ords, recs = _datos(bundle, ctx)
    q = qob or {}
    pid = str(q["proveedor_id"])
    return evaluar_proveedor(pid, c, ords, recs,
                             min_ordenes=int(q.get("min_ordenes", 3)))


def _fn_precios(bundle, ctx, qob):
    c, ords, _ = _datos(bundle, ctx)
    q = qob or {}
    return {"company_id": c,
            "proveedor_id": q.get("proveedor_id"),
            "producto_id": q.get("producto_id"),
            "serie": precios_historicos(
                ords, str(q["proveedor_id"]), q.get("producto_id"))}


def _fn_incumplimientos(bundle, ctx, qob):
    c, ords, recs = _datos(bundle, ctx)
    q = qob or {}
    pid = str(q["proveedor_id"])
    return {"company_id": c, "proveedor_id": pid,
            "entregas_vs_prometido": entregas_vs_prometido(ords, recs, pid),
            "incumplimientos": incumplimientos(ords, recs, pid,
                                               hoy=q.get("hoy"))}


def rutas():
    return {
        "compras/proveedores": _fn_proveedores,
        "compras/proveedores/ficha": _fn_ficha,
        "compras/proveedores/precios": _fn_precios,
        "compras/proveedores/incumplimientos": _fn_incumplimientos,
    }
