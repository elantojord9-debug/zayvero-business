"""BLOQUE CONTABILIDAD — CxC/CxP: saldos, aging por tramos, vencidos.

- CxC: REUTILIZA os_crm.cobranzas (aging, tramos 0-30/31-60/61-90/90+,
  promesas de pago). No se duplica la lógica.
- CxP: espejo simétrico sobre Tesoreria._cxp con los MISMOS tramos
  (se importan de os_crm.cobranzas: una sola definición de tramos).

Todo por moneda (nunca se mezclan). Solo lectura: los cobros/pagos se
registran en Tesorería, aquí solo se informa.

Caso trabajado (ver tests, hoy = 2026-10-13):
  CxC c1: 10_000 DOP vence 2026-09-01 -> 42 días vencida -> tramo 31-60.
  CxC c2: 5_000 DOP vence 2026-10-20 -> 0 días (no vencida).
  CxP p1: 4_000 DOP vence 2026-08-01 -> 73 días -> tramo 61-90.
  total vencido CxC = 10_000; CxP = 4_000.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from os_crm.cobranzas import TRAMOS  # noqa: E402 — una sola definición


def _parse_fecha(f: str) -> datetime:
    return datetime.strptime(f[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _tramo(dias_vencida: int) -> str:
    dias = max(dias_vencida, 0)
    return next(t for a, b, t in TRAMOS if a <= dias <= b)


def resumen_cxc(cobranzas, hoy: str | None = None) -> dict:
    """Aging CxC reutilizando os_crm.cobranzas (con promesas)."""
    items = cobranzas.aging(hoy)
    tramos: dict[str, dict[str, Decimal]] = {}
    por_moneda: dict[str, Decimal] = {}
    vencido: dict[str, Decimal] = {}
    detalle = []
    for it in items:
        d = it.to_dict()
        detalle.append(d)
        t = tramos.setdefault(it.tramo, {})
        t[it.moneda] = t.get(it.moneda, Decimal("0")) + _dec(it.pendiente)
        por_moneda[it.moneda] = (por_moneda.get(it.moneda, Decimal("0"))
                                 + _dec(it.pendiente))
        if it.dias_vencida > 0:
            vencido[it.moneda] = (vencido.get(it.moneda, Decimal("0"))
                                  + _dec(it.pendiente))
    return {
        "ok": True, "cartera": "por_cobrar",
        "cuentas_activas": len(detalle),
        "total_pendiente_por_moneda": {m: str(v)
                                       for m, v in sorted(por_moneda.items())},
        "total_vencido_por_moneda": {m: str(v)
                                     for m, v in sorted(vencido.items())},
        "por_tramos": {t: {m: str(v) for m, v in sorted(d.items())}
                       for t, d in sorted(tramos.items())},
        "tramos_definicion": [t for _, _, t in TRAMOS],
        "detalle": detalle,
        "nota": ("aging calculado con os_crm.cobranzas; las promesas de "
                 "pago viajan en cada cuenta (campo promesa)"),
    }


def resumen_cxp(tesoreria, hoy: str | None = None) -> dict:
    """Aging CxP simétrico (mismos tramos que CxC)."""
    ahora = _parse_fecha(hoy) if hoy else datetime.now(timezone.utc)
    detalle = []
    tramos: dict[str, dict[str, Decimal]] = {}
    por_moneda: dict[str, Decimal] = {}
    vencido: dict[str, Decimal] = {}
    for c in sorted(tesoreria._cxp.values(), key=lambda x: x.cuenta_id):
        pend = c.pendiente()
        if pend <= 0:
            continue
        dias = max((ahora - _parse_fecha(c.vencimiento)).days, 0)
        tramo = _tramo(dias)
        detalle.append({"cuenta_id": c.cuenta_id,
                        "proveedor_id": c.proveedor_id,
                        "pendiente": str(pend), "moneda": c.moneda,
                        "vencimiento": c.vencimiento,
                        "dias_vencida": dias, "tramo": tramo,
                        "referencia": c.referencia})
        t = tramos.setdefault(tramo, {})
        t[c.moneda] = t.get(c.moneda, Decimal("0")) + pend
        por_moneda[c.moneda] = por_moneda.get(c.moneda, Decimal("0")) + pend
        if dias > 0:
            vencido[c.moneda] = vencido.get(c.moneda, Decimal("0")) + pend
    detalle.sort(key=lambda d: d["dias_vencida"], reverse=True)
    return {
        "ok": True, "cartera": "por_pagar",
        "cuentas_activas": len(detalle),
        "total_pendiente_por_moneda": {m: str(v)
                                       for m, v in sorted(por_moneda.items())},
        "total_vencido_por_moneda": {m: str(v)
                                     for m, v in sorted(vencido.items())},
        "por_tramos": {t: {m: str(v) for m, v in sorted(d.items())}
                       for t, d in sorted(tramos.items())},
        "tramos_definicion": [t for _, _, t in TRAMOS],
        "detalle": detalle,
        "nota": ("mismos tramos que CxC (definición única en "
                 "os_crm.cobranzas); pagar a tiempo evita mora con "
                 "proveedores"),
    }
