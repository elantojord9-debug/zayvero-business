"""LOTE 4 — Contratos: Finanzas/CxP, PostgreSQL y devoluciones.

Puertos (Protocols) que los módulos futuros implementan; os_compras no
depende de ellos.
"""
from __future__ import annotations

import sys
import os
from typing import Protocol, runtime_checkable

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import Compra  # noqa: E402
from .ordenes import OrdenCompra  # noqa: E402
from .recepciones import Recepcion  # noqa: E402


@runtime_checkable
class RepositorioCompras(Protocol):
    """Persistencia (la implementará el adaptador PostgreSQL del issue #1)."""

    def guardar_orden(self, orden: OrdenCompra) -> None: ...
    def obtener_orden(self, compra_id: str) -> OrdenCompra | None: ...
    def guardar_recepcion(self, recep: Recepcion) -> None: ...
    def listar_ordenes(self, company_id: str) -> list[OrdenCompra]: ...


@runtime_checkable
class FuenteCxP(Protocol):
    """Lo que Finanzas/Cuentas por Pagar necesita de Compras."""

    def cuentas_por_pagar(self, compra: Compra) -> list[dict]:
        """Devuelve [{proveedor_id, monto, moneda, vencimiento, referencia}]."""
        ...


def cxp_desde_orden_cerrada(orden: OrdenCompra, dias_credito: int = 30) -> list[dict]:
    """Construye la CxP de una orden cerrada (implementación de referencia).

    Vencimiento = fecha de emisión + días de crédito. Sin fecha de emisión,
    el vencimiento queda vacío (no se inventa).
    """
    from datetime import datetime, timedelta, timezone
    venc = ""
    if orden.fecha_emision:
        base = datetime.strptime(orden.fecha_emision[:19],
                                 "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
        venc = (base + timedelta(days=dias_credito)).strftime("%Y-%m-%d")
    return [{
        "proveedor_id": orden.proveedor_id,
        "company_id": orden.company_id,
        "monto": str(orden.total()),
        "moneda": orden.moneda,
        "vencimiento": venc,
        "referencia": orden.compra_id,
        "tipo": "cuenta_por_pagar",
    }]


def devolucion_a_proveedor(service, compra_id: str, producto_id: str,
                           almacen_id: str, cantidad: str,
                           motivo: str = "") -> str:
    """Registra devolución a proveedor: genera DEV_OUT en inventario.

    `service` es un ComprasService. Devuelve el movimiento_id.
    """
    from oschema.models import _dec
    if _dec(cantidad) <= 0:
        raise ValueError("cantidad a devolver debe ser > 0")
    orden = service.get_orden(compra_id)
    if orden is None:
        raise ValueError(f"orden inexistente: {compra_id}")
    return service.inventario.devolucion_proveedor(
        producto_id, almacen_id, cantidad,
        referencia_id=compra_id,
        motivo=motivo or f"devolución a proveedor {orden.proveedor_id}")
