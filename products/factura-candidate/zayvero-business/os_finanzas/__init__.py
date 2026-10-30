"""ZAYVERO Business OS — LOTE 5: Finanzas Inteligentes.

- gastos: RegistroGastos por categoría/sucursal/centro de costo.
- costo: CostoInventario (promedio ponderado) — implementa FuenteFinanzas.
- expense_hunter: duplicados + anomalías (reutiliza Inspector) + desperdicios.
- profit: LibroFinanzas (P&L verificable) + Margin Guard.
- tesoreria: caja, CxC/CxP, cash flow 30/60/90 determinista.
- contratos: RepositorioFinanzas (PG) + conversión explícita de moneda.

REGLAS: Decimal, ISO 4217, sin mezclar monedas sin tasa explícita,
sin doble conteo (venta 1 vez en Profit; cobro solo mueve caja),
sin inventar cobros ni predicciones.
"""
from . import gastos, costo, expense_hunter, profit, tesoreria, contratos  # noqa: F401

__all__ = ["gastos", "costo", "expense_hunter", "profit", "tesoreria", "contratos"]
