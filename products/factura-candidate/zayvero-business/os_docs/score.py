"""LOTE 7 — Business Score 0-100: explicable, sin inventar datos.

Criterios (peso):
- ventas 25%: tendencia de ingresos 90d vs 90d previos.
- rentabilidad 20%: margen OPERATIVO promedio del período (2026-10-13:
  el P&L corregido distingue utilidad operativa de neta; la "neta" exige
  impuestos registrados — dato del contador — y antes este criterio
  puntuaba de hecho el margen operativo bajo el nombre "neto", así que
  los números no cambian, solo el cableado de datos).
- caja 25%: días de cobertura (caja / gasto diario promedio).
- inventario 15%: % de productos sin dead stock.
- cobranzas 10%: % de CxC no vencida.
- clientes 10%: % de clientes activos (no inactivos).

Cada criterio: 0-100 + explicación con los números. Si no hay datos
suficientes para un criterio -> "sin_datos" (no se inventa).
Score = promedio ponderado de criterios disponibles; si hay menos de 4
disponibles -> None ("datos_insuficientes"), no un número inventado.
"""
from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402


def _parse(f: str):
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


_SIMBOLOS = {"DOP": "RD$", "USD": "US$", "EUR": "€", "GBP": "£", "MXN": "MX$"}


def _fmt_money(monto: Decimal, moneda: str) -> str:
    """RD$7,000.00 — presentación, nunca mezcla monedas."""
    sym = _SIMBOLOS.get(moneda, moneda + " ")
    return f"{sym}{float(monto):,.2f}"


@dataclass
class Criterio:
    nombre: str
    peso: float
    puntaje: float | None  # None = sin_datos
    detalle: str

    def to_dict(self):
        return {"nombre": self.nombre, "peso": self.peso,
                "puntaje": self.puntaje, "detalle": self.detalle}


@dataclass
class Score:
    puntaje: float | None
    criterios: list = field(default_factory=list)
    estado: str = "calculado"  # calculado | datos_insuficientes

    def to_dict(self):
        return {"puntaje": self.puntaje, "estado": self.estado,
                "criterios": [c.to_dict() for c in self.criterios]}


def _ingresos_ventana(libro, desde, hasta) -> Decimal:
    tot = Decimal("0")
    for v in libro._ventas.values():
        if desde <= v.fecha <= hasta and v.moneda == "DOP":
            tot += v.total()
    return tot


