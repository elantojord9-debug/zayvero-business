"""Contratos de integración: puertos que conectan el motor de inventario
con Compras, Ventas, Proveedores y Finanzas.

Son Protocols (interfaces), no implementaciones: los módulos futuros
(Lote 4 Compras, Ventas existente, Finanzas) los implementan sin que
os_inventory dependa de ellos. Patrón puertos-y-adaptadores.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from oschema.models import MovimientoInventario


@runtime_checkable
class RepositorioMovimientos(Protocol):
    """Persistencia de movimientos (la implementará el adaptador PG del issue #1)."""

    def guardar(self, mov: MovimientoInventario) -> None: ...
    def obtener(self, movimiento_id: str) -> MovimientoInventario | None: ...
    def listar(self, company_id: str) -> list[MovimientoInventario]: ...


@runtime_checkable
class PublicadorAlertas(Protocol):
    """Destino de alertas (panel webapp, Autopilot, notificaciones)."""

    def publicar(self, alertas: list) -> None: ...


@runtime_checkable
class Reloj(Protocol):
    """Fuente de tiempo inyectable (determinismo en pruebas)."""

    def ahora_iso(self) -> str: ...


@runtime_checkable
class FuenteCompras(Protocol):
    """Lo que el módulo de Compras (Lote 4) debe proveer al inventario."""

    def recepcion_a_entrada(self, compra_id: str) -> list[MovimientoInventario]:
        """Convierte una recepción de compra en movimientos ENTRADA."""
        ...


@runtime_checkable
class FuenteVentas(Protocol):
    """Lo que el módulo de Ventas debe proveer al inventario."""

    def venta_a_salida(self, venta_id: str) -> list[MovimientoInventario]:
        """Convierte una venta en movimientos SALIDA (con referencia_id=venta_id)."""
        ...


@runtime_checkable
class FuenteFinanzas(Protocol):
    """Lo que Finanzas necesita del inventario (costo de lo vendido)."""

    def costo_promedio(self, producto_id: str, almacen_id: str) -> str:
        """Costo promedio ponderado por unidad (para COGS)."""
        ...


class RelojSistema:
    def ahora_iso(self) -> str:
        from datetime import datetime, timezone
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
