"""LOTE 7 — Document Intelligence: lectura de facturas en PDF.

- extraer_texto_pdf(): usa pdftotext del sistema (sin costo, sin dependencias
  nuevas). Si el PDF no tiene texto extraíble -> DocumentoIlegible.
- parsear_factura(): extracción estructurada con referencia al documento
  (nº de página/línea donde se halló cada dato). Formatos del mundo real
  varían: el parser trae plantillas para el formato simple ZAYVERO y
  heurísticas para facturas dominicanas (NCF, RNC, ITBIS). Formatos no
  reconocidos se marcan "revision_manual", no se inventan datos.
- validar_factura(): líneas suman == subtotal; subtotal + impuestos == total.
- RegistroDocumentos: idempotencia por documento_id + detección de
  duplicados (mismo proveedor + número).
- a_dict_cruce(): alimenta cruzar() del Lote 4.
- OCR (2026-10-13): tesseract-ocr + tesseract-ocr-spa instalados localmente
  (apt, sin costo). ocr_imagen() extrae texto REAL de imágenes
  (PNG/JPEG/TIFF); si tesseract no está instalado (p.ej. tras un rebuild
  de la VM) lanza OCRNoDisponible. extraer_factura_desde_imagen() encadena
  OCR -> parsear_factura -> validar: nunca inventa datos, lo que no se
  reconoce queda en formato "revision_manual".
"""
from __future__ import annotations

import sys
import os
import re
import shutil
import subprocess
import threading
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from oschema.contract import require_iso_currency  # noqa: E402


class ErrorDocumento(Exception):
    pass


class DocumentoIlegible(ErrorDocumento):
    pass


class DocumentoDuplicado(ErrorDocumento):
    pass


class OCRNoDisponible(ErrorDocumento):
    pass


@dataclass
class LineaFactura:
    descripcion: str
    cantidad: str
    precio_unitario: str
    referencia: str = ""  # dónde se encontró en el documento

    def total(self) -> Decimal:
        return _dec(self.cantidad) * _dec(self.precio_unitario)


@dataclass
class FacturaExtraida:
    documento_id: str
    company_id: str
    numero: str = ""
    fecha: str = ""
    proveedor_nombre: str = ""
    proveedor_id: str = ""
    lineas: list = field(default_factory=list)
    subtotal: str = "0"
    impuestos: str = "0"
    total: str = "0"
    moneda: str = "DOP"
    formato: str = "desconocido"  # zayvero_simple | dominicana | revision_manual
    advertencias: list = field(default_factory=list)

    def validar(self):
        """Comprueba aritmética interna. No afirma validez fiscal."""
        if not self.numero:
            raise ErrorDocumento("factura sin número")
        if self.fecha:
            try:
                datetime.strptime(self.fecha[:10], "%Y-%m-%d")
            except ValueError:
                raise ErrorDocumento(f"fecha inválida: {self.fecha}")
        require_iso_currency(self.moneda)
        suma = sum((l.total() for l in self.lineas), Decimal("0"))
        if abs(suma - _dec(self.subtotal)) > Decimal("0.01"):
            raise ErrorDocumento(
                f"líneas suman {suma} pero subtotal dice {self.subtotal}")
        if abs(_dec(self.subtotal) + _dec(self.impuestos) - _dec(self.total)) \
                > Decimal("0.01"):
            raise ErrorDocumento("subtotal + impuestos != total")

    def a_dict_cruce(self, mapa_productos: dict | None = None) -> dict:
        """Formato que espera cruzar() del Lote 4."""
        mapa = mapa_productos or {}
        return {"lineas": [
            {"producto_id": mapa.get(l.descripcion, l.descripcion),
             "cantidad": str(_dec(l.cantidad)),
             "precio_unitario": str(_dec(l.precio_unitario))}
            for l in self.lineas]}


