"""Adaptador SIMULADO de Odoo. MODO="simulado": datos sintéticos
locales, CERO llamadas de red. No usar contra una instancia real."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from os_integraciones.contrato import AdaptadorSimulado  # noqa: E402


class AdaptadorOdoo(AdaptadorSimulado):
    MODO = "simulado"
    FUENTE = "odoo"
    NOMBRE = "Odoo (simulado)"

    _PRODUCTOS = [
        {"id": 11, "default_code": "OD-ACEITE-010",
         "name": "Aceite de motor 10W30 (L)", "list_price": 850.0,
         "standard_price": 620.0, "currency": "DOP",
         "qty_available": 200, "uom": "L"},
        {"id": 12, "default_code": "OD-FILTRO-020",
         "name": "Filtro de aceite", "list_price": 350.0,
         "standard_price": 210.0, "currency": "DOP",
         "qty_available": 80, "uom": "und"},
    ]
    _PEDIDOS = [
        {"id": 301, "name": "SO/2026/0001", "date_order": "2026-10-09",
         "amount_total": 1700.0, "currency": "DOP", "state": "sale",
         "partner_email": "taller@ejemplo.com",
         "order_line": [{"default_code": "OD-ACEITE-010",
                         "product_uom_qty": 2, "price_unit": 850.0}]},
    ]
    _CLIENTES = [
        {"id": 501, "name": "Taller Hermanos Díaz",
         "email": "taller@ejemplo.com", "phone": "+18095550111",
         "is_company": True},
    ]

    def listar_productos(self):
        return self._marcar(self._PRODUCTOS, "productos")

    def listar_pedidos(self):
        return self._marcar(self._PEDIDOS, "pedidos")

    def listar_clientes(self):
        return self._marcar(self._CLIENTES, "clientes")

    def leer_stock(self, producto_id=None):
        items = [{"sku": p["default_code"],
                  "producto_id_origen": str(p["id"]),
                  "stock": str(p["qty_available"]), "almacen": "WH/Stock"}
                 for p in self._PRODUCTOS
                 if producto_id is None
                 or p["default_code"] == producto_id
                 or str(p["id"]) == str(producto_id)]
        return self._marcar(items, "stock")

    def mapeo_campos(self):
        return {
            "productos": {"default_code": "sku", "name": "nombre",
                          "list_price": "precio",
                          "standard_price": "costo",
                          "currency": "moneda",
                          "qty_available": "stock_inicial"},
            "pedidos": {"name": "referencia", "date_order": "fecha",
                        "amount_total": "total", "currency": "moneda",
                        "state": "extra:estado"},
            "clientes": {"email": "email", "name": "nombre",
                         "phone": "telefono"},
        }

    @classmethod
    def que_falta_para_real(cls):
        return [
            {"requisito": "Instancia Odoo accesible (Online/SH/on-premise)",
             "detalle": "URL base de la instancia del cliente"},
            {"requisito": "Credenciales: API key de usuario",
             "detalle": ("Odoo 16+: clave API por usuario con permisos de "
                         "lectura en product, sale, stock, res.partner")},
            {"requisito": "Autorización del administrador Odoo",
             "detalle": "crear el usuario/clave API y definir alcance"},
            {"requisito": "Base de datos de pruebas",
             "detalle": "duplicado o entorno staging del cliente"},
            {"requisito": "Mapeo de almacenes",
             "detalle": "los stock.location de Odoo no son 1:1 con el OS"},
        ]
