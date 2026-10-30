"""Caja: turnos de apertura/cierre y movimientos.

- Un solo turno abierto por empresa. El POS exige turno abierto.
- Al cerrar se compara el conteo físico (monto_final) con el esperado
  (monto_inicial + movimientos) y se registra la diferencia.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

from factura_persist import db
from factura_persist.uow import atomic_company
from factura_core import inventario  # noqa: F401 (coherencia de paquete)


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def turno_abierto(empresa_id: int) -> dict | None:
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(
            cur, "SELECT * FROM factura_caja WHERE empresa_id=%s"
                 " AND estado='abierta' ORDER BY id DESC LIMIT 1",
            (empresa_id,))
        return dict(fila) if fila else None
    finally:
        conn.close()


@atomic_company
def abrir(empresa_id: int, usuario_id: int, monto_inicial,
          sucursal_id: int | None = None) -> dict:
    """Abre un turno de caja. Falla si ya hay uno abierto."""
    try:
        m = Decimal(str(monto_inicial))
    except Exception:
        raise ValueError("monto inicial inválido")
    if m < 0:
        raise ValueError("el monto inicial no puede ser negativo")
    if turno_abierto(empresa_id):
        raise ValueError("ya hay un turno de caja abierto")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            cid = db.insert_id(
                cur, "INSERT INTO factura_caja (empresa_id, sucursal_id,"
                     " estado, abierta_por, monto_inicial, abierta_en)"
                     " VALUES (%s,%s,'abierta',%s,%s,%s)",
                (empresa_id, sucursal_id, usuario_id, str(m), _ahora()))
            db.execute(
                cur, "INSERT INTO factura_caja_movimientos (caja_id,"
                     " empresa_id, tipo, monto, motivo, creado_por, creado_en)"
                     " VALUES (%s,%s,'apertura',%s,'apertura de turno',%s,%s)",
                (cid, empresa_id, str(m), usuario_id, _ahora()))
        return turno_abierto(empresa_id)
    finally:
        conn.close()


@atomic_company
def registrar_movimiento(empresa_id: int, tipo: str, monto, motivo: str = "",
                         creado_por: int | None = None,
                         idempotencia: str | None = None) -> dict:
    """Registra un movimiento en el turno abierto (tipos libres: venta,
    pago, gasto, retiro, ingreso...). Monto con signo: positivo suma,
    negativo resta."""
    turno = turno_abierto(empresa_id)
    if not turno:
        raise ValueError("no hay turno de caja abierto")
    try:
        m = Decimal(str(monto))
    except Exception:
        raise ValueError("monto inválido")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            mid = db.insert_id(
                cur, "INSERT INTO factura_caja_movimientos (caja_id,"
                     " empresa_id, tipo, monto, motivo, creado_por, creado_en)"
                     " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (turno["id"], empresa_id, (tipo or "").strip()[:40],
                 str(m), (motivo or "").strip(), creado_por, _ahora()))
            fila = db.fetchone(
                cur, "SELECT * FROM factura_caja_movimientos WHERE id=%s",
                (mid,))
        return dict(fila)
    finally:
        conn.close()


def movimientos(caja_id: int, empresa_id: int) -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        filas = db.fetchall(
            cur, "SELECT * FROM factura_caja_movimientos"
                 " WHERE caja_id=%s AND empresa_id=%s ORDER BY id",
            (caja_id, empresa_id))
        return [dict(f) for f in filas]
    finally:
        conn.close()


def esperado(caja_id: int, empresa_id: int) -> Decimal:
    """Efectivo esperado = suma de movimientos del turno."""
    movs = movimientos(caja_id, empresa_id)
    return sum((db.dec(m["monto"]) for m in movs), Decimal("0"))


@atomic_company
def cerrar(empresa_id: int, usuario_id: int, monto_final) -> dict:
    """Cierra el turno: registra el conteo físico y la diferencia."""
    turno = turno_abierto(empresa_id)
    if not turno:
        raise ValueError("no hay turno de caja abierto")
    try:
        mf = Decimal(str(monto_final))
    except Exception:
        raise ValueError("monto final inválido")
    if mf < 0:
        raise ValueError("el monto final no puede ser negativo")
    esp = esperado(turno["id"], empresa_id)
    dif = mf - esp
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(
                cur, "INSERT INTO factura_caja_movimientos (caja_id,"
                     " empresa_id, tipo, monto, motivo, creado_por, creado_en)"
                     " VALUES (%s,%s,'cierre',%s,'conteo físico de cierre',%s,%s)",
                (turno["id"], empresa_id, "0", usuario_id, _ahora()))
            db.execute(
                cur, "UPDATE factura_caja SET estado='cerrada',"
                     " monto_final=%s, diferencia=%s, cerrada_en=%s,"
                     " cerrada_por=%s WHERE id=%s AND empresa_id=%s",
                (str(mf), str(dif), _ahora(), usuario_id,
                 turno["id"], empresa_id))
        t = turno_abierto(empresa_id)
        conn2 = db.connect()
        try:
            cur2 = conn2.cursor()
            fila = db.fetchone(cur2, "SELECT * FROM factura_caja WHERE id=%s",
                               (turno["id"],))
            return dict(fila)
        finally:
            conn2.close()
    finally:
        conn.close()


def historial(empresa_id: int, limite: int = 30) -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        filas = db.fetchall(
            cur, "SELECT * FROM factura_caja WHERE empresa_id=%s"
                 " ORDER BY id DESC LIMIT %s", (empresa_id, limite))
        return [dict(f) for f in filas]
    finally:
        conn.close()


def exigir_abierta(empresa_id: int) -> dict:
    """El POS la llama antes de cobrar: sin turno abierto no hay venta."""
    t = turno_abierto(empresa_id)
    if not t:
        raise ValueError("debe abrir la caja antes de vender (turno cerrado)")
    return t
