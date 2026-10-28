"""Modelos de datos del módulo e-CF (Fase 1, local, sintético).

CONTRATO DE INTERFACES — los demás módulos (`validacion`, `xml_ecf`,
`pdf`, `proveedor`, `flujo`, `almacen_pg`, `panel`) consumen estas clases.
No cambie firmas sin coordinar.

e-NCF: 13 caracteres = "E" + tipo (2 dígitos) + secuencia (10 dígitos).
Ejemplo: E310000000001.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP

from . import AVISO_SIMULACION


class ErrorECF(Exception):
    """Error de dominio del módulo e-CF."""


# --- Catálogo de tipos -------------------------------------------------------
# Fase 1 habilitados: 31, 32, 33, 34. El resto está definido para extensión
# futura (habilitado=False: validacion.py los rechaza en fase 1).
TIPOS_ECF: dict[str, dict] = {
    "31": {"nombre": "Factura de Crédito Fiscal", "habilitado": True},
    "32": {"nombre": "Factura de Consumo", "habilitado": True},
    "33": {"nombre": "Nota de Débito", "habilitado": True},
    "34": {"nombre": "Nota de Crédito", "habilitado": True},
    "41": {"nombre": "Compras", "habilitado": False},
    "43": {"nombre": "Gastos Menores", "habilitado": False},
    "44": {"nombre": "Regímenes Especiales", "habilitado": False},
    "45": {"nombre": "Gubernamental", "habilitado": False},
    "46": {"nombre": "Exportaciones", "habilitado": False},
    "47": {"nombre": "Pagos al Exterior", "habilitado": False},
}

NOTAS = {"33", "34"}  # requieren e-NCF original enlazado

_E_NCF_RE = re.compile(r"^E(\d{2})(\d{10})$")
_RNC_RE = re.compile(r"^\d{9}$|^\d{11}$")

MODOS = ("disabled", "mock", "sandbox", "certified_provider")
MODO_POR_DEFECTO = "disabled"


def formatear_e_ncf(tipo: str, secuencial: int) -> str:
    """Construye un e-NCF válido: E + tipo(2) + secuencia(10)."""
    tipo = (tipo or "").strip()
    if tipo not in TIPOS_ECF:
        raise ErrorECF(f"tipo e-CF desconocido: {tipo!r}")
    if secuencial < 1 or secuencial > 9_999_999_999:
        raise ErrorECF(f"secuencial fuera de rango: {secuencial}")
    return f"E{tipo}{secuencial:010d}"


def parsear_e_ncf(e_ncf: str) -> tuple[str, int]:
    """Valida formato y devuelve (tipo, secuencial)."""
    m = _E_NCF_RE.match((e_ncf or "").strip().upper())
    if not m:
        raise ErrorECF(
            f"e-NCF inválido: {e_ncf!r} (formato E + tipo(2) + secuencia(10))")
    tipo, sec = m.group(1), int(m.group(2))
    if tipo not in TIPOS_ECF:
        raise ErrorECF(f"tipo e-CF desconocido en {e_ncf!r}")
    return tipo, sec


def validar_rnc(rnc: str) -> str:
    """RNC: 9 u 11 dígitos. Devuelve normalizado o lanza ErrorECF."""
    rnc = re.sub(r"\D", "", rnc or "")
    if not _RNC_RE.match(rnc):
        raise ErrorECF(f"RNC inválido: {rnc!r} (9 u 11 dígitos)")
    return rnc


def _dec(v) -> Decimal:
    return v if isinstance(v, Decimal) else Decimal(str(v))


def _q2(v: Decimal) -> Decimal:
    return v.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


# --- Entidades ----------------------------------------------------------------

@dataclass
class Emisor:
    rnc: str
    razon_social: str
    nombre_comercial: str = ""

    def __post_init__(self):
        self.rnc = validar_rnc(self.rnc)
        if not (self.razon_social or "").strip():
            raise ErrorECF("emisor sin razón social")


@dataclass
class Receptor:
    rnc: str
    razon_social: str

    def __post_init__(self):
        self.rnc = validar_rnc(self.rnc)
        if not (self.razon_social or "").strip():
            raise ErrorECF("receptor sin razón social")


@dataclass
class Linea:
    descripcion: str
    cantidad: Decimal
    precio_unitario: Decimal
    tasa_itbis: Decimal | None = None  # None = exenta
    descuento: Decimal = Decimal("0")

    def __post_init__(self):
        self.cantidad = _dec(self.cantidad)
        self.precio_unitario = _dec(self.precio_unitario)
        self.descuento = _dec(self.descuento)
        if self.tasa_itbis is not None:
            self.tasa_itbis = _dec(self.tasa_itbis)
        if not (self.descripcion or "").strip():
            raise ErrorECF("línea sin descripción")
        if self.cantidad <= 0 or self.precio_unitario < 0:
            raise ErrorECF("cantidad/precio inválidos en línea")

    @property
    def subtotal(self) -> Decimal:
        return _q2(self.cantidad * self.precio_unitario - self.descuento)

    @property
    def itbis(self) -> Decimal:
        if self.tasa_itbis is None:
            return Decimal("0.00")
        return _q2(self.subtotal * self.tasa_itbis / Decimal("100"))


@dataclass
class Totales:
    subtotal: Decimal
    descuento_global: Decimal
    itbis: Decimal
    total: Decimal


def calcular_totales(lineas: list[Linea],
                     descuento_global: Decimal = Decimal("0")) -> Totales:
    """Totales con Decimal exacto: total = Σlíneas − desc. global + ITBIS."""
    descuento_global = _dec(descuento_global)
    subtotal = _q2(sum((l.subtotal for l in lineas), Decimal("0")))
    itbis = _q2(sum((l.itbis for l in lineas), Decimal("0")))
    total = _q2(subtotal - descuento_global + itbis)
    return Totales(subtotal=subtotal, descuento_global=_q2(descuento_global),
                   itbis=itbis, total=total)


ESTADOS = (
    "borrador", "validado", "pendiente_aprobacion", "encolado",
    "transmitido", "recibido", "aceptado", "observado", "rechazado",
    "contingencia", "anulado",
)

# Estados que cuentan como "emitido" para la regla cronológica oficial:
# una vez emitido, el comprobante no se edita y la fecha de emisión no
# puede retroceder para el mismo emisor+tipo.
ESTADOS_EMITIDOS = (
    "transmitido", "recibido", "aceptado", "observado", "rechazado",
    "contingencia",
)

# Estados cuyo contenido fiscal es inmutable (emitidos + anulado final).
# Solo se admiten transiciones de estado; el contenido se corrige con
# nota de crédito/débito, nunca por edición directa.
ESTADOS_INMUTABLES = ESTADOS_EMITIDOS + ("anulado",)

# Mapa canónico de transiciones de estado (orden ejecutiva P0 #11).
# Regla de negocio: un comprobante existente solo puede cambiar de
# estado si el nuevo estado está en TRANSICIONES_VALIDAS[estado_actual].
# Sin cambio de estado no se exige nada. `flujo.py` aplica su propio
# mapa (TRANSICIONES_PERMITIDAS) en la máquina de estados en memoria;
# este mapa es el que se hace cumplir en la persistencia (guardar).
TRANSICIONES_VALIDAS: dict[str, set[str]] = {
    "borrador": {"validado", "anulado"},
    "validado": {"pendiente_aprobacion", "anulado"},
    "pendiente_aprobacion": {"encolado", "anulado"},
    "encolado": {"transmitido", "anulado"},
    "transmitido": {"recibido", "rechazado", "contingencia"},
    "recibido": {"aceptado", "observado", "rechazado"},
    "aceptado": {"anulado"},
    "observado": {"aceptado", "rechazado", "anulado"},
    "rechazado": {"anulado"},
    "contingencia": {"transmitido"},
    "anulado": set(),
}


def validar_transicion_estado(estado_actual: str,
                              estado_nuevo: str) -> None:
    """Lanza ErrorECF si la transición no está en TRANSICIONES_VALIDAS.

    Sin cambio de estado no se exige nada (edición de contenido en
    borrador sigue permitida).
    """
    if estado_nuevo == estado_actual:
        return
    permitidos = TRANSICIONES_VALIDAS.get(estado_actual, set())
    if estado_nuevo not in permitidos:
        raise ErrorECF(
            "transición de estado no permitida: "
            f"{estado_actual!r} -> {estado_nuevo!r}")


@dataclass
class Comprobante:
    """Comprobante e-CF. `e_ncf` se asigna al reservar secuencia (almacen_pg)."""
    id: str
    company_id: str
    tipo: str
    emisor: Emisor
    receptor: Receptor
    lineas: list[Linea]
    estado: str = "borrador"
    modo: str = MODO_POR_DEFECTO
    e_ncf: str = ""
    encf_original: str = ""  # para notas 33/34
    descuento_global: Decimal = Decimal("0")
    moneda: str = "DOP"
    creado_en: str = ""
    fecha_emision: str = ""  # ISO; regla cronológica: no retrocede por emisor+tipo
    aviso: str = AVISO_SIMULACION

    def __post_init__(self):
        if self.tipo not in TIPOS_ECF:
            raise ErrorECF(f"tipo e-CF desconocido: {self.tipo!r}")
        if self.estado not in ESTADOS:
            raise ErrorECF(f"estado inválido: {self.estado!r}")
        if self.modo not in MODOS:
            raise ErrorECF(f"modo inválido: {self.modo!r}")
        self.descuento_global = _dec(self.descuento_global)
        if not self.creado_en:
            self.creado_en = datetime.now(timezone.utc).isoformat()
        if not self.fecha_emision:
            self.fecha_emision = self.creado_en

    @property
    def totales(self) -> Totales:
        return calcular_totales(self.lineas, self.descuento_global)

    def to_dict(self) -> dict:
        t = self.totales
        return {
            "id": self.id, "company_id": self.company_id, "tipo": self.tipo,
            "tipo_nombre": TIPOS_ECF[self.tipo]["nombre"],
            "e_ncf": self.e_ncf, "encf_original": self.encf_original,
            "estado": self.estado, "modo": self.modo, "moneda": self.moneda,
            "emisor": {"rnc": self.emisor.rnc,
                       "razon_social": self.emisor.razon_social,
                       "nombre_comercial": self.emisor.nombre_comercial},
            "receptor": {"rnc": self.receptor.rnc,
                         "razon_social": self.receptor.razon_social},
            "lineas": [{"descripcion": l.descripcion,
                        "cantidad": str(l.cantidad),
                        "precio_unitario": str(l.precio_unitario),
                        "tasa_itbis": (str(l.tasa_itbis)
                                       if l.tasa_itbis is not None else None),
                        "descuento": str(l.descuento),
                        "subtotal": str(l.subtotal),
                        "itbis": str(l.itbis)} for l in self.lineas],
            "totales": {"subtotal": str(t.subtotal),
                        "descuento_global": str(t.descuento_global),
                        "itbis": str(t.itbis), "total": str(t.total)},
            "creado_en": self.creado_en, "fecha_emision": self.fecha_emision,
            "aviso": self.aviso,
        }
