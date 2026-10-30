"""Adaptador SIMULADO de SAP. MODO="simulado": datos sintéticos
locales, CERO llamadas de red. No usar contra un sistema SAP real.

Nota honesta: SAP real (S/4HANA, Business One) exige conector
certificado y proyecto de integración; este simulador solo fija el
contrato de campos que el OS esperaría.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from os_integraciones.contrato import AdaptadorSimulado  # noqa: E402


class AdaptadorSAP(AdaptadorSimulado):
    MODO = "simulado"
    FUENTE = "sap"
    NOMBRE = "SAP (simulado)"

    _PRODUCTOS = [
        {"MATNR": "SAP-000100", "MAKTX": "Tornillo M8x40 (caja 100)",
         "MEINS": "caja", "STPRS": "1250.00", "WAERS": "DOP",
         "LABST": 500, "WERKS": "1000", "LGORT": "0001"},
        {"MATNR": "SAP-000200", "MAKTX": "Tuerca M8 (caja 100)",
         "MEINS": "caja", "STPRS": "980.00", "WAERS": "DOP",
         "LABST": 320, "WERKS": "1000", "LGORT": "0001"},
    ]
    _PEDIDOS = [
        {"VBELN": "10002345", "ERDAT": "2026-10-08",
         "NETWR": "2500.00", "WAERK": "DOP", "AUART": "OR",
         "KUNNR_EMAIL": "ferreteria@ejemplo.com",
         "posiciones": [{"MATNR": "SAP-000100", "KWMENG": 2,
                         "NETPR": "1250.00"}]},
    ]
    _CLIENTES = [
        {"KUNNR": "100100", "NAME1": "Ferretería El Tornillo",
         "EMAIL": "ferreteria@ejemplo.com", "TELF1": "+18095550222"},
    ]

    def listar_productos(self):
        return self._marcar(self._PRODUCTOS, "productos")

    def listar_pedidos(self):
        return self._marcar(self._PEDIDOS, "pedidos")

    def listar_clientes(self):
        return self._marcar(self._CLIENTES, "clientes")

    def leer_stock(self, producto_id=None):
        items = [{"sku": p["MATNR"], "producto_id_origen": p["MATNR"],
                  "stock": str(p["LABST"]),
                  "almacen": f"{p['WERKS']}/{p['LGORT']}"}
                 for p in self._PRODUCTOS
                 if producto_id is None or p["MATNR"] == producto_id]
        return self._marcar(items, "stock")

    def mapeo_campos(self):
        return {
            "productos": {"MATNR": "sku", "MAKTX": "nombre",
                          "MEINS": "unidad", "STPRS": "costo",
                          "WAERS": "moneda", "LABST": "stock_inicial",
                          "WERKS/LGORT": "almacen_id"},
            "pedidos": {"VBELN": "referencia", "ERDAT": "fecha",
                        "NETWR": "total", "WAERK": "moneda",
                        "AUART": "extra:clase_documento"},
            "clientes": {"EMAIL": "email", "NAME1": "nombre",
                         "TELF1": "telefono", "KUNNR": "extra:id_origen"},
        }

    @classmethod
    def que_falta_para_real(cls):
        return [
            {"requisito": "Sistema SAP del cliente (S/4HANA o Business One)",
             "detalle": "versión y módulos activos (MM/SD/FI)"},
            {"requisito": "Credenciales: usuario técnico + destino RFC/OData",
             "detalle": ("S/4HANA: OData (API_PRODUCT_SRV_A2, "
                         "API_SALES_ORDER_SRV); B1: Service Layer")},
            {"requisito": "Autorización del basis/admin SAP",
             "detalle": "roles y autorizaciones para el usuario técnico"},
            {"requisito": "Proyecto de integración con consultor SAP",
             "detalle": ("mapeo de centros/almacenes (WERKS/LGORT), "
                         "clases de documento y esquema fiscal")},
            {"requisito": "Ambiente QA de SAP",
             "detalle": "mandante de pruebas, nunca desarrollo directo en PRD"},
            {"requisito": "Evaluación de licenciamiento",
             "detalle": "usuarios indirectos/digital access según contrato SAP"},
        ]
