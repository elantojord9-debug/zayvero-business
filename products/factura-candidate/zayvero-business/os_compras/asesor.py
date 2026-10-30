"""Purchase Advisor: propuestas de compra completas.

Integra:
- Stock Predictor (`os_inventory.predictor`): solo productos con
  suficiencia="suficiente" generan propuesta; con "datos_insuficientes"
  o "sin_demanda" se reporta sin propuesta (nunca se inventa).
- Catálogo maestro: nombre/sku del producto si el engine lo tiene
  (`engine.ficha_producto`); precio de venta vigente para el margen.
- Supplier Intelligence: comparación de proveedores por producto
  (precio, cumplimiento, puntualidad) y proveedor recomendado.
- Órdenes abiertas: lo ya pedido y no recibido (en tránsito) se descuenta.
- Tesorería: impacto en caja — costo total vs saldo por moneda.
- Rentabilidad: margen unitario estimado (precio venta catálogo −
  precio compra recomendado); alerta si la compra deja margen negativo.

Propuesta por producto:
  necesidad = tasa_diaria × (lead_time + cobertura_objetivo + seguridad)
              − stock_actual − en_transito
  cantidad_recomendada = ceil(max(0, necesidad)); 0 -> sin propuesta.

El lead_time por producto sale del histórico del proveedor
(retraso_promedio → lead_time_origen="proveedor"); si no hay dato,
parámetro con origen "defecto" y supuesto explícito.
"""
from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from os_inventory.predictor import predecir  # noqa: E402
from .ordenes import EstadoOC  # noqa: E402
from .proveedores import ficha_proveedor  # noqa: E402
from .inteligencia import listar_proveedores  # noqa: E402


def _en_transito(compras, producto_id: str) -> Decimal:
    """Ordenado − recibido en OCs abiertas (enviada/parcial)."""
    tot = Decimal("0")
    for o in compras.listar_ordenes():
        if o.estado not in (EstadoOC.ENVIADA, EstadoOC.PARCIAL):
            continue
        ord_c = o.cantidad_ordenada(producto_id)
        if ord_c > 0:
            rec = compras.recibido_acumulado(o.compra_id, producto_id)
            tot += max(ord_c - rec, Decimal("0"))
    return tot


def _comparar_proveedores(ordenes, recepciones, company_id,
                          producto_id: str) -> list[dict]:
    """Proveedores que han surtido el producto, comparados."""
    filas = []
    for pid in listar_proveedores(ordenes, company_id):
        ficha = ficha_proveedor(pid, company_id, ordenes, recepciones)
        prod = next((p for p in ficha.productos
                     if p["producto_id"] == producto_id), None)
        if prod is None:
            continue
        filas.append({
            "proveedor_id": pid,
            "ultimo_precio": prod["ultimo_precio"],
            "precio_promedio": prod["precio_promedio"],
            "precio_min": prod["precio_min"],
            "veces": prod["veces"],
            "tasa_cumplimiento": ficha.tasa_cumplimiento,
            "retraso_promedio_dias": ficha.retraso_promedio_dias,
            "ordenes_totales": ficha.total_ordenes,
        })
    filas.sort(key=lambda f: _dec(f["ultimo_precio"]))
    return filas


