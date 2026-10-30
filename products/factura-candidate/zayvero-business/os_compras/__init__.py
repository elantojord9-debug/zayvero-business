"""ZAYVERO Business OS — LOTE 4: Compras y Proveedores.

- ordenes: OrdenCompra con máquina de estados, líneas, totales Decimal.
- recepciones: ComprasService — recepciones parciales/completas conectadas
  al inventario (os_inventory), anti sobre-recepción, idempotencia.
- cruce: match de 3 vías (orden vs factura vs mercancía).
- proveedores: ficha inteligente (cumplimiento, retrasos, precios).
- inteligencia: Supplier Intelligence vía API (precios históricos,
  plazos reales vs prometidos, incumplimientos, evaluación ponderada).
- entregas: Delivery Monitor (pendientes, atrasadas, parciales, OTIF).
- asesor: Purchase Advisor (propuestas con cantidades, comparativa de
  proveedores, impacto en caja y margen; integrado con Stock Predictor
  y catálogo maestro).
- facturas: registro de facturas de proveedor (anti-duplicadas).
- conciliacion: orden→recepción→factura + pagos indebidos, con evidencia.
- contratos: puertos para Finanzas/CxP y PostgreSQL; devoluciones a proveedor.

LIMITACIÓN: atomicidad duradera Compras↔Inventario exige transacción
PostgreSQL (issue #1); hoy es atómica solo dentro del proceso.
"""
from . import ordenes, recepciones, cruce, proveedores, contratos  # noqa: F401
from . import inteligencia, entregas, asesor, facturas, conciliacion  # noqa: F401

__all__ = ["ordenes", "recepciones", "cruce", "proveedores", "contratos",
           "inteligencia", "entregas", "asesor", "facturas", "conciliacion"]


def rutas():
    """Contrato de rutas del bloque compras: {ruta: fn}.

    Cada fn recibe (bundle, ctx, qob) y devuelve dict JSON-serializable.
    company_id siempre sale del ctx (anti-IDOR).
    """
    merged = {}
    for mod in (inteligencia, entregas, asesor, facturas, conciliacion):
        for k, v in mod.rutas().items():
            if k in merged:
                raise ValueError(f"ruta duplicada: {k}")
            if not k.startswith("compras/"):
                raise ValueError(f"ruta sin prefijo compras/: {k}")
            merged[k] = v
    return merged
