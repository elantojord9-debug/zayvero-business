"""Soporte (tickets por empresa) y solicitudes de demo (landing pública)."""
from __future__ import annotations

from datetime import datetime, timezone

from factura_persist import db


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def crear_ticket(empresa_id: int, usuario_id: int | None, asunto: str,
                 mensaje: str) -> dict:
    asunto = (asunto or "").strip()
    mensaje = (mensaje or "").strip()
    if not asunto or not mensaje:
        raise ValueError("el asunto y el mensaje son obligatorios")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            tid = db.insert_id(
                cur, "INSERT INTO factura_tickets (empresa_id, usuario_id,"
                     " asunto, mensaje, estado, creado_en)"
                     " VALUES (%s,%s,%s,%s,'abierto',%s)",
                (empresa_id, usuario_id, asunto, mensaje, _ahora()))
            fila = db.fetchone(cur, "SELECT * FROM factura_tickets"
                                    " WHERE id=%s", (tid,))
        return dict(fila)
    finally:
        conn.close()


def listar_tickets(empresa_id: int, incluir_cerrados: bool = True) -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        sql = ("SELECT t.*, u.email AS usuario_email FROM factura_tickets t"
               " LEFT JOIN factura_usuarios u ON u.id=t.usuario_id"
               " WHERE t.empresa_id=%s")
        if not incluir_cerrados:
            sql += " AND t.estado='abierto'"
        sql += " ORDER BY t.id DESC"
        return [dict(f) for f in db.fetchall(cur, sql, (empresa_id,))]
    finally:
        conn.close()


def cambiar_estado_ticket(empresa_id: int, ticket_id: int,
                          estado: str) -> dict:
    if estado not in ("abierto", "cerrado"):
        raise ValueError("estado inválido")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "UPDATE factura_tickets SET estado=%s,"
                            " actualizado_en=%s WHERE id=%s AND empresa_id=%s",
                       (estado, _ahora(), ticket_id, empresa_id))
            fila = db.fetchone(cur, "SELECT * FROM factura_tickets"
                                    " WHERE id=%s", (ticket_id,))
        if not fila:
            raise ValueError("ticket no encontrado")
        return dict(fila)
    finally:
        conn.close()


def crear_solicitud(nombre: str, empresa: str, email: str, telefono: str,
                    mensaje: str) -> dict:
    nombre = (nombre or "").strip()
    email = (email or "").strip().lower()
    if not nombre:
        raise ValueError("el nombre es obligatorio")
    if "@" not in email:
        raise ValueError("email inválido")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            sid = db.insert_id(
                cur, "INSERT INTO factura_solicitudes (nombre, empresa,"
                     " email, telefono, mensaje, estado, creada_en)"
                     " VALUES (%s,%s,%s,%s,%s,'nueva',%s)",
                (nombre, (empresa or "").strip(), email,
                 (telefono or "").strip(), (mensaje or "").strip(), _ahora()))
            fila = db.fetchone(cur, "SELECT * FROM factura_solicitudes"
                                    " WHERE id=%s", (sid,))
        return dict(fila)
    finally:
        conn.close()


def listar_solicitudes() -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        filas = db.fetchall(cur, "SELECT * FROM factura_solicitudes"
                                 " ORDER BY id DESC LIMIT 200")
        return [dict(f) for f in filas]
    finally:
        conn.close()


def cambiar_estado_solicitud(solicitud_id: int, estado: str) -> None:
    if estado not in ("nueva", "contactada", "descartada"):
        raise ValueError("estado inválido")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "UPDATE factura_solicitudes SET estado=%s"
                            " WHERE id=%s", (estado, solicitud_id))
    finally:
        conn.close()
