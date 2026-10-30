"""BLOQUE 5 (sprint 2, 2026-10-12) — CATÁLOGO MAESTRO.

Una sola fuente de verdad para productos, categorías, costos, precios y
almacenes, usable por Inventario, Simulator y Price Advisor SIN duplicar
datos. Ver docs/catalogo.md.

Submódulos:
- `catalogo.modelo`: entidades, excepciones y validaciones puras.
- `catalogo.api`: CRUD con locks por empresa (PostgreSQL).
- `catalogo.integracion`: adaptadores de LECTURA para los motores
  (fallback automático si el catálogo está vacío o no hay PG).
- `catalogo.http`: despachador HTTP /api/v1/os/catalogo/* (BLOQUE 6).
- `catalogo.config`: umbrales por empresa (defaults de demo marcados).
- `catalogo.idempotencia`: Idempotency-Key para fijar_precio/costo.
"""
