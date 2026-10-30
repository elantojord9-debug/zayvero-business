"""LOTE 6 — Ventas: contado y crédito conectadas al Inventario (Lote 3),
al Profit (Lote 5) y a la Tesorería/CxC (Lote 5).

REGLA CRÍTICA: una venta afecta inventario, ingresos y CxC EXACTAMENTE UNA VEZ.
- vender(): valida TODO antes de mutar; luego: SALIDA inventario (IDs
  deterministas vta-<venta_id>-<producto>) -> LibroFinanzas (ingreso + COGS)
  -> CxC en Tesorería (solo si es a crédito) -> historial CRM.
  venta_id duplicado se rechaza (idempotencia).
- Un cobro posterior SOLO mueve caja y reduce la CxC (Tesorería ya lo
  garantiza: no genera ingreso de nuevo).
- cancelar_venta(): reversa la venta completa — DEV_IN al inventario al
  costo unitario guardado, marca la venta cancelada en el libro, anula la
  CxC pendiente (o genera nota de crédito si ya hubo cobros).
- devolucion_parcial(): DEV_IN + nota de crédito proporcional.

LIMITACIÓN: atomicidad duradera entre los 4 sistemas exige transacción
PostgreSQL (issue #1); hoy es todo-o-nada solo dentro del proceso.
"""
from __future__ import annotations

import sys
import os
import threading
from dataclasses import dataclass, field
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec, MovimientoInventario  # noqa: E402
from oschema.contract import require_iso_currency  # noqa: E402
from os_inventory.engine import InventoryEngine  # noqa: E402
from os_finanzas.profit import LibroFinanzas, VentaFinanciera, LineaVenta  # noqa: E402
from os_finanzas.tesoreria import Tesoreria, CuentaCobrar  # noqa: E402
from .clientes import CRM  # noqa: E402


class ErrorVentas(Exception):
    pass


@dataclass
class SolicitudVenta:
    venta_id: str
    company_id: str
    cliente_id: str
    lineas: list = field(default_factory=list)  # [{producto_id, cantidad, precio_unitario, almacen_id}]
    moneda: str = "DOP"
    fecha: str = ""
    a_credito: bool = False
    dias_credito: int = 30
    sucursal_id: str = ""


