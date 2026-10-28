"""LOTE 5 — Gastos: registro por categoría, empresa, sucursal y centro de costo.

Envuelve oschema.Gasto (categoría, monto, moneda ISO, proveedor, comprobante)
añadiendo sucursal y centro de costo. Idempotencia por gasto_id, aislamiento
por empresa, concurrencia con lock.
"""
from __future__ import annotations

import sys
import os
import threading
from dataclasses import dataclass

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import Gasto, _dec  # noqa: E402


class ErrorFinanzas(Exception):
    pass


class GastoDuplicado(ErrorFinanzas):
    pass


@dataclass
class GastoDetallado:
    """Gasto + dimensiones analíticas (sucursal, centro de costo)."""
    gasto: Gasto
    sucursal_id: str = ""
    centro_costo: str = ""

    def validate(self):
        self.gasto.validate()
        if _dec(self.gasto.monto) <= 0:
            raise ValueError("el monto del gasto debe ser > 0")

    def to_dict(self):
        d = self.gasto.to_dict()
        d.update({"sucursal_id": self.sucursal_id,
                  "centro_costo": self.centro_costo})
        return d


class RegistroGastos:
    """Libro de gastos de UNA empresa."""

    def __init__(self, company_id: str):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        self.company_id = company_id
        self._lock = threading.RLock()
        self._gastos: dict[str, GastoDetallado] = {}

    def registrar(self, g: GastoDetallado) -> bool:
        with self._lock:
            g.validate()
            if g.gasto.company_id != self.company_id:
                raise ErrorFinanzas("gasto de otra empresa")
            if g.gasto.gasto_id in self._gastos:
                raise GastoDuplicado(g.gasto.gasto_id)
            self._gastos[g.gasto.gasto_id] = g
            return True

    def registrar_idempotente(self, g: GastoDetallado) -> bool:
        try:
            return self.registrar(g)
        except GastoDuplicado:
            return False

    def todos(self) -> list[GastoDetallado]:
        with self._lock:
            return list(self._gastos.values())

    def por_categoria(self, categoria: str) -> list[GastoDetallado]:
        with self._lock:
            return [g for g in self._gastos.values()
                    if g.gasto.categoria == categoria]

    def total_por_moneda(self, categoria: str | None = None,
                         sucursal_id: str | None = None) -> dict[str, str]:
        """Totales agrupados por moneda (nunca mezcla monedas)."""
        tot: dict[str, __import__("decimal").Decimal] = {}
        with self._lock:
            for g in self._gastos.values():
                if categoria and g.gasto.categoria != categoria:
                    continue
                if sucursal_id and g.sucursal_id != sucursal_id:
                    continue
                m = g.gasto.moneda or "SIN_MONEDA"
                tot[m] = tot.get(m, _dec("0")) + _dec(g.gasto.monto)
        return {k: str(v) for k, v in sorted(tot.items())}
