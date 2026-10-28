"""LOTE 4 — Recepciones: conexión real con os_inventory.

Cada recepción válida genera movimientos ENTRADA en el inventario con
movimiento_id determinista (rcp-<recepcion_id>-<producto_id>): re-procesar
la misma recepción no duplica (idempotencia).

Reglas:
- Solo se recibe sobre órdenes ENVIADA/PARCIAL (no borrador, cerrada ni cancelada).
- Nunca más de lo ordenado por línea (anti sobre-recepción).
- recepcion_id único por empresa.
- Atomicidad: las validaciones ocurren ANTES de tocar el inventario; si una
  línea falla, no se registra nada (todo o nada a nivel de proceso).
  LIMITACIÓN: la atomicidad duradera entre Compras e Inventario exige una
  transacción PostgreSQL (issue #1); hoy es atómica solo dentro del proceso.
"""
from __future__ import annotations

import sys
import os
import threading
from dataclasses import dataclass, field
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from os_inventory.engine import InventoryEngine  # noqa: E402
from .ordenes import OrdenCompra, EstadoOC, ErrorCompras, _req  # noqa: E402


class RecepcionDuplicada(ErrorCompras):
    pass


class SobreRecepcion(ErrorCompras):
    pass


class EstadoNoRecibible(ErrorCompras):
    pass


@dataclass
class LineaRecepcion:
    producto_id: str
    cantidad: str  # > 0

    def validate(self):
        _req(self.producto_id, "producto_id")
        if _dec(self.cantidad) <= 0:
            raise ValueError("cantidad recibida debe ser > 0")

    def to_dict(self):
        return {"producto_id": self.producto_id,
                "cantidad": str(_dec(self.cantidad))}

    @classmethod
    def from_dict(cls, d):
        return cls(d["producto_id"], d["cantidad"])


@dataclass
class Recepcion:
    recepcion_id: str
    company_id: str
    compra_id: str
    almacen_id: str
    lineas: list = field(default_factory=list)
    fecha: str = ""
    factura_ref: str = ""

    def validate(self):
        _req(self.recepcion_id, "recepcion_id")
        _req(self.company_id, "company_id")
        _req(self.compra_id, "compra_id")
        _req(self.almacen_id, "almacen_id")
        if not self.lineas:
            raise ValueError("la recepción necesita al menos una línea")
        for l in self.lineas:
            l.validate()

    def to_dict(self):
        return {"recepcion_id": self.recepcion_id, "company_id": self.company_id,
                "compra_id": self.compra_id, "almacen_id": self.almacen_id,
                "lineas": [l.to_dict() for l in self.lineas],
                "fecha": self.fecha, "factura_ref": self.factura_ref}

    @classmethod
    def from_dict(cls, d):
        return cls(recepcion_id=d["recepcion_id"], company_id=d["company_id"],
                   compra_id=d["compra_id"], almacen_id=d["almacen_id"],
                   lineas=[LineaRecepcion.from_dict(x) for x in d.get("lineas", [])],
                   fecha=d.get("fecha", ""), factura_ref=d.get("factura_ref", ""))