class VentasService:
    def __init__(self, company_id: str, inventario: InventoryEngine,
                 libro: LibroFinanzas, tesoreria: Tesoreria, crm: CRM):
        for s in (inventario, libro, tesoreria, crm):
            if s.company_id != company_id:
                raise ValueError("todos los servicios deben ser de la misma empresa")
        self.company_id = company_id
        self.inventario = inventario
        self.libro = libro
        self.tesoreria = tesoreria
        self.crm = crm
        self._lock = threading.RLock()
        self._ventas: dict[str, dict] = {}  # venta_id -> {solicitud, estado, cogs_unitario}

    def vender(self, sol: SolicitudVenta) -> dict:
        with self._lock:
            self._validar(sol)
            if sol.venta_id in self._ventas:
                raise ErrorVentas(f"venta duplicada: {sol.venta_id}")

            # 1. Inventario: SALIDAs con ID determinista (idempotentes)
            for lin in sol.lineas:
                m = MovimientoInventario(
                    movimiento_id=f"vta-{sol.venta_id}-{lin['producto_id']}",
                    company_id=self.company_id, producto_id=lin["producto_id"],
                    almacen_id=lin.get("almacen_id", "principal"),
                    tipo="salida", cantidad=f"-{abs(_dec(lin['cantidad']))}",
                    fecha=sol.fecha or None, referencia_id=sol.venta_id,
                    motivo=f"venta {sol.venta_id}")
                # validar stock antes: el engine bloquea negativo por defecto
                self.inventario.registrar_idempotente(m)

            # 2. Profit: ingreso + COGS (una sola vez)
            vf = VentaFinanciera(
                venta_id=sol.venta_id, company_id=self.company_id,
                moneda=sol.moneda, fecha=sol.fecha,
                lineas=[LineaVenta(l["producto_id"], str(_dec(l["cantidad"])),
                                     str(_dec(l["precio_unitario"])),
                                     almacen_id=l.get("almacen_id", "principal"),
                                     sucursal_id=sol.sucursal_id,
                                     descuento_pct=str(l.get("descuento_pct",
                                                             "0")))
                        for l in sol.lineas])
            self.libro.registrar_venta(vf)

            # 3. CxC solo si es a crédito (una sola vez)
            if sol.a_credito:
                from datetime import datetime, timezone, timedelta
                base = datetime.strptime(sol.fecha[:19], "%Y-%m-%dT%H:%M:%S"
                                         ).replace(tzinfo=timezone.utc) \
                    if sol.fecha else datetime.now(timezone.utc)
                venc = (base + timedelta(days=sol.dias_credito)).strftime("%Y-%m-%d")
                self.tesoreria.crear_cxc(CuentaCobrar(
                    cuenta_id=f"cxc-{sol.venta_id}", company_id=self.company_id,
                    cliente_id=sol.cliente_id, monto=str(vf.total()),
                    moneda=sol.moneda, vencimiento=venc,
                    referencia=sol.venta_id))

            # 4. Historial CRM (con detalle de líneas para la ficha 360)
            if sol.cliente_id:
                self.crm.registrar_compra(
                    sol.cliente_id, sol.fecha or "", str(vf.total()),
                    sol.moneda,
                    detalle=[{"producto_id": l["producto_id"],
                              "cantidad": str(_dec(l["cantidad"])),
                              "precio_unitario": str(_dec(l["precio_unitario"])),
                              "almacen_id": l.get("almacen_id", "principal")}
                             for l in sol.lineas])

            self._ventas[sol.venta_id] = {
                "solicitud": sol, "estado": "confirmada",
                "total": str(vf.total()), "cogs": vf.cogs_total}
            return self._ventas[sol.venta_id]

    def _validar(self, sol: SolicitudVenta):
        if not sol.venta_id or not sol.cliente_id:
            raise ErrorVentas("venta_id y cliente_id son obligatorios")
        if sol.company_id != self.company_id:
            raise ErrorVentas("venta de otra empresa")
        require_iso_currency(sol.moneda)
        if not sol.lineas:
            raise ErrorVentas("la venta necesita líneas")
        self.crm._exigir(sol.cliente_id)
        for lin in sol.lineas:
            cant = _dec(lin["cantidad"])
            if cant <= 0 or _dec(lin["precio_unitario"]) < 0:
                raise ErrorVentas("cantidad > 0 y precio >= 0")
            # stock suficiente (el engine lo revalida al registrar)
            disp = self.inventario.stock(lin["producto_id"],
                                        lin.get("almacen_id", "principal"))
            if disp < cant:
                raise ErrorVentas(
                    f"stock insuficiente de {lin['producto_id']}: "
                    f"hay {disp}, piden {cant}")

    def cancelar_venta(self, venta_id: str, motivo: str = "") -> dict:
        """Reversa completa: inventario, libro, CxC y CRM."""
        with self._lock:
            reg = self._ventas.get(venta_id)
            if reg is None:
                raise ErrorVentas(f"venta inexistente: {venta_id}")
            if reg["estado"] != "confirmada":
                raise ErrorVentas(f"venta en estado {reg['estado']}, no cancelable")
            sol = reg["solicitud"]

            # BLOQUE CRM (2026-10-13): una venta con devoluciones parciales
            # registradas NO se cancela completa (evita doble reversión de
            # inventario y P&L). El wiring la expone vía ventas._devoluciones.
            dev = getattr(self, "_devoluciones", None)
            if dev is not None and dev.devuelto_total(venta_id) > 0:
                raise ErrorVentas(
                    f"venta {venta_id} tiene devoluciones parciales "
                    "registradas: no se puede cancelar completa")

            # 1. Devolver stock al costo unitario original (promedio al vender)
            cogs_u = _dec(reg["cogs"]) / sum(_dec(l["cantidad"]) for l in sol.lineas) \
                if _dec(reg["cogs"]) > 0 else Decimal("0")
            for lin in sol.lineas:
                self.inventario.devolucion_cliente(
                    lin["producto_id"], lin.get("almacen_id", "principal"),
                    str(_dec(lin["cantidad"])), referencia_id=venta_id,
                    motivo=motivo or f"cancelación {venta_id}")
                self.libro.costo.aplicar_entrada(
                    lin["producto_id"], lin.get("almacen_id", "principal"),
                    str(_dec(lin["cantidad"])), str(cogs_u), sol.moneda)

            # 2. Nota de crédito en el libro: revierte ingreso y COGS
            # (la venta original se conserva para trazabilidad)
            total = _dec(reg["total"])
            self.libro.registrar_nota_credito(
                venta_id, str(total), sol.moneda, sol.fecha or "",
                cogs_revertido=reg["cogs"])
            # 3. CxC: si no hubo cobros se anula; si hubo, queda nota de crédito
            try:
                cxc = self.tesoreria._cxc[f"cxc-{venta_id}"]
                if _dec(cxc.cobrado) > 0:
                    reg["nota_credito"] = str(_dec(cxc.cobrado))
                else:
                    del self.tesoreria._cxc[f"cxc-{venta_id}"]
            except KeyError:
                pass

            # 4. CRM: quitar la compra exacta del historial (y su detalle
            # de líneas, que va alineado por índice con el historial).
            hist = self.crm._historial.get(sol.cliente_id, [])
            lins = self.crm._lineas_compras.get(sol.cliente_id, [])
            objetivo = (sol.fecha or "", total, sol.moneda)
            if len(hist) == len(lins):
                pares = [(h, l) for h, l in zip(hist, lins) if h != objetivo]
                self.crm._historial[sol.cliente_id] = [h for h, _ in pares]
                self.crm._lineas_compras[sol.cliente_id] = [l for _, l in pares]
            else:  # defensa: nunca romper el historial por desalineación
                self.crm._historial[sol.cliente_id] = [
                    h for h in hist if h != objetivo]
            reg["estado"] = "cancelada"
            return reg

    def estado_venta(self, venta_id: str) -> dict | None:
        with self._lock:
            return self._ventas.get(venta_id)
