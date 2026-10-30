"""LOTE 4 — Ficha inteligente de proveedor.

Todo se calcula de órdenes y recepciones registradas: historial,
cumplimiento (líneas completas), retrasos vs fecha_entrega_esperada
y precios por producto. Sin inventar datos: si no hay historial,
los campos dicen "sin datos".
"""
from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from .ordenes import OrdenCompra, EstadoOC  # noqa: E402
from .recepciones import Recepcion  # noqa: E402


def _parse(f: str):
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


@dataclass
class FichaProveedor:
    proveedor_id: str
    company_id: str
    total_ordenes: int = 0
    ordenes_completas: int = 0
    tasa_cumplimiento: str | None = None   # 0..1 como string, None sin datos
    retraso_promedio_dias: str | None = None
    productos: list = field(default_factory=list)  # [{producto_id, ultimo_precio, precio_promedio, precio_min, precio_max, veces}]
    monto_total_comprado: str = "0"

    def to_dict(self):
        return {
            "proveedor_id": self.proveedor_id, "company_id": self.company_id,
            "total_ordenes": self.total_ordenes,
            "ordenes_completas": self.ordenes_completas,
            "tasa_cumplimiento": self.tasa_cumplimiento,
            "retraso_promedio_dias": self.retraso_promedio_dias,
            "productos": self.productos,
            "monto_total_comprado": self.monto_total_comprado,
        }


def ficha_proveedor(proveedor_id: str, company_id: str,
                   ordenes: list[OrdenCompra],
                   recepciones: list[Recepcion]) -> FichaProveedor:
    propias = [o for o in ordenes if o.proveedor_id == proveedor_id
               and o.company_id == company_id]
    ficha = FichaProveedor(proveedor_id=proveedor_id, company_id=company_id)
    if not propias:
        return ficha

    rec_por_orden: dict[str, list[Recepcion]] = {}
    for r in recepciones:
        rec_por_orden.setdefault(r.compra_id, []).append(r)

    retrasos: list[float] = []
    precios: dict[str, list[Decimal]] = {}
    monto = Decimal("0")

    for o in propias:
        ficha.total_ordenes += 1
        monto += o.total()
        for lin in o.lineas:
            precios.setdefault(lin.producto_id, []).append(_dec(lin.precio_unitario))
        recs = rec_por_orden.get(o.compra_id, [])
        completa = all(
            sum((_dec(l.cantidad) for r in recs for l in r.lineas
                 if l.producto_id == ol.producto_id), Decimal("0"))
            >= _dec(ol.cantidad) for ol in o.lineas)
        if completa:
            ficha.ordenes_completas += 1
        if o.fecha_entrega_esperada and recs:
            ultima = max(r.fecha for r in recs if r.fecha)
            if ultima:
                delta = (_parse(ultima) - _parse(o.fecha_entrega_esperada)
                         ).total_seconds() / 86400
                retrasos.append(max(delta, 0))

    ficha.tasa_cumplimiento = str(round(ficha.ordenes_completas / ficha.total_ordenes, 3))
    ficha.retraso_promedio_dias = (str(round(sum(retrasos) / len(retrasos), 1))
                                   if retrasos else None)
    ficha.monto_total_comprado = str(monto)
    for pid, ps in sorted(precios.items()):
        ficha.productos.append({
            "producto_id": pid, "veces": len(ps),
            "ultimo_precio": str(ps[-1]),
            "precio_promedio": str(round(sum(ps) / len(ps), 2)),
            "precio_min": str(min(ps)), "precio_max": str(max(ps)),
        })
    return ficha
