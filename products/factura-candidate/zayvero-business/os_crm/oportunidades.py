"""BLOQUE CRM/VENTAS (2026-10-13) — Pipeline de oportunidades.

Pipeline simple y verificable:
- Etapas con probabilidad por defecto DOCUMENTADA (editable por
  oportunidad): nueva 10%, contactado 25%, propuesta 50%,
  negociación 75%, ganada 100%, perdida 0%.
- Valor esperado = monto × probabilidad / 100.
- responsable y fecha_estimada por oportunidad; historial de etapas.
- ganada / perdida son terminales (no se puede cambiar de etapa).

Todo aislado por company_id. Las escrituras generan evento de auditoría
tipo "oportunidad" (ver os_crm/http.py).
"""
from __future__ import annotations

import sys
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from oschema.contract import require_iso_currency  # noqa: E402


# (etapa, probabilidad_por_defecto_%)
ETAPAS = (("nueva", 10), ("contactado", 25), ("propuesta", 50),
          ("negociacion", 75), ("ganada", 100), ("perdida", 0))
PROB_POR_ETAPA = dict(ETAPAS)
ETAPAS_TERMINALES = ("ganada", "perdida")


class ErrorOportunidad(Exception):
    pass


def _ahora_iso(hoy: str | None = None) -> str:
    if hoy:
        return hoy
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def valor_esperado(monto: str, probabilidad: int | float) -> str:
    """Valor esperado = monto × probabilidad / 100."""
    return str(_dec(monto) * Decimal(str(probabilidad)) / Decimal("100"))


@dataclass
class Oportunidad:
    oportunidad_id: str
    company_id: str
    titulo: str
    monto: str
    moneda: str
    etapa: str = "nueva"
    probabilidad: int = 10
    cliente_id: str = ""
    responsable: str = ""
    fecha_estimada: str = ""
    creada: str = ""
    historial: list = field(default_factory=list)

    def to_dict(self):
        return {
            "oportunidad_id": self.oportunidad_id,
            "titulo": self.titulo, "monto": str(_dec(self.monto)),
            "moneda": self.moneda, "etapa": self.etapa,
            "probabilidad": self.probabilidad,
            "valor_esperado": valor_esperado(self.monto, self.probabilidad),
            "cliente_id": self.cliente_id, "responsable": self.responsable,
            "fecha_estimada": self.fecha_estimada, "creada": self.creada,
            "historial": list(self.historial)}


