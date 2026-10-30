"""BLOQUE CONTABILIDAD — Presupuestos por categoría y período.

- Un presupuesto = (categoria, periodo "YYYY-MM", limite, moneda).
- La ejecución compara el límite contra los gastos REALES del
  RegistroGastos (misma categoría + mismo período + misma moneda).
  Los gastos en OTRA moneda NO se convierten sin tasa explícita: se
  listan aparte como "excluidos_otra_moneda" (honestidad: no mezclar).
- Alertas: ≥80 % consumido -> "alerta"; >100 % -> "sobreejecutado".
- Proyección de cierre: pro-rata lineal (ejecutado / días transcurridos
  × días del mes); los supuestos viajan explícitos en la salida.

Caso trabajado (ver tests):
  presupuesto "marketing" 2026-10, límite 10_000 DOP.
  gastos: 3_000 (día 5) + 4_000 (día 12) + 1_500 USD (día 20, otra moneda).
  hoy = 2026-10-15 -> ejecutado 7_000 DOP (70 %, estado "ok"; el de USD
  queda en excluidos_otra_moneda), proyección = 7_000/15×31 = 14_466.67
  -> "sobreejecutado_proyectado".
"""
from __future__ import annotations

import calendar
import re
import sys
import os
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from oschema.contract import require_iso_currency  # noqa: E402
from .errores import ErrorContab, NoEncontrado  # noqa: E402

_PERIODO_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")
UMBRAL_ALERTA_PCT = 80  # % consumido que dispara "alerta"


class ErrorPresupuesto(ErrorContab):
    pass


@dataclass
class Presupuesto:
    presupuesto_id: str
    company_id: str
    categoria: str
    periodo: str      # YYYY-MM
    limite: str       # monto máximo, en `moneda`
    moneda: str

    def validate(self):
        if not self.presupuesto_id or not self.categoria:
            raise ErrorPresupuesto(
                "presupuesto_id y categoria son obligatorios")
        if not _PERIODO_RE.match(self.periodo or ""):
            raise ErrorPresupuesto(
                f"periodo inválido (se espera YYYY-MM): {self.periodo}")
        require_iso_currency(self.moneda)
        if _dec(self.limite) <= 0:
            raise ErrorPresupuesto("el límite debe ser > 0")

    def to_dict(self) -> dict:
        return {"presupuesto_id": self.presupuesto_id,
                "company_id": self.company_id,
                "categoria": self.categoria, "periodo": self.periodo,
                "limite": str(_dec(self.limite)), "moneda": self.moneda}


def _en_periodo(fecha_iso: str, periodo: str) -> bool:
    return (fecha_iso or "")[:7] == periodo


