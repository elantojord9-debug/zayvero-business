"""BLOQUE Documentos/Integraciones — Contrato de rutas `docs/`.

Contrato del bloque:
    def rutas(): return {...}
con claves `docs/...` y funciones (bundle, ctx, query/body) -> dict
JSON-serializable.

- GET  -> fn(bundle, ctx, query)
- POST -> fn(bundle, ctx, body)

`manejar()` es el adaptador estilo reportes/rutas.py para que el
cableado futuro en webapp/os_routes.py sea trivial (este bloque no
toca os_routes.py por orden).

Rutas:
    docs/importar/contratos      GET
    docs/importar/vista-previa   POST  body: entidad, formato,
                                      contenido_base64[, import_id]
    docs/importar/aplicar        POST  body: + import_id (obligatorio)
    docs/ocr/estado              GET
    docs/ocr/extraer             POST  body: contenido_base64 (imagen),
                                      [idioma, documento_id]
"""
from __future__ import annotations

import base64
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from os_import.contratos import CONTRATOS  # noqa: E402
from os_import.motor import vista_previa, aplicar  # noqa: E402
from os_import.lector import MAX_BYTES  # noqa: E402


# ------------------------------------------------------------------ handlers

def _contratos(bundle, ctx, query):
    return {"ok": True, "entidades": sorted(CONTRATOS),
            "contratos": CONTRATOS}


def _vista_previa(bundle, ctx, body):
    return vista_previa(bundle, ctx, body or {})


def _aplicar(bundle, ctx, body):
    return aplicar(bundle, ctx, body or {})


def _ocr_estado(bundle, ctx, query):
    from os_docs.documentos import (ocr_disponible, ocr_version,
                                    ocr_idiomas)
    return {"ok": True, "disponible": ocr_disponible(),
            "version": ocr_version(), "idiomas": ocr_idiomas(),
            "nota": ("sin tesseract instalado el OCR queda BLOQUEADO "
                     "(ver docs/bloque-docs-integraciones.md)")}


def _ocr_extraer(bundle, ctx, body):
    """OCR de imagen -> extracción estructurada de factura (JSON)."""
    from os_docs.documentos import (
        extraer_factura_desde_imagen, ErrorDocumento, OCRNoDisponible)
    body = body or {}
    b64 = body.get("contenido_base64", "")
    if not b64:
        return {"ok": False, "error": "falta contenido_base64 (imagen)"}
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:
        return {"ok": False, "error": "contenido_base64 no es base64 válido"}
    if len(raw) > 5 * 1024 * 1024:
        return {"ok": False, "error": "imagen supera 5 MB"}
    company_id = getattr(ctx, "company_id", "") or ""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".img",
                                         delete=False) as fh:
            fh.write(raw)
            tmp = fh.name
        fac = extraer_factura_desde_imagen(
            tmp,
            documento_id=str(body.get("documento_id", "") or "doc-ocr"),
            company_id=company_id,
            lang=str(body.get("idioma", "spa+eng") or "spa+eng"))
        try:
            fac.validar()
            valida = True
            detalle_val = ""
        except ErrorDocumento as e:
            valida = False
            detalle_val = str(e)
        return {"ok": True,
                "documento_id": fac.documento_id,
                "numero": fac.numero, "fecha": fac.fecha,
                "proveedor": fac.proveedor_nombre,
                "moneda": fac.moneda,
                "subtotal": fac.subtotal, "impuestos": fac.impuestos,
                "total": fac.total, "formato": fac.formato,
                "lineas": [{"descripcion": l.descripcion,
                            "cantidad": l.cantidad,
                            "precio_unitario": l.precio_unitario,
                            "referencia": l.referencia}
                           for l in fac.lineas],
                "advertencias": fac.advertencias,
                "aritmetica_valida": valida,
                "detalle_validacion": detalle_val,
                "requiere_revision": (fac.formato == "revision_manual"
                                      or not valida)}
    except OCRNoDisponible as e:
        return {"ok": False, "bloqueado": True, "error": str(e)}
    except ErrorDocumento as e:
        return {"ok": False, "error": f"documento no procesable: {e}"}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


# ------------------------------------------------------------------ contrato

def rutas():
    """Contrato del bloque: prefijo docs/ -> fn(bundle, ctx, query/body)."""
    return {
        "docs/importar/contratos": _contratos,
        "docs/importar/vista-previa": _vista_previa,
        "docs/importar/aplicar": _aplicar,
        "docs/ocr/estado": _ocr_estado,
        "docs/ocr/extraer": _ocr_extraer,
    }


def manejar(bundle, ctx, sub, query=None, body=None, method="GET"):
    """Adaptador estilo reportes/rutas.py::manejar.

    Devuelve (manejada: bool, respuesta: dict). El cableado HTTP futuro
    en webapp/os_routes.py puede delegar aquí sin tocar este módulo.
    """
    tabla = rutas()
    if sub not in tabla:
        return False, {}
    fn = tabla[sub]
    try:
        if method.upper() == "GET":
            resp = fn(bundle, ctx, query or {})
        else:
            resp = fn(bundle, ctx, body or {})
    except Exception as e:  # fail-closed: nunca 500 crudo
        resp = {"ok": False, "error": f"error interno: {e}"}
    # Garantía del contrato: JSON-serializable.
    json.dumps(resp, default=str)
    return True, resp
