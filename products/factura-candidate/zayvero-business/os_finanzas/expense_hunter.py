"""LOTE 5 — Expense Hunter: duplicados, anomalías y desperdicios.

- Duplicados: mismo proveedor + mismo monto + fechas a ≤3 días.
- Anomalías: REUTILIZA el Inspector existente
  (anomalies.products._outlier_detector, IQR k=3) mapeando cada gasto a una
  "transacción": Product=categoría, Transaction=gasto_id, Customer=proveedor.
  No se reconstruye el detector.
- Desperdicios: categoría cuyo total del último mes supera 1.5x su promedio
  mensual histórico (exige ≥3 meses de historia; si no, lo dice).
"""
from __future__ import annotations

import sys
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from .gastos import GastoDetallado  # noqa: E402


@dataclass
class HallazgoGasto:
    tipo: str  # duplicado | anomalia | desperdicio
    severidad: str
    gasto_ids: list
    detalle: str

    def to_dict(self):
        return {"tipo": self.tipo, "severidad": self.severidad,
                "gasto_ids": self.gasto_ids, "detalle": self.detalle}


def _parse(f: str):
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


def cazar_duplicados(gastos: list[GastoDetallado]) -> list[HallazgoGasto]:
    out = []
    gs = [g for g in gastos if g.gasto.fecha]
    for i, a in enumerate(gs):
        for b in gs[i + 1:]:
            if (a.gasto.proveedor_id and
                    a.gasto.proveedor_id == b.gasto.proveedor_id and
                    _dec(a.gasto.monto) == _dec(b.gasto.monto) and
                    abs((_parse(a.gasto.fecha) - _parse(b.gasto.fecha)).days) <= 3):
                out.append(HallazgoGasto(
                    tipo="duplicado", severidad="alta",
                    gasto_ids=[a.gasto.gasto_id, b.gasto.gasto_id],
                    detalle=f"posible duplicado: {a.gasto.proveedor_id} "
                            f"cobró {_dec(a.gasto.monto)} dos veces en 3 días"))
    return out


def cazar_anomalias(gastos: list[GastoDetallado], company_id: str,
                    dataset_label: str = "gastos") -> list[HallazgoGasto]:
    """Anomalías con el detector IQR del Inspector (no reconstruido)."""
    import pandas as pd
    from anomalies.products import _outlier_detector

    rows = [{"Product": g.gasto.categoria, "Transaction": g.gasto.gasto_id,
             "Date": g.gasto.fecha, "Customer": g.gasto.proveedor_id or "",
             "transaction_status": "completed",
             "Amount": float(_dec(g.gasto.monto))}
            for g in gastos if g.gasto.fecha and _dec(g.gasto.monto) > 0]
    if not rows:
        return []
    df = pd.DataFrame(rows)
    crudos = _outlier_detector(
        df, "Amount", "EXPENSE_ANOMALY", company_id, dataset_label,
        "período", descriptions={}, upper_only=True, min_history=6)
    out = []
    for c in crudos:
        ev = c.get("evidence", {}) or {}
        out.append(HallazgoGasto(
            tipo="anomalia", severidad=str(c.get("severity", "LOW")).lower(),
            gasto_ids=[ev.get("transaction", "")],
            detalle=c.get("explanation", "monto atípico en su categoría")))
    return out


def cazar_desperdicios(gastos: list[GastoDetallado],
                       hoy: str | None = None) -> list[HallazgoGasto]:
    """Categorías que el último mes gastaron >1.5x su promedio mensual."""
    ahora = _parse(hoy) if hoy else datetime.now(timezone.utc)
    # Agrupar por (categoría, año-mes)
    mensual: dict[tuple[str, str], Decimal] = {}
    for g in gastos:
        if not g.gasto.fecha:
            continue
        f = _parse(g.gasto.fecha)
        if (ahora - f).days > 365:
            continue
        k = (g.gasto.categoria, f.strftime("%Y-%m"))
        mensual[k] = mensual.get(k, Decimal("0")) + _dec(g.gasto.monto)
    por_cat: dict[str, dict[str, Decimal]] = {}
    for (cat, mes), tot in mensual.items():
        por_cat.setdefault(cat, {})[mes] = tot
    out = []
    ultimo_mes = ahora.strftime("%Y-%m")
    for cat, meses in por_cat.items():
        if len(meses) < 4:  # necesita ≥3 meses de historia + el actual
            continue
        hist = [v for m, v in meses.items() if m != ultimo_mes]
        actual = meses.get(ultimo_mes, Decimal("0"))
        prom = sum(hist) / len(hist)
        if prom > 0 and actual > prom * Decimal("1.5"):
            out.append(HallazgoGasto(
                tipo="desperdicio", severidad="media", gasto_ids=[],
                detalle=f"{cat}: RD$ {actual} este mes vs promedio "
                        f"RD$ {round(prom, 2)} (+{round((actual/prom-1)*100)}%)"))
    return out


def cazar(gastos: list[GastoDetallado], company_id: str,
          hoy: str | None = None) -> list[HallazgoGasto]:
    return (cazar_duplicados(gastos)
            + cazar_anomalias(gastos, company_id)
            + cazar_desperdicios(gastos, hoy))
