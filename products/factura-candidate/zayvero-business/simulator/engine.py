"""EXTRA 2 (sprint 12-oct-2026) — Motor del Simulator.

Cálculo puro y COMPROBABLE sobre el estado real de una empresa:
- simular_compra: impacto en stock, costo promedio, capital
  inmovilizado y días de inventario.
- simular_precio: margen unitario y margen total proyectado bajo
  escenarios parametrizables (optimista/base/pesimista). NO inventa
  elasticidades: los multiplicadores son parámetros explícitos.
- simular_quiebre: días para agotar cada producto al ritmo de ventas
  actual y qué compra lo evitaría.
- rentabilidad: margen bruto por producto/línea con los costos
  REALES registrados (COGS al promedio ponderado vigente).

GARANTÍAS:
- Solo lectura: usa `stock()`, `stock_empresa()`,
  `costo_promedio()`, `unidades_con_costo()` y la lectura de
  `libro._ventas` (mismo patrón que `os_finanzas/profit.py::
  evaluar_margenes`). Jamás llama a registrar/aplicar/auditar.
- Aislamiento: el Simulador se liga a UN bundle (una empresa); no
  recibe ni toca datos de otra.
- Todo resultado trae `insumos` (valores usados), `formulas`
  (cómo se calculó) y `supuestos` (qué se asumió y por qué).
- Decimales exactos; la serialización a string conserva precisión.
"""
from __future__ import annotations

import math
import os
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from oschema.contract import require_iso_currency  # noqa: E402

# BLOQUE 5 (2026-10-12) — Catálogo maestro: los lectores de costo/precio
# prefieren el dato vigente del catálogo cuando existe; si el catálogo
# está vacío/ausente, `catalogo.integracion` devuelve None y se usa la
# derivación histórica de siempre. Ver docs/catalogo.md.
try:
    from catalogo import integracion as _cat  # noqa: E402
except Exception:  # pragma: no cover - el catálogo siempre existe en el repo
    _cat = None


class ErrorSimulador(Exception):
    """Error de validación del escenario (→ 400 en la API)."""


def _hoy_iso(hoy: str | None) -> str:
    if hoy is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        return date.fromisoformat(hoy[:10]).isoformat()
    except ValueError:
        raise ErrorSimulador(f"hoy inválido (se espera YYYY-MM-DD): {hoy!r}")


def _s(x: Decimal) -> str:
    """Serializa Decimal sin perder precisión."""
    return str(x)


