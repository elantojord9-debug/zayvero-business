"""BLOQUE CRM/VENTAS (2026-10-13) — Devoluciones de clientes.

- Registro con motivo obligatorio (sin motivo no hay devolución).
- Impacto en inventario: reingreso a stock (DEVOLUCION_IN) al costo
  unitario distribuido de la venta.
- Impacto en margen: nota de crédito proporcional en el libro (revierte
  ingreso y COGS de lo devuelto; la venta original se conserva).
- Evento de auditoría tipo "devolucion" (ver os_crm/http.py).
- Una venta con devoluciones parciales NO se puede cancelar completa
  (ver ventas.py): evita la doble reversión.

Distribución del COGS por producto: el libro guarda el COGS total de
la venta; aquí se reparte por línea en proporción a su ingreso
(cogs_producto = cogs_total × ingreso_linea / total_venta). Documentado
y verificable en tests.

LIMITACIÓN DOCUMENTADA: si la venta fue a crédito, la CxC NO se ajusta
automáticamente (Tesorería no tiene nota de crédito sobre CxC); la
gestión de la cuenta queda en cobranzas con la nota de crédito como
respaldo.
"""
from __future__ import annotations

import sys
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402


class ErrorDevolucion(Exception):
    pass


@dataclass
class Devolucion:
    devolucion_id: str
    venta_id: str
    cliente_id: str
    lineas: list       # [{producto_id, almacen_id, cantidad,
                       #   precio_unitario, monto, cogs_revertido}]
    motivo: str
    fecha: str
    moneda: str

    def monto_total(self) -> Decimal:
        return sum((_dec(l["monto"]) for l in self.lineas), Decimal("0"))

    def cogs_total(self) -> Decimal:
        return sum((_dec(l["cogs_revertido"]) for l in self.lineas),
                   Decimal("0"))

    def to_dict(self):
        return {"devolucion_id": self.devolucion_id,
                "venta_id": self.venta_id, "cliente_id": self.cliente_id,
                "lineas": self.lineas, "motivo": self.motivo,
                "fecha": self.fecha, "moneda": self.moneda,
                "monto_total": str(self.monto_total()),
                "cogs_revertido_total": str(self.cogs_total())}


