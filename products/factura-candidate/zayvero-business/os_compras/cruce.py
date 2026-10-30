"""LOTE 4 — Cruce de 3 vías: orden de compra vs factura vs mercancía recibida.

Detecta diferencias sin inventar datos: todo sale de los tres documentos.
Severidades: alta (precio distinto, producto no ordenado), media (faltante),
baja (sobrante dentro de tolerancia cero — aquí cualquier sobrante se reporta).
"""
from __future__ import annotations

import sys
import os
from dataclasses import dataclass
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from .ordenes import OrdenCompra  # noqa: E402


@dataclass
class Diferencia:
    tipo: str       # faltante | sobrante | precio | no_ordenado
    severidad: str  # alta | media | baja
    producto_id: str
    detalle: str
    ordenado: str = ""
    recibido: str = ""
    facturado: str = ""

    def to_dict(self):
        return {"tipo": self.tipo, "severidad": self.severidad,
                "producto_id": self.producto_id, "detalle": self.detalle,
                "ordenado": self.ordenado, "recibido": self.recibido,
                "facturado": self.facturado}


def _lineas_factura(factura: dict) -> dict[str, dict]:
    """factura: {lineas: [{producto_id, cantidad, precio_unitario}]}"""
    out = {}
    for l in factura.get("lineas", []):
        out[l["producto_id"]] = {
            "cantidad": _dec(l["cantidad"]),
            "precio": _dec(l["precio_unitario"]),
        }
    return out


def cruzar(orden: OrdenCompra, factura: dict, recibido: dict[str, Decimal]
           ) -> list[Diferencia]:
    """Cruza los tres documentos.

    recibido: {producto_id: cantidad acumulada recibida} (del ComprasService).
    Devuelve lista de diferencias (vacía = cruce limpio).
    """
    difs: list[Diferencia] = []
    fac = _lineas_factura(factura)

    for lin in orden.lineas:
        pid = lin.producto_id
        cant_ord = _dec(lin.cantidad)
        precio_ord = _dec(lin.precio_unitario)
        cant_rec = recibido.get(pid, Decimal("0"))
        f = fac.get(pid)

        if cant_rec < cant_ord:
            difs.append(Diferencia(
                tipo="faltante", severidad="media", producto_id=pid,
                detalle=f"ordenado {cant_ord}, recibido {cant_rec}",
                ordenado=str(cant_ord), recibido=str(cant_rec),
                facturado=str(f["cantidad"]) if f else ""))
        elif cant_rec > cant_ord:
            difs.append(Diferencia(
                tipo="sobrante", severidad="baja", producto_id=pid,
                detalle=f"recibido {cant_rec} supera lo ordenado {cant_ord}",
                ordenado=str(cant_ord), recibido=str(cant_rec)))

        if f and f["precio"] != precio_ord:
            difs.append(Diferencia(
                tipo="precio", severidad="alta", producto_id=pid,
                detalle=f"precio ordenado {precio_ord}, facturado {f['precio']}",
                ordenado=str(precio_ord), facturado=str(f["precio"])))

    for pid, f in fac.items():
        if orden.cantidad_ordenada(pid) == 0:
            difs.append(Diferencia(
                tipo="no_ordenado", severidad="alta", producto_id=pid,
                detalle=f"facturado {f['cantidad']} sin estar en la orden",
                facturado=str(f["cantidad"])))
    return difs
