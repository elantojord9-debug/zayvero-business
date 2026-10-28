"""ZAYVERO Business OS — LOTE 3: Inventario Inteligente.

Motor: engine.InventoryEngine
Análisis: rotacion (rotación + dead stock), alertas (niveles mínimos),
    predictor (pronóstico de demanda con regla de datos insuficientes)
Trazabilidad: lotes (lotes/seriales, vencimientos, devoluciones,
    garantías), integridad (guardia anti-negativos, ajustes y
    transferencias auditadas)
Integración: contratos (puertos para Compras, Ventas, Finanzas)
"""
from . import engine, alertas, rotacion, contratos  # noqa: F401
from . import predictor, lotes, integridad  # noqa: F401

__all__ = ["engine", "alertas", "rotacion", "contratos",
           "predictor", "lotes", "integridad"]


def rutas():
    """Contrato de rutas del bloque inventario: {ruta: fn}.

    Cada fn recibe (bundle, ctx, qob) y devuelve dict JSON-serializable.
    company_id siempre sale del ctx (anti-IDOR).
    """
    merged = {}
    for mod in (predictor, lotes, integridad):
        for k, v in mod.rutas().items():
            if k in merged:
                raise ValueError(f"ruta duplicada: {k}")
            if not k.startswith("inventario/"):
                raise ValueError(f"ruta sin prefijo inventario/: {k}")
            merged[k] = v
    return merged
