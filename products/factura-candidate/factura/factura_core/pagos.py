"""Pagos y cuentas por cobrar (CxC)."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import config
from factura_persist import db
from factura_persist.uow import atomic_company
from factura_core import ventas


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def pagos_de_venta(empresa_id: int, venta_id: int) -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        filas = db.fetchall(
            cur, "SELECT * FROM factura_pagos WHERE empresa_id=%s AND venta_id=%s"
                 " ORDER BY id", (empresa_id, venta_id))
        return [dict(f) for f in filas]
    finally:
        conn.close()


def balance(empresa_id: int, venta_id: int) -> Decimal:
    v = ventas.obtener(empresa_id, venta_id)
    if not v:
        raise ValueError("venta no encontrada")
    pagado = sum((db.dec(p["monto"]) for p in pagos_de_venta(empresa_id, venta_id)),
                 Decimal("0"))
    conn = db.connect()
    try:
        cur = conn.cursor()
        credits = sum((db.dec(row[0]) for row in db.fetchall(cur, "SELECT total FROM factura_devoluciones WHERE empresa_id=%s AND venta_id=%s AND financiera=1", (empresa_id, venta_id))), Decimal("0"))
        refunds = sum((db.dec(row[0]) for row in db.fetchall(cur, "SELECT monto FROM factura_reembolsos WHERE empresa_id=%s AND venta_id=%s", (empresa_id, venta_id))), Decimal("0"))
        return db.dec(v["total"]) - credits - pagado + refunds
    finally:
        conn.close()


@atomic_company
def registrar_pago(empresa_id: int, venta_id: int, monto,
                   metodo: str = "efectivo",
                   creado_por: int | None = None,
                   idempotencia: str | None = None) -> dict:
    """Registra un abono. FASE 2 — idempotencia: con la misma clave no se
    duplica el cobro (el índice parcial UNIQUE(empresa_id, idempotencia)
    es la red de seguridad ante carreras)."""
    try:
        m = Decimal(str(monto))
    except Exception:
        raise ValueError("monto inválido")
    if m <= 0:
        raise ValueError("el monto debe ser positivo")
    if metodo not in config.METODOS_PAGO:
        raise ValueError(f"método inválido: {metodo!r}")
    idem = (idempotencia or "").strip()
    v = ventas.obtener(empresa_id, venta_id)
    if not v:
        raise ValueError("venta no encontrada")
    if v["estado"] == "anulada":
        raise ValueError("no se puede cobrar una venta anulada")
    bal = balance(empresa_id, venta_id)
    if m > bal:
        raise ValueError(f"el monto supera el balance pendiente (RD$ {bal})")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.locked(conn):
            if idem:
                fila = db.fetchone(
                    cur, "SELECT id FROM factura_pagos"
                         " WHERE empresa_id=%s AND idempotencia=%s",
                    (empresa_id, idem))
                if fila:
                    return {"venta_id": venta_id, "monto": str(m),
                            "balance_restante": str(
                                balance(empresa_id, venta_id)),
                            "duplicado_evitado": True}
            db.execute(
                cur, "INSERT INTO factura_pagos (empresa_id, venta_id, monto,"
                     " metodo, creada, creado_por, idempotencia)"
                     " VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (empresa_id, venta_id, str(m), metodo, _ahora(), creado_por,
                 idem))
            if m >= bal:
                db.execute(cur, "UPDATE factura_ventas SET estado='pagada'"
                                " WHERE id=%s AND empresa_id=%s",
                           (venta_id, empresa_id))
        from factura_core import caja
        if metodo == "efectivo" and caja.turno_abierto(empresa_id):
            caja.registrar_movimiento(empresa_id, "pago", str(m), "abono venta " + str(venta_id), creado_por)
        return {"venta_id": venta_id, "monto": str(m),
                "balance_restante": str(balance(empresa_id, venta_id))}
    finally:
        conn.close()


def cxc_pendientes(empresa_id: int) -> list[dict]:
    """Ventas a crédito pendientes con su balance."""
    out = []
    for v in ventas.listar(empresa_id, estado="pendiente", limite=500):
        if v["tipo"] not in ("factura", "pos"):
            continue
        v["balance"] = str(balance(empresa_id, v["id"]))
        out.append(v)
    return out


def total_cxc(empresa_id: int) -> Decimal:
    return sum((Decimal(v["balance"]) for v in cxc_pendientes(empresa_id)),
               Decimal("0"))


def cxc_vencidas(empresa_id: int) -> list[dict]:
    """CxC vencidas: fecha límite < hoy y balance > 0. Ordenadas por
    vencimiento (la más antigua primero). Incluye días vencidos y nombre
    del cliente."""
    from datetime import date
    from factura_core import clientes as cli_mod
    hoy = date.today().isoformat()
    nombres: dict[int | None, str] = {}
    out = []
    for v in cxc_pendientes(empresa_id):
        ven = (v.get("vencimiento") or "").strip()
        if not ven or ven >= hoy:
            continue
        if Decimal(v["balance"]) <= 0:
            continue
        cid = v.get("cliente_id")
        if cid not in nombres:
            c = cli_mod.obtener(empresa_id, cid) if cid else None
            nombres[cid] = c["nombre"] if c else "Consumidor final"
        v["cliente_nombre"] = nombres[cid]
        try:
            v["dias_vencidos"] = (date.today() -
                                  date.fromisoformat(ven)).days
        except Exception:
            v["dias_vencidos"] = 0
        out.append(v)
    return sorted(out, key=lambda v: v.get("vencimiento") or "")


def total_vencido(empresa_id: int) -> Decimal:
    return sum((Decimal(v["balance"]) for v in cxc_vencidas(empresa_id)),
               Decimal("0"))


def resumen_vencidas_por_cliente(empresa_id: int) -> list[dict]:
    """Agrupado por cliente: cantidad de facturas vencidas y total."""
    grupos: dict[int | None, dict] = {}
    for v in cxc_vencidas(empresa_id):
        g = grupos.setdefault(
            v.get("cliente_id"),
            {"cliente_id": v.get("cliente_id"),
             "cliente_nombre": v.get("cliente_nombre", ""),
             "cantidad": 0, "total": Decimal("0")})
        g["cantidad"] += 1
        g["total"] += Decimal(v["balance"])
    filas = sorted(grupos.values(), key=lambda g: g["total"], reverse=True)
    for g in filas:
        g["total"] = str(g["total"])
    return filas
