"""Adaptador SIMULADO de QuickBooks. MODO="simulado": datos sintéticos
locales, CERO llamadas de red. No usar contra una compañía real."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from os_integraciones.contrato import AdaptadorSimulado  # noqa: E402


class AdaptadorQuickBooks(AdaptadorSimulado):
    MODO = "simulado"
    FUENTE = "quickbooks"
    NOMBRE = "QuickBooks (simulado)"

    _PRODUCTOS = [
        {"Id": "1", "Name": "Consultoría por hora", "Sku": "QB-CONS-01",
         "UnitPrice": 2500.0, "PurchaseCost": 0.0,
         "CurrencyRef": "DOP", "QtyOnHand": 0, "Type": "Service"},
        {"Id": "2", "Name": "Licencia software anual", "Sku": "QB-LIC-02",
         "UnitPrice": 12000.0, "PurchaseCost": 7000.0,
         "CurrencyRef": "DOP", "QtyOnHand": 25, "Type": "Inventory"},
    ]
    _PEDIDOS = [
        {"Id": "9001", "DocNumber": "INV-2026-001",
         "TxnDate": "2026-10-07", "TotalAmt": 5000.0,
         "CurrencyRef": "DOP",
         "CustomerRef_email": "oficina@ejemplo.com",
         "Line": [{"Sku": "QB-CONS-01", "Qty": 2,
                   "UnitPrice": 2500.0}]},
    ]
    _CLIENTES = [
        {"Id": "7001", "DisplayName": "Oficina Contable Pérez",
         "PrimaryEmailAddr": "oficina@ejemplo.com",
         "PrimaryPhone": "+18095550333"},
    ]

    def listar_productos(self):
        return self._marcar(self._PRODUCTOS, "productos")

    def listar_pedidos(self):
        return self._marcar(self._PEDIDOS, "pedidos")

    def listar_clientes(self):
        return self._marcar(self._CLIENTES, "clientes")

    def leer_stock(self, producto_id=None):
        items = [{"sku": p["Sku"], "producto_id_origen": p["Id"],
                  "stock": str(p["QtyOnHand"]), "almacen": "quickbooks"}
                 for p in self._PRODUCTOS
                 if producto_id is None or p["Sku"] == producto_id
                 or p["Id"] == str(producto_id)]
        return self._marcar(items, "stock")

    def mapeo_campos(self):
        return {
            "productos": {"Sku": "sku", "Name": "nombre",
                          "UnitPrice": "precio", "PurchaseCost": "costo",
                          "CurrencyRef": "moneda",
                          "QtyOnHand": "stock_inicial",
                          "Type": "extra:tipo_qb"},
            "pedidos": {"DocNumber": "referencia", "TxnDate": "fecha",
                        "TotalAmt": "total", "CurrencyRef": "moneda"},
            "clientes": {"PrimaryEmailAddr": "email",
                         "DisplayName": "nombre",
                         "PrimaryPhone": "telefono"},
        }

    @classmethod
    def que_falta_para_real(cls):
        return [
            {"requisito": "Cuenta QuickBooks Online del cliente",
             "detalle": "compañía activa (realmId)"},
            {"requisito": "App registrada en Intuit Developer",
             "detalle": "client_id + client_secret de ZAYVERO"},
            {"requisito": "Autorización OAuth 2.0 del cliente",
             "detalle": ("flujo 'Connect to QuickBooks'; scopes "
                         "com.intuit.quickbooks.accounting")},
            {"requisito": "Sandbox de Intuit",
             "detalle": "compañía de pruebas para desarrollo"},
            {"requisito": "Manejo de refresh tokens",
             "detalle": "rotación cada 100 días; almacenamiento seguro"},
        ]
