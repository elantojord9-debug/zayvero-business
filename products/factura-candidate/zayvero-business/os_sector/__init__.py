"""BLOQUE SECTOR (2026-10-13) — Plantillas sectoriales RD.

Módulo de CONFIGURACIÓN, no de diagnóstico: cada empresa elige su
sector (tienda/electrodomésticos, restaurante, clínica, servicios) y
el OS le muestra qué KPIs mirar, con qué umbrales y qué módulos usar.
La plantilla nunca inventa datos: el panel marca cada KPI como
"disponibles"/"sin_datos" según lo que el bundle de la empresa tenga
registrado.

Submódulos:
- plantillas: las 4 plantillas + validación de referencias reales.
- estado: asignación por empresa (owner/admin), cambio auditado.
- rutas: contrato rutas() para el cableado (prefijo `sector/`).
- errores: ErrorSector / NoEncontrado / PermisoDenegado.

LIMITACIONES (ver docs/bloque-sector.md):
- Estado en memoria del bundle (como os_contab): sin wiring del panel
  no hay persistencia en snapshots/PG.
- El tipo de evento "sector" está en TIPOS_EVENTO (Python) pero el
  CHECK de os_eventos en PG aún no lo incluye: antes de cablear estas
  rutas con backend PG, el coordinador debe extender el CHECK (patrón
  de la migración 013).
"""
from __future__ import annotations

from .plantillas import (PLANTILLAS, SECTORES, NOMBRES_SECTOR,
                         FUENTES_VALIDAS, obtener_plantilla,
                         listar_plantillas, validar_plantilla)
from .estado import estado, asignar
from .errores import ErrorSector, NoEncontrado, PermisoDenegado
from .rutas import rutas

__all__ = ["PLANTILLAS", "SECTORES", "NOMBRES_SECTOR", "FUENTES_VALIDAS",
           "obtener_plantilla", "listar_plantillas", "validar_plantilla",
           "estado", "asignar", "rutas",
           "ErrorSector", "NoEncontrado", "PermisoDenegado"]
