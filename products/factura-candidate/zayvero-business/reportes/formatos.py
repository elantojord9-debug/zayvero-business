"""SPRINT 5 PRIORIDAD 4 — Renderizadores CSV / XLSX / PDF de Reporte.

- CSV: texto plano UTF-8 con BOM (abre bien en Excel).
- XLSX: openpyxl; una hoja por sección, encabezados con estilo,
  fila de totales en negrita y columnas numéricas con formato moneda.
- PDF: fpdf2 (sin compresión para que el contenido sea inspeccionable);
  tablas con anchos proporcionales y nota de criterio por sección.
"""
from __future__ import annotations

import csv
import io

from reportes.datos import Reporte

CONTENT_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "xlsx": ("application/vnd.openxmlformats-officedocument."
             "spreadsheetml.sheet"),
    "pdf": "application/pdf",
}


def generar(rep: Reporte, formato: str) -> bytes:
    fn = {"csv": _csv, "xlsx": _xlsx, "pdf": _pdf}[formato]
    return fn(rep)


# ---------------------------------------------------------------------
# CSV
# ---------------------------------------------------------------------
def _csv(rep: Reporte) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    w.writerow([rep.titulo])
    w.writerow([rep.aviso])
    t = _etiquetas(rep)
    w.writerow([f"{t['empresa']}: {rep.empresa}"])
    w.writerow([f"{t['generado']}: {rep.generado}"])
    w.writerow([])
    if rep.vacio:
        w.writerow([rep.nota_vacia])
        w.writerow([])
        for sec in rep.secciones:
            w.writerow([sec.titulo])
        return buf.getvalue().encode("utf-8-sig")  # BOM para Excel
    for sec in rep.secciones:
        w.writerow([sec.titulo])
        w.writerow(sec.columnas)
        for fila in sec.filas:
            w.writerow(fila)
        if sec.totales:
            w.writerow([])
            for tot in sec.totales:
                w.writerow(tot)
        if sec.nota:
            w.writerow([f"{t['nota']}: {sec.nota}"])
        w.writerow([])
    return buf.getvalue().encode("utf-8-sig")  # BOM para Excel


def _etiquetas(rep: Reporte) -> dict:
    return ({"empresa": "Empresa", "generado": "Generado",
             "nota": "Nota"} if rep.lang == "es"
            else {"empresa": "Company", "generado": "Generated",
                  "nota": "Note"})


# ---------------------------------------------------------------------
# XLSX
# ---------------------------------------------------------------------
def _xlsx(rep: Reporte) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Font, PatternFill, Alignment
    from openpyxl.utils import get_column_letter
    from decimal import Decimal

    wb = Workbook()
    wb.remove(wb.active)
    titulo_font = Font(bold=True, size=14)
    aviso_font = Font(italic=True, size=10, color="7A7A7A")
    head_font = Font(bold=True, color="FFFFFF")
    head_fill = PatternFill("solid", fgColor="1F4E79")
    tot_font = Font(bold=True)
    wrap = Alignment(wrap_text=True, vertical="top")
    money_fmt = '#,##0.00'
    t = _etiquetas(rep)

    if rep.vacio:
        # Una sola hoja con el aviso: no hay datos que tabular.
        ws = wb.create_sheet("reporte")
        ws.cell(row=1, column=1, value=rep.titulo).font = titulo_font
        ws.cell(row=2, column=1, value=rep.aviso).font = aviso_font
        ws.cell(row=3, column=1,
                value=f"{t['empresa']}: {rep.empresa} · "
                      f"{t['generado']}: {rep.generado}")
        ws.cell(row=5, column=1, value=rep.nota_vacia).font = aviso_font
        ws.column_dimensions["A"].width = 100
        out = io.BytesIO()
        wb.save(out)
        return out.getvalue()

    for i, sec in enumerate(rep.secciones):
        ws = wb.create_sheet(f"S{i + 1}-{_hoja(sec.titulo)}")
        fila = 1
        ws.cell(row=fila, column=1, value=rep.titulo).font = titulo_font
        fila += 1
        ws.cell(row=fila, column=1, value=rep.aviso).font = aviso_font
        fila += 1
        ws.cell(row=fila, column=1,
                value=f"{t['empresa']}: {rep.empresa} · "
                      f"{t['generado']}: {rep.generado}")
        fila += 2
        ws.cell(row=fila, column=1, value=sec.titulo).font = Font(
            bold=True, size=12)
        fila += 1
        ncols = len(sec.columnas)
        for c, h in enumerate(sec.columnas, 1):
            cell = ws.cell(row=fila, column=c, value=h)
            cell.font = head_font
            cell.fill = head_fill
            cell.alignment = wrap
        fila += 1
        for fr in sec.filas:
            for c, v in enumerate(fr, 1):
                cell = ws.cell(row=fila, column=c, value=_num(v, c, sec))
                if c in sec.num_cols:
                    cell.number_format = money_fmt
            fila += 1
        if sec.totales:
            fila += 1
            for tr in sec.totales:
                for c, v in enumerate(tr, 1):
                    cell = ws.cell(row=fila, column=c, value=_num(v, c, sec))
                    cell.font = tot_font
                    if c in sec.num_cols:
                        cell.number_format = money_fmt
                fila += 1
        if sec.nota:
            fila += 1
            ws.cell(row=fila, column=1,
                    value=f"{t['nota']}: {sec.nota}").alignment = wrap
        # Anchos: proporcional al contenido, con topes.
        for c in range(1, ncols + 1):
            ancho = 12
            for r in range(1, fila + 1):
                v = ws.cell(row=r, column=c).value
                if v is not None:
                    ancho = max(ancho, min(45, len(str(v)) + 2))
            ws.column_dimensions[get_column_letter(c)].width = ancho
    out = io.BytesIO()
    wb.save(out)
    return out.getvalue()


