"""BLOQUE CRM/VENTAS (2026-10-13) — Descuentos y promociones.

- Registro de cada descuento con motivo y autorización (quién lo
  autorizó): ambos obligatorios, no se registra un descuento anónimo
  ni sin motivo.
- Tipos: "monto" (valor fijo en la moneda de la venta) o "porcentaje"
  (0 < p <= 100).
- Impacto en margen visible: margen bruto de la venta con y sin el
  descuento, en puntos porcentuales, más alerta si el margen final
  queda bajo el umbral (15%, coherente con Margin Guard).

El descuento NO reescribe la venta: queda registrado aparte y el
impacto se calcula sobre el total y el COGS guardados de la venta.
Las escrituras generan evento de auditoría tipo "descuento"
(ver os_crm/http.py).
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

UMBRAL_MARGEN_BAJO = Decimal("15")


class ErrorDescuento(Exception):
    pass


@dataclass
class Descuento:
    descuento_id: str
    venta_id: str
    cliente_id: str
    tipo: str            # monto | porcentaje
    valor: str           # monto en moneda de la venta, o %
    motivo: str
    autorizado_por: str
    fecha: str
    moneda: str

    def to_dict(self):
        return {"descuento_id": self.descuento_id, "venta_id": self.venta_id,
                "cliente_id": self.cliente_id, "tipo": self.tipo,
                "valor": self.valor, "motivo": self.motivo,
                "autorizado_por": self.autorizado_por, "fecha": self.fecha,
                "moneda": self.moneda}


class RegistroDescuentos:
    """Registro de descuentos de UNA empresa."""

    def __init__(self, company_id: str, ventas):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        if getattr(ventas, "company_id", None) != company_id:
            raise ValueError("el servicio de ventas debe ser de la empresa")
        self.company_id = company_id
        self._ventas = ventas
        self._lock = threading.RLock()
        self._descuentos: dict[str, Descuento] = {}

    def _venta(self, venta_id: str) -> dict:
        reg = self._ventas.estado_venta(venta_id)
        if reg is None:
            raise ErrorDescuento(f"venta inexistente: {venta_id}")
        if reg.get("estado") != "confirmada":
            raise ErrorDescuento(
                f"venta en estado {reg.get('estado')}: no admite descuentos")
        return reg

    def registrar(self, descuento_id: str, venta_id: str, tipo: str,
                  valor: str, motivo: str, autorizado_por: str,
                  fecha: str = "") -> dict:
        with self._lock:
            if descuento_id in self._descuentos:
                raise ErrorDescuento(
                    f"descuento duplicado: {descuento_id}")
            if tipo not in ("monto", "porcentaje"):
                raise ErrorDescuento(
                    "tipo inválido: use 'monto' o 'porcentaje'")
            if not (motivo or "").strip():
                raise ErrorDescuento("el descuento requiere motivo")
            if not (autorizado_por or "").strip():
                raise ErrorDescuento(
                    "el descuento requiere quién lo autorizó")
            v = _dec(valor)
            if v <= 0:
                raise ErrorDescuento("el valor del descuento debe ser > 0")
            reg = self._venta(venta_id)
            total = _dec(reg["total"])
            if tipo == "porcentaje" and v > 100:
                raise ErrorDescuento("el porcentaje no puede superar 100")
            if tipo == "monto" and v > total:
                raise ErrorDescuento(
                    f"el descuento ({v}) supera el total de la venta "
                    f"({total})")
            d = Descuento(
                descuento_id=descuento_id, venta_id=venta_id,
                cliente_id=reg["solicitud"].cliente_id, tipo=tipo,
                valor=str(v), motivo=motivo.strip(),
                autorizado_por=autorizado_por.strip(),
                fecha=fecha or datetime.now(timezone.utc).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"),
                moneda=reg["solicitud"].moneda)
            self._descuentos[descuento_id] = d
            return d.to_dict()

    def _monto_descuento(self, d: Descuento, total: Decimal) -> Decimal:
        v = _dec(d.valor)
        return total * v / Decimal("100") if d.tipo == "porcentaje" else v

    @staticmethod
    def _margen_pct(total: Decimal, cogs: Decimal) -> Decimal:
        if total <= 0:
            return Decimal("0")
        return (total - cogs) / total * Decimal("100")

    def impacto(self, descuento_id: str) -> dict:
        """Margen bruto de la venta con y sin el descuento."""
        with self._lock:
            d = self._descuentos.get(descuento_id)
            if d is None:
                raise ErrorDescuento(
                    f"descuento inexistente: {descuento_id}")
            reg = self._venta(d.venta_id)
            total = _dec(reg["total"])
            cogs = _dec(reg["cogs"])
            desc = self._monto_descuento(d, total)
            total_final = total - desc
            mo = self._margen_pct(total, cogs)
            mf = self._margen_pct(total_final, cogs)
            return {
                "descuento_id": descuento_id, "venta_id": d.venta_id,
                "moneda": d.moneda,
                "total_venta": str(total), "cogs_venta": str(cogs),
                "descuento_monto": str(desc),
                "total_con_descuento": str(total_final),
                "margen_bruto_original_pct": str(round(mo, 2)),
                "margen_bruto_con_descuento_pct": str(round(mf, 2)),
                "delta_puntos_porcentuales": str(round(mo - mf, 2)),
                "alerta_margen_bajo": bool(mf < UMBRAL_MARGEN_BAJO),
                "umbral_margen_bajo_pct": str(UMBRAL_MARGEN_BAJO)}

    def listar(self, venta_id: str | None = None) -> list[dict]:
        with self._lock:
            ds = list(self._descuentos.values())
            if venta_id:
                ds = [d for d in ds if d.venta_id == venta_id]
            return [d.to_dict() for d in sorted(
                ds, key=lambda d: d.descuento_id)]
