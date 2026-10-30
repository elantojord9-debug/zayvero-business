"""Generación y verificación de XML e-CF (Fase 1, local, sintético).

El XML es una estructura de SIMULACIÓN inspirada en el Informe Técnico
e-CF v1.0; NO reproduce namespaces ni esquemas oficiales de la DGII y
NUNCA debe interpretarse como un documento fiscal válido.

Secciones:
  A. Encabezado (identificación, emisor, receptor, totales, moneda)
  B. Detalle (una entrada por línea)
  C. Subtotales informativos (exento vs gravado)
  D. Descuentos/Recargos (condicional: solo si hay)
  E. Paginación (página 1 de 1)

Todos los documentos llevan un nodo <AvisoLegal> con la marca de
simulación.

SIMULACIÓN — NO VÁLIDO ANTE DGII. Cero conexión real a DGII, cero
certificados o credenciales reales.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from decimal import Decimal
from xml.dom import minidom

from . import AVISO_SIMULACION
from .modelos import TIPOS_ECF, Comprobante

__all__ = ["generar_xml", "validar_xml"]

AVISO_LEGAL_XML = (
    f"{AVISO_SIMULACION} — estructura de simulación basada en Informe "
    "Técnico e-CF v1.0 — validar contra XSD oficial vigente")

ETIQUETA_AVISO = "AvisoLegal"


def _dec2(v) -> str:
    return f"{v:.2f}"


def _sub(tag: str, parent: ET.Element, text: str | None = None
         ) -> ET.Element:
    el = ET.SubElement(parent, tag)
    if text is not None:
        el.text = text
    return el


def _seccion_encabezado(root: ET.Element, c: Comprobante) -> None:
    enc = _sub("Encabezado", root)
    ident = _sub("Identificacion", enc)
    _sub("Version", ident, "1.0")
    _sub("ENC", ident, c.e_ncf or "SIN-ASIGNAR")
    _sub("TipoECF", ident, c.tipo)
    _sub("TipoECFNombre", ident,
         TIPOS_ECF.get(c.tipo, {}).get("nombre", ""))
    _sub("FechaEmision", ident, c.creado_en)
    _sub("Moneda", ident, c.moneda)
    if c.tipo in ("33", "34") and c.encf_original:
        _sub("ENCOriginal", ident, c.encf_original)

    emis = _sub("Emisor", enc)
    _sub("RNC", emis, c.emisor.rnc)
    _sub("RazonSocial", emis, c.emisor.razon_social)
    if c.emisor.nombre_comercial:
        _sub("NombreComercial", emis, c.emisor.nombre_comercial)

    recep = _sub("Receptor", enc)
    _sub("RNC", recep, c.receptor.rnc)
    _sub("RazonSocial", recep, c.receptor.razon_social)

    tot = c.totales
    totales = _sub("Totales", enc)
    _sub("Subtotal", totales, _dec2(tot.subtotal))
    _sub("DescuentoGlobal", totales, _dec2(tot.descuento_global))
    _sub("ITBIS", totales, _dec2(tot.itbis))
    _sub("Total", totales, _dec2(tot.total))


def _seccion_detalle(root: ET.Element, c: Comprobante) -> None:
    detalle = _sub("Detalle", root)
    for i, l in enumerate(c.lineas, start=1):
        item = _sub("Item", detalle)
        _sub("NumeroLinea", item, str(i))
        _sub("Descripcion", item, l.descripcion)
        _sub("Cantidad", item, _dec2(l.cantidad))
        _sub("PrecioUnitario", item, _dec2(l.precio_unitario))
        _sub("Descuento", item, _dec2(l.descuento))
        _sub("Subtotal", item, _dec2(l.subtotal))
        _sub("TasaITBIS", item,
             _dec2(l.tasa_itbis) if l.tasa_itbis is not None else "")
        _sub("ITBIS", item, _dec2(l.itbis))


def _seccion_subtotales(root: ET.Element, c: Comprobante) -> None:
    gravado = sum((l.subtotal for l in c.lineas if l.tasa_itbis is not None),
                  Decimal("0"))
    exento = sum((l.subtotal for l in c.lineas if l.tasa_itbis is None),
                 Decimal("0"))
    subt = _sub("Subtotales", root)
    _sub("Gravado", subt, _dec2(gravado))
    _sub("Exento", subt, _dec2(exento))


def _seccion_descuentos(root: ET.Element, c: Comprobante) -> None:
    hay_desc_lineas = any(l.descuento != 0 for l in c.lineas)
    hay_desc_global = c.descuento_global != 0
    if not (hay_desc_lineas or hay_desc_global):
        return  # sección condicional: solo si hay descuentos/recargos
    sec = _sub("DescuentosRecargos", root)
    for i, l in enumerate(c.lineas, start=1):
        if l.descuento != 0:
            d = _sub("Descuento", sec)
            _sub("NumeroLinea", d, str(i))
            _sub("Monto", d, _dec2(l.descuento))
    if hay_desc_global:
        g = _sub("DescuentoGlobal", sec)
        _sub("Monto", g, _dec2(c.descuento_global))


def _seccion_paginacion(root: ET.Element) -> None:
    pag = _sub("Paginacion", root)
    _sub("PaginaActual", pag, "1")
    _sub("TotalPaginas", pag, "1")


def generar_xml(c: Comprobante) -> str:
    """Genera el XML de simulación del comprobante (bien formado)."""
    root = ET.Element("ECF")
    root.set("version", "1.0")
    root.set("modo", "simulacion")
    _sub(ETIQUETA_AVISO, root, AVISO_LEGAL_XML)
    _seccion_encabezado(root, c)
    _seccion_detalle(root, c)
    _seccion_subtotales(root, c)
    _seccion_descuentos(root, c)
    _seccion_paginacion(root)
    crudo = ET.tostring(root, encoding="unicode")
    bonito = minidom.parseString(crudo).toprettyxml(indent="  ")
    lineas = [ln for ln in bonito.splitlines() if ln.strip()]
    return "\n".join(lineas) + "\n"


# Nodos requeridos: (ruta XPath relativa a la raíz, descripción)
_REQUERIDOS_A = [
    ("Encabezado/Identificacion/ENC", "e-NCF"),
    ("Encabezado/Identificacion/TipoECF", "tipo e-CF"),
    ("Encabezado/Identificacion/FechaEmision", "fecha de emisión"),
    ("Encabezado/Identificacion/Moneda", "moneda"),
    ("Encabezado/Emisor/RNC", "RNC del emisor"),
    ("Encabezado/Emisor/RazonSocial", "razón social del emisor"),
    ("Encabezado/Receptor/RNC", "RNC del receptor"),
    ("Encabezado/Receptor/RazonSocial", "razón social del receptor"),
    ("Encabezado/Totales/Subtotal", "subtotal"),
    ("Encabezado/Totales/ITBIS", "ITBIS"),
    ("Encabezado/Totales/Total", "total"),
]
_REQUERIDOS_B = [
    ("Detalle/Item", "al menos un ítem de detalle"),
    ("Detalle/Item/Descripcion", "descripción del ítem"),
    ("Detalle/Item/Cantidad", "cantidad del ítem"),
    ("Detalle/Item/PrecioUnitario", "precio del ítem"),
    ("Detalle/Item/Subtotal", "subtotal del ítem"),
    ("Detalle/Item/ITBIS", "ITBIS del ítem"),
]


def validar_xml(xml: str) -> list[str]:
    """Verifica que el XML esté bien formado y contenga los nodos de A y B.

    Devuelve la lista de errores ([] = válido).
    """
    errores: list[str] = []
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        return [f"XML mal formado: {exc}"]
    if root.tag != "ECF":
        errores.append(f"raíz inesperada: {root.tag!r} (se esperaba 'ECF')")
    for ruta, desc in _REQUERIDOS_A + _REQUERIDOS_B:
        nodos = root.findall(ruta)
        if not nodos:
            errores.append(f"falta nodo requerido: {desc} <{ruta}>")
        elif ruta == "Detalle/Item" and not nodos:
            errores.append("el detalle no tiene ítems")
    aviso = root.find(ETIQUETA_AVISO)
    if aviso is None or not (aviso.text or "").strip():
        errores.append("falta el nodo AvisoLegal de simulación")
    elif "SIMULACIÓN" not in aviso.text or "DGII" not in aviso.text:
        errores.append("el AvisoLegal no lleva la marca de simulación")
    return errores