class RegistroDevoluciones:
    """Devoluciones de clientes de UNA empresa."""

    def __init__(self, company_id: str, inventario, libro, ventas):
        for svc in (inventario, libro, ventas):
            if getattr(svc, "company_id", None) != company_id:
                raise ValueError(
                    "todos los servicios deben ser de la misma empresa")
        self.company_id = company_id
        self._inventario = inventario
        self._libro = libro
        self._ventas = ventas
        self._lock = threading.RLock()
        self._devs: dict[str, Devolucion] = {}
        # (venta_id, producto_id) -> cantidad ya devuelta
        self._devuelto: dict[tuple[str, str], Decimal] = {}

    def _lineas_venta(self, venta_id: str):
        reg = self._ventas.estado_venta(venta_id)
        if reg is None:
            raise ErrorDevolucion(f"venta inexistente: {venta_id}")
        if reg.get("estado") != "confirmada":
            raise ErrorDevolucion(
                f"venta en estado {reg.get('estado')}: no admite "
                "devoluciones")
        return reg

    def devuelto_total(self, venta_id: str) -> Decimal:
        with self._lock:
            return sum((c for (v, _), c in self._devuelto.items()
                        if v == venta_id), Decimal("0"))

    def registrar(self, devolucion_id: str, venta_id: str, lineas: list,
                  motivo: str, fecha: str = "") -> dict:
        with self._lock:
            if devolucion_id in self._devs:
                raise ErrorDevolucion(
                    f"devolución duplicada: {devolucion_id}")
            if not (motivo or "").strip():
                raise ErrorDevolucion("la devolución requiere motivo")
            if not lineas:
                raise ErrorDevolucion("la devolución necesita líneas")
            reg = self._lineas_venta(venta_id)
            sol = reg["solicitud"]
            total_venta = _dec(reg["total"])
            cogs_venta = _dec(reg["cogs"])
            fecha = fecha or datetime.now(timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")

            vendidas = {}
            for lin in sol.lineas:
                pid = lin["producto_id"]
                vendidas[pid] = {
                    "cantidad": _dec(lin["cantidad"]),
                    "precio": _dec(lin["precio_unitario"]),
                    "almacen": lin.get("almacen_id", "principal")}
            detalle = []
            for lin in lineas:
                pid = lin.get("producto_id", "")
                cant = _dec(str(lin.get("cantidad", "0")))
                if pid not in vendidas:
                    raise ErrorDevolucion(
                        f"producto {pid} no está en la venta {venta_id}")
                if cant <= 0:
                    raise ErrorDevolucion(
                        f"cantidad a devolver debe ser > 0 ({pid})")
                ya = self._devuelto.get((venta_id, pid), Decimal("0"))
                if ya + cant > vendidas[pid]["cantidad"]:
                    raise ErrorDevolucion(
                        f"de {pid} se vendieron "
                        f"{vendidas[pid]['cantidad']} y ya se devolvieron "
                        f"{ya}: no se pueden devolver {cant}")
                ingreso_linea = (vendidas[pid]["cantidad"]
                                 * vendidas[pid]["precio"])
                cogs_prod = (cogs_venta * ingreso_linea / total_venta
                             if total_venta > 0 else Decimal("0"))
                cogs_unit = (cogs_prod / vendidas[pid]["cantidad"]
                             if vendidas[pid]["cantidad"] > 0
                             else Decimal("0"))
                monto = cant * vendidas[pid]["precio"]
                cogs_rev = cant * cogs_unit
                alm = vendidas[pid]["almacen"]
                # 1. Inventario: reingreso al costo distribuido
                self._inventario.devolucion_cliente(
                    pid, alm, str(cant), referencia_id=venta_id,
                    motivo=motivo.strip())
                self._libro.costo.aplicar_entrada(
                    pid, alm, str(cant), str(cogs_unit), sol.moneda)
                self._devuelto[(venta_id, pid)] = ya + cant
                detalle.append({
                    "producto_id": pid, "almacen_id": alm,
                    "cantidad": str(cant),
                    "precio_unitario": str(vendidas[pid]["precio"]),
                    "monto": str(monto),
                    "cogs_unitario": str(cogs_unit),
                    "cogs_revertido": str(cogs_rev)})

            dev = Devolucion(
                devolucion_id=devolucion_id, venta_id=venta_id,
                cliente_id=sol.cliente_id, lineas=detalle,
                motivo=motivo.strip(), fecha=fecha, moneda=sol.moneda)
            # 2. P&L: nota de crédito proporcional (revierte ingreso y COGS)
            self._libro.registrar_nota_credito(
                venta_id, str(dev.monto_total()), sol.moneda, fecha,
                cogs_revertido=str(dev.cogs_total()))
            self._devs[devolucion_id] = dev
            return dev.to_dict()

    def impacto_margen(self, venta_id: str) -> dict:
        """Margen de la venta antes y después de las devoluciones."""
        with self._lock:
            reg = self._lineas_venta(venta_id)
            total = _dec(reg["total"])
            cogs = _dec(reg["cogs"])
            dev_monto = Decimal("0")
            dev_cogs = Decimal("0")
            for d in self._devs.values():
                if d.venta_id == venta_id:
                    dev_monto += d.monto_total()
                    dev_cogs += d.cogs_total()
            t2, c2 = total - dev_monto, cogs - dev_cogs

            def _margen(t, c):
                return ((t - c) / t * Decimal("100") if t > 0
                        else Decimal("0"))
            return {"venta_id": venta_id, "moneda": reg["solicitud"].moneda,
                    "total_original": str(total), "cogs_original": str(cogs),
                    "margen_original_pct": str(round(_margen(total, cogs), 2)),
                    "monto_devuelto": str(dev_monto),
                    "cogs_revertido": str(dev_cogs),
                    "total_neto": str(t2), "cogs_neto": str(c2),
                    "margen_neto_pct": str(round(_margen(t2, c2), 2))}

    def listar(self, venta_id: str | None = None) -> list[dict]:
        with self._lock:
            ds = list(self._devs.values())
            if venta_id:
                ds = [d for d in ds if d.venta_id == venta_id]
            return [d.to_dict() for d in sorted(
                ds, key=lambda d: d.devolucion_id)]
