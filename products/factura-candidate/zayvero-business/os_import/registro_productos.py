"""BLOQUE Documentos/Integraciones — Registro de productos importados.

Los productos importados se validan con catalogo.modelo.Producto (la
misma validación pura del catálogo maestro) y viven en un registro por
empresa, en memoria, con el mismo patrón de aislamiento que
RegistroDocumentos.

NOTA DE DISEÑO: el catálogo maestro HTTP es PG-only; este registro es
el destino del importador en modo memoria y la fuente que el importador
de movimientos usa para verificar que el producto existe. La
persistencia en snapshots/normalizadas es un pendiente documentado
(mismo gap #9 del inventario: el corte snapshots->normalizadas).
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalogo.modelo import Producto  # noqa: E402


@dataclass
class ProductoImportado:
    company_id: str
    producto_id: str
    sku: str
    nombre: str
    descripcion: str = ""
    unidad: str = "und"
    precio: str = ""
    costo: str = ""
    moneda: str = "DOP"
    import_id: str = ""

    def to_dict(self) -> dict:
        return {
            "company_id": self.company_id, "producto_id": self.producto_id,
            "sku": self.sku, "nombre": self.nombre,
            "descripcion": self.descripcion, "unidad": self.unidad,
            "precio": self.precio, "costo": self.costo,
            "moneda": self.moneda, "import_id": self.import_id,
        }


class RegistroProductos:
    """Productos importados de UNA empresa. SKU único por empresa."""

    def __init__(self, company_id: str):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        self.company_id = company_id
        self._lock = threading.RLock()
        self._por_sku: dict[str, ProductoImportado] = {}
        self._por_id: dict[str, ProductoImportado] = {}

    def registrar(self, p: ProductoImportado) -> bool:
        """True si es nuevo; False si el sku ya existía (idempotente)."""
        # Misma validación pura del catálogo maestro: el importador no
        # inventa sus propias reglas de producto.
        Producto(self.company_id, p.producto_id, p.sku, p.nombre,
                 p.descripcion, None, p.unidad).validar()
        with self._lock:
            if p.company_id != self.company_id:
                raise ValueError("producto de otra empresa")
            if p.sku in self._por_sku:
                return False
            self._por_sku[p.sku] = p
            self._por_id[p.producto_id] = p
            return True

    def existe_sku(self, sku: str) -> bool:
        with self._lock:
            return sku in self._por_sku

    def por_producto_id(self, producto_id: str) -> ProductoImportado | None:
        """Busca por producto_id o por sku (los movimientos usan ambos)."""
        with self._lock:
            return (self._por_id.get(producto_id) or
                    self._por_sku.get(producto_id))

    def listar(self) -> list[dict]:
        with self._lock:
            return [p.to_dict() for p in self._por_sku.values()]


_REGISTROS: dict[str, RegistroProductos] = {}
_LOCK = threading.RLock()


def obtener_registro(company_id: str) -> RegistroProductos:
    """Registro de productos importados de la empresa (uno por empresa)."""
    with _LOCK:
        r = _REGISTROS.get(company_id)
        if r is None:
            r = RegistroProductos(company_id)
            _REGISTROS[company_id] = r
        return r
