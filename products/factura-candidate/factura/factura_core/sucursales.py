"""Sucursales: CRUD básico + límite según plan.

El selector de sucursal al vender se guarda en
`factura_ventas.sucursal_id` (nullable). Solo el plan Pro permite más de
una sucursal activa.
"""
from __future__ import annotations

from datetime import datetime, timezone

from factura_persist import db
import config


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def listar(empresa_id: int, solo_activas: bool = False) -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        sql = "SELECT * FROM factura_sucursales WHERE empresa_id=%s"
        if solo_activas:
            sql += " AND activa=1"
        sql += " ORDER BY id"
        return [dict(f) for f in db.fetchall(cur, sql, (empresa_id,))]
    finally:
        conn.close()


def obtener(empresa_id: int, sucursal_id: int) -> dict | None:
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(
            cur, "SELECT * FROM factura_sucursales WHERE id=%s"
                 " AND empresa_id=%s", (sucursal_id, empresa_id))
        return dict(fila) if fila else None
    finally:
        conn.close()


def contar_activas(empresa_id: int) -> int:
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(
            cur, "SELECT COUNT(*) FROM factura_sucursales"
                 " WHERE empresa_id=%s AND activa=1", (empresa_id,))
        return int(fila[0])
    finally:
        conn.close()


def _plan_limite(empresa_id: int) -> int:
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(cur, "SELECT plan FROM factura_empresas WHERE id=%s",
                           (empresa_id,))
        plan = (fila[0] if fila else "basico") or "basico"
    finally:
        conn.close()
    return config.PLANES.get(plan, config.PLANES["basico"])["max_sucursales"]


def crear(empresa_id: int, nombre: str, direccion: str = "",
          telefono: str = "") -> dict:
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("el nombre de la sucursal es obligatorio")
    limite = _plan_limite(empresa_id)
    if contar_activas(empresa_id) >= limite:
        raise ValueError(
            "límite del plan alcanzado: su plan permite "
            f"{limite} sucursal(es) activa(s)")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            sid = db.insert_id(
                cur, "INSERT INTO factura_sucursales (empresa_id, nombre,"
                     " direccion, telefono, activa, creada)"
                     " VALUES (%s,%s,%s,%s,1,%s)",
                (empresa_id, nombre, (direccion or "").strip(),
                 (telefono or "").strip(), _ahora()))
        return obtener(empresa_id, sid)
    finally:
        conn.close()


def actualizar(empresa_id: int, sucursal_id: int, nombre: str,
               direccion: str = "", telefono: str = "",
               activa: bool = True) -> dict:
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("el nombre de la sucursal es obligatorio")
    if not obtener(empresa_id, sucursal_id):
        raise ValueError("sucursal no encontrada")
    if activa:
        # Reactivar también respeta el límite
        limite = _plan_limite(empresa_id)
        n = contar_activas(empresa_id)
        actual = obtener(empresa_id, sucursal_id)
        if not actual["activa"] and n >= limite:
            raise ValueError(
                "límite del plan alcanzado: su plan permite "
                f"{limite} sucursal(es) activa(s)")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(
                cur, "UPDATE factura_sucursales SET nombre=%s, direccion=%s,"
                     " telefono=%s, activa=%s WHERE id=%s AND empresa_id=%s",
                (nombre, (direccion or "").strip(),
                 (telefono or "").strip(), 1 if activa else 0,
                 sucursal_id, empresa_id))
        return obtener(empresa_id, sucursal_id)
    finally:
        conn.close()
