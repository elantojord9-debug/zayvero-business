"""
ZAYVERO BUSINESS — FASE 4C: Prediction Validation & Learning.

Capa independiente de validación del desempeño REAL de las predicciones
generadas por FASE 4A.

    PREDICCIÓN → ESPERAR DATOS REALES → OBTENER RESULTADO REAL
    → COMPARAR → MEDIR ERROR → EVALUAR DESEMPEÑO → REGISTRAR

Reglas fundamentales:
- Nunca se evalúa una predicción contra un resultado real de un periodo
  distinto (mismo indicador, misma entidad, mismo periodo).
- Una predicción solo es VALIDATED cuando existe un valor real válido del
  mismo periodo. Si el dato real aún no existe: PENDING.
- No se inventan valores ni se estima el resultado real.
- No hay data leakage: la validación usa únicamente filas del dataset cuyo
  periodo pertenece al horizonte pronosticado; las predicciones de 4A se
  leen como copias de solo lectura y nunca se modifican.
- FASE 4C NO mejora el modelo: primero mide su desempeño real.

API pública:
    validate_predictions(predictions_path, parquet_path, ...) -> dict
"""

from .report import build_validation_report, save_report

__all__ = ["build_validation_report", "save_report"]
