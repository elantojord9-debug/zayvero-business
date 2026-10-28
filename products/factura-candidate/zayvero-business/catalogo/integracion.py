"""BLOQUE 5 — Catálogo maestro: adaptadores de LECTURA para los motores.

Contrato con Inventario, Simulator y Price Advisor:

- Si el catálogo tiene dato vigente para (empresa, producto[, moneda]),
  se devuelve ese dato (el catálogo es la fuente maestra).
- Si el catálogo está vacío, la tabla no existe, no hay PG/DSN o algo
  falla: se devuelve None (o False/{} según la función) y el motor usa
  SU derivación actual. NINGUNA excepción sale de aquí: el fallback es
  total y los motores nunca se rompen por el catálogo.

Toda lectura abre su propia conexión de corta vida; no toma locks
(las escrituras del catálogo ya serializan por empresa).
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

DSN_ENV = "ZAYVERO_OS_DATABASE_URL"


def _consulta(company_id: str, sql: str, args: tuple):
    """SELECT tolerante: None ante cualquier problema (sin PG, sin
    migración 007, empresa sin datos...)."""
    try:
        dsn = os.environ.get(DSN_ENV)
        if not dsn or os.environ.get("ZAYVERO_OS_BACKEND",
                                     "memoria") != "postgres":
            return None
        import psycopg
        conn = psycopg.connect(dsn)
        try:
            return conn.execute(sql, (company_id, *args)).fetchall()
        finally:
            conn.close()
    except Exception:
        return None


def catalogo_tiene_datos(company_id: str) -> bool:
    """True si la empresa tiene ≥1 producto en el catálogo."""
    filas = _consulta(
        company_id,
        "SELECT 1 FROM os_catalogo_productos WHERE company_id=%s LIMIT 1",
        ())
    return bool(filas)


def precio_catalogo(company_id: str, producto_id: str,
                    moneda: str) -> Decimal | None:
    """Precio de venta vigente del catálogo, o None (fallback del motor)."""
    filas = _consulta(
        company_id,
        """SELECT precio FROM os_catalogo_precios
           WHERE company_id=%s AND producto_id=%s AND moneda=%s
             AND vigente_hasta IS NULL
           ORDER BY vigente_desde DESC LIMIT 1""",
        (producto_id, moneda))
    if not filas:
        return None
    return Decimal(str(filas[0][0]))


def costo_catalogo(company_id: str, producto_id: str,
                   moneda: str) -> Decimal | None:
    """Costo de referencia vigente del catálogo, o None."""
    d = detalle_costo_catalogo(company_id, producto_id, moneda)
    return d["costo"] if d else None


def detalle_precio_catalogo(company_id: str, producto_id: str,
                            moneda: str) -> dict | None:
    """{precio, vigente_desde} del catálogo, o None (fallback del motor)."""
    filas = _consulta(
        company_id,
        """SELECT precio, vigente_desde FROM os_catalogo_precios
           WHERE company_id=%s AND producto_id=%s AND moneda=%s
             AND vigente_hasta IS NULL
           ORDER BY vigente_desde DESC LIMIT 1""",
        (producto_id, moneda))
    if not filas:
        return None
    return {"precio": Decimal(str(filas[0][0])),
            "vigente_desde": str(filas[0][1])}


def detalle_costo_catalogo(company_id: str, producto_id: str,
                           moneda: str) -> dict | None:
    """{costo, fuente, vigente_desde} del catálogo, o None."""
    filas = _consulta(
        company_id,
        """SELECT costo, fuente, vigente_desde FROM os_catalogo_costos
           WHERE company_id=%s AND producto_id=%s AND moneda=%s
             AND vigente_hasta IS NULL
           ORDER BY vigente_desde DESC LIMIT 1""",
        (producto_id, moneda))
    if not filas:
        return None
    return {"costo": Decimal(str(filas[0][0])), "fuente": filas[0][1],
            "vigente_desde": str(filas[0][2])}


def ficha_producto(company_id: str, producto_id: str) -> dict | None:
    """Ficha maestra (nombre, sku, categoría, unidad, activo) o None."""
    filas = _consulta(
        company_id,
        """SELECT p.producto_id, p.sku, p.nombre, p.descripcion,
                  p.categoria_id, c.nombre, p.unidad, p.activo
           FROM os_catalogo_productos p
           LEFT JOIN os_catalogo_categorias c
             ON c.company_id=p.company_id AND c.categoria_id=p.categoria_id
           WHERE p.company_id=%s AND p.producto_id=%s""",
        (producto_id,))
    if not filas:
        return None
    r = filas[0]
    return {"producto_id": r[0], "sku": r[1], "nombre": r[2],
            "descripcion": r[3], "categoria_id": r[4],
            "categoria_nombre": r[5], "unidad": r[6], "activo": r[7]}


def mapa_categorias(company_id: str) -> dict[str, str]:
    """{producto_id: nombre_categoría} para productos con categoría.

    Lo usa Simulator.rentabilidad como `mapa_lineas` por defecto cuando
    quien llama no provee uno (antes: "sin_línea" para todo)."""
    filas = _consulta(
        company_id,
        """SELECT p.producto_id, c.nombre
           FROM os_catalogo_productos p
           JOIN os_catalogo_categorias c
             ON c.company_id=p.company_id AND c.categoria_id=p.categoria_id
           WHERE p.company_id=%s AND p.activo AND c.activa""",
        ())
    return {r[0]: r[1] for r in (filas or [])}


def almacen_registrado(company_id: str, almacen_id: str) -> bool | None:
    """True/False si el almacén está en el catálogo; None si no hay
    catálogo (el motor no puede opinar)."""
    filas = _consulta(
        company_id,
        "SELECT activo FROM os_almacenes WHERE company_id=%s AND almacen_id=%s",
        (almacen_id,))
    if filas is None:
        return None  # sin catálogo disponible
    if not filas:
        return False
    return bool(filas[0][0])


def validar_contra_catalogo(company_id: str, producto_id: str,
                            almacen_id: str) -> list[str]:
    """Advertencias de calidad de datos contra el catálogo.

    Vacío = todo bien o catálogo ausente (no bloquea). Pensado para la
    capa API antes de registrar movimientos en Inventario.
    """
    avisos: list[str] = []
    if not catalogo_tiene_datos(company_id):
        return avisos
    ficha = ficha_producto(company_id, producto_id)
    if ficha is None:
        avisos.append(f"producto {producto_id!r} no está en el catálogo")
    elif not ficha["activo"]:
        avisos.append(f"producto {producto_id!r} está desactivado"
                      " en el catálogo")
    reg = almacen_registrado(company_id, almacen_id)
    if reg is False:
        avisos.append(f"almacén {almacen_id!r} no registrado en el catálogo")
    return avisos
