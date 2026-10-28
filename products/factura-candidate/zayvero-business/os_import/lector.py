"""BLOQUE Documentos/Integraciones — Lectura de CSV/XLSX a filas.

Entrada: bytes del archivo + formato ("csv" | "xlsx").
Salida: lista de dicts {columna_canonica: valor_crudo} + metadatos.

Reglas:
- CSV <= 1 MB (convención de os_routes), encoding utf-8-sig con
  fallback a latin-1 (misma estrategia que ingestion/ legacy).
- XLSX: primera hoja, cabeceras en la fila 1; filas totalmente vacías
  se descartan.
- Las cabeceras se normalizan con contratos.normalizar_cabecera; las
  columnas no reconocidas se conservan como "extra:<nombre>" para que
  el reporte las muestre (no se pierden datos en silencio).
- Nunca se escribe nada: lectura pura.
"""
from __future__ import annotations

import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from os_import.contratos import normalizar_cabecera  # noqa: E402

MAX_BYTES = 1 * 1024 * 1024  # 1 MB, como los CSV de os_routes


class ErrorLectura(Exception):
    pass


def _decodificar_csv(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, ValueError):
            continue
    raise ErrorLectura("encoding del CSV no reconocible")


def _leer_csv(raw: bytes, entidad: str) -> tuple[list[dict], dict]:
    texto = _decodificar_csv(raw)
    muestra = texto[:4096]
    try:
        dialecto = csv.Sniffer().sniff(muestra, delimiters=[",", ";", "\t", "|"])
    except csv.Error:
        dialecto = csv.excel
    lector = csv.DictReader(io.StringIO(texto), dialect=dialecto)
    if not lector.fieldnames:
        raise ErrorLectura("el CSV no tiene cabeceras")
    cabeceras = [_cab(c, entidad) for c in lector.fieldnames]
    filas = []
    for n, cruda in enumerate(lector, start=2):  # línea 2 = primera de datos
        fila = {}
        for original, canonica in zip(lector.fieldnames, cabeceras):
            v = cruda.get(original)
            fila[canonica] = "" if v is None else str(v).strip()
        if all(v == "" for v in fila.values()):
            continue  # fila vacía
        fila["_linea"] = n
        filas.append(fila)
    return filas, {"formato": "csv", "cabeceras": cabeceras}


def _cab(nombre: str, entidad: str) -> str:
    canonica = normalizar_cabecera(nombre or "", entidad)
    return canonica if canonica else f"extra:{(nombre or '').strip()}"


def _leer_xlsx(raw: bytes, entidad: str) -> tuple[list[dict], dict]:
    try:
        import pandas as pd
    except ImportError:
        raise ErrorLectura("pandas no disponible para leer XLSX")
    try:
        df = pd.read_excel(io.BytesIO(raw), sheet_name=0, dtype=str,
                           engine="openpyxl")
    except Exception as e:
        raise ErrorLectura(f"XLSX no legible: {e}")
    cabeceras = [_cab(str(c), entidad) for c in df.columns]
    df = df.where(pd.notna(df), "")
    filas = []
    for i, (_, serie) in enumerate(df.iterrows(), start=2):
        fila = {cab: str(serie[col]).strip()
                for cab, col in zip(cabeceras, df.columns)}
        if all(v == "" for v in fila.values()):
            continue
        fila["_linea"] = i
        filas.append(fila)
    return filas, {"formato": "xlsx", "cabeceras": cabeceras}


def leer(raw: bytes, formato: str, entidad: str) -> tuple[list[dict], dict]:
    """Lee el archivo. Lanza ErrorLectura ante archivo inválido."""
    if not isinstance(raw, (bytes, bytearray)) or len(raw) == 0:
        raise ErrorLectura("archivo vacío")
    if len(raw) > MAX_BYTES:
        raise ErrorLectura(
            f"archivo de {len(raw)} bytes supera el máximo de {MAX_BYTES}")
    formato = (formato or "").lower().strip()
    if formato == "csv":
        return _leer_csv(bytes(raw), entidad)
    if formato in ("xlsx", "xls"):
        return _leer_xlsx(bytes(raw), entidad)
    raise ErrorLectura(f"formato no soportado: {formato!r} (use csv o xlsx)")
