"""Validación de comprobantes e-CF (Fase 1, local, sintético).

Reglas de negocio:
- Solo tipos habilitados en fase 1 (31, 32, 33, 34).
- RNC de emisor y receptor válidos (9 u 11 dígitos) y DISTINTOS.
- Al menos una línea de detalle.
- Totales coherentes: recalculo exacto con Decimal y comparación.
- Moneda DOP en fase 1.
- Notas 33/34 exigen e-NCF original enlazado (formato válido y existencia
  verificable con el callable opcional `existe_encf`).
- Solo los estados borrador/validado son editables; emitidos jamás se
  editan (solo nota de crédito/débito o anulación).

SIMULACIÓN — NO VÁLIDO ANTE DGII. Cero conexión real a DGII, cero
certificados o credenciales reales.
"""
from __future__ import annotations

from typing import Callable

from . import AVISO_SIMULACION
from .modelos import (
    NOTAS,
    TIPOS_ECF,
    Comprobante,
    ErrorECF,
    calcular_totales,
    parsear_e_ncf,
    validar_rnc,  # re-exportado para conveniencia de los consumidores
)

__all__ = ["validar_rnc", "puede_editarse", "validar_comprobante",
           "AVISO_SIMULACION"]

ESTADOS_EDITABLES = ("borrador", "validado")
MONEDA_FASE1 = "DOP"


def puede_editarse(c: Comprobante) -> bool:
    """True solo si el comprobante puede editarse (borrador o validado).

    Comprobantes emitidos (transmitido, aceptado, etc.) JAMÁS se editan:
    la corrección se hace con nota de crédito/débito o anulación.
    """
    return c.estado in ESTADOS_EDITABLES


def _err(cond: bool, msg: str, out: list[str]) -> None:
    if cond:
        out.append(msg)


def validar_comprobante(c: Comprobante,
                        existe_encf: Callable[[str], bool] | None = None
                        ) -> list[str]:
    """Valida un comprobante y devuelve la lista de errores ([] = válido).

    `existe_encf`: callable opcional que recibe un e-NCF (str) y devuelve
    True si ese comprobante original existe. Solo se usa para notas 33/34.
    """
    errores: list[str] = []

    # 1. Tipo habilitado en fase 1
    info_tipo = TIPOS_ECF.get(c.tipo)
    if info_tipo is None:
        errores.append(f"tipo e-CF desconocido: {c.tipo!r}")
    elif not info_tipo.get("habilitado"):
        errores.append(
            f"tipo {c.tipo} ({info_tipo['nombre']}) no habilitado en fase 1")

    # 2. RNC emisor y receptor válidos y distintos
    rnc_emisor, rnc_receptor = "", ""
    for etiqueta, rnc in (("emisor", c.emisor.rnc),
                         ("receptor", c.receptor.rnc)):
        try:
            normalizado = validar_rnc(rnc)
        except ErrorECF:
            errores.append(f"RNC de {etiqueta} inválido: {rnc!r}")
            normalizado = ""
        if etiqueta == "emisor":
            rnc_emisor = normalizado
        else:
            rnc_receptor = normalizado
    if rnc_emisor and rnc_emisor == rnc_receptor:
        errores.append("emisor y receptor tienen el mismo RNC")

    # 3. Al menos una línea
    _err(not c.lineas, "el comprobante no tiene líneas de detalle", errores)

    # 4. Totales coherentes (recalculo exacto en Decimal)
    recalc = calcular_totales(c.lineas, c.descuento_global)
    actuales = c.totales
    if recalc.subtotal != actuales.subtotal:
        errores.append(
            f"subtotal incoherente: {actuales.subtotal} vs {recalc.subtotal}")
    if recalc.itbis != actuales.itbis:
        errores.append(
            f"ITBIS incoherente: {actuales.itbis} vs {recalc.itbis}")
    if recalc.total != actuales.total:
        errores.append(
            f"total incoherente: {actuales.total} vs {recalc.total}")

    # 5. Moneda fase 1
    _err((c.moneda or "").upper() != MONEDA_FASE1,
         f"moneda {c.moneda!r} no soportada en fase 1 (solo {MONEDA_FASE1})",
         errores)

    # 6. e-NCF asignado (si ya tiene) con formato válido
    if c.e_ncf:
        try:
            parsear_e_ncf(c.e_ncf)
        except ErrorECF:
            errores.append(f"e-NCF con formato inválido: {c.e_ncf!r}")

    # 7. Notas 33/34: e-NCF original obligatorio, válido y existente
    if c.tipo in NOTAS:
        if not (c.encf_original or "").strip():
            errores.append(
                f"nota tipo {c.tipo} exige e-NCF original (encf_original)")
        else:
            try:
                parsear_e_ncf(c.encf_original)
            except ErrorECF:
                errores.append(
                    f"e-NCF original con formato inválido: "
                    f"{c.encf_original!r}")
            else:
                if existe_encf is not None and not existe_encf(c.encf_original):
                    errores.append(
                        f"e-NCF original no existe: {c.encf_original!r}")

    # 8. Estado editable (emitidos jamás se editan)
    if not puede_editarse(c):
        errores.append(
            f"estado {c.estado!r} no editable: los comprobantes emitidos no "
            f"se editan; use nota de crédito/débito o anulación")

    return errores
