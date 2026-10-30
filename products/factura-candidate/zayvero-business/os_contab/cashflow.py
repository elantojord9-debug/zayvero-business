"""BLOQUE CONTABILIDAD — Cash Flow: proyección DETERMINISTA.

Aritmética pura, sin pronósticos:

1. Base programada (datos conocidos del OS):
   - CxC pendientes con vencimiento -> entradas
   - CxP pendientes con vencimiento -> salidas
   - gastos futuros programados en Tesorería -> salidas
2. Ritmo histórico (opcional y explícito):
   - `historial_cobros` / `historial_pagos`: listas de
     {monto, moneda, fecha}; se calcula el promedio DIARIO por moneda
     sobre la ventana indicada. Si no se proveen -> el componente queda
     en "datos_insuficientes" (NO se inventa; se dice).

La salida lista cada supuesto. No pronostica ventas, no inventa cobros,
no mezcla monedas.

Caso trabajado (ver tests, hoy = 2026-10-13, dias = 14):
  caja inicial: 20_000 DOP.
  CxC c1: 10_000 DOP vence 2026-10-20 (semana 2) -> entrada.
  CxP p1: 4_000 DOP vence 2026-10-25 (semana 2) -> salida.
  CxC c2: 5_000 DOP vence 2026-11-30 (fuera del horizonte) -> no entra.
  historial_cobros: 3_000 DOP diarios del 2026-09-14 al 2026-10-13
  (30 días × 3_000 = 90_000) -> promedio 3_000/día. Sin historial_pagos.
  semana 1 (13→19 oct): ent = 0 + 7×3_000 = 21_000; sal = 0;
    fin = 20_000 + 21_000 = 41_000.
  semana 2 (20→26 oct): ent = 10_000 + 7×3_000 = 31_000; sal = 4_000;
    fin = 41_000 + 31_000 − 4_000 = 68_000.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timezone, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402

DATO_INSUFICIENTE = "datos insuficientes"
VENTANA_HISTORIAL_DIAS = 30


def _parse_fecha(f: str) -> datetime:
    return datetime.strptime(f[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


def _promedio_diario(historial: list | None, moneda: str,
                     hoy: datetime, ventana: int = VENTANA_HISTORIAL_DIAS
                     ) -> tuple[Decimal | None, str]:
    """Promedio diario por moneda. (None, motivo) si no hay datos."""
    if historial is None:
        return None, "historial no provisto: componente en datos "
        "insuficientes (no se inventa)"
    total = Decimal("0")
    n = 0
    corte = hoy - timedelta(days=ventana)
    for h in historial:
        try:
            f = _parse_fecha(str(h.get("fecha", "")))
            m = _dec(str(h.get("monto", "0")))
        except Exception:
            continue
        if (h.get("moneda") or "") != moneda or m <= 0:
            continue
        if f < corte or f > hoy:
            continue
        total += m
        n += 1
    if n == 0:
        return Decimal("0"), (f"sin movimientos en la ventana de {ventana} "
                              "días: promedio 0")
    return total / ventana, (f"promedio de {n} movimientos en {ventana} días "
                             f"({hoy.strftime('%Y-%m-%d')})")


def proyectar(tesoreria, dias: int = 90, hoy: str | None = None,
              historial_cobros: list | None = None,
              historial_pagos: list | None = None) -> dict:
    if dias <= 0 or dias > 365:
        raise ValueError("dias debe estar entre 1 y 365")
    ahora = _parse_fecha(hoy) if hoy else datetime.now(timezone.utc)
    ahora = ahora.replace(hour=0, minute=0, second=0, microsecond=0)
    limite = ahora + timedelta(days=dias)

    # ---- base programada (datos conocidos) ------------------------------
    # Lo VENCIDO (fecha < hoy) se considera exigible/pagadero AHORA:
    # se fija en el inicio del horizonte (semana 1), no se pierde.
    prog_entradas: dict[str, list] = {}   # moneda -> [(fecha, monto, ref)]
    prog_salidas: dict[str, list] = {}
    with tesoreria._lock:
        for c in tesoreria._cxc.values():
            if c.pendiente() > 0:
                v = max(_parse_fecha(c.vencimiento), ahora)
                if v <= limite:
                    prog_entradas.setdefault(c.moneda, []).append(
                        (v, c.pendiente(), f"cxc:{c.cuenta_id}"))
        for c in tesoreria._cxp.values():
            if c.pendiente() > 0:
                v = max(_parse_fecha(c.vencimiento), ahora)
                if v <= limite:
                    prog_salidas.setdefault(c.moneda, []).append(
                        (v, c.pendiente(), f"cxp:{c.cuenta_id}"))
        for g in tesoreria._futuros:
            f = max(_parse_fecha(g.fecha), ahora)
            if f <= limite:
                prog_salidas.setdefault(g.moneda, []).append(
                    (f, _dec(g.monto), f"futuro:{g.descripcion}"))
        caja = {m: v for m, v in tesoreria._caja.items()}

    monedas = sorted(set(caja) | set(prog_entradas) | set(prog_salidas))
    supuestos = [
        "proyección DETERMINISTA: solo aritmética sobre datos conocidos",
        f"horizonte: {dias} días desde {ahora.strftime('%Y-%m-%d')}",
        "entradas programadas = CxC con vencimiento dentro del horizonte "
        "(las vencidas se consideran exigibles en la semana 1)",
        "salidas programadas = CxP con vencimiento + gastos futuros "
        "programados en Tesorería (los vencidos, en la semana 1)",
        "NO pronostica ventas futuras ni inventa cobros",
        "monedas separadas: no hay conversión sin tasa explícita",
    ]
    por_moneda = {}
    for mon in monedas:
        prom_cobro, nota_cobro = _promedio_diario(
            historial_cobros, mon, ahora)
        prom_pago, nota_pago = _promedio_diario(
            historial_pagos, mon, ahora)
        supuestos_mon = [
            f"ritmo de cobros: {nota_cobro}",
            f"ritmo de pagos: {nota_pago}",
        ]
        # Buckets semanales.
        semanas = []
        saldo = caja.get(mon, Decimal("0"))
        n_sem = (dias + 6) // 7
        for s in range(n_sem):
            ini = ahora + timedelta(days=7 * s)
            fin = min(ini + timedelta(days=7), limite + timedelta(days=1))
            ent = sum((m for f, m, _ in prog_entradas.get(mon, [])
                       if ini <= f < fin), Decimal("0"))
            sal = sum((m for f, m, _ in prog_salidas.get(mon, [])
                       if ini <= f < fin), Decimal("0"))
            if prom_cobro is not None:
                ent += prom_cobro * 7
            if prom_pago is not None:
                sal += prom_pago * 7
            saldo_ant = saldo
            saldo = saldo + ent - sal
            semanas.append({
                "semana": s + 1,
                "desde": ini.strftime("%Y-%m-%d"),
                "hasta": (fin - timedelta(days=1)).strftime("%Y-%m-%d"),
                "saldo_inicial": str(saldo_ant),
                "entradas": str(ent),
                "salidas": str(sal),
                "flujo_neto": str(ent - sal),
                "saldo_final": str(saldo),
                "ritmo_cobros_datos": ("datos insuficientes"
                                      if prom_cobro is None else "ok"),
                "ritmo_pagos_datos": ("datos insuficientes"
                                      if prom_pago is None else "ok"),
            })
        por_moneda[mon] = {
            "moneda": mon,
            "saldo_inicial_caja": str(caja.get(mon, Decimal("0"))),
            "semanas": semanas,
            "saldo_proyectado_final": str(saldo),
            "supuestos_moneda": supuestos_mon,
        }
    return {"ok": True, "determinista": True,
            "hoy": ahora.strftime("%Y-%m-%d"), "dias": dias,
            "por_moneda": por_moneda, "supuestos": supuestos,
            "nota": ("proyección aritmética con datos conocidos + ritmos "
                     "históricos provistos; no es un pronóstico de ventas")}
