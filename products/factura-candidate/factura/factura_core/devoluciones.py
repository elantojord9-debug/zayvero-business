"""Devoluciones y ajustes de inventario.

- Nota de devolución SOBRE una venta: devuelve stock al inventario y deja
  constancia con motivo. La venta original NO se edita (principio del
  sistema: las ventas emitidas solo se anulan).
- Ajustes manuales de inventario: siempre con motivo; el stock resultante
  nunca puede quedar negativo.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

from factura_persist import db
from factura_persist.uow import atomic_company
from factura_core import inventario, ventas


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _numero_devolucion(conn, empresa_id: int) -> str:
    cur = conn.cursor()
    fila = db.fetchone(
        cur, "SELECT COUNT(*) FROM factura_devoluciones WHERE empresa_id=%s",
        (empresa_id,))
    return f"D-{(int(fila[0]) + 1):06d}"


@atomic_company
def crear_devolucion(empresa_id: int, venta_id: int,
                     items_devueltos: list[dict], motivo: str = "",
                     creada_por: int | None = None,
                     idempotencia: str | None = None) -> dict:
    """Crea una nota de devolución sobre `venta_id`.

    items_devueltos: [{producto_id, cantidad}]. La cantidad no puede
    superar lo vendido (menos lo ya devuelto en notas previas).
    """
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValueError("la devolución requiere un motivo")
    if not items_devueltos:
        raise ValueError("indique al menos un ítem a devolver")
    v = ventas.obtener(empresa_id, venta_id)
    if not v:
        raise ValueError("venta no encontrada")
    if v["estado"] == "anulada":
        raise ValueError("no se puede devolver sobre una venta anulada")

    vendidos: dict[int, dict] = {}
    for it in v["items"]:
        pid = it.get("producto_id")
        if pid:
            pid = int(pid)
            if pid not in vendidos:
                vendidos[pid] = dict(it, cantidad="0", subtotal="0", itbis="0")
            for field in ("cantidad", "subtotal", "itbis"):
                vendidos[pid][field] = str(Decimal(vendidos[pid][field]) + Decimal(str(it.get(field, "0"))))

    ya_devuelto: dict[int, Decimal] = {}
    conn0 = db.connect()
    try:
        cur0 = conn0.cursor()
        filas = db.fetchall(
            cur0, "SELECT items FROM factura_devoluciones"
                  " WHERE empresa_id=%s AND venta_id=%s",
            (empresa_id, venta_id))
        for f in filas:
            for it in json.loads(f[0] or "[]"):
                pid = int(it["producto_id"])
                ya_devuelto[pid] = ya_devuelto.get(pid, Decimal("0")) + \
                    Decimal(str(it["cantidad"]))
    finally:
        conn0.close()

    agregados = {}
    for item in items_devueltos:
        pid = int(item.get("producto_id") or 0)
        cant = Decimal(str(item.get("cantidad", 0)))
        if not cant.is_finite() or cant <= 0:
            raise ValueError("cantidad inválida en la devolución")
        agregados[pid] = agregados.get(pid, Decimal("0")) + cant
    items_devueltos = [{"producto_id": pid, "cantidad": str(cant)}
                       for pid, cant in agregados.items()]
    normalizados = []
    total = Decimal("0")
    tax_total = Decimal("0")
    from decimal import ROUND_HALF_UP
    gross = Decimal(v["subtotal"]) + Decimal(v["itbis"])
    factor = Decimal(v["total"]) / gross if gross else Decimal("0")
    for it in items_devueltos:
        pid = int(it.get("producto_id") or 0)
        if pid not in vendidos:
            raise ValueError(f"el ítem {pid} no pertenece a la venta")
        try:
            cant = Decimal(str(it.get("cantidad", 0)))
        except Exception:
            raise ValueError("cantidad inválida en la devolución")
        if cant <= 0:
            raise ValueError("la cantidad a devolver debe ser positiva")
        disp = Decimal(str(vendidos[pid]["cantidad"])) - \
            ya_devuelto.get(pid, Decimal("0"))
        if cant > disp:
            raise ValueError(
                f"no se pueden devolver {cant} de "
                f"{vendidos[pid]['nombre']!r}: solo quedan {disp} por devolver")
        precio = Decimal(str(vendidos[pid]["precio"]))
        original = vendidos[pid]
        quantity = Decimal(original["cantidad"])
        sub = ((Decimal(original["subtotal"]) + Decimal(original["itbis"])) * cant / quantity * factor).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        tax = (Decimal(original["itbis"]) * cant / quantity).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        total += sub
        tax_total += tax
        normalizados.append({
            "producto_id": pid,
            "nombre": vendidos[pid]["nombre"],
            "cantidad": str(cant),
            "precio": str(precio),
            "subtotal": str(sub),
        })

    if all(ya_devuelto.get(pid, Decimal("0")) + agregados.get(pid, Decimal("0")) == Decimal(item["cantidad"]) for pid, item in vendidos.items()):
        conn_previous = db.connect()
        try:
            prev = db.fetchall(conn_previous.cursor(), "SELECT total, itbis FROM factura_devoluciones WHERE empresa_id=%s AND venta_id=%s", (empresa_id, venta_id))
            total = Decimal(v["total"]) - sum((db.dec(row[0]) for row in prev), Decimal("0"))
            tax_total = Decimal(v["itbis"]) - sum((db.dec(row[1]) for row in prev), Decimal("0"))
        finally:
            conn_previous.close()
    conn = db.connect()
    try:
        with db.locked(conn):
            for it in normalizados:
                # Los servicios no movieron inventario al vender: no se devuelve.
                if not inventario.es_servicio(conn, empresa_id,
                                              int(it["producto_id"])):
                    inventario.ajustar_stock(conn, empresa_id,
                                             int(it["producto_id"]),
                                             Decimal(it["cantidad"]))
            cur = conn.cursor()
            numero = _numero_devolucion(conn, empresa_id)
            did = db.insert_id(
                cur, "INSERT INTO factura_devoluciones (empresa_id, venta_id,"
                     " numero, items, total, motivo, creada_por, creada_en)"
                     " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                (empresa_id, venta_id, numero,
                 json.dumps(normalizados, ensure_ascii=False), format(total.normalize(), "f"),
                 motivo, creada_por, _ahora()))
            db.execute(cur, "UPDATE factura_devoluciones SET itbis=%s, financiera=1 WHERE id=%s AND empresa_id=%s", (str(tax_total), did, empresa_id))
            fila = db.fetchone(
                cur, "SELECT * FROM factura_devoluciones WHERE id=%s", (did,))
            d = dict(fila)
            d["items"] = json.loads(d["items"] or "[]")
            return d
    finally:
        conn.close()


def listar(empresa_id: int, limite: int = 100) -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        filas = db.fetchall(
            cur, "SELECT d.*, v.numero AS venta_numero"
                 " FROM factura_devoluciones d"
                 " LEFT JOIN factura_ventas v ON v.id=d.venta_id"
                 " WHERE d.empresa_id=%s ORDER BY d.id DESC LIMIT %s",
            (empresa_id, limite))
        out = []
        for f in filas:
            d = dict(f)
            d["items"] = json.loads(d["items"] or "[]")
            out.append(d)
        return out
    finally:
        conn.close()


def obtener(empresa_id: int, devolucion_id: int) -> dict | None:
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(
            cur, "SELECT * FROM factura_devoluciones WHERE id=%s"
                 " AND empresa_id=%s", (devolucion_id, empresa_id))
        if not fila:
            return None
        d = dict(fila)
        d["items"] = json.loads(d["items"] or "[]")
        return d
    finally:
        conn.close()


@atomic_company
def ajuste_manual(empresa_id: int, producto_id: int, stock_nuevo,
                  motivo: str = "", creado_por: int | None = None) -> dict:
    """Ajuste manual de inventario al valor indicado (con motivo)."""
    motivo = (motivo or "").strip()
    if not motivo:
        raise ValueError("el ajuste requiere un motivo")
    try:
        nuevo = Decimal(str(stock_nuevo))
    except Exception:
        raise ValueError("stock inválido")
    if nuevo < 0:
        raise ValueError("el stock no puede quedar negativo")
    conn = db.connect()
    try:
        with db.locked(conn):
            cur = conn.cursor()
            fila = db.fetchone(
                cur, "SELECT stock FROM factura_productos"
                     " WHERE id=%s AND empresa_id=%s",
                (producto_id, empresa_id))
            if not fila:
                raise ValueError("producto no encontrado")
            antes = db.dec(fila[0])
            db.execute(cur, "UPDATE factura_productos SET stock=%s"
                            " WHERE id=%s AND empresa_id=%s",
                       (str(nuevo), producto_id, empresa_id))
            aid = db.insert_id(
                cur, "INSERT INTO factura_ajustes_inventario (empresa_id,"
                     " producto_id, stock_antes, stock_despues, motivo,"
                     " creado_por, creado_en)"
                     " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (empresa_id, producto_id, str(antes), str(nuevo), motivo,
                 creado_por, _ahora()))
            f = db.fetchone(
                cur, "SELECT * FROM factura_ajustes_inventario WHERE id=%s",
                (aid,))
            return dict(f)
    finally:
        conn.close()


def historial_ajustes(empresa_id: int, producto_id: int | None = None,
                      limite: int = 100) -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        sql = ("SELECT a.*, p.nombre AS producto_nombre"
               " FROM factura_ajustes_inventario a"
               " LEFT JOIN factura_productos p ON p.id=a.producto_id"
               " WHERE a.empresa_id=%s")
        params: list = [empresa_id]
        if producto_id:
            sql += " AND a.producto_id=%s"
            params.append(producto_id)
        sql += " ORDER BY a.id DESC LIMIT %s"
        params.append(limite)
        return [dict(f) for f in db.fetchall(cur, sql, params)]
    finally:
        conn.close()


@atomic_company
def reembolsar(empresa_id: int, devolucion_id: int, monto,
                metodo: str = "efectivo", creado_por=None,
                idempotencia: str | None = None):
    """Record an explicitly delivered refund, never initiate bank transfers."""
    from factura_core import pagos, caja
    import config
    if not idempotencia:
        raise ValueError("se requiere clave de idempotencia para el reembolso")
    amount = Decimal(str(monto))
    if not amount.is_finite() or amount <= 0:
        raise ValueError("monto inválido")
    if metodo not in config.METODOS_PAGO:
        raise ValueError("método inválido")
    note = obtener(empresa_id, devolucion_id)
    if not note or not note.get("financiera"):
        raise ValueError("devolución no encontrada o histórica pendiente de conciliación")
    conn = db.connect()
    try:
        cur = conn.cursor()
        prev = sum((db.dec(r[0]) for r in db.fetchall(cur, "SELECT monto FROM factura_reembolsos WHERE empresa_id=%s AND devolucion_id=%s", (empresa_id, devolucion_id))), Decimal("0"))
        available = min(Decimal(note["total"]) - prev,
                        max(Decimal("0"), -pagos.balance(empresa_id, note["venta_id"])))
        if amount > available:
            raise ValueError("el reembolso supera el crédito disponible")
        if metodo == "efectivo":
            caja.exigir_abierta(empresa_id)
            caja.registrar_movimiento(empresa_id, "reembolso", -amount,
                                      "devolución " + note["numero"], creado_por)
        rid = db.insert_id(cur, "INSERT INTO factura_reembolsos (empresa_id, venta_id, devolucion_id, monto, metodo, creado_por, creada) VALUES (%s,%s,%s,%s,%s,%s,%s)", (empresa_id, note["venta_id"], devolucion_id, str(amount), metodo, creado_por, _ahora()))
        return {"id": rid, "devolucion_id": devolucion_id, "monto": str(amount), "metodo": metodo}
    finally:
        conn.close()
