"""BLOQUE CONTABILIDAD — Rentabilidad por producto y por sucursal.

Vistas HONESTAS sobre LibroFinanzas.rentabilidad() (no se reimplementa
el P&L; se reutiliza y se declara qué es atribuible y qué no):

- Por PRODUCTO: vista de CONTRIBUCIÓN. Los gastos operativos son en su
  mayoría indirectos y NO se prorratean entre productos (prorratear
  inventaría una regla de reparto). La salida lo dice explícito y deja
  utilidad operativa/neta en "datos insuficientes".
- Por SUCURSAL: los gastos DIRECTOS con sucursal_id sí se atribuyen
  (campo sucursal_id del gasto); las devoluciones y los impuestos no
  tienen dimensión de sucursal y no se restan (se documenta).

Cada vista incluye `regla_atribucion`: qué se atribuyó y por qué, para
que nadie lea más de lo que los datos sostienen.

Caso trabajado (ver tests):
  venta v1: 10 uds de "p1" a 100 DOP (sucursal "centro"), descuento 10 %
  en la línea; COGS 40/ud (costo registrado antes).
  gasto g1: 200 DOP, sucursal "centro". gasto g2: 500 DOP, sin sucursal.
  vista producto p1 -> brutos 1000, descuentos 100, netos 900, cogs 400,
  bruta 500, margen 55.6 %, gastos "no_atribuible".
  vista sucursal centro -> gastos 200 (solo el directo), bruta 500,
  operativa 300.
"""
from __future__ import annotations

import sys
import os
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from .errores import ErrorContab, NoEncontrado  # noqa: E402

DATO_INSUFICIENTE = "datos insuficientes"

REGLA_PRODUCTO = (
    "vista por producto = CONTRIBUCIÓN (ingresos netos − COGS). Los gastos "
    "operativos son mayormente indirectos y NO se prorratean entre "
    "productos: prorratear inventaría una regla de reparto. Las "
    "devoluciones y los impuestos tampoco son atribuibles por producto.")

REGLA_SUCURSAL = (
    "vista por sucursal: se atribuyen las ventas de la sucursal y los "
    "gastos DIRECTOS con ese sucursal_id. Las devoluciones y los "
    "impuestos no tienen dimensión de sucursal y no se restan.")


def _unidades_vendidas(libro, producto_id: str | None,
                       sucursal_id: str | None, desde, hasta) -> dict:
    """Unidades vendidas por producto (lectura directa, sin P&L)."""
    tot: dict[str, Decimal] = {}
    for v in libro._ventas.values():
        if desde and v.fecha < desde:
            continue
        if hasta and v.fecha > hasta:
            continue
        for lin in v.lineas:
            if producto_id and lin.producto_id != producto_id:
                continue
            if (sucursal_id and
                    (lin.sucursal_id or lin.almacen_id) != sucursal_id):
                continue
            tot[lin.producto_id] = (tot.get(lin.producto_id, Decimal("0"))
                                    + _dec(lin.cantidad))
    return {k: str(v) for k, v in sorted(tot.items())}


def vista_producto(libro, producto_id: str, desde: str | None = None,
                   hasta: str | None = None) -> dict:
    """Contribución por producto (una moneda por entrada)."""
    if not producto_id:
        raise ErrorContab("producto_id es obligatorio")
    unidades = _unidades_vendidas(libro, producto_id, None, desde, hasta)
    if not unidades:
        # rentabilidad() devolvería filas solo por gastos (no filtra
        # gastos por producto): eso sería una vista vacía engañosa.
        raise NoEncontrado(
            f"sin ventas registradas para el producto {producto_id} "
            f"en el período")
    crudo = libro.rentabilidad(desde=desde, hasta=hasta,
                               producto_id=producto_id)
    if not crudo:  # defensa: no debería pasar si hay unidades
        raise NoEncontrado(
            f"sin ventas registradas para el producto {producto_id} "
            f"en el período")
    por_moneda = {}
    for mon, r in crudo.items():
        por_moneda[mon] = {
            "moneda": mon,
            "producto_id": producto_id,
            "unidades_vendidas": unidades.get(producto_id, "0"),
            "ingresos_brutos": r["ingresos_brutos"],
            "descuentos": r["descuentos"],
            "ingresos_netos": r["ingresos_netos"],
            "cogs": r["cogs"],
            "utilidad_bruta": r["utilidad_bruta"],
            "margen_bruto_pct": r["margen_bruto_pct"],
            # Honestidad: lo no atribuible se declara, no se calcula.
            "gastos_operativos": "no_atribuible",
            "utilidad_operativa": DATO_INSUFICIENTE,
            "utilidad_neta": DATO_INSUFICIENTE,
            "regla_atribucion": REGLA_PRODUCTO,
        }
    return {"ok": True, "vista": "contribucion_por_producto",
            "producto_id": producto_id, "desde": desde, "hasta": hasta,
            "por_moneda": por_moneda}


def vista_sucursal(libro, sucursal_id: str, desde: str | None = None,
                   hasta: str | None = None) -> dict:
    """P&L por sucursal con gastos directos atribuidos."""
    if not sucursal_id:
        raise ErrorContab("sucursal_id es obligatorio")
    hay_lineas = bool(_unidades_vendidas(libro, None, sucursal_id,
                                         desde, hasta))
    reg = getattr(libro, "registro_gastos", None)
    fuente = reg.todos() if reg is not None else libro._gastos_fuente()
    hay_gastos = any(g.sucursal_id == sucursal_id and
                     (not desde or (g.gasto.fecha or "") >= desde) and
                     (not hasta or (g.gasto.fecha or "") <= hasta)
                     for g in fuente)
    # Conteo honesto: cuántos gastos directos se atribuyeron.
    directos = [g for g in fuente
                if g.sucursal_id == sucursal_id
                and (not desde or (g.gasto.fecha or "") >= desde)
                and (not hasta or (g.gasto.fecha or "") <= hasta)]
    if not (hay_lineas or directos):
        raise NoEncontrado(
            f"sin movimientos para la sucursal {sucursal_id} en el período")
    crudo = libro.rentabilidad(desde=desde, hasta=hasta,
                               sucursal_id=sucursal_id)
    if not crudo:  # defensa
        raise NoEncontrado(
            f"sin movimientos para la sucursal {sucursal_id} en el período")
    por_moneda = {}
    for mon, r in crudo.items():
        por_moneda[mon] = {
            "moneda": mon,
            "sucursal_id": sucursal_id,
            "ingresos_brutos": r["ingresos_brutos"],
            "descuentos": r["descuentos"],
            "devoluciones": r["devoluciones"],
            "ingresos_netos": r["ingresos_netos"],
            "cogs": r["cogs"],
            "utilidad_bruta": r["utilidad_bruta"],
            "margen_bruto_pct": r["margen_bruto_pct"],
            "gastos_directos_atribuidos": r["gastos_operativos"],
            "gastos_directos_n": len(
                [g for g in directos
                 if (g.gasto.moneda or "SIN_MONEDA") == mon]),
            "utilidad_operativa": r["utilidad_operativa"],
            "margen_operativo_pct": r["margen_operativo_pct"],
            "utilidad_neta": DATO_INSUFICIENTE,
            "regla_atribucion": REGLA_SUCURSAL,
        }
    return {"ok": True, "vista": "pyl_por_sucursal",
            "sucursal_id": sucursal_id, "desde": desde, "hasta": hasta,
            "por_moneda": por_moneda}