class RegistroPresupuestos:
    """Presupuestos de UNA empresa. Clave única: (categoria, periodo)."""

    def __init__(self, company_id: str):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        self.company_id = company_id
        self._lock = threading.RLock()
        self._pres: dict[tuple[str, str], Presupuesto] = {}

    def crear(self, p: Presupuesto) -> Presupuesto:
        with self._lock:
            p.validate()
            if p.company_id != self.company_id:
                raise ErrorPresupuesto("presupuesto de otra empresa")
            clave = (p.categoria, p.periodo)
            if clave in self._pres:
                raise ErrorPresupuesto(
                    f"ya existe presupuesto para {p.categoria} en "
                    f"{p.periodo}")
            self._pres[clave] = p
            return p

    def obtener(self, categoria: str, periodo: str) -> Presupuesto:
        with self._lock:
            p = self._pres.get((categoria, periodo))
            if p is None:
                raise NoEncontrado(
                    f"sin presupuesto para {categoria} en {periodo}")
            return p

    def listar(self) -> list[Presupuesto]:
        with self._lock:
            return sorted(self._pres.values(),
                          key=lambda p: (p.periodo, p.categoria))

    # ---- ejecución ------------------------------------------------------
    def ejecucion(self, categoria: str, periodo: str,
                  gastos: list) -> dict:
        """Compara límite vs gastos reales del RegistroGastos."""
        p = self.obtener(categoria, periodo)
        limite = _dec(p.limite)
        ejecutado = Decimal("0")
        excluidos = []
        n = 0
        for g in gastos:
            if g.gasto.categoria != categoria:
                continue
            if not _en_periodo(g.gasto.fecha or "", periodo):
                continue
            if (g.gasto.moneda or "") != p.moneda:
                excluidos.append({"gasto_id": g.gasto.gasto_id,
                                  "monto": str(_dec(g.gasto.monto)),
                                  "moneda": g.gasto.moneda or "(vacía)",
                                  "motivo": "otra moneda: no se convierte "
                                            "sin tasa explícita"})
                continue
            ejecutado += _dec(g.gasto.monto)
            n += 1
        pct = (ejecutado / limite * 100) if limite > 0 else Decimal("0")
        estado = ("sobreejecutado" if pct > 100
                  else "alerta" if pct >= UMBRAL_ALERTA_PCT else "ok")
        return {
            "presupuesto": p.to_dict(),
            "ejecutado": str(ejecutado),
            "gastos_contados": n,
            "pct_consumido": str(round(pct, 1)),
            "disponible": str(limite - ejecutado),
            "estado": estado,
            "umbral_alerta_pct": UMBRAL_ALERTA_PCT,
            "excluidos_otra_moneda": excluidos,
            "nota": ("gastos del RegistroGastos; misma categoría, período "
                     "y moneda. Otra moneda no se convierte sin tasa."),
        }

    def proyeccion_cierre(self, categoria: str, periodo: str, gastos: list,
                          hoy: str | None = None) -> dict:
        """Proyección lineal de cierre de mes. Supuestos explícitos."""
        ej = self.ejecucion(categoria, periodo, gastos)
        ahora = (datetime.strptime((hoy or "")[:10], "%Y-%m-%d")
                 if hoy else datetime.now(timezone.utc)).replace(
                     tzinfo=timezone.utc)
        anio, mes = int(periodo[:4]), int(periodo[5:7])
        dias_mes = calendar.monthrange(anio, mes)[1]
        if (ahora.year, ahora.month) == (anio, mes):
            dias_transcurridos = ahora.day
        elif (ahora.year, ahora.month) < (anio, mes):
            dias_transcurridos = 0
        else:
            dias_transcurridos = dias_mes
        ejecutado = _dec(ej["ejecutado"])
        limite = _dec(ej["presupuesto"]["limite"])
        if dias_transcurridos > 0:
            proy = ejecutado / dias_transcurridos * dias_mes
        else:
            proy = Decimal("0")
        pct_proy = (proy / limite * 100) if limite > 0 else Decimal("0")
        estado = ("sobreejecutado_proyectado" if pct_proy > 100
                  else "alerta_proyectada"
                  if pct_proy >= UMBRAL_ALERTA_PCT else "dentro_de_presupuesto")

        def _s(d: Decimal) -> str:  # sin ceros de más (como profit)
            s = format(round(d, 2).normalize(), "f")
            return s if s not in ("", "-") else "0"

        return {
            "presupuesto": ej["presupuesto"],
            "hoy": ahora.strftime("%Y-%m-%d"),
            "dias_transcurridos": dias_transcurridos,
            "dias_del_mes": dias_mes,
            "ejecutado_a_la_fecha": str(ejecutado),
            "proyeccion_cierre": _s(proy),
            "pct_proyectado": str(round(pct_proy, 1)),
            "estado_proyectado": estado,
            "umbral_alerta_pct": UMBRAL_ALERTA_PCT,
            "supuestos": [
                "gasto lineal: se asume ritmo constante hasta fin de mes",
                f"hoy = {ahora.strftime('%Y-%m-%d')} (parámetro explícito)",
                "solo gastos en la moneda del presupuesto; otra moneda "
                "no se convierte sin tasa",
                "proyección aritmética, no pronóstico: no anticipa gastos "
                "puntuales futuros",
            ],
        }
