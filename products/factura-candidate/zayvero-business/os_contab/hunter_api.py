"""BLOQUE CONTABILIDAD — Expense Hunter API.

Análisis de SOLO LECTURA sobre el RegistroGastos de la empresa. No muta
nada, no acusa: cada hallazgo lleva su umbral explícito, su evidencia y
una recomendación en tono "revisar" (nunca "fraude").

Detectores:
- duplicado_probable: mismo proveedor + mismo monto + fechas a ≤3 días
  (reutiliza os_finanzas.expense_hunter.cazar_duplicados; no se duplica
  la regla).
- pico_vs_historico: un gasto ≥ FACTOR × el promedio de los OTROS gastos
  de su categoría (exige ≥ MIN_HISTORIA gastos de comparación; si no hay
  historia suficiente, no hay hallazgo: se dice en el resumen).
- monto_redondo: monto ≥ UMBRAL y múltiplo exacto de MULTIPLO sin
  comprobante registrado (comprobante == "").
- fuera_de_horario: registrado en fin de semana o fuera de 07:00–19:00
  (hora local del registro).

Todos los umbrales son parámetros con valores por defecto documentados;
cada hallazgo los repite para que el lector no tenga que buscarlos.
"""
from __future__ import annotations

import sys
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from os_finanzas.expense_hunter import (  # noqa: E402
    cazar_duplicados, cazar_desperdicios)

# Umbrales por defecto (documentados y repetidos en cada hallazgo).
VENTANA_DUPLICADO_DIAS = 3
PICO_FACTOR = "3"          # gasto >= 3x promedio de su categoría
PICO_MIN_HISTORIA = 5      # nº mínimo de OTROS gastos de la categoría
REDONDO_UMBRAL = "5000"    # monto mínimo para marcar redondo
REDONDO_MULTIPLO = "1000"  # múltiplo exacto
HORA_INICIO = 7            # horario laboral: lun–vie 07:00–19:00
HORA_FIN = 19

NOTA_ETICA = (
    "Estos hallazgos son señales para REVISIÓN humana, no evidencia de "
    "fraude ni acusaciones contra nadie: verifique cada caso con su "
    "contexto (facturas, aprobaciones, temporada) antes de concluir algo.")


@dataclass
class Hallazgo:
    tipo: str            # duplicado_probable | pico_vs_historico |
    #                      monto_redondo | fuera_de_horario
    severidad: str       # alta | media | baja
    umbral: str          # regla aplicada, en lenguaje humano
    evidencia: dict      # datos verificables del caso
    gasto_ids: list = field(default_factory=list)
    recomendacion: str = ""

    def to_dict(self) -> dict:
        return {"tipo": self.tipo, "severidad": self.severidad,
                "umbral": self.umbral, "evidencia": self.evidencia,
                "gasto_ids": list(self.gasto_ids),
                "recomendacion": self.recomendacion}


def _parse(f: str) -> datetime:
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc)


def _s(d: Decimal) -> str:
    """Monto sin notación científica ni ceros de más (como profit)."""
    s = format(round(d, 2).normalize(), "f")
    return s if s not in ("", "-") else "0"


def detectar_duplicados(gastos: list) -> list[Hallazgo]:
    """Duplicados probables (reutiliza la regla de os_finanzas)."""
    out = []
    for h in cazar_duplicados(gastos):
        a_id, b_id = h.gasto_ids[0], h.gasto_ids[1]
        ga = next(g for g in gastos if g.gasto.gasto_id == a_id)
        gb = next(g for g in gastos if g.gasto.gasto_id == b_id)
        if (ga.gasto.moneda or "") != (gb.gasto.moneda or ""):
            # La regla base no mira moneda; la API sí (no mezclar).
            continue
        out.append(Hallazgo(
            tipo="duplicado_probable", severidad="alta",
            umbral=(f"mismo proveedor_id + mismo monto exacto + fechas a "
                    f"≤{VENTANA_DUPLICADO_DIAS} días"),
            evidencia={"proveedor_id": ga.gasto.proveedor_id,
                       "monto": str(_dec(ga.gasto.monto)),
                       "moneda": ga.gasto.moneda,
                       "detalle_origen": h.detalle},
            gasto_ids=[a_id, b_id],
            recomendacion=("Revisar si son dos cargos reales o un registro "
                           "duplicado (p. ej. factura cargada dos veces).")))
    return sorted(out, key=lambda x: x.gasto_ids)


