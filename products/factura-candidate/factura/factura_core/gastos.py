"""Gastos e ingresos no-venta.

Tablas: factura_gastos / factura_ingresos. Todo dato lleva empresa_id
(aislamiento estricto); montos como TEXT con Decimal exacto en Python.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal

import config
from factura_persist import db

TIPOS = ("gasto", "ingreso")
_TABLAS = {"gasto": "factura_gastos", "ingreso": "factura_ingresos"}


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validar(tipo: str, fecha: str, categoria: str, descripcion,
             monto, metodo: str) -> dict:
    tipo = (tipo or "").strip().lower()
    if tipo not in TIPOS:
        raise ValueError(f"tipo inválido: {tipo!r}")
    fecha_n = (fecha or "").strip()
    try:
        date.fromisoformat(fecha_n)
    except Exception:
        raise ValueError(f"fecha inválida (use AAAA-MM-DD): {fecha!r}")
    cats = (config.CATEGORIAS_GASTO if tipo == "gasto"
            else config.CATEGORIAS_INGRESO)
    cat_n = (categoria or "").strip().lower()
    if cat_n not in cats:
        raise ValueError(f"categoría inválida: {categoria!r}")
    desc_n = (descripcion or "").strip()
    if not desc_n:
        raise ValueError("la descripción es obligatoria")
    try:
        monto_n = Decimal(str(monto))
    except Exception:
        raise ValueError(f"monto inválido: {monto!r}")
    if monto_n <= 0:
        raise ValueError("el monto debe ser positivo")
    metodo_n = (metodo or "efectivo").strip().lower()
    if metodo_n not in config.METODOS_PAGO:
        raise ValueError(f"método inválido: {metodo!r}")
    return {"tipo": tipo, "fecha": fecha_n, "categoria": cat_n,
            "descripcion": desc_n, "monto": monto_n, "metodo": metodo_n}


def _validar_sucursal(empresa_id: int, sucursal_id) -> int | None:
    if not sucursal_id:
        return None
    from factura_core import sucursales
    s = sucursales.obtener(empresa_id, int(sucursal_id))
    if not s:
        raise ValueError("sucursal inválida")
    if not s["activa"]:
        raise ValueError("la sucursal está inactiva")
    return s["id"]


def _validar_proveedor(empresa_id: int, proveedor_id) -> int | None:
    if not proveedor_id:
        return None
    from factura_core import proveedores
    p = proveedores.obtener(empresa_id, int(proveedor_id))
    if not p:
        raise ValueError("proveedor inválido")
    if not p["activo"]:
        raise ValueError("el proveedor está inactivo")
    return p["id"]


def crear(empresa_id: int, tipo: str, fecha: str, categoria: str,
          descripcion: str, monto, metodo: str = "efectivo",
          sucursal_id=None, proveedor_id=None, comprobante: str = "",
          creada_por: int | None = None) -> dict:
    """Registra un gasto o un ingreso no-venta."""
    v = _validar(tipo, fecha, categoria, descripcion, monto, metodo)
    sid = _validar_sucursal(empresa_id, sucursal_id)
    pid = None
    if v["tipo"] == "gasto":
        pid = _validar_proveedor(empresa_id, proveedor_id)
    elif proveedor_id:
        raise ValueError("los ingresos no llevan proveedor")
    tabla = _TABLAS[v["tipo"]]
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            if v["tipo"] == "gasto":
                gid = db.insert_id(
                    cur, f"INSERT INTO {tabla} (empresa_id, fecha, categoria,"
                         " descripcion, comprobante, monto, metodo,"
                         " sucursal_id, proveedor_id, creado_por, creada)"
                         " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (empresa_id, v["fecha"], v["categoria"],
                     v["descripcion"], (comprobante or "").strip(),
                     str(v["monto"]), v["metodo"], sid, pid, creada_por,
                     _ahora()))
            else:
                gid = db.insert_id(
                    cur, f"INSERT INTO {tabla} (empresa_id, fecha, categoria,"
                         " descripcion, comprobante, monto, metodo,"
                         " sucursal_id, creado_por, creada)"
                         " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (empresa_id, v["fecha"], v["categoria"],
                     v["descripcion"], (comprobante or "").strip(),
                     str(v["monto"]), v["metodo"], sid, creada_por,
                     _ahora()))
        return obtener(empresa_id, v["tipo"], gid)
    finally:
        conn.close()


def obtener(empresa_id: int, tipo: str, mov_id: int) -> dict | None:
    tabla = _TABLAS.get((tipo or "").strip().lower())
    if not tabla:
        return None
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(
            cur, f"SELECT * FROM {tabla} WHERE id=%s AND empresa_id=%s",
            (mov_id, empresa_id))
        if not fila:
            return None
        d = dict(fila)
        d["tipo"] = (tipo or "").strip().lower()
        return d
    finally:
        conn.close()


def listar(empresa_id: int, tipo: str = "gasto",
           desde: str | None = None, hasta: str | None = None,
           categoria: str | None = None,
           limite: int = 500) -> list[dict]:
    tipo = (tipo or "gasto").strip().lower()
    if tipo not in TIPOS:
        raise ValueError(f"tipo inválido: {tipo!r}")
    tabla = _TABLAS[tipo]
    conn = db.connect()
    try:
        cur = conn.cursor()
        sql = f"SELECT * FROM {tabla} WHERE empresa_id=%s"
        params: list = [empresa_id]
        if desde:
            sql += " AND fecha>=%s"
            params.append(desde)
        if hasta:
            sql += " AND fecha<=%s"
            params.append(hasta)
        if categoria:
            sql += " AND categoria=%s"
            params.append(categoria.strip().lower())
        sql += " ORDER BY fecha DESC, id DESC LIMIT %s"
        params.append(limite)
        out = []
        for f in db.fetchall(cur, sql, params):
            d = dict(f)
            d["tipo"] = tipo
            out.append(d)
        return out
    finally:
        conn.close()


def actualizar(empresa_id: int, tipo: str, mov_id: int, fecha: str,
               categoria: str, descripcion: str, monto,
               metodo: str = "efectivo", sucursal_id=None,
               proveedor_id=None, comprobante: str = "") -> dict:
    v = _validar(tipo, fecha, categoria, descripcion, monto, metodo)
    if not obtener(empresa_id, v["tipo"], mov_id):
        raise ValueError("registro no encontrado")
    sid = _validar_sucursal(empresa_id, sucursal_id)
    tabla = _TABLAS[v["tipo"]]
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            if v["tipo"] == "gasto":
                pid = _validar_proveedor(empresa_id, proveedor_id)
                db.execute(
                    cur, f"UPDATE {tabla} SET fecha=%s, categoria=%s,"
                         " descripcion=%s, comprobante=%s, monto=%s,"
                         " metodo=%s, sucursal_id=%s, proveedor_id=%s"
                         " WHERE id=%s AND empresa_id=%s",
                    (v["fecha"], v["categoria"], v["descripcion"],
                     (comprobante or "").strip(), str(v["monto"]),
                     v["metodo"], sid, pid, mov_id, empresa_id))
            else:
                if proveedor_id:
                    raise ValueError("los ingresos no llevan proveedor")
                db.execute(
                    cur, f"UPDATE {tabla} SET fecha=%s, categoria=%s,"
                         " descripcion=%s, comprobante=%s, monto=%s,"
                         " metodo=%s, sucursal_id=%s"
                         " WHERE id=%s AND empresa_id=%s",
                    (v["fecha"], v["categoria"], v["descripcion"],
                     (comprobante or "").strip(), str(v["monto"]),
                     v["metodo"], sid, mov_id, empresa_id))
        return obtener(empresa_id, v["tipo"], mov_id)
    finally:
        conn.close()


def eliminar(empresa_id: int, tipo: str, mov_id: int) -> None:
    tipo = (tipo or "").strip().lower()
    if tipo not in TIPOS:
        raise ValueError(f"tipo inválido: {tipo!r}")
    if not obtener(empresa_id, tipo, mov_id):
        raise ValueError("registro no encontrado")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, f"DELETE FROM {_TABLAS[tipo]}"
                            " WHERE id=%s AND empresa_id=%s",
                       (mov_id, empresa_id))
    finally:
        conn.close()


def total(empresa_id: int, tipo: str = "gasto",
          desde: str | None = None, hasta: str | None = None) -> Decimal:
    return sum((db.dec(m["monto"])
                for m in listar(empresa_id, tipo, desde, hasta,
                                limite=100000)),
               Decimal("0"))


def por_categoria(empresa_id: int, tipo: str = "gasto",
                  desde: str | None = None,
                  hasta: str | None = None) -> list[dict]:
    """Totales agrupados por categoría (para el reporte)."""
    grupos: dict[str, dict] = {}
    for m in listar(empresa_id, tipo, desde, hasta, limite=100000):
        g = grupos.setdefault(m["categoria"],
                              {"categoria": m["categoria"], "cantidad": 0,
                               "total": Decimal("0")})
        g["cantidad"] += 1
        g["total"] += db.dec(m["monto"])
    filas = sorted(grupos.values(), key=lambda g: g["total"], reverse=True)
    for g in filas:
        g["total"] = str(g["total"])
    return filas
