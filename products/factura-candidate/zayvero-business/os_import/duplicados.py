"""BLOQUE Documentos/Integraciones — Detección de duplicados.

Dos niveles:
1. Dentro del archivo: dos filas con la misma clave natural -> la
   segunda (y siguientes) se marcan como error ("duplicado en el
   archivo"). Sin ambigüedad: no se decide cuál gana.
2. Contra lo ya registrado en la empresa: la fila se marca como
   "ya_existente" (idempotencia de contenido) y, al aplicar, se omite
   sin error.

Las claves naturales salen de contratos.CONTRATOS[entidad]["clave_natural"].
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from os_import.contratos import contrato  # noqa: E402


def clave_natural(entidad: str, fila: dict) -> tuple:
    """Tupla hashable con la clave natural de la fila (valores crudos str)."""
    partes = []
    for col in contrato(entidad)["clave_natural"]:
        v = fila.get(col, "")
        partes.append(str(v).strip().lower())
    return tuple(partes)


def marcar_duplicados_archivo(entidad: str,
                              filas: list[dict]) -> list[dict]:
    """Devuelve errores para filas cuya clave natural ya apareció."""
    vistos: dict[tuple, int] = {}
    errores = []
    for fila in filas:
        clave = clave_natural(entidad, fila)
        if all(p == "" for p in clave):
            continue  # sin clave: la validación ya lo marcó
        if clave in vistos:
            errores.append({
                "fila": fila.get("_linea", 0),
                "columna": "+".join(contrato(entidad)["clave_natural"]),
                "valor": "|".join(clave),
                "error": (f"duplicado en el archivo: la clave natural ya "
                          f"apareció en la línea {vistos[clave]}")})
        else:
            vistos[clave] = fila.get("_linea", 0)
    return errores


def existe_en_empresa(entidad: str, fila: dict, company_id: str,
                      bundle) -> bool:
    """True si la clave natural ya está registrada en la empresa."""
    clave = clave_natural(entidad, fila)
    if entidad == "productos":
        from os_import.registro_productos import obtener_registro
        sku = str(fila.get("sku", "")).strip()
        return obtener_registro(company_id).existe_sku(sku)
    if entidad == "clientes":
        email = str(fila.get("email", "")).strip().lower()
        cid = str(fila.get("cliente_id", "")).strip()
        crm = bundle.crm
        with crm._lock:
            for c in crm._clientes.values():
                if cid and c.cliente_id == cid:
                    return True
                if email and c.email.strip().lower() == email:
                    return True
        return False
    if entidad == "movimientos":
        mid = str(fila.get("movimiento_id", "")).strip()
        if not mid:
            return False
        with bundle.inventario._lock:
            return mid in bundle.inventario._movs
    return False
