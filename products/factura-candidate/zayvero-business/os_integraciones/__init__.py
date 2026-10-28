"""BLOQUE Documentos/Integraciones — Adaptadores SIMULADOS (2026-10-13).

ADVERTENCIA CENTRAL, DECLARADA EN CÓDIGO, API Y DOCS:
    MODO = "simulado" en cada adaptador.

Son SIMULADORES con fixtures sintéticas locales. NUNCA hacen llamadas
de red externas (ningún import de requests/urllib/socket/http en estos
módulos; hay un test que lo verifica barriendo el código fuente).
Sirven para desarrollar y probar el CONTRATO de integración (qué
operaciones ofrece cada fuente, con qué campos) sin credenciales ni
cuentas reales.

Contrato común (ver clase AdaptadorSimulado):
    listar_productos() / listar_pedidos() / listar_clientes()
    leer_stock(producto_id=None)
    mapeo_campos()          # campo_origen -> campo del OS por entidad
    que_falta_para_real()   # credenciales, sandbox, autorización...

Adaptadores: Shopify, Odoo, SAP, QuickBooks, POS genérico.
"""
