"""BLOQUE CONTABILIDAD (2026-10-13) — ZAYVERO Business OS.

Paquete nuevo de contabilidad operativa sobre los motores existentes:

- hunter_api:        Expense Hunter API (solo lectura sobre RegistroGastos)
- presupuestos:      presupuestos por categoría/período, ejecución y proyección
- rentabilidad_vistas: vistas honestas por producto (contribución) y sucursal
- conciliacion:      conciliación bancaria SIMULADA (CSV manual, sin bancos)
- cxc_cxp:           saldos y aging de CxC/CxP (reutiliza os_crm.cobranzas)
- cashflow:          proyección determinista de flujo de caja
- itbis:             preparación ITBIS/NCF/e-CF (RD) — NO certificado DGII
- rutas:             contrato rutas() — prefijo `contab/` (lo antepone el cableado)

REGLAS DURAS del bloque:
- Todo local, datos sintéticos. Sin conexiones bancarias reales.
- La conciliación es MANUAL/SIMULADA y cada salida lo dice.
- ITBIS/NCF: "preparación local, no certificado DGII" en cada salida.
- El Expense Hunter NUNCA acusa: tono "revisar", umbrales y evidencia
  explícitos en cada hallazgo.
- Por producto la vista es de CONTRIBUCIÓN: los gastos indirectos NO se
  prorratean (decirlo en la salida, no inventar reglas de reparto).
- company_id SIEMPRE del ctx (anti-IDOR). Mutaciones: auditoría en
  os_eventos en la misma transacción (patrón mutar_os; en memoria cae en
  bundle.eventos_memoria). Errores: ErrorContab/ValueError -> 400,
  NoEncontrado -> 404, PermisoDenegado -> 403.
"""
from .errores import ErrorContab, NoEncontrado, PermisoDenegado
from .rutas import rutas

__all__ = ["ErrorContab", "NoEncontrado", "PermisoDenegado", "rutas"]