def extraer_texto_pdf(path: str) -> str:
    """Extrae texto con pdftotext. Sin texto -> DocumentoIlegible."""
    if not os.path.exists(path):
        raise ErrorDocumento(f"archivo inexistente: {path}")
    if shutil.which("pdftotext") is None:
        raise ErrorDocumento("pdftotext no disponible en el sistema")
    try:
        r = subprocess.run(["pdftotext", "-layout", path, "-"],
                           capture_output=True, timeout=30)
    except subprocess.TimeoutExpired:
        raise DocumentoIlegible("timeout extrayendo el PDF")
    texto = r.stdout.decode("utf-8", errors="replace")
    if not texto.strip():
        raise DocumentoIlegible(
            "el PDF no contiene texto extraíble (¿escaneado? ver ocr_imagen)")
    return texto


def ocr_disponible() -> bool:
    """True si el binario tesseract está instalado en el sistema."""
    return shutil.which("tesseract") is not None


def ocr_version() -> str:
    """Versión de tesseract ("" si no instalado)."""
    if not ocr_disponible():
        return ""
    try:
        r = subprocess.run(["tesseract", "--version"],
                           capture_output=True, timeout=10)
        primera = r.stdout.decode("utf-8", errors="replace").splitlines()
        return primera[0].strip() if primera else ""
    except Exception:
        return ""


def ocr_idiomas() -> list:
    """Idiomas de OCR instalados (spa/eng esperados)."""
    if not ocr_disponible():
        return []
    try:
        r = subprocess.run(["tesseract", "--list-langs"],
                           capture_output=True, timeout=10)
        return [l.strip() for l in
                r.stdout.decode("utf-8", errors="replace").splitlines()
                if l.strip() and not l.startswith("List of")]
    except Exception:
        return []


_IMAGENES_MAGIA = (
    b"\x89PNG",      # PNG
    b"\xff\xd8\xff",  # JPEG
    b"II*\x00",      # TIFF little-endian
    b"MM\x00*",      # TIFF big-endian
)


