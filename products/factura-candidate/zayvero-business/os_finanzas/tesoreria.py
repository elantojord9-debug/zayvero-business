"""LOTE 5 — Tesorería y Cash Flow.

Tesorería: distingue dinero EFECTIVO (cobrado/pagado) de PENDIENTE.
- CxC: venta registrada en LibroFinanzas genera cuenta por cobrar;
  registrar_cobro la reduce. Solo lo cobrado mueve la caja.
- CxP: del Lote 4 (cxp_desde_orden_cerrada); registrar_pago la reduce.
- La venta se cuenta UNA vez (en Profit); el cobro NO genera ingreso de nuevo.

Cash Flow: proyección determinista 30/60/90 días SOLO con datos conocidos:
CxC con vencimiento, CxP con vencimiento, gastos futuros programados y
saldo inicial de caja (dato de entrada explícito, no inventado).
No pronostica ventas ni inventa cobros.
"""
from __future__ import annotations

import sys
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from oschema.contract import require_iso_currency  # noqa: E402


def _parse(f: str):
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


class ErrorTesoreria(Exception):
    pass


@dataclass
class CuentaCobrar:
    cuenta_id: str
    company_id: str
    cliente_id: str
    monto: str
    moneda: str
    vencimiento: str  # YYYY-MM-DD
    referencia: str = ""  # venta_id
    cobrado: str = "0"

    def pendiente(self) -> Decimal:
        return _dec(self.monto) - _dec(self.cobrado)


@dataclass
class CuentaPagar:
    cuenta_id: str
    company_id: str
    proveedor_id: str
    monto: str
    moneda: str
    vencimiento: str
    referencia: str = ""  # compra_id
    pagado: str = "0"

    def pendiente(self) -> Decimal:
        return _dec(self.monto) - _dec(self.pagado)


@dataclass
class GastoFuturo:
    descripcion: str
    monto: str
    moneda: str
    fecha: str  # cuando se pagará


