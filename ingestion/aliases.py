"""
ZAYVERO BUSINESS — FASE 1A.

`ingestion.aliases`: estructura de mapeo de columnas configurable.

NO implementa mapeo automático complejo todavía: solo define el diccionario
canónico de aliases y la función de resolución que:
  1. normaliza los nombres de columna del archivo,
  2. los compara contra los aliases conocidos,
  3. renombra las coincidencias al nombre canónico,
  4. reporta qué columnas quedaron SIN mapear (para revisión humana).

Uso futuro: agregar entradas a CANONICAL_ALIASES o pasar un dict propio.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

import pandas as pd

# Nombre canónico -> lista de variantes conocidas en archivos fuente.
# El nombre canónico es el que usa la capa de normalización.
CANONICAL_ALIASES: dict[str, list[str]] = {
    "Invoice": [
        "invoice", "invoice number", "invoicenumber", "invoice_no", "invoice no",
        "factura", "nro factura", "num factura",
    ],
    "StockCode": [
        "stockcode", "stock code", "product code", "productcode", "sku",
        "codigo producto", "código producto",
    ],
    "Description": [
        "description", "product description", "descripcion", "descripción",
        "product name", "item description",
    ],
    "Quantity": [
        "quantity", "qty", "cantidad", "units", "unidades",
    ],
    "InvoiceDate": [
        "invoicedate", "invoice date", "date", "fecha", "fecha factura",
        "transaction date", "order date", "fecha venta",
    ],
    "UnitPrice": [
        "unitprice", "unit price", "price", "precio", "precio unitario",
        "unit_price",
    ],
    "CustomerID": [
        "customer id", "customerid", "customer_id", "customer", "client id",
        "cliente", "customer number",
    ],
    "Country": [
        "country", "pais", "país", "destination",
    ],
}


def _normalize_name(name: str) -> str:
    """Minúsculas, sin acentos, sin espacios/puntuación extra."""
    name = unicodedata.normalize("NFKD", str(name))
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower().strip()
    name = re.sub(r"[_\-\.]+", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name


def build_lookup(aliases: dict[str, list[str]] | None = None) -> dict[str, str]:
    """variant_normalizada -> nombre canónico."""
    aliases = aliases if aliases is not None else CANONICAL_ALIASES
    lookup: dict[str, str] = {}
    for canonical, variants in aliases.items():
        lookup[_normalize_name(canonical)] = canonical
        for v in variants:
            lookup[_normalize_name(v)] = canonical
    return lookup


@dataclass
class ColumnResolution:
    """Resultado de resolver las columnas de un archivo."""
    renamed: dict[str, str] = field(default_factory=dict)   # original -> canónico
    unmapped: list[str] = field(default_factory=list)       # sin coincidencia
    missing_canonical: list[str] = field(default_factory=list)  # canónicos no encontrados

    def to_dict(self) -> dict[str, Any]:
        return {
            "renamed": self.renamed,
            "unmapped": self.unmapped,
            "missing_canonical": self.missing_canonical,
        }


def resolve_columns(
    df: pd.DataFrame,
    aliases: dict[str, list[str]] | None = None,
    expected_canonical: list[str] | None = None,
) -> tuple[pd.DataFrame, ColumnResolution]:
    """
    Renombra columnas del DataFrame a sus nombres canónicos según aliases.
    Devuelve (df_renombrado, resolución). NO inventa columnas faltantes:
    las reporta en missing_canonical para revisión humana.
    """
    lookup = build_lookup(aliases)
    renamed: dict[str, str] = {}
    unmapped: list[str] = []
    new_cols: dict[str, str] = {}
    for col in df.columns:
        canonical = lookup.get(_normalize_name(col))
        if canonical:
            renamed[str(col)] = canonical
            new_cols[str(col)] = canonical
        else:
            unmapped.append(str(col))
    out = df.rename(columns=new_cols)
    expected = expected_canonical or list(
        (aliases if aliases is not None else CANONICAL_ALIASES).keys()
    )
    missing = [c for c in expected if c not in out.columns]
    return out, ColumnResolution(
        renamed=renamed, unmapped=unmapped, missing_canonical=missing
    )
