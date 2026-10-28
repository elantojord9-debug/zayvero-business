"""ZAYVERO Business OS — Módulo de Facturación Electrónica RD (e-CF).

FASE 1 — DESARROLLO LOCAL CON DATOS SINTÉTICOS (issue #9).

=====================================================================
AVISOS LEGALES — LEER ANTES DE USAR
=====================================================================
- SIMULACIÓN — NO VÁLIDO ANTE DGII. Todo lo que genera este módulo
  (XML, PDF, e-NCF, paneles) es material de PRUEBA INTERNA.
- Este módulo NO está certificado por la DGII ni por ningún proveedor.
  No lo presente como sistema de facturación electrónica autorizado.
- NO conecta con la DGII real. El único proveedor disponible es
  `MockProvider` (simulador local offline). El modo por defecto es
  `disabled` y la producción real está bloqueada explícitamente.
- NO almacene certificados digitales ni credenciales de contribuyentes
  en el código, los tests ni los logs. Nunca simule una firma digital
  como si fuera verdadera.
- Marco de referencia: Ley 32-23 de Facturación Electrónica (RD).
  Solo emisores electrónicos autorizados pueden emitir e-CF reales,
  con firma digital y validación de la DGII.

Alcance de fase 1: tipos 31 (crédito fiscal), 32 (consumo), 33 (nota de
débito) y 34 (nota de crédito), con arquitectura extensible a
41, 43, 44, 45, 46 y 47.

Esquema propio: `os_ecf/schema/` con tracking `os_ecf_migrations`
(independiente de `os_migrations` y del flujo pre-staging).
"""

AVISO_SIMULACION = "SIMULACIÓN — NO VÁLIDO ANTE DGII"
AVISO_NO_CERTIFICADO = (
    "MÓDULO NO CERTIFICADO: no está autorizado por la DGII ni por ningún "
    "proveedor de facturación electrónica. Uso exclusivo de desarrollo "
    "local con datos sintéticos.")

__all__ = ["AVISO_SIMULACION", "AVISO_NO_CERTIFICADO"]
