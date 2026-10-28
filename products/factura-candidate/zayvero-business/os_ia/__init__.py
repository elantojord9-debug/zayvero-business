"""BLOQUE IA — herramientas de inteligencia empresarial del OS.

Trabaja SOLO sobre el núcleo OS (OSBundle por company_id). No toca el
pipeline legacy (parquet) ni el panel (lo integra otro equipo).

Módulos:
  inspector   detección de anomalías/pérdidas con evidencia
  predict     pronóstico de demanda, backtesting, riesgo de faltantes
  temporada   descomposición estacional de la demanda (mes, día de
              semana) + pronóstico ajustado + reorden estacional
  shield      extiende las 8 reglas de shield/ con 2 nuevas (R9, R10)
  autopilot   propuestas en modo propuesta (NUNCA ejecuta)
  decisiones  centro de decisiones unificado
  ahorro      medidor de ahorro estimado vs. confirmado
  advisor     recomendaciones con evidencia, impacto, prioridad y
              seguimiento de resultados
  almacen     persistencia PG de propuestas y decisiones (P1c);
              solo marca estados, NUNCA ejecuta

Contrato de integración (obligatorio): cada módulo expone
`rutas()` -> {"MÉTODO ia/prefijo/nombre": fn} donde fn recibe
(bundle, ctx, query) en GET o (bundle, ctx, body) en POST y devuelve
un dict JSON-serializable. `ctx` es un dict con `company_id` (del
contexto autenticado); cada handler verifica que el bundle pertenezca
a esa empresa (anti-IDOR). Errores de validación: ValueError (el
integrador los traduce a 400).

Documentación: docs/bloque-ia.md
"""
from __future__ import annotations

__version__ = "1.0.0"

from . import (  # noqa: E402,F401
    advisor,
    ahorro,
    almacen,
    autopilot,
    base,
    decisiones,
    inspector,
    predict,
    shield,
    temporada,
)


def rutas() -> dict:
    """Todas las rutas del bloque IA agregadas (prefijo ia/)."""
    todas: dict = {}
    for modulo in (inspector, predict, shield, autopilot, decisiones,
                   ahorro, advisor, temporada, almacen):
        for nombre, fn in modulo.rutas().items():
            if nombre in todas:
                raise RuntimeError(f"ruta duplicada en bloque IA: {nombre}")
            todas[nombre] = fn
    return todas
