"""EXTRA 2 (sprint 12-oct-2026) — Simulator.

Simulación de compras, precios, inventario y rentabilidad para
ZAYVERO Business OS.

Diseño:
- `engine.Simulador`: cálculo puro sobre el OSBundle de UNA empresa.
  Solo LEE (stock, costos, ventas registradas); NUNCA muta motores,
  nunca toca la BD, nunca audita. Cada resultado incluye `insumos`,
  `formulas` y `supuestos`: nada de cajas negras.
- `api.handle_simulator`: expone `POST /api/v1/os/simulator/escenario`
  con el mismo preámbulo de seguridad que el resto de rutas OS
  (auth, permiso `os.write`, CSRF, rate limiting, aislamiento por
  empresa vía `company_id` del ctx). A diferencia de las rutas de
  mutación, NO pasa por `mutar_os`: no hay nada que persistir.

Fuente de datos (documentado en docs/simulator.md): el OSBundle de la
empresa, que en modo postgres se hidrata desde los snapshots (fuente
de verdad) y en modo memoria desde los motores en vivo.
"""
from .engine import Simulador, ErrorSimulador

__all__ = ["Simulador", "ErrorSimulador"]