class ComprasService:
    """Coordina órdenes, recepciones e inventario para UNA empresa."""

    def __init__(self, company_id: str, inventario: InventoryEngine):
        if inventario.company_id != company_id:
            raise ValueError("el inventario debe ser de la misma empresa")
        self.company_id = company_id
        self.inventario = inventario
        self._lock = threading.RLock()
        self._ordenes: dict[str, OrdenCompra] = {}
        self._recepciones: dict[str, Recepcion] = {}
        # (compra_id, producto_id) -> cantidad acumulada recibida
        self._recibido: dict[tuple[str, str], Decimal] = {}

    # ---- órdenes ------------------------------------------------------
    def crear_orden(self, orden: OrdenCompra):
        with self._lock:
            orden.validate()
            if orden.company_id != self.company_id:
                raise ErrorCompras("orden de otra empresa")
            if orden.compra_id in self._ordenes:
                raise ErrorCompras(f"orden duplicada: {orden.compra_id}")
            self._ordenes[orden.compra_id] = orden

    def get_orden(self, compra_id: str) -> OrdenCompra | None:
        with self._lock:
            return self._ordenes.get(compra_id)

    def listar_ordenes(self) -> list[OrdenCompra]:
        """Todas las órdenes, ordenadas por fecha de emisión y luego id.

        BLOQUE 4 (sprint 12-oct-2026): base para el listado paginado del
        panel (GET /api/v1/os/compras/ordenes).
        """
        with self._lock:
            return sorted(self._ordenes.values(),
                          key=lambda o: (o.fecha_emision or "", o.compra_id))

    # ---- recepciones ---------------------------------------------------
    def recibir(self, recep: Recepcion) -> list[str]:
        """Procesa una recepción y genera las ENTRADAs en inventario.

        Devuelve los movimiento_id creados. Idempotente por recepcion_id.
        """
        with self._lock:
            recep.validate()
            if recep.company_id != self.company_id:
                raise ErrorCompras("recepción de otra empresa")
            if recep.recepcion_id in self._recepciones:
                raise RecepcionDuplicada(recep.recepcion_id)
            orden = self._ordenes.get(recep.compra_id)
            if orden is None:
                raise ErrorCompras(f"orden inexistente: {recep.compra_id}")
            if orden.estado not in (EstadoOC.ENVIADA, EstadoOC.PARCIAL):
                raise EstadoNoRecibible(
                    f"orden en estado {orden.estado}, no recibe mercancía")

            # Validar TODO antes de tocar el inventario (todo o nada)
            for lin in recep.lineas:
                ordenada = orden.cantidad_ordenada(lin.producto_id)
                if ordenada == 0:
                    raise SobreRecepcion(
                        f"{lin.producto_id} no está en la orden {orden.compra_id}")
                ya = self._recibido.get((orden.compra_id, lin.producto_id),
                                        Decimal("0"))
                if ya + _dec(lin.cantidad) > ordenada:
                    raise SobreRecepcion(
                        f"{lin.producto_id}: ordenado {ordenada}, ya recibido "
                        f"{ya}, intentan {lin.cantidad}")

            # Registrar en inventario (idempotente por movimiento_id:
            # un reintento tras fallo parcial no duplica ni rompe)
            from oschema.models import MovimientoInventario
            mov_ids = []
            for lin in recep.lineas:
                mid = f"rcp-{recep.recepcion_id}-{lin.producto_id}"
                m = MovimientoInventario(
                    movimiento_id=mid, company_id=self.company_id,
                    producto_id=lin.producto_id, almacen_id=recep.almacen_id,
                    tipo="entrada", cantidad=str(_dec(lin.cantidad)),
                    fecha=recep.fecha or None,
                    referencia_id=orden.compra_id,
                    motivo=f"recepción {recep.recepcion_id}")
                self.inventario.registrar_idempotente(m)
                mov_ids.append(mid)

            self._recepciones[recep.recepcion_id] = recep
            for lin in recep.lineas:
                k = (orden.compra_id, lin.producto_id)
                self._recibido[k] = self._recibido.get(k, Decimal("0")) \
                    + _dec(lin.cantidad)

            # Actualizar estado de la orden (solo si cambia: en concurrencia
            # dos recepciones parciales no deben lanzar "parcial->parcial")
            completa = all(
                self._recibido.get((orden.compra_id, l.producto_id), Decimal("0"))
                >= _dec(l.cantidad) for l in orden.lineas)
            nuevo_estado = EstadoOC.RECIBIDA if completa else EstadoOC.PARCIAL
            if orden.estado != nuevo_estado:
                orden.cambiar_estado(nuevo_estado)
            return mov_ids

    def recibido_acumulado(self, compra_id: str, producto_id: str) -> Decimal:
        with self._lock:
            return self._recibido.get((compra_id, producto_id), Decimal("0"))

    def recepciones_de(self, compra_id: str) -> list[Recepcion]:
        with self._lock:
            return [r for r in self._recepciones.values()
                    if r.compra_id == compra_id]

    def cerrar_orden(self, compra_id: str):
        """Cierra una orden RECIBIDA (genera el registro financiero)."""
        with self._lock:
            orden = self._ordenes.get(compra_id)
            if orden is None:
                raise ErrorCompras(f"orden inexistente: {compra_id}")
            orden.cambiar_estado(EstadoOC.CERRADA)
            return orden.a_compra()
