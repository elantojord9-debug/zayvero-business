"""ZAYVERO Business OS — LOTE 7: Document Intelligence + Contador IA + Score.

- documentos: lectura de facturas PDF (pdftotext), parseo estructurado,
  validación aritmética, duplicados, conexión con el cruce del Lote 4.
  OCR como hook (no instalado: documentado como pendiente).
- contador: extracto bancario CSV, conciliación con propuestas (nada se
  aprueba solo), asientos Debe/Haber de gestión interna.
- score: Business Score 0-100 explicable; sin datos suficientes no inventa.

Sin afirmar cumplimiento fiscal: asientos y reportes son de gestión interna.
"""
from . import documentos, contador, score  # noqa: F401

__all__ = ["documentos", "contador", "score"]
