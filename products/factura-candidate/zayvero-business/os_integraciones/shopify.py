"""Adaptador SIMULADO de Shopify. MODO="simulado": datos sintéticos
locales, CERO llamadas de red. No usar contra una tienda real."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from os_integraciones.contrato import AdaptadorSimulado  # noqa: E402


class AdaptadorShopify(AdaptadorSimulado):
    MODO = "simulado"
    FUENTE = "shopify"
    NOMBRE = "Shopify (simulado)"

    _PRODUCTOS = [
        {"id": "gid://shopify/Product/101", "sku": "SH-CAM-001",
         "title": "Camiseta básica algodón", "price": "599.00",
         "currency": "DOP", "inventory_quantity": 120,
         "vendor": "Taller Demo RD", "status": "active"},
        {"id": "gid://shopify/Product/102", "sku": "SH-PANT-002",
         "title": "Pantalón jean clásico", "price": "1499.00",
         "currency": "DOP", "inventory_quantity": 45,
         "vendor": "Taller Demo RD", "status": "active"},
        {"id": "gid://shopify/Product/103", "sku": "SH-GORRA-003",
         "title": "Gorra bordada", "price": "450.00",
         "currency": "DOP", "inventory_quantity": 0,
         "vendor": "Taller Demo RD", "status": "active"},
    ]
    _PEDIDOS = [
        {"id": "gid://shopify/Order/5001", "name": "#1001",
         "created_at": "2026-10-10", "currency": "DOP",
         "total_price": "1198.00", "financial_status": "paid",
         "fulfillment_status": "fulfilled",
         "lineas": [{"sku": "SH-CAM-001", "quantity": 2,
                      "price": "599.00"}],
         "customer": {"email": "cliente1@ejemplo.com"}},
        {"id": "gid://shopify/Order/5002", "name": "#1002",
         "created_at": "2026-10-11", "currency": "DOP",
         "total_price": "1499.00", "financial_status": "pending",
         "fulfillment_status": "unfulfilled",
         "lineas": [{"sku": "SH-PANT-002", "quantity": 1,
                      "price": "1499.00"}],
         "customer": {"email": "cliente2@ejemplo.com"}},
    ]
    _CLIENTES = [
        {"id": "gid://shopify/Customer/201", "email": "cliente1@ejemplo.com",
         "first_name": "María", "last_name": "Pérez",
         "phone": "+18095550001", "orders_count": 3,
         "total_spent": "3594.00"},
        {"id": "gid://shopify/Customer/202", "email": "cliente2@ejemplo.com",
         "first_name": "Juan", "last_name": "Santos",
         "phone": "+18095550002", "orders_count": 1,
         "total_spent": "1499.00"},
    ]

    def listar_productos(self):
        return self._marcar(self._PRODUCTOS, "productos")

    def listar_pedidos(self):
        return self._marcar(self._PEDIDOS, "pedidos")

    def listar_clientes(self):
        return self._marcar(self._CLIENTES, "clientes")

    def leer_stock(self, producto_id=None):
        items = [{"sku": p["sku"],
                  "producto_id_origen": p["id"],
                  "stock": str(p["inventory_quantity"]),
                  "almacen": "shopify-default"}
                 for p in self._PRODUCTOS
                 if producto_id is None or p["sku"] == producto_id
                 or p["id"] == producto_id]
        return self._marcar(items, "stock")

    def mapeo_campos(self):
        return {
            "productos": {"sku": "sku", "title": "nombre",
                          "price": "precio", "currency": "moneda",
                          "inventory_quantity": "stock_inicial",
                          "vendor": "extra:vendor", "id": "extra:id_origen"},
            "pedidos": {"name": "referencia", "created_at": "fecha",
                        "total_price": "total", "currency": "moneda",
                        "financial_status": "extra:estado_pago",
                        "lineas": "lineas"},
            "clientes": {"email": "email",
                         "first_name+last_name": "nombre",
                         "phone": "telefono",
                         "orders_count": "extra:compras_origen"},
        }

    @classmethod
    def que_falta_para_real(cls):
        return [
            {"requisito": "Tienda Shopify real + plan con API habilitada",
             "detalle": "URL myshopify.com de la tienda del cliente"},
            {"requisito": "Credenciales: Admin API access token",
             "detalle": ("App privada/custom app con scopes read_products, "
                         "read_orders, read_customers, read_inventory")},
            {"requisito": "Autorización OAuth del dueño de la tienda",
             "detalle": "flujo OAuth; el token lo instala el cliente"},
            {"requisito": "Sandbox de pruebas",
             "detalle": "tienda de desarrollo de Shopify Partners"},
            {"requisito": "Webhooks (opcional, fase 2)",
             "detalle": "orders/create, inventory_levels/update"},
        ]