class Pipeline:
    """Pipeline de oportunidades de UNA empresa."""

    def __init__(self, company_id: str):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        self.company_id = company_id
        self._lock = threading.RLock()
        self._ops: dict[str, Oportunidad] = {}

    def crear(self, oportunidad_id: str, titulo: str, monto: str,
              moneda: str, cliente_id: str = "", responsable: str = "",
              fecha_estimada: str = "", probabilidad: int | None = None,
              etapa: str = "nueva", hoy: str | None = None) -> dict:
        with self._lock:
            if not oportunidad_id or not titulo:
                raise ErrorOportunidad(
                    "oportunidad_id y titulo son obligatorios")
            if oportunidad_id in self._ops:
                raise ErrorOportunidad(
                    f"oportunidad duplicada: {oportunidad_id}")
            if _dec(monto) <= 0:
                raise ErrorOportunidad("el monto debe ser > 0")
            require_iso_currency(moneda)
            if etapa not in PROB_POR_ETAPA:
                raise ErrorOportunidad(
                    f"etapa inválida: {etapa} "
                    f"(use: {', '.join(PROB_POR_ETAPA)})")
            prob = (PROB_POR_ETAPA[etapa] if probabilidad is None
                    else int(probabilidad))
            if not 0 <= prob <= 100:
                raise ErrorOportunidad("probabilidad debe estar entre 0 y 100")
            op = Oportunidad(
                oportunidad_id=oportunidad_id, company_id=self.company_id,
                titulo=titulo, monto=str(_dec(monto)), moneda=moneda,
                etapa=etapa, probabilidad=prob, cliente_id=cliente_id,
                responsable=responsable, fecha_estimada=fecha_estimada,
                creada=_ahora_iso(hoy),
                historial=[{"etapa": etapa, "fecha": _ahora_iso(hoy),
                            "nota": "creada"}])
            self._ops[oportunidad_id] = op
            return op.to_dict()

    def cambiar_etapa(self, oportunidad_id: str, etapa: str, nota: str = "",
                      probabilidad: int | None = None,
                      hoy: str | None = None) -> dict:
        with self._lock:
            op = self._ops.get(oportunidad_id)
            if op is None:
                raise ErrorOportunidad(
                    f"oportunidad inexistente: {oportunidad_id}")
            if op.etapa in ETAPAS_TERMINALES:
                raise ErrorOportunidad(
                    f"oportunidad {op.etapa}: etapa terminal, no se puede "
                    "cambiar")
            if etapa not in PROB_POR_ETAPA:
                raise ErrorOportunidad(
                    f"etapa inválida: {etapa} "
                    f"(use: {', '.join(PROB_POR_ETAPA)})")
            op.etapa = etapa
            op.probabilidad = (PROB_POR_ETAPA[etapa] if probabilidad is None
                               else int(probabilidad))
            if not 0 <= op.probabilidad <= 100:
                raise ErrorOportunidad(
                    "probabilidad debe estar entre 0 y 100")
            op.historial.append({"etapa": etapa, "fecha": _ahora_iso(hoy),
                                 "nota": nota})
            return op.to_dict()

    def obtener(self, oportunidad_id: str) -> dict:
        with self._lock:
            op = self._ops.get(oportunidad_id)
            if op is None:
                raise ErrorOportunidad(
                    f"oportunidad inexistente: {oportunidad_id}")
            return op.to_dict()

    def listar(self, etapa: str | None = None,
               responsable: str | None = None) -> list[dict]:
        with self._lock:
            ops = list(self._ops.values())
            if etapa:
                if etapa not in PROB_POR_ETAPA:
                    raise ErrorOportunidad(f"etapa inválida: {etapa}")
                ops = [o for o in ops if o.etapa == etapa]
            if responsable:
                ops = [o for o in ops if o.responsable == responsable]
            return [o.to_dict() for o in sorted(
                ops, key=lambda o: o.oportunidad_id)]

    def resumen(self) -> dict:
        """Pipeline por etapa: nº, monto y valor esperado; total ponderado.

        Las monedas NO se mezclan: el resumen agrupa por moneda.
        """
        with self._lock:
            por_etapa: dict[str, dict] = {}
            total_ve: dict[str, Decimal] = {}
            for op in self._ops.values():
                e = por_etapa.setdefault(op.etapa, {
                    "etapa": op.etapa, "n": 0,
                    "monto_por_moneda": {}, "valor_esperado_por_moneda": {}})
                e["n"] += 1
                m = _dec(op.monto)
                e["monto_por_moneda"][op.moneda] = str(
                    _dec(e["monto_por_moneda"].get(op.moneda, "0")) + m)
                ve = _dec(valor_esperado(op.monto, op.probabilidad))
                e["valor_esperado_por_moneda"][op.moneda] = str(
                    _dec(e["valor_esperado_por_moneda"].get(op.moneda, "0"))
                    + ve)
                total_ve[op.moneda] = total_ve.get(op.moneda, Decimal("0")) + ve
            orden = {e: i for i, (e, _) in enumerate(ETAPAS)}
            return {
                "etapas": sorted(por_etapa.values(),
                                 key=lambda x: orden.get(x["etapa"], 99)),
                "valor_esperado_total": {m: str(v)
                                        for m, v in sorted(total_ve.items())},
                "probabilidad_por_etapa": dict(PROB_POR_ETAPA)}
