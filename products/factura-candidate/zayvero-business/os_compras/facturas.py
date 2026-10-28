"""Registro de facturas de proveedor (para la conciliación 3 vías).

Cada factura enlaza con su orden (compra_id) y trae sus líneas con
precio y cantidad facturados. Detección de duplicadas: mismo
(proveedor_id, numero) con distinto factura_id -> se rechaza el registro
y se reporta la evidencia (no se permite cargar dos veces la misma
factura del proveedor).

Registro en memoria por empresa (igual que el resto del bloque).
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass, field
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402


class ErrorFacturas(Exception):
    pass


class FacturaDuplicada(ErrorFacturas):
    pass


@dataclass
class LineaFactura:
    producto_id: str
    cantidad: str
    precio_unitario: str

    def validate(self):
        if not self.producto_id:
            raise ValueError("producto_id obligatorio")
        if _dec(self.cantidad) <= 0:
            raise ValueError("cantidad debe ser > 0")
        if _dec(self.precio_unitario) < 0:
            raise ValueError("precio_unitario no puede ser negativo")

    def to_dict(self):
        return {"producto_id": self.producto_id,
                "cantidad": str(_dec(self.cantidad)),
                "precio_unitario": str(_dec(self.precio_unitario))}


@dataclass
class FacturaProveedor:
    factura_id: str
    company_id: str
    proveedor_id: str
    numero: str                 # número del proveedor (NCF/folio)
    compra_id: str = ""         # orden vinculada (puede estar vacía)
    lineas: list = field(default_factory=list)
    fecha: str = ""
    moneda: str = "DOP"

    def validate(self):
        for f in ("factura_id", "company_id", "proveedor_id", "numero"):
            if not getattr(self, f):
                raise ValueError(f"{f} es obligatorio")
        if not self.lineas:
            raise ValueError("la factura necesita al menos una línea")
        for l in self.lineas:
            l.validate()

    def total(self) -> Decimal:
        return sum((_dec(l.cantidad) * _dec(l.precio_unitario)
                    for l in self.lineas), Decimal("0"))

    def to_dict(self):
        return {
            "factura_id": self.factura_id, "company_id": self.company_id,
            "proveedor_id": self.proveedor_id, "numero": self.numero,
            "compra_id": self.compra_id,
            "lineas": [l.to_dict() for l in self.lineas],
            "fecha": self.fecha, "moneda": self.moneda,
            "total": str(self.total()),
        }


class RegistroFacturas:
    _instancias: dict[str, "RegistroFacturas"] = {}
    _glob_lock = threading.Lock()

    def __init__(self, company_id: str):
        self.company_id = company_id
        self._lock = threading.RLock()
        self._facturas: dict[str, FacturaProveedor] = {}

    @classmethod
    def por_empresa(cls, company_id: str) -> "RegistroFacturas":
        with cls._glob_lock:
            return cls._instancias.setdefault(company_id, cls(company_id))

    def registrar(self, f: FacturaProveedor) -> FacturaProveedor:
        with self._lock:
            f.validate()
            if f.company_id != self.company_id:
                raise ErrorFacturas("factura de otra empresa")
            if f.factura_id in self._facturas:
                raise ErrorFacturas(f"factura_id duplicado: {f.factura_id}")
            for otra in self._facturas.values():
                if (otra.proveedor_id == f.proveedor_id
                        and otra.numero == f.numero):
                    raise FacturaDuplicada(
                        f"{f.proveedor_id} #{f.numero} ya registrada como "
                        f"{otra.factura_id}")
            self._facturas[f.factura_id] = f
            return f

    def obtener(self, factura_id: str) -> FacturaProveedor | None:
        with self._lock:
            return self._facturas.get(factura_id)

    def listar(self, proveedor_id: str | None = None,
               compra_id: str | None = None) -> list[FacturaProveedor]:
        with self._lock:
            return [f for f in self._facturas.values()
                    if (proveedor_id is None
                        or f.proveedor_id == proveedor_id)
                    and (compra_id is None or f.compra_id == compra_id)]


# ---- contrato de rutas -------------------------------------------------
def _ctx_company(ctx) -> str:
    c = getattr(ctx, "company_id", "") or ""
    if not c:
        raise ValueError("ctx sin company_id")
    return c


def _fn_facturas(bundle, ctx, qob):
    c = _ctx_company(ctx)
    if bundle.compras.company_id != c:
        raise ValueError("empresa ajena")
    reg = RegistroFacturas.por_empresa(c)
    q = qob or {}
    if isinstance(q, dict) and q.get("_metodo") == "POST":
        f = FacturaProveedor(
            factura_id=str(q["factura_id"]), company_id=c,
            proveedor_id=str(q["proveedor_id"]), numero=str(q["numero"]),
            compra_id=str(q.get("compra_id", "")),
            lineas=[LineaFactura(str(l["producto_id"]), str(l["cantidad"]),
                                 str(l["precio_unitario"]))
                    for l in q["lineas"]],
            fecha=str(q.get("fecha", "")), moneda=str(q.get("moneda", "DOP")))
        try:
            reg.registrar(f)
        except FacturaDuplicada as e:
            return {"ok": False, "error": "factura_duplicada",
                    "detalle": str(e)}
        return {"ok": True, "factura": f.to_dict()}
    return {"company_id": c,
            "facturas": [f.to_dict() for f in reg.listar(
                proveedor_id=q.get("proveedor_id"),
                compra_id=q.get("compra_id"))]}


def rutas():
    return {"compras/facturas": _fn_facturas}
