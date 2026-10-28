"""Representación PDF de un comprobante e-CF (Fase 1 — SIMULACIÓN).

Este PDF es SOLO una representación de trabajo interno con datos
sintéticos. NO es un documento fiscal válido ni sustituye la
representación oficial exigida por la DGII. Todo PDF generado lleva
la marca obligatoria: "SIMULACIÓN — NO VÁLIDO ANTE DGII".

No hay conexión real con la DGII, ni certificados, ni firmas digitales.
"""
from __future__ import annotations

from decimal import Decimal

from fpdf import FPDF
from fpdf.enums import XPos, YPos

from . import AVISO_SIMULACION, AVISO_NO_CERTIFICADO
from .modelos import TIPOS_ECF, Comprobante

TITULO = "ZAYVERO Business OS — Representación de e-CF"


def _txt(valor) -> str:
    """Sanitiza texto para las fuentes core (latin-1) de fpdf2."""
    s = "" if valor is None else str(valor)
    return (
        s.replace("—", "-")
        .replace("–", "-")
        .replace("“", '"')
        .replace("”", '"')
        .replace("‘", "'")
        .replace("’", "'")
        .replace("…", "...")
    )


def _moneda(v: Decimal) -> str:
    return f"{Decimal(v):,.2f}"


class _ECFPDF(FPDF):
    """PDF con encabezado de simulación y pie legal en cada página."""

    def header(self):
        self.set_font("Helvetica", "B", 11)
        self.cell(0, 8, _txt(TITULO),
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(180, 0, 0)
        self.cell(0, 8, _txt(AVISO_SIMULACION),
                  new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="C")
        self.set_text_color(0, 0, 0)
        self.ln(2)

    def footer(self):
        self.set_y(-26)
        self.set_text_color(180, 0, 0)
        self.set_font("Helvetica", "B", 7)
        self.cell(0, 4, _txt(AVISO_SIMULACION), new_x=XPos.LMARGIN,
                  new_y=YPos.NEXT, align="C")
        self.set_text_color(90, 90, 90)
        self.set_font("Helvetica", "I", 6.5)
        self.multi_cell(0, 3.5, _txt(AVISO_NO_CERTIFICADO), align="C")


def _marca_agua(pdf: FPDF) -> None:
    """Marca de agua diagonal de simulación sobre la página actual."""
    pdf.set_font("Helvetica", "B", 34)
    pdf.set_text_color(226, 226, 226)
    cx, cy = 105, 155  # centro de la página A4
    with pdf.rotation(45, x=cx, y=cy):
        pdf.text(cx - 108, cy, _txt(AVISO_SIMULACION))
    pdf.set_text_color(0, 0, 0)


def _linea_campo(pdf: FPDF, etiqueta: str, valor: str) -> None:
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(38, 6, _txt(etiqueta), new_x=XPos.END, new_y=YPos.TOP)
    pdf.set_font("Helvetica", "", 9)
    pdf.cell(0, 6, _txt(valor), new_x=XPos.LMARGIN, new_y=YPos.NEXT)


def _seccion_partes(pdf: FPDF, c: Comprobante) -> None:
    pdf.set_font("Helvetica", "B", 10)
    pdf.set_fill_color(240, 240, 240)
    pdf.cell(95, 7, "EMISOR", new_x=XPos.END, new_y=YPos.TOP,
             align="C", fill=True)
    pdf.cell(95, 7, "RECEPTOR", new_x=XPos.LMARGIN, new_y=YPos.NEXT,
             align="C", fill=True)
    pdf.set_font("Helvetica", "", 9)
    nc_emisor = c.emisor.nombre_comercial or c.emisor.razon_social
    pdf.multi_cell(95, 5.5,
                   _txt(f"{c.emisor.razon_social}\n"
                        f"Nombre comercial: {nc_emisor}\n"
                        f"RNC: {c.emisor.rnc}"),
                   new_x=XPos.END, new_y=YPos.TOP)
    pdf.multi_cell(95, 5.5,
                   _txt(f"{c.receptor.razon_social}\n"
                        f"RNC: {c.receptor.rnc}"),
                   new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    pdf.ln(3)


def _seccion_datos(pdf: FPDF, c: Comprobante) -> None:
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "DATOS DEL COMPROBANTE",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT, align="L")
    _linea_campo(pdf, "e-NCF:", c.e_ncf or "(pendiente de asignar)")
    _linea_campo(pdf, "Tipo:", f"{c.tipo} - {TIPOS_ECF[c.tipo]['nombre']}")
    if c.tipo in ("33", "34"):
        _linea_campo(pdf, "e-NCF original:",
                     c.encf_original or "(no indicado)")
    _linea_campo(pdf, "Fecha:", (c.creado_en or "")[:19].replace("T", " "))
    _linea_campo(pdf, "Moneda:", c.moneda)
    _linea_campo(pdf, "Estado:", c.estado)
    _linea_campo(pdf, "Modo:", c.modo)
    pdf.ln(3)


def _tabla_lineas(pdf: FPDF, c: Comprobante) -> None:
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 7, "LINEAS DEL COMPROBANTE",
             new_x=XPos.LMARGIN, new_y=YPos.NEXT)
    cols = [("Descripción", 92), ("Cant.", 16), ("Precio unit.",
                                                 30), ("ITBIS %", 22),
            ("Subtotal", 30)]
    pdf.set_font("Helvetica", "B", 8)
    pdf.set_fill_color(230, 230, 230)
    for nombre, ancho in cols:
        pdf.cell(ancho, 7, _txt(nombre), align="C", fill=True,
                 new_x=XPos.END, new_y=YPos.TOP)
    pdf.ln()
    pdf.set_font("Helvetica", "", 8)
    for i, l in enumerate(c.lineas):
        fill = i % 2 == 1
        tasa = "Exenta" if l.tasa_itbis is None else f"{l.tasa_itbis}%"
        fila = [_txt(l.descripcion)[:70], f"{l.cantidad}",
                _moneda(l.precio_unitario), _txt(tasa),
                _moneda(l.subtotal)]
        aligns = ["L", "R", "R", "C", "R"]
        for (texto, ancho), al in zip(zip(fila, [w for _, w in cols]),
                                     aligns):
            pdf.cell(ancho, 6.5, texto, align=al, fill=fill,
                     new_x=XPos.END, new_y=YPos.TOP)
        pdf.ln()
    pdf.ln(3)


