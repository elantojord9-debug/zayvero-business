"""SPRINT 5 PRIORIDAD 4 (2026-10-12) — Reportes operativos CSV/XLSX/PDF.

Backend de exportación para los módulos del Business OS:
inventario, finanzas, compras, cobranzas y shield.

- Lee SOLO los datos reales del snapshot/backend OS de la empresa
  autenticada (el company_id llega del ctx; anti-IDOR).
- Monedas SIEMPRE separadas: columnas/totales RD$ y US$ distintos.
- Encabezado de datos sintéticos de demostración (ES/EN).
- Permisos por rol: `reportes.rutas.SENSIBLES` (finanzas, cobranzas)
  exigen `os.write` (owner/admin); el resto exige `os.read`.

Ver docs/sprint5-p4-reportes.md.
"""
from __future__ import annotations

MODULOS = ("inventario", "finanzas", "compras", "cobranzas", "shield")
FORMATOS = ("csv", "xlsx", "pdf")
