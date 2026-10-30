"""EXTRA 3 (sprint 12-oct-2026) — Motor del Price Advisor.

Cálculo puro y COMPROBABLE sobre el estado real de una empresa:

    precio_recomendado = costo / (1 - margen_objetivo)
    piso               = costo × (1 + margen_mínimo)
    recomendado_final  = max(precio_recomendado, piso)  # nunca < costo

REUTILIZACIÓN (no duplicación):
- El costo promedio y las unidades con costo se leen con
  `simulator.Simulador` (`_costo_prom`, `_und_costo`), que lee
  `os_finanzas.CostoInventario`.
- El precio actual es el promedio ponderado por cantidad del
  precio_unitario en ventas registradas (mismo `_precio_referencia`
  del simulador; None si nunca se vendió: no se inventa).
- La demanda histórica viene de `Simulador._demanda_diaria`.

GARANTÍAS:
- Solo lectura: jamás llama a registrar/aplicar/auditar.
- Aislamiento: el AsesorPrecios se liga a UN bundle (una empresa).
- Cada producto trae `insumos` (valores usados), `formulas`
  (cómo se calculó) y `supuestos` (qué se asumió y por qué).
- Sin elasticidad precio-demanda inventada: la tabla de sensibilidad
  muestra margen unitario y margen total con demanda CONSTANTE
  (supuesto explícito); los escenarios de demanda con multiplicadores
  explícitos ya existen en `simulator.simular_precio`.
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal, ROUND_HALF_UP

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from simulator.engine import Simulador, ErrorSimulador  # noqa: E402
from oschema.contract import require_iso_currency  # noqa: E402
from oschema.models import _dec  # noqa: E402

# BLOQUE 6 (sprint 3, 2026-10-12) — Trazabilidad: cada recomendación
# indica de dónde salió el costo y el precio actual (catálogo vigente
# vs. derivación histórica), con vigencia cuando viene del catálogo.
try:
    from catalogo import integracion as _catint  # noqa: E402
except Exception:  # pragma: no cover
    _catint = None


class ErrorAdvisor(ValueError):
    """Error de validación de parámetros (→ 400 en la API)."""


MARGEN_OBJETIVO_DEF = Decimal("0.40")   # 40 %
MARGEN_MINIMO_DEF = Decimal("0.00")     # piso = costo (nunca bajo costo)
TOLERANCIA_DEF = Decimal("0.03")        # banda ±3 % para "mantener"


def _s(x: Decimal) -> str:
    return str(x)


def _q2(x: Decimal) -> Decimal:
    return x.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _margen_pct(precio: Decimal, costo: Decimal) -> Decimal | None:
    if precio <= 0:
        return None
    return (precio - costo) / precio * 100


class AsesorPrecios:
    """Asesor ligado al OSBundle de UNA empresa (solo lectura)."""

    def __init__(self, bundle):
        if not getattr(bundle, "company_id", None):
            raise ErrorAdvisor("se requiere un bundle con company_id")
        try:
            self.sim = Simulador(bundle)
        except ErrorSimulador as e:
            raise ErrorAdvisor(str(e))
        self.bundle = bundle
        self.company_id = bundle.company_id

    # ---- parámetros ---------------------------------------------------
    def _trazabilidad(self, producto_id: str, moneda: str) -> dict:
        """Fuente del costo y del precio actual usados.

        - "catalogo": dato vigente del catálogo maestro (con
          vigente_desde y fuente de carga).
        - "promedio_registrado" / "promedio_ventas": derivación
          histórica de siempre (fallback cuando el catálogo no tiene
          dato).
        """
        det_costo = (None if _catint is None else
                     _catint.detalle_costo_catalogo(
                         self.company_id, producto_id, moneda))
        det_precio = (None if _catint is None else
                      _catint.detalle_precio_catalogo(
                          self.company_id, producto_id, moneda))
        return {
            "fuente_costo": ("catalogo" if det_costo
                             else "promedio_registrado"),
            "fuente_precio_actual": ("catalogo" if det_precio
                                     else "promedio_ventas"),
            "detalle": {
                "costo": ({"fuente": "catalogo",
                           "vigente_desde": det_costo["vigente_desde"],
                           "fuente_carga": det_costo["fuente"]}
                          if det_costo else
                          {"fuente": "promedio_registrado",
                           "nota": "promedio ponderado de CostoInventario; "
                                   "sin dato vigente en el catálogo"}),
                "precio_actual": (
                    {"fuente": "catalogo",
                     "vigente_desde": det_precio["vigente_desde"]}
                    if det_precio else
                    {"fuente": "promedio_ventas",
                     "nota": "promedio ponderado del precio_unitario en "
                             "ventas registradas; sin precio vigente en "
                             "el catálogo"}),
            },
        }

    def _validar(self, moneda: str, margen_objetivo, margen_minimo,
                 tolerancia, ventana_demanda_dias: int):
        require_iso_currency(moneda)
        mo = _dec(str(margen_objetivo))
        mm = _dec(str(margen_minimo))
        tol = _dec(str(tolerancia))
        if not (Decimal("0") < mo < Decimal("1")):
            raise ErrorAdvisor(
                "margen_objetivo debe estar entre 0 y 1 (exclusivo)")
        if mm < 0:
            raise ErrorAdvisor("margen_minimo no puede ser negativo")
        if tol < 0:
            raise ErrorAdvisor("tolerancia no puede ser negativa")
        if ventana_demanda_dias <= 0:
            raise ErrorAdvisor("ventana_demanda_dias debe ser > 0")
        return moneda, mo, mm, tol

    # ---- enumeración de productos --------------------------------------
    def _pares(self, producto_ids: list | None, almacen: str | None,
               moneda: str) -> list[tuple[str, str]]:
        pares = set()
        for (pid, alm) in self.bundle.inventario.stock_empresa():
            pares.add((pid, alm))
        # Productos vendidos aunque hoy tengan stock 0: también se venden
        # y merecen recomendación si hay costo e historial.
        for v in self.sim._ventas():
            if v.moneda != moneda:
                continue
            for lin in v.lineas:
                pares.add((lin.producto_id, lin.almacen_id or "principal"))
        if producto_ids:
            sel = set(producto_ids)
            pares = {(p, a) for (p, a) in pares if p in sel}
        if almacen:
            pares = {(p, a) for (p, a) in pares if a == almacen}
        return sorted(pares)

    # ---- recomendación por producto ------------------------------------
    def _recomendar_uno(self, producto_id: str, almacen_id: str, moneda: str,
                        mo: Decimal, mm: Decimal, tol: Decimal,
                        demanda: Decimal, hoy: str, lang: str) -> dict:
        costo = _dec(self.sim._costo_prom(producto_id, almacen_id, moneda))
        und_costo = _dec(self.sim._und_costo(producto_id, almacen_id,
                                             moneda))
        precio_actual = self.sim._precio_referencia(
            producto_id, almacen_id, moneda)
        costo_ok = costo > 0 and und_costo > 0
        historial_ok = precio_actual is not None

        # BLOQUE 6: trazabilidad del dato (fuente + vigencia).
        traz = self._trazabilidad(producto_id, moneda)

        base = {
            "producto_id": producto_id,
            "almacen_id": almacen_id,
            "moneda": moneda,
            "insumos": {
                "costo_promedio_usado": _s(costo),
                "unidades_con_costo": _s(und_costo),
                "precio_actual_referencia": (
                    _s(precio_actual) if historial_ok else None),
                "demanda_diaria_uds": _s(demanda),
                "fuente_costo": traz["fuente_costo"],
                "fuente_precio_actual": traz["fuente_precio_actual"],
                "trazabilidad": traz["detalle"],
                "parametros": {
                    "margen_objetivo": _s(mo),
                    "margen_minimo": _s(mm),
                    "tolerancia_veredicto": _s(tol),
                },
            },
        }

        if not costo_ok:
            base.update({
                "estado": "datos_insuficientes",
                "veredicto": "datos_insuficientes",
                "resultados": {"precio_recomendado": None},
                "motivo": "sin_costo_confiable",
                "explicacion_es": (
                    "No hay costo promedio confiable registrado "
                    f"(costo={_s(costo)}, unidades con costo={_s(und_costo)}); "
                    "sin costo real no se puede recomendar un precio."),
                "explicacion_en": (
                    "No reliable average cost on record "
                    f"(cost={_s(costo)}, units with cost={_s(und_costo)}); "
                    "a price cannot be recommended without real cost."),
                **_docs(),
            })
            return base
        if not historial_ok:
            base.update({
                "estado": "datos_insuficientes",
                "veredicto": "datos_insuficientes",
                "resultados": {"precio_recomendado": None},
                "motivo": "sin_historial_ventas",
                "explicacion_es": (
                    f"Hay costo promedio ({_s(costo)}) pero el producto nunca "
                    "se ha vendido en esta moneda/almacén: no hay precio "
                    "actual contra el que comparar y no se inventa uno."),
                "explicacion_en": (
                    f"Average cost is known ({_s(costo)}) but the product has "
                    "never been sold in this currency/warehouse: there is no "
                    "current price to compare against, and none is invented."),
                **_docs(),
            })
            return base

        # costo_ok y precio_actual disponible → recomendación real.
        crudo = costo / (Decimal("1") - mo)
        piso = costo * (Decimal("1") + mm)
        recomendado = _q2(max(crudo, piso))
        pa = precio_actual
        margen_actual = _margen_pct(pa, costo)
        margen_efectivo = _margen_pct(recomendado, costo)
        brecha = recomendado - pa

        if pa < recomendado * (Decimal("1") - tol):
            veredicto = "subir"
        elif pa > recomendado * (Decimal("1") + tol):
            veredicto = "bajar"
        else:
            veredicto = "mantener"

        sens = self._sensibilidad(piso, recomendado, pa, costo, demanda)
        tol_pct = _q2(tol * 100)
        explicaciones = {
            "subir": (
                f"El precio actual ({_s(_q2(pa))}) deja un margen de "
                f"{_q2(margen_actual)} %, bajo el objetivo de "
                f"{_q2(mo * 100)} %. Recomendado: {_s(recomendado)} "
                f"(costo promedio {_s(costo)}).",
                f"Current price ({_s(_q2(pa))}) yields a "
                f"{_q2(margen_actual)} % margin, below the "
                f"{_q2(mo * 100)} % target. Recommended: {_s(recomendado)} "
                f"(average cost {_s(costo)})."),
            "mantener": (
                f"El precio actual ({_s(_q2(pa))}) está dentro de "
                f"±{_s(tol_pct)} % del recomendado ({_s(recomendado)}); "
                f"el margen actual ({_q2(margen_actual)} %) cumple el "
                f"objetivo de {_q2(mo * 100)} %.",
                f"Current price ({_s(_q2(pa))}) is within ±{_s(tol_pct)} % of "
                f"the recommended ({_s(recomendado)}); the current margin "
                f"({_q2(margen_actual)} %) meets the {_q2(mo * 100)} % "
                "target."),
            "bajar": (
                f"El precio actual ({_s(_q2(pa))}) supera el recomendado "
                f"({_s(recomendado)}) en más de {_s(tol_pct)} %; bajar a "
                f"{_s(recomendado)} mantiene el margen objetivo de "
                f"{_q2(mo * 100)} % con el costo promedio ({_s(costo)}).",
                f"Current price ({_s(_q2(pa))}) exceeds the recommended "
                f"({_s(recomendado)}) by more than {_s(tol_pct)} %; lowering "
                f"to {_s(recomendado)} keeps the {_q2(mo * 100)} % target "
                f"margin at average cost ({_s(costo)})."),
        }
        es, en = explicaciones[veredicto]
        base.update({
            "estado": "recomendado",
            "veredicto": veredicto,
            "resultados": {
                "precio_recomendado": _s(recomendado),
                "precio_crudo_formula": _s(_q2(crudo)),
                "piso_costo": _s(_q2(piso)),
                "piso_aplicado": recomendado == _q2(piso),
                "margen_actual_pct": _s(_q2(margen_actual)),
                "margen_objetivo_pct": _s(_q2(mo * 100)),
                "margen_efectivo_recomendado_pct": _s(_q2(margen_efectivo)),
                "brecha_precio": _s(_q2(brecha)),
                "sensibilidad": sens,
            },
            "explicacion_es": es,
            "explicacion_en": en,
            **_docs(),
        })
        return base

    def _sensibilidad(self, piso: Decimal, recomendado: Decimal,
                      pa: Decimal, costo: Decimal,
                      demanda: Decimal) -> list[dict]:
        """Tabla precio vs margen en 3-4 puntos. Sin elasticidad: la
        demanda se toma CONSTANTE (supuesto explícito) para mostrar el
        margen total que ese punto daría a demanda histórica."""
        puntos = {piso, recomendado, recomendado * Decimal("1.10"), pa}
        tabla = []
        for p in sorted(puntos):
            p = _q2(p)
            mu = p - costo
            mp = _margen_pct(p, costo)
            uds_30 = demanda * Decimal("30")
            tabla.append({
                "precio": _s(p),
                "margen_unitario": _s(_q2(mu)),
                "margen_pct": _s(_q2(mp)),
                "margen_total_proyectado_30d": _s(_q2(uds_30 * mu)),
            })
        return tabla

    # ---- entrada principal ----------------------------------------------
    def recomendar(self, producto_ids: list | None = None,
                   almacen_id: str | None = None,
                   moneda: str = "DOP",
                   margen_objetivo: Decimal | str = MARGEN_OBJETIVO_DEF,
                   margen_minimo: Decimal | str = MARGEN_MINIMO_DEF,
                   tolerancia: Decimal | str = TOLERANCIA_DEF,
                   ventana_demanda_dias: int = 30,
                   hoy: str | None = None,
                   lang: str = "es") -> dict:
        """Recomendaciones para los productos de la empresa."""
        moneda, mo, mm, tol = self._validar(
            moneda, margen_objetivo, margen_minimo, tolerancia,
            ventana_demanda_dias)
        hoy = _hoy(hoy)
        items = []
        for (pid, alm) in self._pares(producto_ids, almacen_id, moneda):
            demanda = self.sim._demanda_diaria(pid, alm,
                                               ventana_demanda_dias, hoy)
            items.append(self._recomendar_uno(
                pid, alm, moneda, mo, mm, tol, demanda, hoy, lang))
        rec = sum(1 for i in items if i["estado"] == "recomendado")
        return {
            "company_id": self.company_id,
            "insumos": {
                "moneda": moneda,
                "margen_objetivo": _s(mo),
                "margen_minimo": _s(mm),
                "tolerancia_veredicto": _s(tol),
                "ventana_demanda_dias": ventana_demanda_dias,
                "hoy": hoy,
                "productos_evaluados": len(items),
            },
            "resumen": {
                "total": len(items),
                "recomendados": rec,
                "datos_insuficientes": len(items) - rec,
            },
            "resultados": items,
            "formulas": {
                "precio_crudo": "costo_promedio / (1 − margen_objetivo)",
                "piso": "costo_promedio × (1 + margen_minimo)",
                "precio_recomendado": "max(precio_crudo, piso), redondeado "
                                      "a 2 decimales",
                "margen_pct": "(precio − costo_promedio) / precio × 100",
                "brecha": "precio_recomendado − precio_actual",
                "veredicto": "subir si precio_actual < recomendado×(1−"
                             "tolerancia); bajar si > recomendado×"
                             "(1+tolerancia); mantener en la banda",
                "precio_actual": "promedio ponderado por cantidad del "
                                 "precio_unitario en ventas registradas "
                                 "(misma moneda/almacén)",
                "margen_total_30d":
                    "demanda_diaria × 30 × margen_unitario (demanda "
                    "constante, sin elasticidad)",
            },
            "supuestos": [
                "El costo se mantiene = costo promedio registrado hoy; "
                "no se modelan futuras compras.",
                "La sensibilidad NO inventa elasticidad precio-demanda: "
                "la demanda se toma constante para comparar márgenes; "
                "los escenarios de demanda parametrizables están en "
                "simulator.simular_precio.",
                "Demanda histórica = uds vendidas últimos N días / N; "
                "notas de crédito no descuentan demanda; ventas sin fecha "
                "se excluyen.",
                "Sin costo confiable o sin ventas registradas no se "
                "recomienda precio: estado 'datos_insuficientes'.",
            ],
        }


def _docs() -> dict:
    """Formulas/supuestos por producto (mismo patrón que el simulador)."""
    return {
        "formulas": {
            "precio_crudo": "costo_promedio / (1 − margen_objetivo)",
            "piso": "costo_promedio × (1 + margen_minimo)",
            "precio_recomendado": "max(precio_crudo, piso)",
        },
        "supuestos": [
            "Costo = costo promedio ponderado registrado hoy.",
            "Precio actual = promedio ponderado por cantidad del "
            "precio_unitario en ventas (misma moneda/almacén).",
            "El piso garantiza que el recomendado nunca quede bajo el "
            "costo.",
        ],
    }


def _hoy(hoy: str | None) -> str:
    from datetime import datetime, timezone, date
    if hoy is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        return date.fromisoformat(hoy[:10]).isoformat()
    except ValueError:
        raise ErrorAdvisor(f"hoy inválido (se espera YYYY-MM-DD): {hoy!r}")
