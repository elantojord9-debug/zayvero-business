"""Alertas de inventario: bajo mínimo, agotado. Datos puros (sin envío).

El envío (email/WhatsApp) lo hará el módulo de notificaciones (P1-8);
aquí solo se EVALÚA y se devuelve la lista para que el panel la muestre
o el Autopilot la proponga en modo propuesta.
"""
from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass
class AlertaInventario:
    tipo: str  # bajo_minimo | agotado
    producto_id: str
    almacen_id: str
    stock_actual: str
    nivel_minimo: str
    mensaje: str

    def to_dict(self):
        return {
            "tipo": self.tipo, "producto_id": self.producto_id,
            "almacen_id": self.almacen_id, "stock_actual": self.stock_actual,
            "nivel_minimo": self.nivel_minimo, "mensaje": self.mensaje,
        }


def evaluar_alertas(engine) -> list[AlertaInventario]:
    """Evalúa niveles mínimos y agotados sobre el stock derivado."""
    alertas: list[AlertaInventario] = []
    for (prod, alm), stock in sorted(engine.stock_empresa().items()):
        nivel = engine.nivel_minimo(prod, alm)
        if stock <= 0:
            alertas.append(AlertaInventario(
                tipo="agotado", producto_id=prod, almacen_id=alm,
                stock_actual=str(stock), nivel_minimo=str(nivel or 0),
                mensaje=f"{prod} agotado en {alm} (stock {stock})"))
        elif nivel is not None and stock <= nivel:
            alertas.append(AlertaInventario(
                tipo="bajo_minimo", producto_id=prod, almacen_id=alm,
                stock_actual=str(stock), nivel_minimo=str(nivel),
                mensaje=f"{prod} bajo mínimo en {alm}: {stock} <= {nivel}"))
    return alertas
