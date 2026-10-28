"""Importación asistida de clientes y productos desde CSV (stdlib).

Flujo: pegar CSV → previsualizar (valida cada fila con los motores
existentes) → confirmar (crea solo las filas válidas).
"""
from __future__ import annotations

import csv
import io
import json

from factura_core import clientes, inventario

MAX_FILAS = 500

CAMPOS_CLIENTES = ("nombre", "rnc", "telefono", "direccion")
CAMPOS_PRODUCTOS = ("sku", "nombre", "precio", "costo", "itbis_exento",
                    "stock")


def _leer_csv(texto: str) -> tuple[list[str], list[list[str]]]:
    texto = (texto or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not texto:
        raise ValueError("pegue el contenido del CSV")
    # Detecta delimitador entre coma y punto y coma
    primera = texto.split("\n", 1)[0]
    delim = ";" if primera.count(";") > primera.count(",") else ","
    lector = csv.reader(io.StringIO(texto), delimiter=delim)
    filas = [f for f in lector if any(c.strip() for c in f)]
    if not filas:
        raise ValueError("el CSV está vacío")
    encabezado = [c.strip().lower() for c in filas[0]]
    datos = filas[1:MAX_FILAS + 1]
    if len(filas) - 1 > MAX_FILAS:
        raise ValueError(f"máximo {MAX_FILAS} filas por importación")
    return encabezado, datos


def previsualizar_clientes(texto: str) -> dict:
    encabezado, datos = _leer_csv(texto)
    faltan = [c for c in ("nombre",) if c not in encabezado]
    if faltan:
        raise ValueError("el CSV debe tener columna 'nombre' "
                         "(opcionales: rnc, telefono, direccion)")
    idx = {c: encabezado.index(c) for c in encabezado if c in CAMPOS_CLIENTES}
    filas, errores = [], []
    for n, f in enumerate(datos, start=2):
        def col(c):
            return f[idx[c]].strip() if c in idx and idx[c] < len(f) else ""
        fila = {c: col(c) for c in CAMPOS_CLIENTES}
        try:
            if not fila["nombre"]:
                raise ValueError("nombre vacío")
            clientes.normalizar_rnc(fila["rnc"])
            filas.append(fila)
        except ValueError as exc:
            errores.append({"linea": n, "error": str(exc), "datos": fila})
    return {"encabezado": encabezado, "filas": filas, "errores": errores}


def previsualizar_productos(texto: str) -> dict:
    encabezado, datos = _leer_csv(texto)
    if "nombre" not in encabezado or "precio" not in encabezado:
        raise ValueError("el CSV debe tener columnas 'nombre' y 'precio' "
                         "(opcionales: sku, costo, itbis_exento, stock)")
    idx = {c: encabezado.index(c) for c in encabezado if c in CAMPOS_PRODUCTOS}
    filas, errores = [], []
    for n, f in enumerate(datos, start=2):
        def col(c):
            return f[idx[c]].strip() if c in idx and idx[c] < len(f) else ""
        fila = {c: col(c) for c in CAMPOS_PRODUCTOS}
        try:
            inventario._validar_producto(
                fila["sku"] or "SIN-SKU", fila["nombre"], fila["precio"] or 0,
                fila["costo"] or 0, fila["stock"] or 0)
            fila["itbis_exento"] = fila["itbis_exento"].lower() in (
                "1", "si", "sí", "true", "x")
            filas.append(fila)
        except ValueError as exc:
            errores.append({"linea": n, "error": str(exc), "datos": fila})
    return {"encabezado": encabezado, "filas": filas, "errores": errores}


def confirmar_clientes(empresa_id: int, filas: list[dict]) -> dict:
    creados, errores = 0, []
    for fila in filas:
        try:
            clientes.crear(empresa_id, fila.get("nombre", ""),
                           fila.get("rnc", ""), fila.get("telefono", ""),
                           fila.get("direccion", ""))
            creados += 1
        except ValueError as exc:
            errores.append(str(exc))
    return {"creados": creados, "errores": errores}


def confirmar_productos(empresa_id: int, filas: list[dict]) -> dict:
    creados, errores = 0, []
    for fila in filas:
        try:
            inventario.crear(empresa_id, fila.get("sku") or "SIN-SKU",
                             fila.get("nombre", ""), fila.get("precio") or 0,
                             fila.get("costo") or 0,
                             itbis_exento=bool(fila.get("itbis_exento")),
                             stock=fila.get("stock") or 0)
            creados += 1
        except ValueError as exc:
            errores.append(str(exc))
    return {"creados": creados, "errores": errores}


def empaquetar(filas: list[dict]) -> str:
    """Serializa las filas validadas para el paso de confirmación."""
    return json.dumps(filas, ensure_ascii=False)


def desempaquetar(crudo: str) -> list[dict]:
    try:
        datos = json.loads(crudo or "[]")
    except Exception:
        raise ValueError("datos de importación inválidos")
    if not isinstance(datos, list) or len(datos) > MAX_FILAS:
        raise ValueError("datos de importación inválidos")
    return datos