class Tesoreria:
    """Caja y cuentas por cobrar/pagar de UNA empresa, por moneda."""

    def __init__(self, company_id: str):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        self.company_id = company_id
        self._lock = threading.RLock()
        self._caja: dict[str, Decimal] = {}
        self._cxc: dict[str, CuentaCobrar] = {}
        self._cxp: dict[str, CuentaPagar] = {}
        self._futuros: list[GastoFuturo] = []

    # ---- caja ---------------------------------------------------------
    def fijar_saldo_inicial(self, moneda: str, monto: str):
        require_iso_currency(moneda)
        with self._lock:
            self._caja[moneda] = _dec(monto)

    def saldo(self, moneda: str) -> str:
        with self._lock:
            return str(self._caja.get(moneda, Decimal("0")))

    # ---- CxC -----------------------------------------------------------
    def crear_cxc(self, c: CuentaCobrar):
        with self._lock:
            self._exigir_empresa(c.company_id)
            require_iso_currency(c.moneda)
            if c.cuenta_id in self._cxc:
                raise ErrorTesoreria(f"CxC duplicada: {c.cuenta_id}")
            self._cxc[c.cuenta_id] = c

    def registrar_cobro(self, cuenta_id: str, monto: str, fecha: str = ""):
        """El cobro SÍ mueve la caja. No genera ingreso (ya está en Profit)."""
        with self._lock:
            c = self._cxc.get(cuenta_id)
            if c is None:
                raise ErrorTesoreria(f"CxC inexistente: {cuenta_id}")
            m = _dec(monto)
            if m <= 0 or m > c.pendiente():
                raise ErrorTesoreria(
                    f"cobro inválido: {monto} (pendiente {c.pendiente()})")
            c.cobrado = str(_dec(c.cobrado) + m)
            self._caja[c.moneda] = self._caja.get(c.moneda, Decimal("0")) + m

    # ---- CxP -----------------------------------------------------------
    def crear_cxp(self, c: CuentaPagar):
        with self._lock:
            self._exigir_empresa(c.company_id)
            require_iso_currency(c.moneda)
            if c.cuenta_id in self._cxp:
                raise ErrorTesoreria(f"CxP duplicada: {c.cuenta_id}")
            self._cxp[c.cuenta_id] = c

    def registrar_pago(self, cuenta_id: str, monto: str):
        with self._lock:
            c = self._cxp.get(cuenta_id)
            if c is None:
                raise ErrorTesoreria(f"CxP inexistente: {cuenta_id}")
            m = _dec(monto)
            if m <= 0 or m > c.pendiente():
                raise ErrorTesoreria(
                    f"pago inválido: {monto} (pendiente {c.pendiente()})")
            if self._caja.get(c.moneda, Decimal("0")) < m:
                raise ErrorTesoreria("caja insuficiente para el pago")
            c.pagado = str(_dec(c.pagado) + m)
            self._caja[c.moneda] -= m

    def programar_gasto(self, g: GastoFuturo):
        require_iso_currency(g.moneda)
        with self._lock:
            self._futuros.append(g)

    def _exigir_empresa(self, company_id: str):
        if company_id != self.company_id:
            raise ErrorTesoreria("cuenta de otra empresa")

    # ---- cash flow ------------------------------------------------------
    def proyectar(self, dias: int = 30, hoy: str | None = None) -> dict:
        """Proyección determinista con datos conocidos. Por moneda."""
        ahora = _parse(hoy) if hoy else datetime.now(timezone.utc)
        limite = ahora + timedelta(days=dias)
        flujo: dict[str, dict[str, Decimal]] = {}

        def bucket(moneda):
            return flujo.setdefault(moneda, {"entradas": Decimal("0"),
                                             "salidas": Decimal("0")})

        with self._lock:
            for c in self._cxc.values():
                v = datetime.strptime(c.vencimiento, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc)
                if v <= limite and c.pendiente() > 0:
                    bucket(c.moneda)["entradas"] += c.pendiente()
            for c in self._cxp.values():
                v = datetime.strptime(c.vencimiento, "%Y-%m-%d").replace(
                    tzinfo=timezone.utc)
                if v <= limite and c.pendiente() > 0:
                    bucket(c.moneda)["salidas"] += c.pendiente()
            for g in self._futuros:
                if _parse(g.fecha) <= limite:
                    bucket(g.moneda)["salidas"] += _dec(g.monto)

        out = {}
        for mon, b in sorted(flujo.items()):
            neto = b["entradas"] - b["salidas"]
            inicial = self._caja.get(mon, Decimal("0"))
            out[mon] = {
                "saldo_inicial": str(inicial),
                "entradas_proyectadas": str(b["entradas"]),
                "salidas_proyectadas": str(b["salidas"]),
                "flujo_neto": str(neto),
                "saldo_proyectado": str(inicial + neto),
                "dias": dias,
            }
        return out

    def posiciones_por_moneda(self) -> dict:
        """BLOQUE CONTABILIDAD (2026-10-13) — posición de caja por moneda.

        Método ADITIVO: no cambia ningún comportamiento existente.
        caja = efectivo; por_cobrar/por_pagar = derechos y obligaciones
        pendientes (NO son efectivo en caja); posicion_neta = caja +
        por_cobrar − por_pagar. Por moneda, sin mezclar.
        """
        with self._lock:
            monedas = (set(self._caja)
                       | {c.moneda for c in self._cxc.values()}
                       | {c.moneda for c in self._cxp.values()})
            out = {}
            for m in sorted(monedas):
                caja = self._caja.get(m, Decimal("0"))
                cxc = sum((c.pendiente() for c in self._cxc.values()
                           if c.moneda == m), Decimal("0"))
                cxp = sum((c.pendiente() for c in self._cxp.values()
                           if c.moneda == m), Decimal("0"))
                out[m] = {
                    "caja": str(caja),
                    "por_cobrar": str(cxc),
                    "por_pagar": str(cxp),
                    "posicion_neta": str(caja + cxc - cxp),
                    "nota": ("por_cobrar/por_pagar son derechos y "
                             "obligaciones, no efectivo en caja"),
                }
            return out

    def resumen_cxc_cxp(self) -> dict:
        with self._lock:
            cxc = {}
            for c in self._cxc.values():
                if c.pendiente() > 0:
                    cxc[c.moneda] = cxc.get(c.moneda, Decimal("0")) + c.pendiente()
            cxp = {}
            for c in self._cxp.values():
                if c.pendiente() > 0:
                    cxp[c.moneda] = cxp.get(c.moneda, Decimal("0")) + c.pendiente()
        return {"por_cobrar": {k: str(v) for k, v in sorted(cxc.items())},
                "por_pagar": {k: str(v) for k, v in sorted(cxp.items())}}
