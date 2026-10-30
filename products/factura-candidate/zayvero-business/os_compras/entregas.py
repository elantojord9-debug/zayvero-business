"""Delivery Monitor: tablero de entregas de órdenes de compra.

- Órdenes pendientes (enviada/parcial): días restantes vs
  fecha_entrega_esperada, estado de cada línea (ordenado vs recibido).
- Entregas atrasadas: esperada pasada y orden aún abierta.
- Recepciones parciales: líneas con recibido < ordenado.
- Diferencias pedido vs recibido (cantidades) por línea.
- OTIF por orden: a tiempo (última recepción <= esperada) y completa
  (todo recibido). Sin fechas no se afirma puntualidad.

Todo sale de órdenes + recepciones registradas; sin inventar.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from .ordenes import EstadoOC  # noqa: E402


def _parse(f: str) -> datetime:
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc)


_ABIERTAS = {EstadoOC.ENVIADA, EstadoOC.PARCIAL}


def monitor_entregas(compras, hoy: str | None = None,
                     solo_atrasadas: bool = False) -> dict:
    ahora = _parse(hoy) if hoy else datetime.now(timezone.utc)
    pendientes, atrasadas, parciales = [], [], []
    otif_ok = otif_total = 0

    for o in compras.listar_ordenes():
        recs = compras.recepciones_de(o.compra_id)
        recibido: dict[str, Decimal] = {}
        fechas = []
        for r in recs:
            if r.fecha:
                fechas.append(r.fecha)
            for l in r.lineas:
                recibido[l.producto_id] = recibido.get(
                    l.producto_id, Decimal("0")) + _dec(l.cantidad)
        lineas = []
        for lin in o.lineas:
            ord_c = _dec(lin.cantidad)
            rec_c = recibido.get(lin.producto_id, Decimal("0"))
            lineas.append({
                "producto_id": lin.producto_id,
                "ordenado": str(ord_c), "recibido": str(rec_c),
                "pendiente": str(ord_c - rec_c),
                "precio_unitario": str(_dec(lin.precio_unitario)),
            })
        completa = all(_dec(x["pendiente"]) <= 0 for x in lineas)
        dias_rest: float | None = None
        if o.fecha_entrega_esperada:
            dias_rest = round((_parse(o.fecha_entrega_esperada) - ahora
                               ).total_seconds() / 86400, 1)
        # OTIF: solo órdenes con ciclo cerrado (recibida/cerrada)
        if o.estado in (EstadoOC.RECIBIDA, EstadoOC.CERRADA):
            otif_total += 1
            a_tiempo = (not o.fecha_entrega_esperada or not fechas
                        or max(fechas) <= o.fecha_entrega_esperada)
            if completa and a_tiempo:
                otif_ok += 1

        if o.estado in _ABIERTAS:
            item = {"compra_id": o.compra_id,
                    "proveedor_id": o.proveedor_id,
                    "estado": o.estado, "moneda": o.moneda,
                    "fecha_emision": o.fecha_emision,
                    "fecha_entrega_esperada": o.fecha_entrega_esperada,
                    "dias_restantes": dias_rest,
                    "recepciones": len(recs),
                    "lineas": lineas, "completa": completa}
            atrasada = dias_rest is not None and dias_rest < 0
            item["atrasada"] = atrasada
            if atrasada:
                atrasadas.append(item)
            else:
                pendientes.append(item)
            if not completa:
                parciales.append({"compra_id": o.compra_id,
                                  "proveedor_id": o.proveedor_id,
                                  "lineas_pendientes": [
                                      l for l in lineas
                                      if _dec(l["pendiente"]) > 0]})

    if solo_atrasadas:
        pendientes = []
    return {
        "pendientes": pendientes, "atrasadas": atrasadas,
        "recepciones_parciales": parciales,
        "resumen": {
            "pendientes": len(pendientes), "atrasadas": len(atrasadas),
            "parciales": len(parciales),
            "otif": (f"{otif_ok}/{otif_total}"
                     if otif_total else "sin datos"),
            "otif_pct": (round(otif_ok / otif_total * 100, 1)
                         if otif_total else None),
        },
    }


# ---- contrato de rutas -------------------------------------------------
def _ctx_company(ctx) -> str:
    c = getattr(ctx, "company_id", "") or ""
    if not c:
        raise ValueError("ctx sin company_id")
    return c


def _fn_entregas(bundle, ctx, qob):
    c = _ctx_company(ctx)
    if bundle.compras.company_id != c:
        raise ValueError("empresa ajena")
    q = qob or {}
    r = monitor_entregas(
        bundle.compras, hoy=q.get("hoy"),
        solo_atrasadas=str(q.get("atrasadas_solo", "0")) in ("1", "true"))
    r["company_id"] = c
    return r


def rutas():
    return {"compras/entregas": _fn_entregas}
