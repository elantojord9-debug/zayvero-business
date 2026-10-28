"""BLOQUE Documentos/Integraciones (2026-10-13) — Importador CSV/Excel -> OS.

Entrada real de datos al Business OS (gap crítico #3 del inventario Fase 0):
importa productos, clientes y movimientos desde CSV/XLSX con contratos
de datos explícitos, validación fila por fila, control de duplicados
(claves naturales + idempotencia por import_id) y vista previa (dry-run)
antes de aplicar.

Todo aplicado queda auditado en os_eventos (tipo 'importacion') con
aislamiento por company_id.

Rutas expuestas (contrato `def rutas()`):
    docs/importar/contratos      GET   contratos de datos por entidad
    docs/importar/vista-previa   POST  dry-run: valida sin escribir
    docs/importar/aplicar        POST  aplica con auditoría + idempotencia
"""