def detectar_picos(gastos: list, factor: str = PICO_FACTOR,
                   min_historia: int = PICO_MIN_HISTORIA) -> list[Hallazgo]:
    """Gastos muy por encima del promedio de su categoría.

    Agrupa por (categoría, moneda): nunca promedia montos de monedas
    distintas.
    """
    fac = _dec(factor)
    por_cat: dict[tuple[str, str], list] = {}
    for g in gastos:
        if g.gasto.fecha and _dec(g.gasto.monto) > 0:
            por_cat.setdefault(
                (g.gasto.categoria, g.gasto.moneda or ""), []).append(g)
    out = []
    for (cat, mon), gs in por_cat.items():
        if len(gs) < min_historia + 1:
            continue
        for g in gs:
            otros = [_dec(x.gasto.monto) for x in gs if x is not g]
            prom = sum(otros, Decimal("0")) / len(otros)
            monto = _dec(g.gasto.monto)
            if prom > 0 and monto >= prom * fac:
                out.append(Hallazgo(
                    tipo="pico_vs_historico", severidad="media",
                    umbral=(f"monto ≥ {factor}× promedio de los otros "
                            f"{len(otros)} gastos de la categoría en la "
                            f"misma moneda (mínimo {min_historia} de "
                            f"historia)"),
                    evidencia={"categoria": cat,
                               "monto": str(monto),
                               "promedio_categoria": _s(prom),
                               "ratio": _s(monto / prom),
                               "moneda": g.gasto.moneda,
                               "fecha": g.gasto.fecha[:10],
                               "proveedor_id": g.gasto.proveedor_id or ""},
                    gasto_ids=[g.gasto.gasto_id],
                    recomendacion=("Revisar si el gasto corresponde a una "
                                   "necesidad real puntual (reparación, "
                                   "compra anual) o a un error de registro.")))
    out.sort(key=lambda x: x.gasto_ids)
    return out


def detectar_montos_redondos(gastos: list, umbral: str = REDONDO_UMBRAL,
                             multiplo: str = REDONDO_MULTIPLO) -> list[Hallazgo]:
    """Montos grandes, exactos y sin comprobante: pedir el soporte."""
    umb, mul = _dec(umbral), _dec(multiplo)
    out = []
    for g in gastos:
        monto = _dec(g.gasto.monto)
        if (monto >= umb and mul > 0 and monto % mul == 0
                and not (g.gasto.comprobante or "").strip()):
            out.append(Hallazgo(
                tipo="monto_redondo", severidad="baja",
                umbral=(f"monto ≥ {umbral} y múltiplo exacto de {multiplo} "
                        f"sin comprobante registrado"),
                evidencia={"monto": str(monto), "moneda": g.gasto.moneda,
                           "categoria": g.gasto.categoria,
                           "proveedor_id": g.gasto.proveedor_id or "",
                           "fecha": (g.gasto.fecha or "")[:10],
                           "comprobante": g.gasto.comprobante or "(vacío)"},
                gasto_ids=[g.gasto.gasto_id],
                recomendacion=("Pedir el comprobante/factura de respaldo; "
                               "los montos redondos sin soporte merecen "
                               "una segunda mirada, nada más.")))
    return sorted(out, key=lambda x: x.gasto_ids)


def detectar_fuera_horario(gastos: list, hora_inicio: int = HORA_INICIO,
                           hora_fin: int = HORA_FIN) -> list[Hallazgo]:
    """Gastos registrados en fin de semana o fuera del horario laboral."""
    out = []
    for g in gastos:
        if not g.gasto.fecha:
            continue
        f = _parse(g.gasto.fecha)
        es_finde = f.weekday() >= 5  # 5=sábado, 6=domingo
        fuera_hora = not (hora_inicio <= f.hour < hora_fin)
        if not (es_finde or fuera_hora):
            continue
        motivo = ("fin de semana" if es_finde
                  else f"fuera de horario ({f.hour:02d}:00)")
        out.append(Hallazgo(
            tipo="fuera_de_horario", severidad="baja",
            umbral=(f"horario laboral esperado: lunes a viernes de "
                    f"{hora_inicio:02d}:00 a {hora_fin:02d}:00"),
            evidencia={"motivo": motivo,
                       "fecha_hora": g.gasto.fecha[:16].replace("T", " "),
                       "dia_semana": ["lun", "mar", "mié", "jue", "vie",
                                      "sáb", "dom"][f.weekday()],
                       "monto": str(_dec(g.gasto.monto)),
                       "moneda": g.gasto.moneda,
                       "categoria": g.gasto.categoria},
            gasto_ids=[g.gasto.gasto_id],
            recomendacion=("Revisar si el gasto fuera de horario tiene "
                           "justificación operativa (emergencia, turno "
                           "nocturno) o fue un registro tardío.")))
    return sorted(out, key=lambda x: x.gasto_ids)


def analizar(gastos: list, incluir_desperdicios: bool = True,
             hoy: str | None = None) -> dict:
    """Análisis completo de solo lectura. No muta el registro."""
    hallazgos = (detectar_duplicados(gastos)
                 + detectar_picos(gastos)
                 + detectar_montos_redondos(gastos)
                 + detectar_fuera_horario(gastos))
    if incluir_desperdicios:
        for h in cazar_desperdicios(gastos, hoy):
            hallazgos.append(Hallazgo(
                tipo="pico_vs_historico", severidad=h.severidad,
                umbral=("total del último mes > 1.5× promedio mensual "
                        "histórico (exige ≥3 meses de historia)"),
                evidencia={"detalle_origen": h.detalle},
                gasto_ids=list(h.gasto_ids),
                recomendacion=("Revisar si el aumento de la categoría es "
                               "estacional/justificado o un desperdicio "
                               "recurrente.")))
    por_tipo: dict[str, int] = {}
    for h in hallazgos:
        por_tipo[h.tipo] = por_tipo.get(h.tipo, 0) + 1
    return {"ok": True,
            "fuente": "RegistroGastos (solo lectura; no se modificó nada)",
            "gastos_analizados": len(gastos),
            "hallazgos": [h.to_dict() for h in hallazgos],
            "resumen_por_tipo": por_tipo,
            "nota_etica": NOTA_ETICA}
