"""LOTE 6 — CRM: ficha de cliente, historial, segmentación y seguimiento.

Segmentación por reglas verificables (sin inventar scores):
- nuevo: 1 compra registrada.
- recurrente: ≥2 compras en los últimos 90 días.
- vip: LTV histórico ≥ umbral configurable (defecto RD$ 100,000).
- inactivo: sin compras en >180 días.
- El resto: ocasional.
"""
from __future__ import annotations

import sys
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import Cliente, _dec  # noqa: E402


def _parse(f: str):
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


class ErrorCRM(Exception):
    pass


@dataclass
class FichaCliente:
    cliente: Cliente
    total_compras: int = 0
    ltv: str = "0"               # suma histórica de ingresos (por moneda base)
    moneda_ltv: str = ""
    ultima_compra: str = ""
    segmento: str = "nuevo"
    notas_seguimiento: list = field(default_factory=list)

    def to_dict(self):
        d = {"cliente": self.cliente.to_dict(),
             "total_compras": self.total_compras, "ltv": self.ltv,
             "moneda_ltv": self.moneda_ltv, "ultima_compra": self.ultima_compra,
             "segmento": self.segmento,
             "notas_seguimiento": self.notas_seguimiento}
        return d


class CRM:
    """CRM de UNA empresa."""

    UMBRAL_VIP = "100000"

    def __init__(self, company_id: str):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        self.company_id = company_id
        self._lock = threading.RLock()
        self._clientes: dict[str, Cliente] = {}
        self._notas: dict[str, list] = {}
        # historial de compras: cliente_id -> [(fecha, monto, moneda)]
        self._historial: dict[str, list] = {}
        # BLOQUE CRM (2026-10-13): detalle de líneas por compra, alineado
        # por índice con _historial: cliente_id -> [[{producto_id,
        # cantidad, precio_unitario}]]. VentasService lo alimenta; las
        # cancelaciones lo mantienen en sync (ver ventas.py).
        self._lineas_compras: dict[str, list] = {}

    def registrar_cliente(self, c: Cliente):
        with self._lock:
            c.validate()
            if c.company_id != self.company_id:
                raise ErrorCRM("cliente de otra empresa")
            if c.cliente_id in self._clientes:
                raise ErrorCRM(f"cliente duplicado: {c.cliente_id}")
            self._clientes[c.cliente_id] = c
            self._notas[c.cliente_id] = []
            self._historial[c.cliente_id] = []
            self._lineas_compras[c.cliente_id] = []

    def agregar_nota(self, cliente_id: str, nota: str, fecha: str = ""):
        with self._lock:
            self._exigir(cliente_id)
            self._notas[cliente_id].append({"fecha": fecha, "nota": nota})

    def registrar_compra(self, cliente_id: str, fecha: str, monto: str,
                         moneda: str, detalle: list | None = None):
        """Alimenta el historial (lo llama VentasService, no el usuario).

        `detalle`: lista opcional de {producto_id, cantidad,
        precio_unitario} de la compra; se guarda alineada por índice con
        el historial y la usa la ficha 360 (productos preferidos).
        """
        with self._lock:
            self._exigir(cliente_id)
            self._historial[cliente_id].append((fecha, _dec(monto), moneda))
            det = [dict(d) for d in (detalle or [])]
            # setdefault defensivo: un bundle restaurado de un snapshot
            # anterior a _lineas_compras no tiene la clave (ver
            # os_persist/snapshots.py).
            self._lineas_compras.setdefault(cliente_id, []).append(det)

    def _exigir(self, cliente_id: str):
        if cliente_id not in self._clientes:
            raise ErrorCRM(f"cliente inexistente: {cliente_id}")

    def ficha(self, cliente_id: str, hoy: str | None = None) -> FichaCliente:
        with self._lock:
            self._exigir(cliente_id)
            ahora = _parse(hoy) if hoy else datetime.now(timezone.utc)
            hist = sorted(self._historial[cliente_id])
            ltv: dict[str, Decimal] = {}
            for f, m, mon in hist:
                ltv[mon] = ltv.get(mon, Decimal("0")) + m
            mon = max(ltv, key=lambda k: ltv[k]) if ltv else ""
            ultima = hist[-1][0] if hist else ""
            n = len(hist)
            if n == 0:
                seg = "prospecto"
            elif ultima and (ahora - _parse(ultima)).days > 180:
                seg = "inactivo"
            elif n >= 2 and all((ahora - _parse(f)).days <= 90 for f, _, _ in hist[-2:]):
                seg = "recurrente"
            elif mon and ltv[mon] >= _dec(self.UMBRAL_VIP):
                seg = "vip"
            elif n == 1:
                seg = "nuevo"
            else:
                seg = "ocasional"
            return FichaCliente(
                cliente=self._clientes[cliente_id], total_compras=n,
                ltv=str(ltv.get(mon, Decimal("0"))), moneda_ltv=mon,
                ultima_compra=ultima, segmento=seg,
                notas_seguimiento=list(self._notas[cliente_id]))

    def clientes_por_segmento(self, segmento: str,
                              hoy: str | None = None) -> list[str]:
        with self._lock:
            return [cid for cid in self._clientes
                    if self.ficha(cid, hoy).segmento == segmento]
