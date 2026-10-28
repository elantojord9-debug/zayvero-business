"""ZAYVERO Business OS — Contrato de datos transversal (LOTE 1).

Esquema compartido por TODOS los módulos del OS: inventario, compras, ventas,
gastos, clientes, proveedores, tesorería. Diseñado una vez, usado en todas partes.

Reglas del contrato:
- Toda entidad lleva `company_id` (aislamiento multiempresa a nivel de esquema).
- Toda entidad lleva `schema_version` (evolución sin romper lectores viejos).
- Dinero: Decimal + código ISO 4217 (nunca float para contabilidad).
- IDs: strings opacos; los genera quien crea el registro.
- Fechas: ISO-8601 UTC ("2026-10-11T20:00:00Z").
- `validate()` falla rápido ante datos inválidos; `to_dict()`/`from_dict()`
  son la única forma de serializar (JSON-safe).

Versión del esquema: ver SCHEMA_VERSION en contract.py.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation

from .contract import (
    EstadoCompra,
    EstadoDevolucion,
    EstadoPago,
    EstadoVenta,
    MedioPago,
    TipoDevolucion,
    TipoMovimiento,
    TipoPago,
    require_iso_currency,
    utcnow_iso,
)


def _dec(v) -> Decimal:
    try:
        return Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        raise ValueError(f"monto inválido: {v!r}")


@dataclass
class Producto:
    producto_id: str
    company_id: str
    sku: str
    nombre: str
    descripcion: str = ""
    categoria: str = ""
    unidad: str = "unidad"
    precio_venta: str = "0"       # Decimal serializado como string
    costo_promedio: str = "0"     # Decimal serializado como string
    moneda: str = ""              # ISO 4217
    activo: bool = True
    schema_version: str = "1"

    def validate(self):
        if not self.producto_id or not self.company_id or not self.sku or not self.nombre:
            raise ValueError("producto_id, company_id, sku y nombre son obligatorios")
        require_iso_currency(self.moneda, allow_empty=True)
        _dec(self.precio_venta)
        _dec(self.costo_promedio)

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Producto":
        o = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        o.validate()
        return o


@dataclass
class Almacen:
    almacen_id: str
    company_id: str
    nombre: str
    tipo: str = "almacen"  # almacen | sucursal
    direccion: str = ""
    activo: bool = True
    schema_version: str = "1"

    def validate(self):
        if not self.almacen_id or not self.company_id or not self.nombre:
            raise ValueError("almacen_id, company_id y nombre son obligatorios")
        if self.tipo not in ("almacen", "sucursal"):
            raise ValueError(f"tipo inválido: {self.tipo}")

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Almacen":
        o = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        o.validate()
        return o


@dataclass
class MovimientoInventario:
    """Cada entrada/salida/ajuste/transferencia/devolución de stock.

    El stock actual SIEMPRE se deriva sumando movimientos (nunca se guarda
    un "stock actual" mutable que pueda descuadrarse).
    """
    movimiento_id: str
    company_id: str
    producto_id: str
    almacen_id: str
    tipo: str                      # TipoMovimiento
    cantidad: str                  # Decimal string; signo según tipo
    costo_unitario: str = "0"
    moneda: str = ""
    fecha: str = ""
    referencia_id: str = ""        # compra_id / venta_id / devolucion_id
    motivo: str = ""
    schema_version: str = "1"

    def validate(self):
        for f in ("movimiento_id", "company_id", "producto_id", "almacen_id"):
            if not getattr(self, f):
                raise ValueError(f"{f} es obligatorio")
        if self.tipo not in TipoMovimiento.VALORES:
            raise ValueError(f"tipo de movimiento inválido: {self.tipo}")
        _dec(self.cantidad)
        require_iso_currency(self.moneda, allow_empty=True)

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "MovimientoInventario":
        o = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        o.validate()
        return o


@dataclass
class LineaDoc:
    producto_id: str
    cantidad: str
    precio_unitario: str = "0"
    descuento_pct: str = "0"

    def validate(self):
        if not self.producto_id:
            raise ValueError("producto_id obligatorio en línea")
        if _dec(self.cantidad) <= 0:
            raise ValueError("cantidad debe ser > 0")
        _dec(self.precio_unitario)

    def to_dict(self): return asdict(self)


@dataclass
class Compra:
    compra_id: str
    company_id: str
    proveedor_id: str
    fecha: str = ""
    estado: str = EstadoCompra.BORRADOR
    lineas: list = field(default_factory=list)  # list[LineaDoc] serializada
    moneda: str = ""
    notas: str = ""
    schema_version: str = "1"

    def validate(self):
        for f in ("compra_id", "company_id", "proveedor_id"):
            if not getattr(self, f):
                raise ValueError(f"{f} es obligatorio")
        if self.estado not in EstadoCompra.VALORES:
            raise ValueError(f"estado inválido: {self.estado}")
        require_iso_currency(self.moneda, allow_empty=True)

    def total(self) -> Decimal:
        return sum(_dec(l["cantidad"]) * _dec(l.get("precio_unitario", 0))
                   for l in self.lineas)

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Compra":
        o = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        o.validate()
        return o


@dataclass
class Venta:
    venta_id: str
    company_id: str
    cliente_id: str = ""
    almacen_id: str = ""           # sucursal donde se vendió
    fecha: str = ""
    estado: str = EstadoVenta.BORRADOR
    lineas: list = field(default_factory=list)
    moneda: str = ""
    medio_pago: str = ""           # MedioPago
    notas: str = ""
    schema_version: str = "1"

    def validate(self):
        if not self.venta_id or not self.company_id:
            raise ValueError("venta_id y company_id son obligatorios")
        if self.estado not in EstadoVenta.VALORES:
            raise ValueError(f"estado inválido: {self.estado}")
        if self.medio_pago and self.medio_pago not in MedioPago.VALORES:
            raise ValueError(f"medio de pago inválido: {self.medio_pago}")
        require_iso_currency(self.moneda, allow_empty=True)

    def total(self) -> Decimal:
        tot = Decimal("0")
        for l in self.lineas:
            sub = _dec(l["cantidad"]) * _dec(l.get("precio_unitario", 0))
            desc = _dec(l.get("descuento_pct", 0)) / 100
            tot += sub * (1 - desc)
        return tot

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Venta":
        o = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        o.validate()
        return o


@dataclass
class Gasto:
    gasto_id: str
    company_id: str
    categoria: str
    descripcion: str = ""
    monto: str = "0"
    moneda: str = ""
    fecha: str = ""
    proveedor_id: str = ""
    comprobante: str = ""          # nº de factura/comprobante
    schema_version: str = "1"

    def validate(self):
        if not self.gasto_id or not self.company_id or not self.categoria:
            raise ValueError("gasto_id, company_id y categoria son obligatorios")
        if _dec(self.monto) < 0:
            raise ValueError("el monto no puede ser negativo")
        require_iso_currency(self.moneda, allow_empty=True)

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Gasto":
        o = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        o.validate()
        return o


@dataclass
class Cliente:
    cliente_id: str
    company_id: str
    nombre: str
    email: str = ""
    telefono: str = ""
    segmento: str = ""
    activo: bool = True
    schema_version: str = "1"

    def validate(self):
        if not self.cliente_id or not self.company_id or not self.nombre:
            raise ValueError("cliente_id, company_id y nombre son obligatorios")

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Cliente":
        o = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        o.validate()
        return o


@dataclass
class Proveedor:
    proveedor_id: str
    company_id: str
    nombre: str
    contacto: str = ""
    email: str = ""
    telefono: str = ""
    dias_entrega_promedio: str = ""
    activo: bool = True
    schema_version: str = "1"

    def validate(self):
        if not self.proveedor_id or not self.company_id or not self.nombre:
            raise ValueError("proveedor_id, company_id y nombre son obligatorios")

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Proveedor":
        o = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        o.validate()
        return o


@dataclass
class Pago:
    """Cobro (CxC) o pago (CxP): trazable a su documento origen."""
    pago_id: str
    company_id: str
    tipo: str                      # TipoPago: cobro | pago
    referencia_id: str = ""        # venta_id o compra_id
    monto: str = "0"
    moneda: str = ""
    fecha: str = ""
    medio: str = ""                # MedioPago
    estado: str = EstadoPago.PENDIENTE
    notas: str = ""
    schema_version: str = "1"

    def validate(self):
        if not self.pago_id or not self.company_id:
            raise ValueError("pago_id y company_id son obligatorios")
        if self.tipo not in TipoPago.VALORES:
            raise ValueError(f"tipo inválido: {self.tipo}")
        if self.estado not in EstadoPago.VALORES:
            raise ValueError(f"estado inválido: {self.estado}")
        if _dec(self.monto) <= 0:
            raise ValueError("el monto debe ser > 0")
        require_iso_currency(self.moneda, allow_empty=True)

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Pago":
        o = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        o.validate()
        return o


@dataclass
class Devolucion:
    devolucion_id: str
    company_id: str
    tipo: str                      # TipoDevolucion: cliente | proveedor
    referencia_id: str = ""        # venta_id o compra_id
    lineas: list = field(default_factory=list)
    motivo: str = ""
    estado: str = EstadoDevolucion.SOLICITADA
    fecha: str = ""
    schema_version: str = "1"

    def validate(self):
        if not self.devolucion_id or not self.company_id:
            raise ValueError("devolucion_id y company_id son obligatorios")
        if self.tipo not in TipoDevolucion.VALORES:
            raise ValueError(f"tipo inválido: {self.tipo}")
        if self.estado not in EstadoDevolucion.VALORES:
            raise ValueError(f"estado inválido: {self.estado}")

    def to_dict(self): return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Devolucion":
        o = cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})
        o.validate()
        return o


def calcular_stock(movimientos: list[MovimientoInventario]) -> dict[tuple[str, str], Decimal]:
    """Stock actual derivado de movimientos: (producto_id, almacen_id) -> cantidad.

    Nunca se almacena un 'stock actual' mutable: siempre se deriva.
    """
    stock: dict[tuple[str, str], Decimal] = {}
    for m in movimientos:
        m.validate()
        key = (m.producto_id, m.almacen_id)
        stock[key] = stock.get(key, Decimal("0")) + _dec(m.cantidad)
    return stock
