"""EXTRA 1 — SHIELD: umbrales configurables del motor de riesgos.

Todos los umbrales son argumentos del constructor: el API y las pruebas
pueden ajustar la sensibilidad sin tocar el código de las reglas.

Decisiones de defaults (documentadas, no mágicas):

- Días de inventario: 7 (crítico) / 15 (alerta). Una PYME típica
  repone en 1-2 semanas; con menos de 7 días de cobertura el quiebre
  es inminente si el proveedor se retrasa un solo día.
- Mora CxC: 30 días (alerta, seguimiento firme) / 90 días (crítico,
  riesgo real de incobrable). Alineado con los tramos de cobranzas
  (31-60 / 61-90 / 90+).
- Concentración de proveedor: share >= 40% (alerta) / >= 60%
  (crítico) del gasto en compras del último año, con al menos 5
  órdenes (menos es ruido de muestra pequeña).
- Crédito sin cobro: 45 días sin ningún pago registrado (alerta) /
  90 días (crítico). Los 45 días dan margen sobre el plazo típico de
  30 días antes de encender la alarma.
- Picos de movimiento: z robusto (MAD) >= 3 (alerta) / >= 5
  (crítico) del consumo del último día con datos vs. su histórico,
  exigiendo al menos 15 días con consumo previo (misma filosofía que
  anomalies/temporal.py: sin historia suficiente no se emite —
  mejor silencio que falso positivo). Materialidad: el día debe ser
  al menos el doble de la mediana (evita disparar por series
  constantes con micro-variaciones).
- Devoluciones: ratio devoluciones/ventas >= 5% (alerta) / >= 10%
  (crítico) en 90 días, con al menos 10 ventas (muestra mínima).
- Concentración de clientes: un cliente >= 40% (alerta) / >= 60%
  (crítico) de la facturación en ventas confirmadas de 365 días, con
  al menos 10 ventas (misma filosofía que R3).
- Reposición tardía: quiebre proyectado anterior a la fecha de entrega
  esperada de una orden abierta; severidad por inminencia del quiebre
  (<= 7 días crítico, resto alerta).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConfigShield:
    # -- R1: días de inventario --------------------------------------
    ventana_consumo_dias: int = 30      # días atrás para el ritmo medio
    dias_stock_alerta: int = 15
    dias_stock_critico: int = 7

    # -- R2: CxC vencidas ---------------------------------------------
    dias_mora_alerta: int = 30          # (alineado a tramos de cobranzas)
    dias_mora_critico: int = 90
    cxc_monto_minimo: str = "100"      # ignora saldos menores (ruido)

    # -- R3: concentración de proveedores ------------------------------
    proveedor_ventana_dias: int = 365
    proveedor_min_ordenes: int = 5
    proveedor_share_alerta: float = 0.40
    proveedor_share_critico: float = 0.60

    # -- R4: ventas a crédito sin cobro --------------------------------
    credito_dias_alerta: int = 45
    credito_dias_critico: int = 90

    # -- R5: movimientos inusuales -------------------------------------
    pico_ventana_dias: int = 90
    pico_min_historia_dias: int = 15   # días con consumo previo exigidos
    pico_z_alerta: float = 3.0
    pico_z_critico: float = 5.0
    pico_factor_materialidad: float = 2.0  # día >= 2x mediana

    # -- R6: devoluciones/cancelaciones ---------------------------------
    devoluciones_ventana_dias: int = 90
    devoluciones_ratio_alerta: float = 0.05
    devoluciones_ratio_critico: float = 0.10
    devoluciones_min_ventas: int = 10

    # -- R7: concentración de clientes ----------------------------------
    # Un cliente que concentra la facturación es riesgo de demanda
    # (si se va o deja de pagar, el hueco es grande). Mismos umbrales
    # que proveedores (40 % alerta / 60 % crítico) y misma exigencia
    # de muestra mínima (10 ventas en 365 días).
    cliente_ventana_dias: int = 365
    cliente_min_ventas: int = 10
    cliente_share_alerta: float = 0.40
    cliente_share_critico: float = 0.60

    # -- R8: reposición que llega tarde ----------------------------------
    # Si hay una orden de compra abierta (enviada/parcial) con fecha de
    # entrega esperada, pero el quiebre proyectado es ANTERIOR a esa
    # fecha, la reposición no alcanza. La severidad usa la misma
    # escala de inminencia que R1 (7/15 días de cobertura).
