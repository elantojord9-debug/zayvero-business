"""Adaptador SIMULADO de POS genérico. MODO="simulado": datos
sintéticos locales, CERO llamadas de red.

Representa un punto de venta típico de PYME dominicana (facturación
diaria, inventario por sucursal). El contrato es intencionalmente
genérico: un POS real se mapea a estos mismos campos.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from os_integraciones.contrato import AdaptadorSimulado  # noqa: E402


class AdaptadorPOS(AdaptadorSimulado):
    MODO = "simulado"
    FUENTE = "pos"
    NOMBRE = "POS genérico (simulado)"

    _PRODUCTOS = [
        {"codigo": "POS-CAFE-01", "descripcion": "Café molido 1 lb",
         "precio": "385.00", "costo": "240.00", "moneda": "DOP",
         "existencia": 60, "sucursal": "suc-01"},
        {"codigo": "POS-AZUCAR-02", "descripcion": "Azúcar 5 lb",
         "precio": "295.00", "costo": "190.00", "moneda": "DOP",
         "existencia": 40, "sucursal": "suc-01"},
    ]
    _PEDIDOS = [
        {"numero": "F-2026-881", "fecha": "2026-10-12",
         "total": "770.00", "moneda": "DOP", "tipo": "contado",
         "cliente_email": "vecina@ejemplo.com",
         "detalle": [{"codigo": "POS-CAFE-01", "cantidad": 2,
                      "precio": "385.00"}]},
    ]
    _CLIENTES = [
        {"codigo": "C-001", "nombre": "Doña Vecina",
         "email": "vecina@ejemplo.com", "telefono": "+18095550444"},
    ]

    def listar_productos(self):
        return self._marcar(self._PRODUCTOS, "productos")

    def listar_pedidos(self):
        return self._marcar(self._PEDIDOS, "pedidos")

    def listar_clientes(self):
        return self._marcar(self._CLIENTES, "clientes")

    def leer_stock(self, producto_id=None):
        items = [{"sku": p["codigo"], "producto_id_origen": p["codigo"],
                  "stock": str(p["existencia"]), "almacen": p["sucursal"]}
                 for p in self._PRODUCTOS
                 if producto_id is None or p["codigo"] == producto_id]
        return self._marcar(items, "stock")

    def mapeo_campos(self):
        return {
            "productos": {"codigo": "sku", "descripcion": "nombre",
                          "precio": "precio", "costo": "costo",
                          "moneda": "moneda", "existencia": "stock_inicial",
                          "sucursal": "almacen_id"},
            "pedidos": {"numero": "referencia", "fecha": "fecha",
                        "total": "total", "moneda": "moneda",
                        "tipo": "extra:tipo_venta"},
            "clientes": {"email": "email", "nombre": "nombre",
                         "telefono": "telefono",
                         "codigo": "extra:id_origen"},
        }

    @classmethod
    def que_falta_para_real(cls):
        return [
            {"requisito": "Identificar el POS real del cliente",
             "detalle": ("marca/modelo: los POS de PYME varían mucho; "
                         "algunos no tienen API")},
            {"requisito": "Método de extracción disponible",
             "detalle": ("API, exportación CSV programada, o conector "
                         "del fabricante")},
            {"requisito": "Credenciales del método elegido",
             "detalle": "token, usuario de integración o carpeta compartida"},
            {"requisito": "Autorización del dueño del negocio",
             "detalle": "acceso a datos de ventas e inventario"},
            {"requisito": "Mapeo de sucursales",
             "detalle": "cada sucursal del POS -> almacen_id del OS"},
            {"requisito": "Frecuencia de sincronización acordada",
             "detalle": "diaria/horaria según volumen (cierre de caja)"},
        ]
