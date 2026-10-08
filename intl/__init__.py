"""FASE 7C — Preparación global de ZAYVERO Business.

Paquete `intl`: configuración internacional por empresa (tenant).

- config.py     — normalización y validación de la configuración por empresa.
- formatting.py — presentación regional de fechas, números y moneda (sin FX).
- i18n.py       — catálogo de textos de interfaz (es/en, extensible a pt).

PRINCIPIO: PRODUCTO = GLOBAL DESDE EL DISEÑO. Ninguna lógica depende de
un país. La configuración pertenece exclusivamente a cada empresa y el
company_id siempre proviene del TenantContext.
"""
