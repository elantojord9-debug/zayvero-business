"""Inventario: CRUD de productos con validaciones Decimal reutilizadas.

Reutiliza catalogo.modelo.validar_precio / validar_costo (reglas puras).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from factura_persist import db
from factura_persist.uow import atomic_company
import config

# Motores reutilizados del catálogo maestro
from catalogo.modelo import (
    validar_precio, validar_costo, validar_id, ValidacionCatalogo,
)


def _validar_producto(sku: str, nombre: str, precio, costo, stock,
                      tipo: str = "producto",
                      stock_minimo=5) -> dict:
    try:
        sku_n = validar_id(sku or "SIN-SKU", "sku")
        nombre_n = validar_id(nombre or "", "nombre")
        precio_n = validar_precio(precio)
        costo_n = validar_costo(costo)
    except ValidacionCatalogo as exc:
        raise ValueError(str(exc))
    try:
        stock_n = Decimal(str(stock or "0"))
    except Exception:
        raise ValueError(f"stock debe ser numérico: {stock!r}")
    if stock_n < 0:
        raise ValueError("el stock no puede ser negativo")
    tipo_n = (tipo or "producto").strip().lower()
    if tipo_n not in config.TIPOS_PRODUCTO:
        raise ValueError(f"tipo inválido: {tipo!r}")
    try:
        minimo_n = Decimal(str(stock_minimo
                                if stock_minimo not in ("", None) else "5"))
    except Exception:
        raise ValueError(f"stock mínimo inválido: {stock_minimo!r}")
    if minimo_n < 0:
        raise ValueError("el stock mínimo no puede ser negativo")
    if tipo_n == "servicio":
        # Los servicios no llevan control de stock.
        stock_n = Decimal("0")
    return {"sku": sku_n, "nombre": nombre_n, "precio": precio_n,
            "costo": costo_n, "stock": stock_n, "tipo": tipo_n,
            "stock_minimo": minimo_n}


@atomic_company
def crear(empresa_id: int, sku: str, nombre: str, precio, costo=0,
          itbis_exento: bool = False, stock=0,
          tipo: str = "producto", stock_minimo=5,
          activo: bool = True) -> dict:
    v = _validar_producto(sku, nombre, precio, costo, stock, tipo,
                          stock_minimo)
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            dup = db.fetchone(
                cur, "SELECT id FROM factura_productos "
                     "WHERE empresa_id=%s AND sku=%s", (empresa_id, v["sku"]))
            if dup:
                raise ValueError(f"el SKU {v['sku']!r} ya existe")
            pid = db.insert_id(
                cur, "INSERT INTO factura_productos (empresa_id, sku, nombre, "
                     "precio, costo, itbis_exento, stock, tipo, stock_minimo,"
                     " activo, creado) "
                     "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (empresa_id, v["sku"], v["nombre"], str(v["precio"]),
                 str(v["costo"]), 1 if itbis_exento else 0, str(v["stock"]),
                 v["tipo"], str(v["stock_minimo"]),
                 1 if activo else 0,
                 datetime.now(timezone.utc).isoformat()))
        return obtener(empresa_id, pid)
    finally:
        conn.close()


def obtener(empresa_id: int, producto_id: int) -> dict | None:
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(
            cur, "SELECT * FROM factura_productos WHERE id=%s AND empresa_id=%s",
            (producto_id, empresa_id))
        return dict(fila) if fila else None
    finally:
        conn.close()


def listar(empresa_id: int, q: str = "", solo_activos: bool = True) -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        sql = "SELECT * FROM factura_productos WHERE empresa_id=%s"
        params: list = [empresa_id]
        if solo_activos:
            sql += " AND activo=1"
        if q:
            sql += " AND (nombre LIKE %s OR sku LIKE %s)"
            params += [f"%{q}%", f"%{q}%"]
        sql += " ORDER BY nombre"
        return [dict(f) for f in db.fetchall(cur, sql, params)]
    finally:
        conn.close()


@atomic_company
def actualizar(empresa_id: int, producto_id: int, sku: str, nombre: str,
               precio, costo=0, itbis_exento: bool = False, stock=0,
               activo: bool = True, tipo: str = "producto",
               stock_minimo=5) -> dict:
    v = _validar_producto(sku, nombre, precio, costo, stock, tipo,
                          stock_minimo)
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            dup = db.fetchone(
                cur, "SELECT id FROM factura_productos "
                     "WHERE empresa_id=%s AND sku=%s AND id<>%s",
                (empresa_id, v["sku"], producto_id))
            if dup:
                raise ValueError(f"el SKU {v['sku']!r} ya existe")
            db.execute(
                cur, "UPDATE factura_productos SET sku=%s, nombre=%s, "
                     "precio=%s, costo=%s, itbis_exento=%s, stock=%s,"
                     " tipo=%s, stock_minimo=%s, activo=%s "
                     "WHERE id=%s AND empresa_id=%s",
                (v["sku"], v["nombre"], str(v["precio"]), str(v["costo"]),
                 1 if itbis_exento else 0, str(v["stock"]),
                 v["tipo"], str(v["stock_minimo"]),
                 1 if activo else 0, producto_id, empresa_id))
        return obtener(empresa_id, producto_id)
    finally:
        conn.close()


def ajustar_stock(conn, empresa_id: int, producto_id: int,
                  delta: Decimal) -> None:
    """Ajusta stock dentro de una transacción ya abierta (llamador decide)."""
    from factura_persist.uow import require_company
    require_company(empresa_id)
    cur = conn.cursor()
    fila = db.fetchone(
        cur, "SELECT stock FROM factura_productos "
             "WHERE id=%s AND empresa_id=%s", (producto_id, empresa_id))
    if not fila:
        raise ValueError("producto no encontrado")
    nuevo = db.dec(fila[0]) + Decimal(str(delta))
    if nuevo < 0:
        raise ValueError("stock insuficiente para esta operación")
    db.execute(cur, "UPDATE factura_productos SET stock=%s "
                    "WHERE id=%s AND empresa_id=%s",
               (str(nuevo), producto_id, empresa_id))


def es_servicio(conn, empresa_id: int, producto_id: int) -> bool:
    """True si el ítem es de tipo servicio (no mueve inventario)."""
    cur = conn.cursor()
    fila = db.fetchone(
        cur, "SELECT tipo FROM factura_productos"
             " WHERE id=%s AND empresa_id=%s", (producto_id, empresa_id))
    return bool(fila) and str(fila[0] or "").lower() == "servicio"


def stock_bajo(empresa_id: int,
               umbral: float | None = None) -> list[dict]:
    """Alertas de inventario: productos de tipo 'producto' con
    stock <= su mínimo configurado (stock_minimo por producto).

    Cada alerta trae 'estado': 'agotado' (stock <= 0) o 'bajo'.
    Si se pasa `umbral`, se usa para todos (comportamiento anterior).
    """
    out = []
    for p in listar(empresa_id):
        if str(p.get("tipo") or "producto").lower() == "servicio":
            continue
        minimo = (Decimal(str(umbral)) if umbral is not None
                  else db.dec(p.get("stock_minimo") or
                              config.STOCK_BAJO_UMBRAL))
        stock = db.dec(p["stock"])
        if stock <= minimo:
            p["estado"] = "agotado" if stock <= 0 else "bajo"
            out.append(p)
    return sorted(out, key=lambda p: (db.dec(p["stock"]),
                                      str(p.get("nombre") or "")))