def asesorar(bundle, cobertura_objetivo_dias: int = 30,
             dias_seguridad: int = 7, min_dias: int = 30,
             dias_ventana: int = 90, margen_minimo_pct: str = "15",
             hoy: str | None = None,
             producto_id: str | None = None) -> dict:
    company = bundle.inventario.company_id
    ords = bundle.compras.listar_ordenes()
    recs = []
    for o in ords:
        recs.extend(bundle.compras.recepciones_de(o.compra_id))

    # Lead time por producto desde el histórico del proveedor más usado.
    lt_por_prod: dict[str, int] = {}
    for pid in listar_proveedores(ords, company):
        ficha = ficha_proveedor(pid, company, ords, recs)
        if ficha.retraso_promedio_dias is not None:
            # Conservador: el peor retraso observado manda.
            lt = max(1, int(round(float(ficha.retraso_promedio_dias))))
            for p in ficha.productos:
                pid2 = p["producto_id"]
                lt_por_prod[pid2] = max(lt_por_prod.get(pid2, 0), lt)

    prs = predecir(bundle.inventario, dias=dias_ventana, min_dias=min_dias,
                   dias_seguridad=dias_seguridad,
                   lead_time_por_producto=lt_por_prod or None,
                   hoy=hoy, producto_id=producto_id)
    propuestas, sin_propuesta = [], []
    for pr in prs:
        ficha_cat = bundle.inventario.ficha_producto(pr.producto_id) or {}
        base = {"producto_id": pr.producto_id,
                "nombre": ficha_cat.get("nombre", ""),
                "sku": ficha_cat.get("sku", ""),
                "almacen_id": pr.almacen_id,
                "stock_actual": pr.stock_actual,
                "suficiencia": pr.suficiencia,
                "riesgo": pr.riesgo}
        if pr.suficiencia != "suficiente":
            sin_propuesta.append({
                **base,
                "motivo": ("sin demanda observada" if pr.suficiencia
                           == "sin_demanda"
                           else "histórico insuficiente: no se propone "
                                "compra sin datos")})
            continue
        tasa = _dec(pr.tasa_diaria)
        transito = _en_transito(bundle.compras, pr.producto_id)
        necesidad = (tasa * (pr.lead_time_dias + cobertura_objetivo_dias
                             + dias_seguridad)
                     - _dec(pr.stock_actual) - transito)
        cantidad = max(0, math.ceil(float(necesidad)))
        comp = _comparar_proveedores(ords, recs, company, pr.producto_id)
        if cantidad <= 0:
            sin_propuesta.append({**base, "motivo": "cobertura suficiente",
                                  "en_transito": str(transito)})
            continue
        if not comp:
            sin_propuesta.append({
                **base, "motivo": "sin proveedores registrados para este "
                                  "producto: dar de alta proveedor antes de "
                                  "proponer",
                "cantidad_sugerida": str(cantidad),
                "en_transito": str(transito)})
            continue
        # Recomendado: mejor equilibrio precio/cumplimiento entre los que
        # tienen muestra (>=2 órdenes); si no, el más barato con advertencia.
        con_muestra = [c for c in comp if c["ordenes_totales"] >= 2]
        candidatos = con_muestra or comp
        mejor = min(candidatos,
                    key=lambda c: (float(c["ultimo_precio"])
                                   / max(float(c["tasa_cumplimiento"] or 0.01),
                                         0.01)))
        precio_u = _dec(mejor["ultimo_precio"])
        moneda = next((o.moneda for o in ords
                       if o.proveedor_id == mejor["proveedor_id"]),
                      "DOP")
        costo_total = precio_u * cantidad
        saldo_caja = bundle.tesoreria.saldo(moneda)
        cabe = _dec(saldo_caja) >= costo_total
        # Margen vs precio de venta del catálogo (si existe)
        margen = None
        alerta_margen = None
        pv = ficha_cat.get("precio_venta") or ficha_cat.get("precio")
        try:
            if pv is not None:
                margen_u = _dec(str(pv)) - precio_u
                margen_pct = (margen_u / _dec(str(pv)) * 100
                              if _dec(str(pv)) > 0 else Decimal("0"))
                margen = {"precio_venta": str(_dec(str(pv))),
                          "margen_unitario": str(margen_u),
                          "margen_pct": str(round(margen_pct, 1))}
                if margen_pct < _dec(margen_minimo_pct):
                    alerta_margen = (
                        f"margen {round(margen_pct, 1)}% bajo el mínimo "
                        f"{margen_minimo_pct}%")
                if margen_u < 0:
                    alerta_margen = "VENTA BAJO COSTO: el precio de venta no cubre el costo de compra"
        except Exception:
            margen = None
        propuestas.append({
            **base,
            "cantidad_recomendada": str(cantidad),
            "cobertura_objetivo_dias": cobertura_objetivo_dias,
            "en_transito": str(transito),
            "tasa_diaria": pr.tasa_diaria,
            "dias_cobertura_actual": pr.dias_cobertura,
            "punto_reposicion": pr.punto_reposicion,
            "proveedor_recomendado": mejor["proveedor_id"],
            "precio_unitario": str(precio_u), "moneda": moneda,
            "costo_total_estimado": str(costo_total),
            "comparativa_proveedores": comp,
            "impacto_caja": {
                "saldo_actual": str(_dec(saldo_caja)), "moneda": moneda,
                "cabe_en_caja": cabe,
                "faltante": str(costo_total - _dec(saldo_caja))
                if not cabe else "0",
            },
            "margen_estimado": margen,
            "alerta_margen": alerta_margen,
            "supuestos": pr.supuestos + [
                f"cobertura_objetivo={cobertura_objetivo_dias}d",
                "proveedor recomendado = mejor precio/cumplimiento "
                "(muestra >= 2 órdenes si existe)",
            ],
        })
    propuestas.sort(key=lambda p: ({"critico": 0, "alto": 1, "medio": 2,
                                    "bajo": 3}.get(p["riesgo"], 4),
                                   p["producto_id"]))
    return {"company_id": company, "propuestas": propuestas,
            "sin_propuesta": sin_propuesta,
            "total_propuestas": len(propuestas),
            "costo_total_propuestas": str(sum(
                (_dec(p["costo_total_estimado"]) for p in propuestas
                 if p.get("moneda") == "DOP"), Decimal("0"))),
            "nota_moneda": "el total suma solo propuestas en DOP; otras "
                           "monedas van por separado en cada propuesta"}


# ---- contrato de rutas -------------------------------------------------
def _ctx_company(ctx) -> str:
    c = getattr(ctx, "company_id", "") or ""
    if not c:
        raise ValueError("ctx sin company_id")
    return c


def _fn_asesor(bundle, ctx, qob):
    c = _ctx_company(ctx)
    if bundle.inventario.company_id != c:
        raise ValueError("empresa ajena")
    q = qob or {}
    r = asesorar(
        bundle,
        cobertura_objetivo_dias=int(q.get("cobertura_objetivo_dias", 30)),
        dias_seguridad=int(q.get("dias_seguridad", 7)),
        min_dias=int(q.get("min_dias", 30)),
        dias_ventana=int(q.get("dias_ventana", 90)),
        margen_minimo_pct=str(q.get("margen_minimo_pct", "15")),
        hoy=q.get("hoy"), producto_id=q.get("producto_id"))
    return r


def rutas():
    return {"compras/asesor": _fn_asesor}