class Simulador:
    """Simulador ligado al OSBundle de UNA empresa (solo lectura)."""

    def __init__(self, bundle):
        if not getattr(bundle, "company_id", None):
            raise ErrorSimulador("se requiere un bundle con company_id")
        self.bundle = bundle
        self.company_id = bundle.company_id

    # ---- lecturas del estado real ------------------------------------
    def _stock(self, producto_id: str, almacen_id: str) -> Decimal:
        return self.bundle.inventario.stock(producto_id, almacen_id)

    def _costo_prom(self, producto_id: str, almacen_id: str,
                    moneda: str) -> Decimal:
        # BLOQUE 5: el catálogo maestro es la fuente de verdad del costo
        # de referencia cuando tiene dato vigente; si no, el promedio
        # ponderado registrado (CostoInventario) como siempre.
        if _cat is not None:
            cc = _cat.costo_catalogo(self.company_id, producto_id, moneda)
            if cc is not None:
                return cc
        return _dec(self.bundle.costo.costo_promedio(
            producto_id, almacen_id, moneda))

    def _und_costo(self, producto_id: str, almacen_id: str,
                   moneda: str) -> Decimal:
        return _dec(self.bundle.costo.unidades_con_costo(
            producto_id, almacen_id, moneda))

    def _fuente_costo(self, producto_id: str, moneda: str) -> str:
        """BLOQUE 6 (sprint 3) — trazabilidad: de dónde salió el costo
        usado ("catalogo" = vigente del catálogo maestro;
        "promedio_registrado" = derivación histórica)."""
        if _cat is not None:
            try:
                if _cat.detalle_costo_catalogo(
                        self.company_id, producto_id, moneda) is not None:
                    return "catalogo"
            except Exception:
                pass
        return "promedio_registrado"

    def _fuente_precio(self, producto_id: str, moneda: str) -> str:
        """Trazabilidad del precio de referencia (ver _fuente_costo)."""
        if _cat is not None:
            try:
                if _cat.detalle_precio_catalogo(
                        self.company_id, producto_id, moneda) is not None:
                    return "catalogo"
            except Exception:
                pass
        return "promedio_ventas"

    def _ventas(self):
        """Ventas financieras registradas (lectura; mismo patrón que
        profit.evaluar_margenes)."""
        return list(self.bundle.libro._ventas.values())

    def _demanda_diaria(self, producto_id: str, almacen_id: str,
                        ventana_dias: int, hoy: str) -> Decimal:
        """Ritmo de ventas: uds vendidas en la ventana / ventana_días.

        Solo ventas con fecha dentro de [hoy-ventana, hoy]. Las ventas
        sin fecha no se pueden ubicar en el tiempo y se excluyen
        (supuesto explícito). Las notas de crédito NO descuentan
        demanda: una devolución no reduce la demanda futura esperada.
        """
        if ventana_dias <= 0:
            raise ErrorSimulador("ventana_dias debe ser > 0")
        ini = (date.fromisoformat(hoy) - timedelta(days=ventana_dias)
               ).isoformat()
        tot = Decimal("0")
        for v in self._ventas():
            f = (v.fecha or "")[:10]
            if not f or f < ini or f > hoy:
                continue
            for lin in v.lineas:
                if (lin.producto_id == producto_id
                        and (lin.almacen_id or "principal") == almacen_id):
                    tot += abs(_dec(lin.cantidad))
        return tot / Decimal(ventana_dias)

    def _precio_referencia(self, producto_id: str, almacen_id: str,
                           moneda: str) -> Decimal | None:
        """Precio de venta 'actual'.

        BLOQUE 5: primero el precio vigente del catálogo maestro (si
        existe); si no, el promedio ponderado por cantidad del
        precio_unitario registrado en ventas (misma moneda y almacén).
        None si el producto nunca se vendió y no hay precio en catálogo:
        no se inventa precio."""
        if _cat is not None:
            pc = _cat.precio_catalogo(self.company_id, producto_id, moneda)
            if pc is not None:
                return pc
        cant, val = Decimal("0"), Decimal("0")
        for v in self._ventas():
            if v.moneda != moneda:
                continue
            for lin in v.lineas:
                if (lin.producto_id == producto_id
                        and (lin.almacen_id or "principal") == almacen_id):
                    c = abs(_dec(lin.cantidad))
                    cant += c
                    val += c * _dec(lin.precio_unitario)
        return (val / cant) if cant > 0 else None

    def _unidades_vendidas(self, producto_id: str, moneda: str,
                           desde: str | None, hasta: str | None
                           ) -> tuple[Decimal, Decimal | None]:
        """(unidades, precio_promedio) en el período y moneda dados."""
        cant, val = Decimal("0"), Decimal("0")
        for v in self._ventas():
            if v.moneda != moneda:
                continue
            f = (v.fecha or "")[:10]
            if desde and f < desde:
                continue
            if hasta and f > hasta:
                continue
            for lin in v.lineas:
                if lin.producto_id == producto_id:
                    c = abs(_dec(lin.cantidad))
                    cant += c
                    val += c * _dec(lin.precio_unitario)
        return cant, (val / cant if cant > 0 else None)

    def _ficha_proveedor(self, proveedor_id: str):
        """Historial real del proveedor (Lote 4); None si no existe."""
        from os_compras.proveedores import ficha_proveedor
        ordenes = self.bundle.compras.listar_ordenes()
        recepciones = list(
            getattr(self.bundle.compras, "_recepciones", {}).values())
        ficha = ficha_proveedor(proveedor_id, self.company_id, ordenes,
                                recepciones)
        return ficha if ficha.total_ordenes > 0 else None

    # ---- escenario 1: compra ------------------------------------------
    def simular_compra(self, proveedor_id: str, almacen_id: str = "principal",
                       lineas: list | None = None, moneda: str = "DOP",
                       ventana_demanda_dias: int = 30,
                       hoy: str | None = None) -> dict:
        """Simula una compra sin tocar el estado real.

        Por línea: stock y costo promedio ANTES/DESPUÉS, total de la
        línea, delta de capital inmovilizado y días de inventario.
        """
        require_iso_currency(moneda)
        hoy = _hoy_iso(hoy)
        if not proveedor_id:
            raise ErrorSimulador("proveedor_id es obligatorio")
        if not lineas:
            raise ErrorSimulador("la compra necesita al menos una línea")
        ficha = self._ficha_proveedor(proveedor_id)
        ultimos = {}
        if ficha:
            for p in ficha.productos:
                ultimos[p["producto_id"]] = p.get("ultimo_precio")

        items, total_compra, delta_capital = [], Decimal("0"), Decimal("0")
        for i, lin in enumerate(lineas):
            try:
                pid = str(lin["producto_id"])
                q = _dec(lin["cantidad"])
                pu = _dec(lin["precio_unitario"])
            except (KeyError, TypeError, ValueError) as e:
                raise ErrorSimulador(f"línea {i} inválida: {e}")
            if q <= 0:
                raise ErrorSimulador(f"línea {i}: cantidad debe ser > 0")
            if pu < 0:
                raise ErrorSimulador(
                    f"línea {i}: precio_unitario no puede ser negativo")

            stock_antes = self._stock(pid, almacen_id)
            prom_antes = self._costo_prom(pid, almacen_id, moneda)
            und_antes = self._und_costo(pid, almacen_id, moneda)
            demanda = self._demanda_diaria(pid, almacen_id,
                                           ventana_demanda_dias, hoy)

            # Promedio ponderado: MISMA fórmula que
            # os_finanzas/costo.py::CostoInventario.aplicar_entrada.
            prom_despues = ((und_antes * prom_antes + q * pu)
                            / (und_antes + q))
            stock_despues = stock_antes + q
            total_linea = q * pu
            valor_antes = stock_antes * prom_antes
            valor_despues = stock_despues * prom_despues
            delta = valor_despues - valor_antes
            dias = (stock_despues / demanda) if demanda > 0 else None

            total_compra += total_linea
            delta_capital += delta
            items.append({
                "producto_id": pid,
                "almacen_id": almacen_id,
                "insumos": {
                    "cantidad_simulada": _s(q),
                    "precio_unitario_simulado": _s(pu),
                    "moneda": moneda,
                    "stock_antes": _s(stock_antes),
                    "costo_promedio_antes": _s(prom_antes),
                    "fuente_costo": self._fuente_costo(pid, moneda),
                    "unidades_con_costo_antes": _s(und_antes),
                    "ultimo_precio_a_este_proveedor":
                        ultimos.get(pid),  # None = sin historial
                    "demanda_diaria_uds": _s(demanda),
                    "ventana_demanda_dias": ventana_demanda_dias,
                },
                "resultados": {
                    "total_linea": _s(total_linea),
                    "stock_despues": _s(stock_despues),
                    "costo_promedio_despues": _s(prom_despues),
                    "valor_inventario_antes": _s(valor_antes),
                    "valor_inventario_despues": _s(valor_despues),
                    "delta_capital_inmovilizado": _s(delta),
                    "dias_inventario_despues":
                        (_s(dias) if dias is not None else None),
                },
                "formulas": {
                    "costo_promedio_despues":
                        "(und_antes × prom_antes + q × pu) / (und_antes + q)"
                        " — promedio ponderado, idéntico a CostoInventario",
                    "stock_despues": "stock_antes + q",
                    "valor_inventario":
                        "stock_físico × costo_promedio (misma moneda)",
                    "delta_capital_inmovilizado":
                        "valor_despues − valor_antes",
                    "dias_inventario":
                        "stock_despues / demanda_diaria; demanda_diaria = "
                        "uds vendidas últimos N días / N",
                },
            })
        return {
            "tipo": "compra",
            "company_id": self.company_id,
            "insumos": {
                "proveedor_id": proveedor_id,
                "almacen_id": almacen_id,
                "moneda": moneda,
                "n_lineas": len(items),
                "proveedor_con_historial": ficha is not None,
            },
            "resultados": {
                "total_compra": _s(total_compra),
                "delta_capital_inmovilizado_total": _s(delta_capital),
                "lineas": items,
            },
            "formulas": {
                "total_compra": "Σ (cantidad × precio_unitario)",
                "delta_capital_inmovilizado_total":
                    "Σ delta_capital_inmovilizado por línea",
            },
            "supuestos": [
                "La compra se recibe completa y de inmediato en el "
                "almacén indicado (sin mermas ni fletes).",
                "El costo promedio se recalcula con promedio ponderado, "
                "el mismo método que usa el registro real.",
                "Costos en una sola moneda: no hay conversión; costos "
                "registrados en otra moneda no se mezclan.",
                "Días de inventario supone demanda futura = demanda "
                "histórica (sin estacionalidad modelada).",
            ],
        }

    # ---- escenario 2: cambio de precio ---------------------------------
    def simular_precio(self, producto_id: str, nuevo_precio: str,
                       almacen_id: str = "principal", moneda: str = "DOP",
                       horizonte_dias: int = 30,
                       ventana_demanda_dias: int = 30,
                       escenarios: dict | None = None,
                       hoy: str | None = None) -> dict:
        """Simula un cambio de precio de venta.

        NO inventa elasticidad: cada escenario aplica un multiplicador
        EXPLÍCITO (parametrizable) sobre la demanda histórica.
        """
        require_iso_currency(moneda)
        hoy = _hoy_iso(hoy)
        if not producto_id:
            raise ErrorSimulador("producto_id es obligatorio")
        nuevo = _dec(nuevo_precio)
        if nuevo <= 0:
            raise ErrorSimulador("el nuevo precio debe ser > 0")
        if horizonte_dias <= 0:
            raise ErrorSimulador("horizonte_dias debe ser > 0")
        esc = escenarios or {"optimista": "1.15", "base": "1.0",
                             "pesimista": "0.85"}
        mults = {}
        for nombre, m in esc.items():
            dm = _dec(m)
            if dm < 0:
                raise ErrorSimulador(
                    f"escenario {nombre!r}: multiplicador no puede ser "
                    "negativo")
            mults[str(nombre)] = dm

        costo = self._costo_prom(producto_id, almacen_id, moneda)
        precio_ref = self._precio_referencia(producto_id, almacen_id, moneda)
        demanda = self._demanda_diaria(producto_id, almacen_id,
                                       ventana_demanda_dias, hoy)

        margen_u_nuevo = nuevo - costo
        margen_pct_nuevo = (round(margen_u_nuevo / nuevo * 100, 1)
                            if nuevo else None)
        margen_u_actual = ((precio_ref - costo) if precio_ref is not None
                           else None)

        por_escenario = {}
        for nombre, dm in mults.items():
            dem_proy = demanda * dm * Decimal(horizonte_dias)
            por_escenario[nombre] = {
                "multiplicador_demanda": _s(dm),
                "demanda_proyectada_uds": _s(dem_proy),
                "ingreso_proyectado": _s(dem_proy * nuevo),
                "margen_total_proyectado": _s(dem_proy * margen_u_nuevo),
            }
        return {
            "tipo": "precio",
            "company_id": self.company_id,
            "insumos": {
                "producto_id": producto_id,
                "almacen_id": almacen_id,
                "moneda": moneda,
                "precio_nuevo": _s(nuevo),
                "costo_promedio_actual": _s(costo),
                "fuente_costo": self._fuente_costo(producto_id, moneda),
                "precio_referencia_actual": (
                    _s(precio_ref) if precio_ref is not None else None),
                "fuente_precio_referencia": self._fuente_precio(
                    producto_id, moneda),
                "demanda_diaria_uds": _s(demanda),
                "ventana_demanda_dias": ventana_demanda_dias,
                "horizonte_dias": horizonte_dias,
            },
            "resultados": {
                "margen_unitario_actual": (
                    _s(margen_u_actual) if margen_u_actual is not None
                    else None),
                "margen_unitario_nuevo": _s(margen_u_nuevo),
                "margen_pct_nuevo": (
                    _s(margen_pct_nuevo) if margen_pct_nuevo is not None
                    else None),
                "alerta_bajo_costo": nuevo < costo,
                "escenarios": por_escenario,
            },
            "formulas": {
                "margen_unitario": "precio − costo_promedio_actual",
                "margen_pct": "margen_unitario / precio × 100",
                "demanda_proyectada":
                    "demanda_diaria × multiplicador_escenario × "
                    "horizonte_dias",
                "margen_total_proyectado":
                    "demanda_proyectada × margen_unitario_nuevo",
                "precio_referencia_actual":
                    "promedio ponderado por cantidad del precio_unitario "
                    "en ventas registradas (misma moneda/almacén)",
            },
            "supuestos": [
                "SIN elasticidad precio-demanda inventada: los "
                "escenarios solo escalan la demanda histórica con "
                "multiplicadores explícitos y parametrizables.",
                "Demanda histórica = uds vendidas últimos N días / N; "
                "notas de crédito no descuentan demanda.",
                "El costo se mantiene = costo promedio registrado hoy; "
                "no se modelan futuras compras.",
                "Si el producto nunca se vendió, no hay precio de "
                "referencia actual (se reporta null, no se inventa).",
            ],
        }

    # ---- escenario 3: quiebre ------------------------------------------
    def simular_quiebre(self, almacen_id: str | None = None,
                        moneda: str = "DOP", horizonte_dias: int = 30,
                        dias_cobertura_objetivo: int = 30,
                        ventana_demanda_dias: int = 30,
                        hoy: str | None = None) -> dict:
        """Proyecta el agotamiento de cada producto al ritmo de ventas
        actual y sugiere la compra que lo evitaría."""
        require_iso_currency(moneda)
        hoy = _hoy_iso(hoy)
        if horizonte_dias <= 0 or dias_cobertura_objetivo <= 0:
            raise ErrorSimulador(
                "horizonte_dias y dias_cobertura_objetivo deben ser > 0")

        pares = sorted(self.bundle.inventario.stock_empresa().items())
        items, en_riesgo, inversion_total = [], 0, Decimal("0")
        for (pid, alm), stock in pares:
            if almacen_id is not None and alm != almacen_id:
                continue
            demanda = self._demanda_diaria(pid, alm, ventana_demanda_dias,
                                           hoy)
            costo = self._costo_prom(pid, alm, moneda)
            if demanda > 0:
                dias = (Decimal("0") if stock <= 0 else stock / demanda)
                estado = ("agotado" if stock <= 0
                          else "en_riesgo" if dias < horizonte_dias
                          else "ok")
            else:
                dias, estado = None, "sin_demanda"
            sugerida = None
            if estado in ("agotado", "en_riesgo"):
                en_riesgo += 1
                cant = math.ceil(
                    float(demanda * dias_cobertura_objetivo - stock))
                cant = max(cant, 0)
                inversion = Decimal(cant) * costo
                inversion_total += inversion
                sugerida = {
                    "cantidad": str(cant),
                    "costo_unitario_referencia": _s(costo),
                    "inversion_estimada": _s(inversion),
                }
            items.append({
                "producto_id": pid,
                "almacen_id": alm,
                "insumos": {
                    "stock_actual": _s(stock),
                    "demanda_diaria_uds": _s(demanda),
                    "ventana_demanda_dias": ventana_demanda_dias,
                    "costo_promedio_actual": _s(costo),
                    "fuente_costo": self._fuente_costo(pid, moneda),
                    "moneda": moneda,
                },
                "resultados": {
                    "dias_para_quiebre": (
                        _s(dias) if dias is not None else None),
                    "estado": estado,
                    "compra_sugerida": sugerida,
                },
                "formulas": {
                    "dias_para_quiebre":
                        "stock_actual / demanda_diaria (0 si stock ≤ 0; "
                        "null sin demanda registrada)",
                    "cantidad_sugerida":
                        "ceil(demanda_diaria × dias_cobertura_objetivo "
                        "− stock_actual), mínimo 0",
                    "inversion_estimada":
                        "cantidad_sugerida × costo_promedio_actual",
                },
            })
        return {
            "tipo": "quiebre",
            "company_id": self.company_id,
            "insumos": {
                "almacen_id": almacen_id,  # None = todos
                "moneda": moneda,
                "horizonte_dias": horizonte_dias,
                "dias_cobertura_objetivo": dias_cobertura_objetivo,
                "ventana_demanda_dias": ventana_demanda_dias,
                "hoy": hoy,
            },
            "resultados": {
                "productos": items,
                "productos_en_riesgo": en_riesgo,
                "inversion_total_sugerida": _s(inversion_total),
            },
            "formulas": {
                "productos_en_riesgo":
                    "conteo con estado agotado o en_riesgo "
                    "(dias_para_quiebre < horizonte_dias)",
            },
            "supuestos": [
                "Demanda futura constante = promedio histórico; sin "
                "estacionalidad ni tendencia.",
                "La compra sugerida se recibe de inmediato (lead time "
                "del proveedor no modelado).",
                "Sin demanda registrada no se puede proyectar quiebre "
                "(estado sin_demanda, sin sugerencia).",
                "La inversión se valora al costo promedio actual, no a "
                "un precio futuro de proveedor.",
            ],
        }

    # ---- escenario 4: rentabilidad --------------------------------------
    def rentabilidad(self, producto_ids: list | None = None,
                     moneda: str = "DOP", desde: str | None = None,
                     hasta: str | None = None,
                     mapa_lineas: dict | None = None) -> dict:
        """Margen bruto por producto (y por línea si se provee el mapa)
        con los costos REALES registrados.

        Reutiliza `LibroFinanzas.rentabilidad(producto_id=...)`: el COGS
        es el registrado venta por venta al promedio vigente (incluye
        notas de crédito como reversión, igual que el P&L oficial).
        """
        require_iso_currency(moneda)
        libro = self.bundle.libro
        if producto_ids is None:
            producto_ids = sorted(
                {lin.producto_id
                 for v in self._ventas() if v.moneda == moneda
                 for lin in v.lineas})
        items = []
        n_ventas = 0
        for pid in producto_ids:
            r = libro.rentabilidad(desde=desde, hasta=hasta,
                                   producto_id=pid).get(moneda)
            ing = _dec(r["ingresos"]) if r else Decimal("0")
            cogs = _dec(r["cogs"]) if r else Decimal("0")
            bruta = ing - cogs
            # Redondeo a 1 decimal: misma convención que
            # LibroFinanzas.rentabilidad().
            pct = round(bruta / ing * 100, 1) if ing else None
            uds, precio_prom = self._unidades_vendidas(pid, moneda, desde,
                                                      hasta)
            n_ventas += 1 if uds > 0 else 0
            items.append({
                "producto_id": pid,
                "insumos": {
                    "moneda": moneda,
                    "desde": desde,
                    "hasta": hasta,
                    "unidades_vendidas": _s(uds),
                    "precio_promedio_venta": (
                        _s(precio_prom) if precio_prom is not None
                        else None),
                    "costo_promedio_actual": _s(self._costo_prom(
                        pid, "principal", moneda)),
                    "fuente_costo": self._fuente_costo(pid, moneda),
                },
                "resultados": {
                    "ingresos": _s(ing),
                    "cogs": _s(cogs),
                    "margen_bruto": _s(bruta),
                    "margen_bruto_pct": _s(pct) if pct is not None else None,
                },
                "formulas": {
                    "ingresos": "Σ cantidad × precio_unitario de líneas "
                                "vendidas (período/moneda)",
                    "cogs": "prorrateo del COGS registrado por venta, "
                            "igual que LibroFinanzas.rentabilidad()",
                    "margen_bruto": "ingresos − cogs",
                    "margen_bruto_pct": "margen_bruto / ingresos × 100",
                },
            })
        # BLOQUE 5: sin mapa explícito, las categorías del catálogo
        # maestro sirven como líneas (solo si el catálogo tiene datos;
        # si no, comportamiento anterior: por_linea = None).
        if mapa_lineas is None and _cat is not None:
            mapa_lineas = _cat.mapa_categorias(self.company_id) or None
        por_linea = None
        if mapa_lineas:
            agg: dict[str, dict[str, Decimal]] = {}
            for it in items:
                lin = mapa_lineas.get(it["producto_id"], "sin_línea")
                a = agg.setdefault(lin, {"ing": Decimal("0"),
                                         "cogs": Decimal("0")})
                a["ing"] += _dec(it["resultados"]["ingresos"])
                a["cogs"] += _dec(it["resultados"]["cogs"])
            por_linea = []
            for lin, a in sorted(agg.items()):
                bruta = a["ing"] - a["cogs"]
                por_linea.append({
                    "linea": lin,
                    "ingresos": _s(a["ing"]),
                    "cogs": _s(a["cogs"]),
                    "margen_bruto": _s(bruta),
                    "margen_bruto_pct": _s(round(bruta / a["ing"] * 100, 1))
                    if a["ing"] else None,
                })
        return {
            "tipo": "rentabilidad",
            "company_id": self.company_id,
            "insumos": {
                "moneda": moneda,
                "desde": desde,
                "hasta": hasta,
                "n_productos": len(items),
                "n_productos_con_ventas": n_ventas,
                "agrupado_por_linea": mapa_lineas is not None,
            },
            "resultados": {
                "productos": items,
                "por_linea": por_linea,
            },
            "formulas": {
                "por_linea": "suma de ingresos/cogs de sus productos",
            },
            "supuestos": [
                "Costos 100% reales: COGS registrado al costo promedio "
                "vigente en cada venta (sin revalorizaciones).",
                "Las notas de crédito revierten ingreso y COGS, igual "
                "que en el P&L oficial.",
                "No hay mapa producto→línea explícito: se usan las "
                "categorías del catálogo maestro si tiene datos; si no, "
                "el mapa lo provee quien llama (explícito).",
                "Monedas no se mezclan: cada moneda se reporta por "
                "separado (aquí, la indicada).",
            ],
        }