def _totales(pdf: FPDF, c: Comprobante) -> None:
    t = c.totales
    filas = [("Subtotal:", _moneda(t.subtotal), False),
             ("Descuento global:", _moneda(t.descuento_global), False),
             ("ITBIS:", _moneda(t.itbis), False),
             ("TOTAL:", _moneda(t.total), True)]
    x0 = 130
    pdf.set_x(x0)
    for etiqueta, valor, negrita in filas:
        pdf.set_font("Helvetica", "B" if negrita else "", 9)
        pdf.cell(38, 7, _txt(etiqueta), align="R",
                 new_x=XPos.END, new_y=YPos.TOP)
        pdf.cell(22, 7, valor, align="R",
                 new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        pdf.set_x(x0)


def generar_pdf(c: Comprobante, ruta: str) -> str:
    """Genera la representación PDF de `c` en `ruta`.

    Devuelve la ruta del PDF generado. El contenido es simulado:
    incluye la marca "SIMULACIÓN — NO VÁLIDO ANTE DGII" como título,
    marca de agua y pie legal.
    """
    pdf = _ECFPDF(format="A4")
    pdf.compress = False  # texto legible en el PDF (auditoría/tests)
    pdf.set_auto_page_break(True, margin=30)
    pdf.add_page()
    _marca_agua(pdf)
    _seccion_partes(pdf, c)
    _seccion_datos(pdf, c)
    _tabla_lineas(pdf, c)
    _totales(pdf, c)
    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 7)
    pdf.set_text_color(120, 120, 120)
    pdf.multi_cell(
        0, 4,
        _txt("Documento de prueba interna generado con datos sintéticos. "
             "No constituye un comprobante fiscal electrónico válido."),
        align="C")
    pdf.output(ruta)
    return ruta
