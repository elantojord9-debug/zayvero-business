"""Exportación de reportes (stdlib).

- CSV real con el módulo csv.
- "Excel" vía SpreadsheetML (XML de Excel 2003): lo abren Excel, LibreOffice
  y Google Sheets sin librerías externas.
- PDF: se genera con la vista imprimible del navegador (CSS @media print +
  botón "Imprimir / Guardar PDF"); ver pagina_reporte_imprimible en
  paginas_nuevas.py.
"""
from __future__ import annotations

import csv
import io
import xml.sax.saxutils as sax


def _csv_safe(value):
    text = "" if value is None else str(value)
    if text.lstrip().startswith(("=", "+", "-", "@", "\t", "\r")):
        from decimal import Decimal, InvalidOperation
        try:
            if Decimal(text).is_finite():
                return text
        except InvalidOperation:
            pass
        return "'" + text
    return text


def a_csv(columnas: list[str], filas: list[list]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([_csv_safe(c) for c in columnas])
    for f in filas:
        w.writerow([_csv_safe(c) for c in f])
    return buf.getvalue().encode("utf-8")


def _celda(valor, tipo: str) -> str:
    v = "" if valor is None else str(valor)
    if tipo == "Number":
        try:
            float(v.replace(",", ""))
            return (f'<Cell><Data ss:Type="Number">'
                    f'{sax.escape(v.replace(",", ""))}</Data></Cell>')
        except Exception:
            tipo = "String"
    return f'<Cell><Data ss:Type="{tipo}">{sax.escape(v)}</Data></Cell>'


def a_spreadsheetml(titulo: str, columnas: list[str],
                    filas: list[list]) -> bytes:
    """Workbook XML compatible con Excel (.xls)."""
    partes = ["""<?xml version="1.0" encoding="UTF-8"?>
<Workbook xmlns="urn:schemas-microsoft-com:office:spreadsheet"
 xmlns:ss="urn:schemas-microsoft-com:office:spreadsheet">
<Worksheet ss:Name="%s"><Table>""" % sax.escape(titulo[:31])]
    partes.append("<Row>" + "".join(
        f'<Cell><Data ss:Type="String">{sax.escape(c)}</Data></Cell>'
        for c in columnas) + "</Row>")
    for f in filas:
        celdas = []
        for c in f:
            txt = "" if c is None else str(c)
            es_num = False
            try:
                float(txt.replace(",", ""))
                es_num = bool(txt.strip()) and not any(
                    ch.isalpha() for ch in txt)
            except Exception:
                es_num = False
            celdas.append(_celda(txt, "Number" if es_num else "String"))
        partes.append("<Row>" + "".join(celdas) + "</Row>")
    partes.append("</Table></Worksheet></Workbook>")
    return "".join(partes).encode("utf-8")


def filas_ventas(ventas: list[dict]) -> tuple[list[str], list[list]]:
    cols = ["Número", "Tipo", "Fecha", "Cliente", "Subtotal", "ITBIS",
            "Descuento", "Total", "Estado"]
    filas = [[v.get("numero"), v.get("tipo"),
              (v.get("creada") or "")[:16].replace("T", " "),
              v.get("cliente_nombre") or "", v.get("subtotal"),
              v.get("itbis"), v.get("descuento"), v.get("total"),
              v.get("estado")] for v in ventas]
    return cols, filas


def filas_inventario(productos: list[dict]) -> tuple[list[str], list[list]]:
    cols = ["SKU", "Nombre", "Precio", "Costo", "Stock", "Exento ITBIS",
            "Activo"]
    filas = [[p.get("sku"), p.get("nombre"), p.get("precio"),
              p.get("costo"), p.get("stock"),
              "Sí" if p.get("itbis_exento") else "No",
              "Sí" if p.get("activo") else "No"] for p in productos]
    return cols, filas


def filas_cxc(pendientes: list[dict]) -> tuple[list[str], list[list]]:
    cols = ["Número", "Tipo", "Fecha", "Cliente", "Total", "Balance pendiente"]
    filas = [[v.get("numero"), v.get("tipo"),
              (v.get("creada") or "")[:16].replace("T", " "),
              v.get("cliente_nombre") or "", v.get("total"),
              v.get("balance")] for v in pendientes]
    return cols, filas


def filas_gastos(movs: list[dict]) -> tuple[list[str], list[list]]:
    """Gastos e ingresos (los dicts traen 'tipo': gasto/ingreso)."""
    cols = ["Fecha", "Tipo", "Categoría", "Descripción", "Comprobante",
            "Método", "Monto"]
    filas = [[(m.get("fecha") or "")[:10],
              "Gasto" if m.get("tipo") == "gasto" else "Ingreso",
              m.get("categoria"), m.get("descripcion"),
              m.get("comprobante"), m.get("metodo"), m.get("monto")]
             for m in movs]
    return cols, filas


def filas_estado_resultados(er: dict) -> tuple[list[str], list[list]]:
    cols = ["Concepto", "Monto (RD$)"]
    filas = [
        ["Ventas del período", er.get("ventas")],
        [f"Cantidad de ventas", er.get("cantidad_ventas")],
        ["(-) Costo de ventas", er.get("costo_ventas")],
        ["= Utilidad bruta", er.get("utilidad_bruta")],
        ["(+) Otros ingresos", er.get("otros_ingresos")],
        ["(-) Gastos", er.get("gastos")],
        ["= Utilidad neta", er.get("utilidad_neta")],
    ]
    return cols, filas