def _num(v: str, col: int, sec) -> object:
    """Convierte a número lo que es numérico (para formato moneda)."""
    if col in sec.num_cols and v not in ("", None):
        try:
            from decimal import Decimal
            return float(Decimal(str(v)))
        except Exception:
            pass
    return v


def _hoja(titulo: str) -> str:
    """Nombre de hoja válido (<=31 chars, sin []:*?/\\)."""
    for ch in "[]:*?/\\":
        titulo = titulo.replace(ch, " ")
    return titulo.strip()[:28] or "datos"


# ---------------------------------------------------------------------
# PDF
# ---------------------------------------------------------------------
def _pdf(rep: Reporte) -> bytes:
    from fpdf import FPDF

    class Rep(FPDF):
        pass

    pdf = Rep(orientation="L", unit="mm", format="A4")
    # Sin compresión: el texto queda inspeccionable (tests + auditoría).
    pdf.set_compression(False)
    pdf.set_auto_page_break(True, margin=15)
    pdf.add_page()
    t = _etiquetas(rep)
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, _latin(rep.titulo), new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "I", 10)
    pdf.set_text_color(110, 110, 110)
    pdf.multi_cell(0, 6, _latin(rep.aviso), new_x="LMARGIN", new_y="NEXT")
    pdf.set_text_color(0, 0, 0)
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 7, _latin(f"{t['empresa']}: {rep.empresa}  ·  "
                          f"{t['generado']}: {rep.generado}"),
             new_x="LMARGIN", new_y="NEXT")
    pdf.ln(3)
    if rep.vacio:
        pdf.set_font("Helvetica", "", 11)
        pdf.multi_cell(0, 6, _latin(rep.nota_vacia))
        return bytes(pdf.output())
    for sec in rep.secciones:
        pdf.set_font("Helvetica", "B", 12)
        pdf.cell(0, 8, _latin(sec.titulo), new_x="LMARGIN", new_y="NEXT")
        if rep.vacio and not sec.filas:
            pdf.set_font("Helvetica", "", 11)
            pdf.multi_cell(0, 6, _latin(rep.nota_vacia))
            continue
        _tabla(pdf, sec.columnas, sec.filas, sec.totales)
        if sec.nota:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(90, 90, 90)
            pdf.multi_cell(0, 5, _latin(f"{t['nota']}: {sec.nota}"),
                           new_x="LMARGIN", new_y="NEXT")
            pdf.set_text_color(0, 0, 0)
        pdf.ln(4)
    return bytes(pdf.output())


def _latin(s: str) -> str:
    """Las fuentes base de fpdf2 son latin-1: transliterar lo necesario."""
    return (s or "").encode("latin-1", "replace").decode("latin-1")


def _tabla(pdf, columnas, filas, totales) -> None:
    """Tabla con la API nativa de fpdf2 (ajuste de texto y salto de
    página automáticos)."""
    n = len(columnas)

    def _row(celdas):
        return [_latin(str(v)) for v in
                (list(celdas) + [""] * (n - len(celdas)))]

    data = [_row(columnas)] + [_row(f) for f in filas]
    for ft in totales:
        data.append(_row(ft))
    with pdf.table(first_row_as_headings=True,
                   text_align="LEFT",
                   line_height=6,
                   width=pdf.w - pdf.l_margin - pdf.r_margin,
                   ) as table:
        for i, celdas in enumerate(data):
            row = table.row()
            for v in celdas:
                row.cell(v)
