"""Proveedores: CRUD con balance; enlazables a gastos.

Todo dato lleva empresa_id (aislamiento estricto). El RNC es opcional y,
cuando se indica, se valida como en clientes (9/11 dígitos).
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from factura_persist import db
from factura_core.clientes import normalizar_rnc


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validar(nombre: str, rnc: str, balance) -> dict:
    nombre_n = (nombre or "").strip()
    if not nombre_n:
        raise ValueError("el nombre del proveedor es obligatorio")
    try:
        balance_n = Decimal(str(balance if balance not in ("", None) else "0"))
    except Exception:
        raise ValueError(f"balance inválido: {balance!r}")
    return {"nombre": nombre_n, "rnc": normalizar_rnc(rnc),
            "balance": balance_n}


def crear(empresa_id: int, nombre: str, rnc: str = "",
          telefono: str = "", email: str = "", direccion: str = "",
          balance=0) -> dict:
    v = _validar(nombre, rnc, balance)
    email_n = (email or "").strip()
    if email_n and ("@" not in email_n or "." not in email_n):
        raise ValueError("email inválido")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            pid = db.insert_id(
                cur, "INSERT INTO factura_proveedores (empresa_id, nombre,"
                     " rnc, telefono, email, direccion, balance, activo,"
                     " creado) VALUES (%s,%s,%s,%s,%s,%s,%s,1,%s)",
                (empresa_id, v["nombre"], v["rnc"],
                 (telefono or "").strip(), email_n,
                 (direccion or "").strip(), str(v["balance"]), _ahora()))
        return obtener(empresa_id, pid)
    finally:
        conn.close()


def obtener(empresa_id: int, proveedor_id: int) -> dict | None:
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(
            cur, "SELECT * FROM factura_proveedores"
                 " WHERE id=%s AND empresa_id=%s",
            (proveedor_id, empresa_id))
        return dict(fila) if fila else None
    finally:
        conn.close()


def listar(empresa_id: int, q: str = "",
           solo_activos: bool = True) -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        sql = "SELECT * FROM factura_proveedores WHERE empresa_id=%s"
        params: list = [empresa_id]
        if solo_activos:
            sql += " AND activo=1"
        if q:
            sql += " AND (nombre LIKE %s OR rnc LIKE %s)"
            params += [f"%{q}%", f"%{q}%"]
        sql += " ORDER BY nombre"
        return [dict(f) for f in db.fetchall(cur, sql, params)]
    finally:
        conn.close()


def actualizar(empresa_id: int, proveedor_id: int, nombre: str,
               rnc: str = "", telefono: str = "", email: str = "",
               direccion: str = "", balance=0,
               activo: bool = True) -> dict:
    v = _validar(nombre, rnc, balance)
    if not obtener(empresa_id, proveedor_id):
        raise ValueError("proveedor no encontrado")
    email_n = (email or "").strip()
    if email_n and ("@" not in email_n or "." not in email_n):
        raise ValueError("email inválido")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(
                cur, "UPDATE factura_proveedores SET nombre=%s, rnc=%s,"
                     " telefono=%s, email=%s, direccion=%s, balance=%s,"
                     " activo=%s WHERE id=%s AND empresa_id=%s",
                (v["nombre"], v["rnc"], (telefono or "").strip(), email_n,
                 (direccion or "").strip(), str(v["balance"]),
                 1 if activo else 0, proveedor_id, empresa_id))
        return obtener(empresa_id, proveedor_id)
    finally:
        conn.close()


def eliminar(empresa_id: int, proveedor_id: int) -> dict:
    """Baja lógica: conserva el historial (gastos enlazados)."""
    p = obtener(empresa_id, proveedor_id)
    if not p:
        raise ValueError("proveedor no encontrado")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "UPDATE factura_proveedores SET activo=0"
                            " WHERE id=%s AND empresa_id=%s",
                       (proveedor_id, empresa_id))
        return obtener(empresa_id, proveedor_id)
    finally:
        conn.close()


def gastos_de_proveedor(empresa_id: int, proveedor_id: int,
                        limite: int = 100) -> list[dict]:
    """Gastos enlazados a un proveedor (historial de compras)."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        filas = db.fetchall(
            cur, "SELECT * FROM factura_gastos"
                 " WHERE empresa_id=%s AND proveedor_id=%s"
                 " ORDER BY fecha DESC, id DESC LIMIT %s",
            (empresa_id, proveedor_id, limite))
        return [dict(f) for f in filas]
    finally:
        conn.close()
