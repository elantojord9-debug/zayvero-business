"""LOTE 6 — Cobranzas: aging por tramos, promesas de pago y recordatorios.

- aging(): CxC pendientes agrupadas en tramos 0-30 / 31-60 / 61-90 / 90+ días
  de vencida, con días de retraso por cuenta.
- promesa_pago(): registra compromiso del cliente (fecha + monto). No mueve
  dinero; es seguimiento.
- recordatorios(): BORRADORES de mensajes para aprobación humana.
  NUNCA se envían automáticamente (regla del lote).
"""
from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402


def _parse_fecha(f: str):
    return datetime.strptime(f[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


TRAMOS = [(0, 30, "0-30"), (31, 60, "31-60"), (61, 90, "61-90"), (91, 10**9, "90+")]


@dataclass
class ItemAging:
    cuenta_id: str
    cliente_id: str
    pendiente: str
    moneda: str
    vencimiento: str
    dias_vencida: int
    tramo: str
    promesa: dict | None = None

    def to_dict(self):
        return {"cuenta_id": self.cuenta_id, "cliente_id": self.cliente_id,
                "pendiente": self.pendiente, "moneda": self.moneda,
                "vencimiento": self.vencimiento,
                "dias_vencida": self.dias_vencida, "tramo": self.tramo,
                "promesa": self.promesa}


@dataclass
class BorradorRecordatorio:
    """Borrador para aprobación humana. No se envía solo."""
    cliente_id: str
    cuenta_id: str
    texto: str
    pendiente: str
    dias_vencida: int

    def to_dict(self):
        return {"cliente_id": self.cliente_id, "cuenta_id": self.cuenta_id,
                "texto": self.texto, "pendiente": self.pendiente,
                "dias_vencida": self.dias_vencida,
                "requiere_aprobacion": True}


class Cobranzas:
    def __init__(self, tesoreria):
        self.tesoreria = tesoreria
        self._promesas: dict[str, dict] = {}  # cuenta_id -> {fecha, monto,
        # registrada_por, fecha_registro, notas}

    def promesa_pago(self, cuenta_id: str, fecha: str, monto: str,
                     registrada_por: str = "", notas: str = "",
                     hoy: str | None = None):
        if cuenta_id not in self.tesoreria._cxc:
            raise ValueError(f"CxC inexistente: {cuenta_id}")
        if _dec(monto) <= 0:
            raise ValueError("monto de promesa debe ser > 0")
        ahora = (_parse_fecha(hoy) if hoy
                 else datetime.now(timezone.utc)).strftime("%Y-%m-%d")
        self._promesas[cuenta_id] = {
            "fecha": fecha, "monto": str(_dec(monto)),
            "registrada_por": str(registrada_por or "").strip(),
            "fecha_registro": ahora, "notas": notas or ""}

    def _promesa_publica(self, cuenta_id: str) -> dict | None:
        """Forma estable que ve el aging: solo fecha y monto."""
        p = self._promesas.get(cuenta_id)
        if p is None:
            return None
        return {"fecha": p["fecha"], "monto": p["monto"]}

    def seguimiento_promesas(self, hoy: str | None = None) -> list[dict]:
        """Estado de cada promesa: vigente | vencida | cumplida.

        - cumplida: la CxC quedó en 0 pendiente.
        - vencida: pasó la fecha prometida y aún hay pendiente.
        - vigente: fecha futura (o hoy) con pendiente.
        """
        ahora = _parse_fecha(hoy) if hoy else datetime.now(timezone.utc)
        out = []
        for cuenta_id, p in sorted(self._promesas.items()):
            cxc = self.tesoreria._cxc.get(cuenta_id)
            pendiente = str(cxc.pendiente()) if cxc else None
            if cxc is not None and cxc.pendiente() <= 0:
                estado = "cumplida"
            elif _parse_fecha(p["fecha"]) < ahora:
                estado = "vencida"
            else:
                estado = "vigente"
            out.append({"cuenta_id": cuenta_id,
                        "cliente_id": cxc.cliente_id if cxc else "",
                        "moneda": cxc.moneda if cxc else "",
                        "pendiente": pendiente, "estado": estado,
                        **p})
        return out

    def aging(self, hoy: str | None = None) -> list[ItemAging]:
        ahora = _parse_fecha(hoy) if hoy else datetime.now(timezone.utc)
        out = []
        for c in self.tesoreria._cxc.values():
            pend = c.pendiente()
            if pend <= 0:
                continue
            dias = (ahora - _parse_fecha(c.vencimiento)).days
            dias = max(dias, 0)
            tramo = next(t for a, b, t in TRAMOS if a <= dias <= b)
            out.append(ItemAging(
                cuenta_id=c.cuenta_id, cliente_id=c.cliente_id,
                pendiente=str(pend), moneda=c.moneda,
                vencimiento=c.vencimiento, dias_vencida=dias, tramo=tramo,
                promesa=self._promesa_publica(c.cuenta_id)))
        return sorted(out, key=lambda x: x.dias_vencida, reverse=True)

    def resumen_tramos(self, hoy: str | None = None) -> dict:
        tot: dict[str, dict[str, Decimal]] = {}
        for it in self.aging(hoy):
            d = tot.setdefault(it.tramo, {})
            d[it.moneda] = d.get(it.moneda, Decimal("0")) + _dec(it.pendiente)
        return {t: {m: str(v) for m, v in sorted(d.items())}
                for t, d in sorted(tot.items())}

    def recordatorios(self, hoy: str | None = None,
                      min_dias: int = 1) -> list[BorradorRecordatorio]:
        """Genera borradores. Requieren aprobación humana explícita."""
        out = []
        for it in self.aging(hoy):
            if it.dias_vencida < min_dias:
                continue
            tono = ("primer recordatorio amable" if it.dias_vencida <= 30
                    else "seguimiento firme" if it.dias_vencida <= 60
                    else "último aviso antes de gestión externa")
            texto = (
                f"Hola, le escribe el equipo de cobranzas. Su factura "
                f"{it.cuenta_id} por {it.pendiente} {it.moneda} venció hace "
                f"{it.dias_vencida} días ({it.vencimiento}). "
                f"¿Podría confirmarnos la fecha de pago? "
                f"[{tono} — BORRADOR, requiere aprobación humana]")
            out.append(BorradorRecordatorio(
                cliente_id=it.cliente_id, cuenta_id=it.cuenta_id, texto=texto,
                pendiente=it.pendiente, dias_vencida=it.dias_vencida))
        return out


def alertas_inactividad(cobranzas: Cobranzas, crm, hoy: str | None = None,
                        umbral_dias: int = 180) -> list[dict]:
    """Alertas de clientes inactivos o en riesgo de abandono.

    - inactivo: sin compras en más de `umbral_dias` (defecto 180).
    - riesgo_churn: recencia mayor a 2x su intervalo promedio (reutiliza
      las señales heurísticas de inteligencia, sin probabilidades
      inventadas).
    Solo considera clientes con al menos una compra registrada.
    """
    from .inteligencia import senales_churn  # import local: sin ciclos
    ahora = datetime.now(timezone.utc) if not hoy else _parse_fecha(hoy)
    sen = {s.cliente_id: s for s in senales_churn(crm, hoy)}
    out = []
    for cid in crm._clientes:
        hs = sorted(h for h in crm._historial.get(cid, []) if h and h[0])
        if not hs:
            continue
        recencia = (ahora - datetime.strptime(
            hs[-1][0][:19], "%Y-%m-%dT%H:%M:%S").replace(
                tzinfo=timezone.utc)).days
        s = sen.get(cid)
        if recencia > umbral_dias or (s is not None and
                                     s.nivel == "inactivo"):
            out.append({"cliente_id": cid,
                        "tipo": "inactivo",
                        "dias_sin_compra": recencia,
                        "ultima_compra": hs[-1][0][:10],
                        "mensaje": (f"sin compras en {recencia} días "
                                    f"(última: {hs[-1][0][:10]})")})
        elif s is not None and s.nivel == "en_riesgo":
            out.append({"cliente_id": cid,
                        "tipo": "riesgo_churn",
                        "dias_sin_compra": recencia,
                        "ultima_compra": hs[-1][0][:10],
                        "mensaje": s.detalle})
    return sorted(out, key=lambda a: a["dias_sin_compra"], reverse=True)
