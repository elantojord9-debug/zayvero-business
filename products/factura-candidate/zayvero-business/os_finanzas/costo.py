"""LOTE 5 — Método de costo de inventario: PROMEDIO PONDERADO.

Definición (probada en tests):
- ENTRADA de Q unidades a costo unitario C: nuevo_promedio =
  (unidades_viejas * promedio_viejo + Q * C) / (unidades_viejas + Q)
- SALIDA de Q unidades: COGS = Q * promedio_actual (el promedio no cambia).
- El costo se lleva POR MONEDA: no se mezclan monedas sin conversión explícita.
- Devolución a proveedor (DEV_OUT): sale al promedio actual.
- Devolución de cliente (DEV_IN): entra al costo promedio vigente
  (no se inventa el costo original de esa unidad).

Implementa el contrato FuenteFinanzas del Lote 3 (costo_promedio).
"""
from __future__ import annotations

import sys
import os
import threading
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from oschema.contract import require_iso_currency  # noqa: E402


class ErrorCosto(Exception):
    pass


class CostoInventario:
    """Costo promedio ponderado por (producto, almacén, moneda)."""

    def __init__(self):
        self._lock = threading.RLock()
        # (producto_id, almacen_id, moneda) -> [unidades, costo_total]
        self._estado: dict[tuple[str, str, str], list[Decimal]] = {}

    def _key(self, producto_id, almacen_id, moneda):
        require_iso_currency(moneda)
        return (producto_id, almacen_id, moneda)

    def aplicar_entrada(self, producto_id: str, almacen_id: str, cantidad: str,
                        costo_unitario: str, moneda: str):
        cant, cu = _dec(cantidad), _dec(costo_unitario)
        if cant <= 0 or cu < 0:
            raise ErrorCosto("entrada exige cantidad > 0 y costo >= 0")
        with self._lock:
            k = self._key(producto_id, almacen_id, moneda)
            und, tot = self._estado.get(k, [Decimal("0"), Decimal("0")])
            self._estado[k] = [und + cant, tot + cant * cu]

    def aplicar_salida(self, producto_id: str, almacen_id: str, cantidad: str,
                       moneda: str) -> Decimal:
        """Registra salida y devuelve el COGS (cantidad * promedio)."""
        cant = _dec(cantidad)
        if cant <= 0:
            raise ErrorCosto("salida exige cantidad > 0")
        with self._lock:
            k = self._key(producto_id, almacen_id, moneda)
            und, tot = self._estado.get(k, [Decimal("0"), Decimal("0")])
            if und < cant:
                raise ErrorCosto(
                    f"sin costo registrado suficiente: {producto_id} "
                    f"({und} unidades con costo, piden {cant})")
            prom = tot / und if und > 0 else Decimal("0")
            cogs = cant * prom
            self._estado[k] = [und - cant, tot - cogs]
            return cogs

    def costo_promedio(self, producto_id: str, almacen_id: str,
                       moneda: str = "DOP") -> str:
        """Implementa FuenteFinanzas.costo_promedio (contrato Lote 3)."""
        with self._lock:
            und, tot = self._estado.get(
                self._key(producto_id, almacen_id, moneda),
                [Decimal("0"), Decimal("0")])
            return str(tot / und) if und > 0 else "0"

    def unidades_con_costo(self, producto_id: str, almacen_id: str,
                           moneda: str = "DOP") -> str:
        with self._lock:
            und, _ = self._estado.get(
                self._key(producto_id, almacen_id, moneda),
                [Decimal("0"), Decimal("0")])
            return str(und)
