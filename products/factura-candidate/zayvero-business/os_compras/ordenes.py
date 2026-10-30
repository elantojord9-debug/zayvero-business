"""LOTE 4 — Compras y Proveedores: órdenes de compra.

Agregado OrdenCompra sobre oschema (usa la entidad Compra para el registro
financiero cuando la orden se cierra). Máquina de estados explícita.
"""
from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema import SCHEMA_VERSION  # noqa: E402
from oschema.contract import require_iso_currency  # noqa: E402
from oschema.models import Compra, _dec  # noqa: E402


def _req(v: str, nombre: str):
    if not v:
        raise ValueError(f"{nombre} es obligatorio")


class EstadoOC:
    BORRADOR = "borrador"
    ENVIADA = "enviada"
    PARCIAL = "parcial"
    RECIBIDA = "recibida"
    CERRADA = "cerrada"
    CANCELADA = "cancelada"
    TODOS = {BORRADOR, ENVIADA, PARCIAL, RECIBIDA, CERRADA, CANCELADA}


_TRANSICIONES = {
    EstadoOC.BORRADOR: {EstadoOC.ENVIADA, EstadoOC.CANCELADA},
    EstadoOC.ENVIADA: {EstadoOC.PARCIAL, EstadoOC.RECIBIDA, EstadoOC.CANCELADA},
    EstadoOC.PARCIAL: {EstadoOC.RECIBIDA, EstadoOC.CANCELADA},
    EstadoOC.RECIBIDA: {EstadoOC.CERRADA},
    EstadoOC.CERRADA: set(),
    EstadoOC.CANCELADA: set(),
}


class ErrorCompras(Exception):
    pass


class TransicionInvalida(ErrorCompras):
    pass


@dataclass
class LineaOC:
    producto_id: str
    cantidad: str       # ordenada (Decimal string, > 0)
    precio_unitario: str  # Decimal string, >= 0

    def validate(self):
        _req(self.producto_id, "producto_id")
        if _dec(self.cantidad) <= 0:
            raise ValueError("cantidad ordenada debe ser > 0")
        if _dec(self.precio_unitario) < 0:
            raise ValueError("precio_unitario no puede ser negativo")

    def total(self) -> Decimal:
        return _dec(self.cantidad) * _dec(self.precio_unitario)

    def to_dict(self):
        return {"producto_id": self.producto_id, "cantidad": str(_dec(self.cantidad)),
                "precio_unitario": str(_dec(self.precio_unitario))}

    @classmethod
    def from_dict(cls, d):
        return cls(d["producto_id"], d["cantidad"], d["precio_unitario"])


@dataclass
class OrdenCompra:
    compra_id: str
    company_id: str
    proveedor_id: str
    moneda: str = "DOP"
    estado: str = EstadoOC.BORRADOR
    lineas: list = field(default_factory=list)
    fecha_emision: str = ""
    fecha_entrega_esperada: str = ""
    notas: str = ""

    def validate(self):
        _req(self.compra_id, "compra_id")
        _req(self.company_id, "company_id")
        _req(self.proveedor_id, "proveedor_id")
        if self.estado not in EstadoOC.TODOS:
            raise ValueError(f"estado inválido: {self.estado}")
        require_iso_currency(self.moneda)
        if not self.lineas:
            raise ValueError("la orden necesita al menos una línea")
        for l in self.lineas:
            l.validate()

    def total(self) -> Decimal:
        return sum((l.total() for l in self.lineas), Decimal("0"))

    def cambiar_estado(self, nuevo: str):
        if nuevo not in _TRANSICIONES[self.estado]:
            raise TransicionInvalida(f"{self.estado} -> {nuevo} no permitido")
        self.estado = nuevo

    def cantidad_ordenada(self, producto_id: str) -> Decimal:
        return sum((_dec(l.cantidad) for l in self.lineas
                    if l.producto_id == producto_id), Decimal("0"))

    def precio_ordenado(self, producto_id: str) -> Decimal | None:
        for l in self.lineas:
            if l.producto_id == producto_id:
                return _dec(l.precio_unitario)
        return None

    def a_compra(self) -> Compra:
        """Registro financiero (oschema.Compra) al cerrar la orden."""
        return Compra(compra_id=self.compra_id, company_id=self.company_id,
                      proveedor_id=self.proveedor_id, moneda=self.moneda,
                      estado="recibida",
                      lineas=[l.to_dict() for l in self.lineas])

    def to_dict(self):
        return {
            "schema_version": SCHEMA_VERSION, "compra_id": self.compra_id,
            "company_id": self.company_id, "proveedor_id": self.proveedor_id,
            "moneda": self.moneda, "estado": self.estado,
            "lineas": [l.to_dict() for l in self.lineas],
            "fecha_emision": self.fecha_emision,
            "fecha_entrega_esperada": self.fecha_entrega_esperada,
            "notas": self.notas,
        }

    @classmethod
    def from_dict(cls, d):
        return cls(compra_id=d["compra_id"], company_id=d["company_id"],
                   proveedor_id=d["proveedor_id"], moneda=d.get("moneda", "DOP"),
                   estado=d.get("estado", EstadoOC.BORRADOR),
                   lineas=[LineaOC.from_dict(x) for x in d.get("lineas", [])],
                   fecha_emision=d.get("fecha_emision", ""),
                   fecha_entrega_esperada=d.get("fecha_entrega_esperada", ""),
                   notas=d.get("notas", ""))
