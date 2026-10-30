"""Clientes: CRUD + validación de formato de RNC (reutiliza os_ecf.modelos)."""
from __future__ import annotations

from datetime import datetime, timezone

from factura_persist import db

# Motor reutilizado: validación de RNC 9/11 dígitos
from os_ecf.modelos import validar_rnc, ErrorECF


def normalizar_rnc(rnc: str | None) -> str:
    """RNC vacío permitido (consumidor final); si trae valor debe ser 9/11 dígitos."""
    rnc = (rnc or "").strip()
    if not rnc:
        return ""
    try:
        return validar_rnc(rnc)
    except ErrorECF as exc:
        raise ValueError(str(exc))


def crear(empresa_id: int, nombre: str, rnc: str = "", telefono: str = "",
          direccion: str = "") -> dict:
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("el nombre del cliente es obligatorio")
    rnc_n = normalizar_rnc(rnc)
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            cid = db.insert_id(
                cur, "INSERT INTO factura_clientes "
                     "(empresa_id, nombre, rnc, telefono, direccion, creado) "
                     "VALUES (%s,%s,%s,%s,%s,%s)",
                (empresa_id, nombre, rnc_n, (telefono or "").strip(),
                 (direccion or "").strip(),
                 datetime.now(timezone.utc).isoformat()))
        return obtener(empresa_id, cid)
    finally:
        conn.close()


def obtener(empresa_id: int, cliente_id: int) -> dict | None:
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(
            cur, "SELECT * FROM factura_clientes WHERE id=%s AND empresa_id=%s",
            (cliente_id, empresa_id))
        return dict(fila) if fila else None
    finally:
        conn.close()


def listar(empresa_id: int, q: str = "") -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        if q:
            filas = db.fetchall(
                cur, "SELECT * FROM factura_clientes WHERE empresa_id=%s "
                     "AND (nombre LIKE %s OR rnc LIKE %s) ORDER BY nombre",
                (empresa_id, f"%{q}%", f"%{q}%"))
        else:
            filas = db.fetchall(
                cur, "SELECT * FROM factura_clientes WHERE empresa_id=%s "
                     "ORDER BY nombre", (empresa_id,))
        return [dict(f) for f in filas]
    finally:
        conn.close()


def actualizar(empresa_id: int, cliente_id: int, nombre: str, rnc: str = "",
               telefono: str = "", direccion: str = "") -> dict:
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("el nombre del cliente es obligatorio")
    rnc_n = normalizar_rnc(rnc)
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(
                cur, "UPDATE factura_clientes SET nombre=%s, rnc=%s, "
                     "telefono=%s, direccion=%s "
                     "WHERE id=%s AND empresa_id=%s",
                (nombre, rnc_n, (telefono or "").strip(),
                 (direccion or "").strip(), cliente_id, empresa_id))
        return obtener(empresa_id, cliente_id)
    finally:
        conn.close()


def eliminar(empresa_id: int, cliente_id: int) -> None:
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            uso = db.fetchone(
                cur, "SELECT COUNT(*) FROM factura_ventas "
                     "WHERE cliente_id=%s AND empresa_id=%s",
                (cliente_id, empresa_id))
            if uso and uso[0]:
                raise ValueError("no se puede eliminar: el cliente tiene "
                                 "ventas registradas")
            db.execute(cur, "DELETE FROM factura_clientes "
                            "WHERE id=%s AND empresa_id=%s",
                       (cliente_id, empresa_id))
    finally:
        conn.close()
