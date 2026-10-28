"""BLOQUE Documentos/Integraciones — Validación fila por fila.

valida_filas(entidad, filas) -> (validas, errores)
- validas: lista de dicts con valores COERCIONADOS (Decimal/str/fecha
  normalizada) listos para aplicar.
- errores: lista de {"fila" (nº de línea del archivo), "columna",
  "valor", "error"}.

Nunca escribe nada: validación pura. Los errores no detienen la
validación de las demás filas (reporte completo, no "falla en la
primera").
"""
from __future__ import annotations

import os
import re
import sys
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from os_import.contratos import contrato  # noqa: E402
from oschema.contract import require_iso_currency  # noqa: E402

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _a_decimal(valor: str, columna: str) -> Decimal:
    v = valor.replace(",", "").strip()
    try:
        d = Decimal(v)
    except (InvalidOperation, ValueError, TypeError, AttributeError):
        raise ValueError(f"{columna} debe ser numérico, llegó {valor!r}")
    if not d.is_finite():
        raise ValueError(f"{columna} no puede ser infinito/NaN")
    return d


def _a_fecha(valor: str, columna: str) -> str:
    v = valor.strip()
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ",
                "%d/%m/%Y"):
        try:
            dt = datetime.strptime(v, fmt)
            return dt.strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(
        f"{columna} debe ser fecha YYYY-MM-DD o ISO 8601, llegó {valor!r}")


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _coercionar(columna: str, spec: dict, valor: str):
    tipo = spec["tipo"]
    if tipo == "texto":
        return valor
    if tipo == "decimal":
        return _a_decimal(valor, columna)
    if tipo == "entero":
        d = _a_decimal(valor, columna)
        if d != d.to_integral_value():
            raise ValueError(f"{columna} debe ser entero, llegó {valor!r}")
        return d
    if tipo == "moneda":
        require_iso_currency(valor.strip().upper())
        return valor.strip().upper()
    if tipo == "fecha":
        return _a_fecha(valor, columna)
    if tipo == "enumeracion":
        v = valor.strip().lower()
        if v not in spec["valores"]:
            raise ValueError(
                f"{columna} debe ser uno de {spec['valores']}, llegó {valor!r}")
        return v
    raise ValueError(f"tipo desconocido en el contrato: {tipo}")


def _aplicar_default(columna: str, spec: dict, fila: dict, n_fila: int,
                     import_id: str) -> str | None:
    """Devuelve el valor por defecto (None si no hay y no es obligatorio)."""
    default = spec.get("default")
    if default == "<sku>":
        return fila.get("sku", "")
    if default == "<derivado del email>":
        return ""  # lo deriva el motor (necesita hash del email)
    if default and str(default).startswith("<imp-"):
        return f"imp-{import_id}-{n_fila}"
    if default == "<ahora ISO>":
        return _ahora_iso()
    return default


def valida_filas(entidad: str, filas: list[dict],
                 import_id: str = "sin-id") -> tuple[list[dict], list[dict]]:
    """Valida y coerciona. Devuelve (validas, errores)."""
    spec = contrato(entidad)
    columnas = spec["columnas"]
    validas: list[dict] = []
    errores: list[dict] = []

    def _error(linea, columna, valor, mensaje):
        errores.append({"fila": linea, "columna": columna,
                        "valor": str(valor), "error": mensaje})

    for fila in filas:
        linea = fila.get("_linea", 0)
        coaccionada = {"_linea": linea}
        extras = sorted(k for k in fila
                        if k.startswith("extra:") and fila[k] != "")
        for c in extras:
            coaccionada[c] = fila[c]  # se conservan, el motor las ignora
        fila_ok = True
        for columna, cspec in columnas.items():
            crudo = fila.get(columna, "")
            if crudo == "" or crudo is None:
                valor = _aplicar_default(columna, cspec, fila, linea,
                                         import_id)
                if valor in (None, ""):
                    if cspec.get("obligatorio"):
                        _error(linea, columna, "",
                               "columna obligatoria ausente o vacía")
                        fila_ok = False
                    continue
                crudo = str(valor)
            try:
                coaccionada[columna] = _coercionar(columna, cspec, crudo)
            except ValueError as e:
                _error(linea, columna, crudo, str(e))
                fila_ok = False
        if entidad == "clientes":
            n_err = len(errores)
            _reglas_clientes(coaccionada, linea, _error)
            fila_ok = fila_ok and len(errores) == n_err
        if entidad == "movimientos" and fila_ok:
            if coaccionada["cantidad"] == 0:
                _error(linea, "cantidad", "0",
                       "la cantidad no puede ser 0")
                fila_ok = False
        if entidad == "productos" and fila_ok:
            if coaccionada["stock_inicial"] < 0:
                _error(linea, "stock_inicial",
                       str(coaccionada["stock_inicial"]),
                       "el stock inicial no puede ser negativo")
                fila_ok = False
        if fila_ok:
            validas.append(coaccionada)
    return validas, errores


def _reglas_clientes(coaccionada: dict, linea: int, _error):
    email = str(coaccionada.get("email", "")).strip()
    if email and not _EMAIL_RE.match(email):
        _error(linea, "email", email, "formato de email inválido")
    if not str(coaccionada.get("cliente_id", "")).strip() and not email:
        _error(linea, "cliente_id", "",
               "se exige cliente_id cuando la fila no trae email")
