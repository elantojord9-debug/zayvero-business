"""BLOQUE 5 — Catálogo maestro: entidades, excepciones y validaciones.

Todo lo de este módulo es puro (sin PG): las reglas se prueban sin base
de datos. La persistencia vive en `catalogo/api.py`.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.contract import require_iso_currency  # noqa: E402


# ---- excepciones ------------------------------------------------------
class ErrorCatalogo(Exception):
    """Base de errores del catálogo."""


class ValidacionCatalogo(ErrorCatalogo):
    """Dato inválido (precio/costo negativo, nombre vacío, etc.)."""


class SKUduplicado(ErrorCatalogo):
    """El SKU ya existe en la empresa."""


class ProductoNoExiste(ErrorCatalogo):
    pass


class CategoriaNoExiste(ErrorCatalogo):
    pass


class AlmacenNoExiste(ErrorCatalogo):
    pass


class CategoriaConProductos(ErrorCatalogo):
    """No se puede eliminar una categoría con productos asociados."""


class ConflictoVersion(ErrorCatalogo):
    """Bloqueo optimista: otro proceso actualizó primero."""


# ---- validaciones puras ------------------------------------------------
MAX_ID = 64


def _dec(valor, campo: str) -> Decimal:
    try:
        d = Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        raise ValidacionCatalogo(f"{campo} debe ser numérico: {valor!r}")
    return d


def validar_id(valor: str, campo: str) -> str:
    v = (valor or "").strip()
    if not v:
        raise ValidacionCatalogo(f"{campo} no puede estar vacío")
    if len(v) > MAX_ID:
        raise ValidacionCatalogo(
            f"{campo} excede {MAX_ID} caracteres: {v!r}")
    return v


def validar_moneda(moneda: str) -> str:
    m = (moneda or "").strip().upper()
    try:
        require_iso_currency(m)
    except ValueError:
        raise ValidacionCatalogo(f"moneda inválida (se espera ISO 4217): {moneda!r}")
    return m


def validar_precio(valor) -> Decimal:
    p = _dec(valor, "precio")
    if p < 0:
        raise ValidacionCatalogo(f"precio no puede ser negativo: {p}")
    return p


def validar_costo(valor) -> Decimal:
    c = _dec(valor, "costo")
    if c < 0:
        raise ValidacionCatalogo(f"costo no puede ser negativo: {c}")
    return c


def advertencias_precio_costo(precio: Decimal | None,
                              costo: Decimal | None) -> list[str]:
    """precio < costo es ADVERTENCIA (no error): hay casos legítimos
    (liquidación, error de carga por corregir, precio de lanzamiento)."""
    if precio is not None and costo is not None and precio < costo:
        return [f"advertencia: precio ({precio}) < costo ({costo}); "
                "se guarda igual, revíselo"]
    return []


# ---- entidades ----------------------------------------------------------
@dataclass
class Categoria:
    company_id: str
    categoria_id: str
    nombre: str
    padre_id: str | None = None
    activa: bool = True
    version: int = 1

    def validar(self):
        self.categoria_id = validar_id(self.categoria_id, "categoria_id")
        self.nombre = validar_id(self.nombre, "nombre de categoría")
        if self.padre_id is not None:
            self.padre_id = validar_id(self.padre_id, "padre_id")
            if self.padre_id == self.categoria_id:
                raise ValidacionCatalogo(
                    "una categoría no puede ser su propio padre")


@dataclass
class Almacen:
    company_id: str
    almacen_id: str
    nombre: str
    direccion: str = ""
    activo: bool = True
    version: int = 1

    def validar(self):
        self.almacen_id = validar_id(self.almacen_id, "almacen_id")
        self.nombre = validar_id(self.nombre, "nombre de almacén")


@dataclass
class Producto:
    company_id: str
    producto_id: str
    sku: str
    nombre: str
    descripcion: str = ""
    categoria_id: str | None = None
    unidad: str = "und"
    activo: bool = True
    metadata: dict = field(default_factory=dict)
    version: int = 1

    def validar(self):
        self.producto_id = validar_id(self.producto_id, "producto_id")
        self.sku = validar_id(self.sku, "sku")
        self.nombre = validar_id(self.nombre, "nombre de producto")
        if self.categoria_id is not None:
            self.categoria_id = validar_id(self.categoria_id, "categoria_id")
        self.unidad = validar_id(self.unidad, "unidad")


@dataclass
class PrecioVigente:
    company_id: str
    producto_id: str
    moneda: str
    precio: Decimal
    vigente_desde: str | None = None
    motivo: str = ""

    def validar(self):
        self.producto_id = validar_id(self.producto_id, "producto_id")
        self.moneda = validar_moneda(self.moneda)
        self.precio = validar_precio(self.precio)


@dataclass
class CostoVigente:
    company_id: str
    producto_id: str
    moneda: str
    costo: Decimal
    fuente: str = "manual"  # manual | compra | importacion | migracion
    vigente_desde: str | None = None
    motivo: str = ""

    def validar(self):
        self.producto_id = validar_id(self.producto_id, "producto_id")
        self.moneda = validar_moneda(self.moneda)
        self.costo = validar_costo(self.costo)
        if self.fuente not in ("manual", "compra", "importacion",
                               "migracion"):
            raise ValidacionCatalogo(f"fuente de costo inválida: {self.fuente!r}")
