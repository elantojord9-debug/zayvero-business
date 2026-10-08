"""ZAYVERO Business — FASE 3 Dashboard MVP.

Estructura:
  adapter.py  — DATA LAYER: adaptador de lectura (solo lectura) sobre los
                outputs reales de FASE 2B (Business Findings) y FASE 2C
                (Business Context Findings). No modifica ni recalcula nada.
  service.py  — BUSINESS LOGIC: conteos, ordenamiento, filtros, búsqueda,
                panorama. Funciones puras, sin dependencia del transporte.
  server.py   — PRESENTATION (transporte): servidor HTTP con la stdlib de
                Python que expone una API JSON y sirve los archivos estáticos
                del frontend.
  static/     — PRESENTATION (UI): HTML + CSS + JS vanilla.
"""