def ocr_imagen(path: str, lang: str = "spa+eng") -> str:
    """Extrae texto de una imagen (PNG/JPEG/TIFF) con tesseract local.

    - Sin tesseract instalado -> OCRNoDisponible (la VM puede perderlo
      en un rebuild; reinstalar con apt es gratis: tesseract-ocr
      + tesseract-ocr-spa).
    - Archivo inexistente -> ErrorDocumento; no es imagen ->
      DocumentoIlegible; imagen sin texto extraíble -> DocumentoIlegible.
    Nunca inventa texto: devuelve exactamente lo que tesseract leyó.
    """
    if not os.path.exists(path):
        raise ErrorDocumento(f"archivo inexistente: {path}")
    if not ocr_disponible():
        raise OCRNoDisponible(
            "OCR no instalado (se requiere tesseract-ocr; "
            "reinstalar gratis con apt)")
    with open(path, "rb") as fh:
        magia = fh.read(8)
    if not any(magia.startswith(m) for m in _IMAGENES_MAGIA):
        raise DocumentoIlegible("no es una imagen PNG/JPEG/TIFF")
    idiomas = ocr_idiomas()
    langs = "+".join(l for l in lang.split("+") if l in idiomas) or "eng"
    try:
        r = subprocess.run(
            ["tesseract", path, "stdout", "-l", langs, "--psm", "6"],
            capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        raise DocumentoIlegible("timeout en el OCR")
    if r.returncode != 0:
        raise DocumentoIlegible(
            "tesseract falló: " +
            r.stderr.decode("utf-8", errors="replace")[:200])
    texto = r.stdout.decode("utf-8", errors="replace")
    if not texto.strip():
        raise DocumentoIlegible(
            "el OCR no extrajo texto de la imagen (¿ilegible o vacía?)")
    return texto


def extraer_factura_desde_imagen(path: str, documento_id: str = "",
                                 company_id: str = "",
                                 lang: str = "spa+eng") -> FacturaExtraida:
    """OCR -> parseo estructurado. Cableado completo de facturas escaneadas.

    Devuelve la FacturaExtraida SIN validar ni registrar: el llamador
    decide (fac.validar() lanza si la aritmética no cuadra; el formato
    'revision_manual' indica que requiere revisión humana). Nunca inventa
    datos: lo no reconocido queda vacío o en advertencias.
    """
    texto = ocr_imagen(path, lang=lang)
    return parsear_factura(
        texto, documento_id=documento_id or "doc-ocr-sin-id",
        company_id=company_id)


_NUM = r"[\d,]+\.\d{2}"


def _buscar(patron: str, texto: str) -> str:
    m = re.search(patron, texto, re.IGNORECASE | re.MULTILINE)
    return m.group(1).strip() if m else ""


def parsear_factura(texto: str, documento_id: str = "",
                    company_id: str = "") -> FacturaExtraida:
    """Extrae estructura de texto plano de factura."""
    f = FacturaExtraida(documento_id=documento_id or "doc-sin-id",
                        company_id=company_id)
    # Número: NCF dominicano o "Factura Nº/ No."
    f.numero = (_buscar(r"NCF\s*[:\-]?\s*([A-Z0-9\-]+)", texto)
                or _buscar(r"[Ff]actura\s*(?:No\.|N[oº°]|#)\s*[:\-]?\s*(\S+)",
                           texto))
    # Fecha: YYYY-MM-DD o DD/MM/YYYY
    f.fecha = _buscar(r"(\d{4}-\d{2}-\d{2})", texto)
    if not f.fecha:
        d = _buscar(r"(\d{2})/(\d{2})/(\d{4})", texto)
        if d:
            dd, mm, aa = re.search(r"(\d{2})/(\d{2})/(\d{4})", texto).groups()
            f.fecha = f"{aa}-{mm}-{dd}"
    f.proveedor_nombre = _buscar(r"Proveedor\s*[:\-]?\s*(.+)", texto)
    # Moneda
    mon = _buscar(r"Moneda\s*[:\-]?\s*([A-Z]{3})", texto)
    f.moneda = mon if mon else ("USD" if re.search(r"\bUS?\$", texto) else "DOP")
    # Totales
    f.subtotal = (_buscar(r"Subtotal\s*[:\-]?\s*(" + _NUM + r")", texto) or "0"
                 ).replace(",", "")
    f.impuestos = (_buscar(r"(?:ITBIS|Impuestos?|IVA)\s*[:\-]?\s*(" + _NUM + r")",
                           texto) or "0").replace(",", "")
    f.total = (_buscar(r"(?<!Sub)(?<!SUB)Total\s*[:\-]?\s*(" + _NUM + r")",
                       texto) or "0").replace(",", "")
    # Líneas: "2 x Taladro 12V @ 1,200.00"
    for m in re.finditer(r"(\d+)\s*x\s*(.+?)\s*@\s*(" + _NUM + r")",
                         texto, re.IGNORECASE):
        cant, desc, precio = m.groups()
        f.lineas.append(LineaFactura(
            descripcion=desc.strip(), cantidad=cant,
            precio_unitario=precio.replace(",", ""),
            referencia=f"línea {texto[:m.start()].count(chr(10)) + 1}"))
    if f.numero and f.lineas:
        f.formato = ("dominicana" if re.search(r"NCF", texto, re.IGNORECASE)
                     else "zayvero_simple")
    else:
        f.formato = "revision_manual"
        f.advertencias.append("formato no reconocido: requiere revisión manual")
    return f


class RegistroDocumentos:
    """Documentos de UNA empresa: idempotencia + anti-duplicados."""

    def __init__(self, company_id: str):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        self.company_id = company_id
        self._lock = threading.RLock()
        self._docs: dict[str, FacturaExtraida] = {}
        self._vistos: set[tuple[str, str]] = set()  # (proveedor, numero)

    def registrar(self, f: FacturaExtraida):
        with self._lock:
            if f.company_id != self.company_id:
                raise ErrorDocumento("documento de otra empresa")
            if f.documento_id in self._docs:
                raise DocumentoDuplicado(f"documento_id: {f.documento_id}")
            clave = (f.proveedor_nombre or f.proveedor_id, f.numero)
            if f.numero and clave in self._vistos:
                raise DocumentoDuplicado(
                    f"factura {f.numero} de {clave[0]} ya registrada")
            f.validar()
            self._docs[f.documento_id] = f
            if f.numero:
                self._vistos.add(clave)

    def get(self, documento_id: str) -> FacturaExtraida | None:
        with self._lock:
            return self._docs.get(documento_id)

    def todos(self) -> list[FacturaExtraida]:
        with self._lock:
            return list(self._docs.values())