def calcular_score(libro=None, tesoreria=None, inventario=None,
                   cobranzas=None, crm=None, gastos=None,
                   hoy: str | None = None) -> Score:
    ahora = _parse(hoy) if hoy else datetime.now(timezone.utc)
    f = "%Y-%m-%dT%H:%M:%SZ"
    crits: list[Criterio] = []

    # 1. Ventas (25%)
    if libro and len(libro._ventas) >= 2:
        h1 = (ahora - timedelta(days=180)).strftime(f)
        h2 = (ahora - timedelta(days=90)).strftime(f)
        h3 = ahora.strftime(f)
        ant = _ingresos_ventana(libro, h1, h2)
        rec = _ingresos_ventana(libro, h2, h3)
        if ant > 0:
            var = (rec - ant) / ant * 100
            punt = max(0, min(100, 50 + float(var)))
            det = (f"ingresos 90d: {_fmt_money(rec, 'DOP')} vs previos "
                   f"{_fmt_money(ant, 'DOP')} ({var:+.1f}%)")
        else:
            punt, det = (75.0, f"sin base previa; recientes: "
                               f"{_fmt_money(rec, 'DOP')}") if rec > 0 else (None, "sin_datos")
            if punt is None:
                det = "sin historial de ventas"
        crits.append(Criterio("ventas", 0.25, punt, det))
    else:
        crits.append(Criterio("ventas", 0.25, None, "sin_datos"))

    # 2. Rentabilidad (20%)
    # 2026-10-13: usa margen_operativo_pct del P&L corregido. La fórmula
    # del puntaje no cambia (v*4): el margen "neto" anterior era
    # (bruta − gastos)/ingresos, idéntico al operativo actual — solo se
    # corrigió el cableado de datos (el "neto" real exige impuestos).
    if libro:
        r = libro.rentabilidad().get("DOP", {})
        mn = r.get("margen_operativo_pct")
        if mn is not None:
            v = float(mn)
            punt = max(0, min(100, v * 4))  # 25% margen -> 100
            crits.append(Criterio("rentabilidad", 0.20, round(punt, 1),
                                  f"margen operativo {mn}%"))
        else:
            crits.append(Criterio("rentabilidad", 0.20, None, "sin_datos"))
    else:
        crits.append(Criterio("rentabilidad", 0.20, None, "sin_datos"))

    # 3. Caja (25%)
    if tesoreria and gastos:
        caja = _dec(tesoreria.saldo("DOP"))
        tot_g = sum((_dec(g.gasto.monto) for g in gastos.todos()
                     if g.gasto.moneda == "DOP"), Decimal("0"))
        n = len([g for g in gastos.todos() if g.gasto.moneda == "DOP"])
        if n > 0 and tot_g > 0:
            diario = tot_g / 90  # ventana de referencia 90 días
            dias = float(caja / diario) if diario > 0 else 0
            punt = max(0, min(100, dias / 90 * 100))
            crits.append(Criterio("caja", 0.25, round(punt, 1),
                                  f"caja {_fmt_money(caja, 'DOP')} cubre "
                                  f"~{round(dias, 1)} días de gasto"))
        else:
            crits.append(Criterio("caja", 0.25, None, "sin_datos"))
    else:
        crits.append(Criterio("caja", 0.25, None, "sin_datos"))

    # 4. Inventario (15%): % de productos sin dead stock.
    # HALLAZGO DOCUMENTADO (2026-10-12, revisión de Miguel):
    # - evaluar_alertas() = criterio OPERATIVO: dispara con stock<=0 o bajo
    #   mínimo. Con stock positivo y sin mínimos -> "sin alertas" (correcto).
    # - detectar_dead_stock(90d) = detector factual con ventana explícita:
    #   tasa = salidas / 90 días. Con historia real < 90 días la tasa se
    #   subestima y todo parece inmovilizado -> el SCORE daría 0/100
    #   engañoso. El motor Lote 3 NO se toca; el juicio vive aquí:
    #   sin historia >= ventana, el criterio es "sin_datos", no 0.
    if inventario:
        from os_inventory.rotacion import detectar_dead_stock
        movs = inventario.movimientos()
        if not movs:
            crits.append(Criterio("inventario", 0.15, None, "sin_datos"))
        else:
            ventana = 90
            fechas = [m.fecha for m in movs if m.fecha]
            if not fechas:
                crits.append(Criterio(
                    "inventario", 0.15, None,
                    "sin_datos: movimientos sin fecha"))
            else:
                mas_antiguo = min(fechas)
                historia_dias = (ahora - _parse(mas_antiguo)).days
                if historia_dias < ventana:
                    crits.append(Criterio(
                        "inventario", 0.15, None,
                        f"sin_datos: historial de {historia_dias}d "
                        f"< {ventana}d; cobertura no evaluable aún"))
                else:
                    with_stock = {(m.producto_id, m.almacen_id) for m in movs
                                  if inventario.stock(m.producto_id,
                                                      m.almacen_id) > 0}
                    dead = {(h.producto_id, h.almacen_id) for h in
                            detectar_dead_stock(inventario, hoy=hoy)}
                    sanos = len(with_stock - dead)
                    punt = sanos / len(with_stock) * 100 if with_stock else None
                    det = (f"{sanos}/{len(with_stock)} productos con stock sano"
                           if punt is not None else "sin_datos")
                    crits.append(Criterio("inventario", 0.15,
                                          round(punt, 1) if punt is not None
                                          else None, det))
    else:
        crits.append(Criterio("inventario", 0.15, None, "sin_datos"))

    # 5. Cobranzas (10%)
    if cobranzas:
        items = cobranzas.aging(hoy)
        if items:
            aldia = sum(1 for i in items if i.dias_vencida == 0)
            punt = aldia / len(items) * 100
            crits.append(Criterio(
                "cobranzas", 0.10, round(punt, 1),
                f"{aldia}/{len(items)} cuentas al día"))
        else:
            crits.append(Criterio("cobranzas", 0.10, None, "sin_datos"))
    else:
        crits.append(Criterio("cobranzas", 0.10, None, "sin_datos"))

    # 6. Clientes (10%)
    if crm:
        from os_crm.inteligencia import senales_churn
        sen = senales_churn(crm, hoy)
        total = len(crm._clientes)
        if total > 0:
            inact = sum(1 for s in sen if s.nivel == "inactivo")
            punt = (total - inact) / total * 100
            crits.append(Criterio(
                "clientes", 0.10, round(punt, 1),
                f"{total - inact}/{total} clientes activos"))
        else:
            crits.append(Criterio("clientes", 0.10, None, "sin_datos"))
    else:
        crits.append(Criterio("clientes", 0.10, None, "sin_datos"))

    disp = [c for c in crits if c.puntaje is not None]
    if len(disp) < 4:
        return Score(puntaje=None, criterios=crits,
                     estado="datos_insuficientes")
    peso_tot = sum(c.peso for c in disp)
    score = sum(c.puntaje * c.peso for c in disp) / peso_tot
    return Score(puntaje=round(score, 1), criterios=crits)
