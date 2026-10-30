"""LOTE 5 — Contratos: persistencia PostgreSQL y conversión de moneda.

- RepositorioFinanzas: puerto para el adaptador PG del issue #1.
- convertir(): la ÚNICA forma de mezclar monedas — con tasa explícita.
  Sin tasa no hay conversión (se agrupa por moneda, no se suma).
"""
from __future__ import annotations

import sys
import os
from typing import Protocol, runtime_checkable
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from oschema.contract import require_iso_currency  # noqa: E402


def convertir(monto: str, de: str, a: str, tasa: str) -> str:
    """Convierte con tasa explícita (unidades de `de` por 1 de `a`).

    Ej: convertir("5800", "DOP", "USD", "58") -> "100" (5800/58).
    """
    require_iso_currency(de)
    require_iso_currency(a)
    t = _dec(tasa)
    if t <= 0:
        raise ValueError("tasa debe ser > 0")
    return str(_dec(monto) / t)


@runtime_checkable
class RepositorioFinanzas(Protocol):
    """Persistencia (la implementará el adaptador PostgreSQL del issue #1)."""

    def guardar_venta(self, venta) -> None: ...
    def guardar_gasto(self, gasto) -> None: ...
    def guardar_cxc(self, cuenta) -> None: ...
    def guardar_cxp(self, cuenta) -> None: ...
    def listar_ventas(self, company_id: str) -> list: ...


class ConversorMoneda:
    """Conversión explícita con tasas registradas por fecha."""

    def __init__(self):
        self._tasas: dict[tuple[str, str], str] = {}  # (de, a) -> tasa

    def fijar_tasa(self, de: str, a: str, tasa: str):
        require_iso_currency(de)
        require_iso_currency(a)
        if _dec(tasa) <= 0:
            raise ValueError("tasa debe ser > 0")
        self._tasas[(de, a)] = tasa

    def convertir(self, monto: str, de: str, a: str) -> str:
        if de == a:
            return str(_dec(monto))
        tasa = self._tasas.get((de, a))
        if tasa is None:
            raise ValueError(f"sin tasa registrada para {de}->{a}")
        return convertir(monto, de, a, tasa)
