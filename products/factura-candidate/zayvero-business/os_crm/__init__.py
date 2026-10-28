"""ZAYVERO Business OS — LOTE 6: CRM, Ventas y Cobranzas.

- clientes: CRM con ficha, historial, segmentación y notas de seguimiento.
- ventas: VentasService — contado/crédito conectadas a Inventario (Lote 3),
  Profit (Lote 5) y CxC/Tesorería (Lote 5). Cancelación con nota de crédito.
- cobranzas: aging por tramos, promesas de pago, borradores de recordatorio
  (requieren aprobación humana; nunca se envían solos).
- inteligencia: frecuencia, ticket, recencia, LTV, señales de churn
  (basadas en historial real, sin probabilidades inventadas) +
  BLOQUE CRM/VENTAS: ficha360, probabilidad_churn explicada, ltv_cliente
  (fórmula explícita), segmento_rfm, productos_preferidos.
- oportunidades: pipeline (etapas, probabilidad, valor esperado).
- descuentos: registro con motivo/autorización e impacto en margen.
- devoluciones: devoluciones de clientes con impacto en inventario y margen.
- http: contrato rutas() del bloque (prefijo crm/) para el wiring del panel.
- contratos: RepositorioCRM para PostgreSQL.

REGLA CRÍTICA: una venta afecta inventario, ingresos y CxC exactamente una
vez; el cobro posterior solo mueve caja (no genera ingreso de nuevo).
"""
from . import (clientes, ventas, cobranzas, inteligencia, contratos,  # noqa: F401
               oportunidades, descuentos, devoluciones, http)

__all__ = ["clientes", "ventas", "cobranzas", "inteligencia", "contratos",
           "oportunidades", "descuentos", "devoluciones", "http"]
