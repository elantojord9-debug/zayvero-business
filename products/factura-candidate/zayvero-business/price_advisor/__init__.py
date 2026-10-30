"""EXTRA 3 (sprint 12-oct-2026) — Price Advisor.

Recomendaciones de precios basadas en costos y márgenes comprobables.

Reutiliza los motores ya probados de `simulator` (`Simulador`), que a su
vez lee el costo promedio real de `os_finanzas` (CostoInventario) y la
demanda histórica de las ventas registradas. No duplica lógica de costos.

Fórmula central: precio_recomendado = costo / (1 - margen_objetivo).

Reglas de prudencia:
- Nunca recomendar por debajo del costo: piso = costo × (1 + margen_mínimo).
- Sin costo confiable (costo ≤ 0 o sin unidades con costo) o sin historial
  de ventas (sin precio actual contra el que comparar): estado
  "datos_insuficientes", NO se inventa nada (igual que el Business Score).
- Todo resultado trae insumos, fórmulas y supuestos: nada de cajas negras.
"""
from .engine import AsesorPrecios, ErrorAdvisor  # noqa: F401
