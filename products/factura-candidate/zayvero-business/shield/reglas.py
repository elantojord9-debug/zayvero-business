"""EXTRA 1 — SHIELD: reglas de detección de riesgos empresariales.

Fuente de datos (decisión documentada): el BUNDLE de motores
(webapp.os_api.OSBundle) — en backend PG es la hidratación del
snapshot, la misma fuente de verdad que usa el panel; en memoria es
el estado vivo. Así SHIELD funciona en ambos backends sin duplicar
lógica de lectura. Los eventos de auditoría (os_eventos) llegan como
parámetro `eventos` (los lee shield/api.py según el backend).

Reutilización (no se reconstruye lo que ya funciona):
- os_crm.cobranzas.Cobranzas.aging(): tramos de mora de CxC.
- anomalies.stats.mad(): z robusto para picos de movimiento (misma
  primitiva estadística que anomalies/temporal.py).
- Acceso a _ordenes/_ventas/_cxc igual que os_persist/normalizacion.py
  (privilegio documentado del coordinador de persistencia).

Reglas (REGLAS):
  stock_critico            días de cobertura de inventario bajo umbral
  cxc_vencidas             cuentas por cobrar vencidas (tramos 30/60/90+)
  proveedor_concentrado    un proveedor concentra el gasto en compras
  credito_sin_cobro        venta a crédito sin ningún pago tras N días
  movimiento_inusual       pico de consumo vs. histórico (z robusto)
  devoluciones_frecuentes  ratio devoluciones/ventas alto (usa os_eventos)
  cliente_concentrado      un cliente concentra la facturación (riesgo)
  reposicion_tardia        quiebre proyectado antes de que llegue la
                           reposición en camino (órdenes abiertas)

Anti-falsos-positivos: cada regla exige muestra mínima o historia
suficiente; sin datos para estimar, la regla guarda silencio en vez
de alertar (ver docstrings).
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from anomalies.stats import mad  # noqa: F401  (reutilizada: ver docstring)
from oschema.models import _dec

from .config import ConfigShield

REGLAS = ("stock_critico", "cxc_vencidas", "proveedor_concentrado",
          "credito_sin_cobro", "movimiento_inusual",
          "devoluciones_frecuentes", "cliente_concentrado",
          "reposicion_tardia")

SEVERIDADES = ("info", "alerta", "critico")

_TIPOS_CONSUMO = ("salida", "devolucion_out")  # restan stock vendido
_MAD_ESCALA = 0.6745  # misma constante que anomalies/stats.py


@dataclass
class Alerta:
    regla: str
    severidad: str                      # info | alerta | critico
    titulo_es: str
    titulo_en: str
    explicacion_es: str                 # lenguaje simple
    explicacion_en: str
    dato: dict = field(default_factory=dict)  # evidencia que la respalda
    accion_es: str = ""                 # qué hacer (sugerencia)
    accion_en: str = ""
    fecha: str = ""                     # ISO del día evaluado

    def to_dict(self) -> dict:
        return {"regla": self.regla, "severidad": self.severidad,
                "titulo_es": self.titulo_es, "titulo_en": self.titulo_en,
                "explicacion_es": self.explicacion_es,
                "explicacion_en": self.explicacion_en,
                "dato": self.dato, "accion_es": self.accion_es,
                "accion_en": self.accion_en, "fecha": self.fecha}


def _hoy(fecha) -> date:
    if fecha is None:
        return datetime.now(timezone.utc).date()
    if isinstance(fecha, date) and not isinstance(fecha, datetime):
        return fecha
    return datetime.strptime(str(fecha)[:10], "%Y-%m-%d").date()


def _dia(fecha_iso: str) -> date | None:
    """'2026-10-01T10:00:00Z' -> date; '' o inválido -> None."""
    try:
        return datetime.strptime(str(fecha_iso or "")[:10],
                                 "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _dinero(v, moneda: str) -> str:
    return f"{Decimal(str(v)):,.2f} {moneda}"


# ---------------------------------------------------------------------
# R1 — Stock crítico / quiebre inminente (días de cobertura)
# ---------------------------------------------------------------------
def _consumo_diario(bundle, producto_id: str, almacen_id: str, hoy: date,
                    ventana: int) -> tuple[Decimal, int]:
    """(consumo medio diario, días con dato) en la ventana.

    Consumo = |salida| + |devolucion_out| por día calendario. Si no
    hubo consumo en la ventana no se puede estimar el ritmo: se
    devuelve 0 y la regla guarda silencio (no es riesgo medible).
    """
    ini = hoy - timedelta(days=ventana - 1)
    por_dia: dict[date, Decimal] = defaultdict(Decimal)
    for m in bundle.inventario.movimientos(producto_id=producto_id,
                                           almacen_id=almacen_id):
        if m.tipo not in _TIPOS_CONSUMO:
            continue
        d = _dia(m.fecha)
        if d is None or d < ini or d > hoy:
            continue
        por_dia[d] += abs(_dec(m.cantidad))
    total = sum(por_dia.values(), Decimal("0"))
    return total / ventana, len(por_dia)


def _regla_stock(bundle, cfg: ConfigShield, hoy: date) -> list[Alerta]:
    out = []
    for (prod, alm), stock in sorted(
            bundle.inventario.stock_empresa().items()):
        if stock <= 0:
            out.append(Alerta(
                regla="stock_critico", severidad="critico", fecha=hoy.isoformat(),
                titulo_es=f"Quiebre de stock: {prod} en {alm}",
                titulo_en=f"Stockout: {prod} at {alm}",
                explicacion_es=(
                    f"El producto {prod} no tiene unidades disponibles en "
                    f"el almacén {alm} (stock {stock}). No se puede vender "
                    f"hasta reponer."),
                explicacion_en=(
                    f"Product {prod} has no units available at warehouse "
                    f"{alm} (stock {stock}). It cannot be sold until "
                    f"restocked."),
                dato={"producto_id": prod, "almacen_id": alm,
                      "stock": str(stock), "dias_cobertura": "0"},
                accion_es=("Genera una orden de compra urgente o transfiere "
                           "stock desde otro almacén."),
                accion_en=("Create an urgent purchase order or transfer "
                           "stock from another warehouse.")))
            continue
        medio, _ = _consumo_diario(bundle, prod, alm, hoy,
                                   cfg.ventana_consumo_dias)
        if medio <= 0:
            continue  # sin ritmo medible: silencio, no falso positivo
        dias = stock / medio
        if dias <= cfg.dias_stock_critico:
            sev = "critico"
        elif dias <= cfg.dias_stock_alerta:
            sev = "alerta"
        else:
            continue
        out.append(Alerta(
            regla="stock_critico", severidad=sev, fecha=hoy.isoformat(),
            titulo_es=f"Stock bajo: {prod} en {alm} ({dias:.1f} días)",
            titulo_en=f"Low stock: {prod} at {alm} ({dias:.1f} days)",
            explicacion_es=(
                f"Quedan {stock} unidades de {prod} en {alm} y se venden "
                f"~{medio:.1f} por día: alcanzan para {dias:.1f} días. "
                f"Si el proveedor se retrasa, habrá quiebre."),
            explicacion_en=(
                f"Only {stock} units of {prod} remain at {alm}, selling "
                f"~{medio:.1f} per day: {dias:.1f} days of cover. Any "
                f"supplier delay means a stockout."),
            dato={"producto_id": prod, "almacen_id": alm,
                  "stock": str(stock),
                  "consumo_diario": f"{medio:.2f}",
                  "dias_cobertura": f"{dias:.1f}"},
            accion_es=("Reordena ahora considerando el tiempo de entrega "
                       "del proveedor."),
            accion_en=("Reorder now, factoring in the supplier lead time.")))
    return out


# ---------------------------------------------------------------------
# R2 — Cuentas por cobrar vencidas (tramos de mora)
# ---------------------------------------------------------------------
_TRAMO_SEV = {"31-60": "alerta", "61-90": "alerta", "90+": "critico"}


def _regla_cxc(bundle, cfg: ConfigShield, hoy: date) -> list[Alerta]:
    grupos: dict[tuple[str, str], list] = defaultdict(list)
    for it in bundle.cobranzas.aging(hoy.isoformat()):
        if it.tramo not in _TRAMO_SEV:
            continue  # tramo 0-30: lo gestiona cobranzas, no es riesgo
        grupos[(it.tramo, it.moneda)].append(it)
    out = []
    for (tramo, moneda), items in sorted(grupos.items()):
        total = sum((_dec(i.pendiente) for i in items), Decimal("0"))
        if total < _dec(cfg.cxc_monto_minimo):
            continue  # centavos vencidos: ruido, no riesgo
        sev = _TRAMO_SEV[tramo]
        n = len(items)
        out.append(Alerta(
            regla="cxc_vencidas", severidad=sev, fecha=hoy.isoformat(),
            titulo_es=(f"{n} cuenta(s) por cobrar vencidas {tramo} días "
                       f"({_dinero(total, moneda)})"),
            titulo_en=(f"{n} receivable(s) overdue {tramo} days "
                       f"({_dinero(total, moneda)})"),
            explicacion_es=(
                f"Hay {_dinero(total, moneda)} pendientes de cobro con "
                f"{tramo} días de retraso ({n} cuenta(s)). Mientras más "
                f"pasa el tiempo, más difícil es recuperar ese dinero."),
            explicacion_en=(
                f"{_dinero(total, moneda)} in receivables are {tramo} days "
                f"overdue ({n} account(s)). The longer it takes, the "
                f"harder it is to collect."),
            dato={"tramo": tramo, "moneda": moneda,
                  "total_pendiente": str(total), "n_cuentas": n,
                  "cuentas": [{"cuenta_id": i.cuenta_id,
                               "cliente_id": i.cliente_id,
                               "dias_vencida": i.dias_vencida,
                               "pendiente": i.pendiente}
                              for i in sorted(items,
                                              key=lambda x: x.dias_vencida,
                                              reverse=True)]},
            accion_es=("Contacta a estos clientes hoy y registra una "
                       "promesa de pago en cobranzas."),
            accion_en=("Contact these customers today and log a payment "
                       "promise in collections.")))
    return out


# ---------------------------------------------------------------------
# R3 — Compras concentradas en un solo proveedor
# ---------------------------------------------------------------------
def _regla_proveedor(bundle, cfg: ConfigShield, hoy: date) -> list[Alerta]:
    ini = hoy - timedelta(days=cfg.proveedor_ventana_dias)
    por_moneda: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(Decimal))
    n_ordenes = 0
    for oc in bundle.compras._ordenes.values():  # coord. persistencia
        if oc.estado == "cancelada":
            continue
        d = _dia(oc.fecha_emision)
        if d is None or d < ini or d > hoy:
            continue
        por_moneda[oc.moneda][oc.proveedor_id] += oc.total()
        n_ordenes += 1
    if n_ordenes < cfg.proveedor_min_ordenes:
        return []  # muestra pequeña: silencio, no falso positivo
    out = []
    for moneda, provs in sorted(por_moneda.items()):
        if len(provs) < 2:
            continue  # un solo proveedor registrado: no hay con qué comparar
        total = sum(provs.values(), Decimal("0"))
        top_id, top_monto = max(provs.items(), key=lambda kv: kv[1])
        share = top_monto / total if total > 0 else Decimal("0")
        if share >= cfg.proveedor_share_critico:
            sev = "critico"
        elif share >= cfg.proveedor_share_alerta:
            sev = "alerta"
        else:
            continue
        out.append(Alerta(
            regla="proveedor_concentrado", severidad=sev,
            fecha=hoy.isoformat(),
            titulo_es=(f"Dependencia de proveedor: {top_id} concentra "
                       f"el {share * 100:.0f}% de las compras"),
            titulo_en=(f"Supplier dependence: {top_id} accounts for "
                       f"{share * 100:.0f}% of purchases"),
            explicacion_es=(
                f"En los últimos {cfg.proveedor_ventana_dias} días, "
                f"{_dinero(top_monto, moneda)} de {_dinero(total, moneda)} "
                f"en compras fueron a {top_id} ({share * 100:.0f}%). Si "
                f"ese proveedor falla o sube precios, el negocio se "
                f"detiene."),
            explicacion_en=(
                f"In the last {cfg.proveedor_ventana_dias} days, "
                f"{_dinero(top_monto, moneda)} of {_dinero(total, moneda)} "
                f"in purchases went to {top_id} ({share * 100:.0f}%). If "
                f"that supplier fails or raises prices, the business "
                f"stalls."),
            dato={"proveedor_id": top_id, "moneda": moneda,
                  "share": f"{share:.3f}",
                  "monto_proveedor": str(top_monto),
                  "monto_total": str(total),
                  "n_ordenes": n_ordenes,
                  "n_proveedores": len(provs),
                  "desglose": [{"proveedor_id": p, "monto": str(m)}
                               for p, m in sorted(
                                   provs.items(), key=lambda kv: kv[1],
                                   reverse=True)[:5]]},
            accion_es=("Busca al menos un proveedor alternativo y reparte "
                       "las próximas órdenes."),
            accion_en=("Find at least one alternative supplier and split "
                       "upcoming orders.")))
    return out


# ---------------------------------------------------------------------
# R4 — Ventas a crédito sin cobro registrado tras N días
# ---------------------------------------------------------------------
def _regla_credito(bundle, cfg: ConfigShield, hoy: date) -> list[Alerta]:
    out = []
    for vid, reg in sorted(bundle.ventas._ventas.items()):
        sol = reg["solicitud"]
        if not sol.a_credito or reg["estado"] != "confirmada":
            continue
        d = _dia(sol.fecha)
        if d is None:
            continue
        dias = (hoy - d).days
        cxc = bundle.tesoreria._cxc.get(f"cxc-{vid}")  # coord. persistencia
        cobrado = _dec(cxc.cobrado) if cxc is not None else Decimal("0")
        if cobrado > 0:
            continue  # hay pagos: se está gestionando, no es "sin cobro"
        if dias >= cfg.credito_dias_critico:
            sev = "critico"
        elif dias >= cfg.credito_dias_alerta:
            sev = "alerta"
        else:
            continue
        out.append(Alerta(
            regla="credito_sin_cobro", severidad=sev, fecha=hoy.isoformat(),
            titulo_es=(f"Venta a crédito {vid} sin cobro hace {dias} días"),
            titulo_en=(f"Credit sale {vid} with no payment for {dias} days"),
            explicacion_es=(
                f"La venta {vid} al cliente {sol.cliente_id} por "
                f"{_dinero(reg['total'], sol.moneda)} se hizo a crédito "
                f"hace {dias} días y no tiene ningún pago registrado. "
                f"Ese dinero debería estar en caja."),
            explicacion_en=(
                f"Credit sale {vid} to customer {sol.cliente_id} for "
                f"{_dinero(reg['total'], sol.moneda)} was made {dias} days "
                f"ago with no payment recorded. That money should be in "
                f"the bank."),
            dato={"venta_id": vid, "cliente_id": sol.cliente_id,
                  "dias_sin_cobro": dias, "monto": str(reg["total"]),
                  "moneda": sol.moneda,
                  "vencimiento": cxc.vencimiento if cxc else ""},
            accion_es=("Llama al cliente hoy y registra el cobro en "
                       "tesorería en cuanto pague."),
            accion_en=("Call the customer today and record the payment in "
                       "treasury as soon as it arrives.")))
    return out


# ---------------------------------------------------------------------
# R5 — Movimientos inusuales (pico de consumo vs. histórico)
# ---------------------------------------------------------------------
def _serie_consumo(bundle, producto_id: str, hoy: date,
                   ventana: int) -> dict[date, Decimal]:
    ini = hoy - timedelta(days=ventana - 1)
    por_dia: dict[date, Decimal] = defaultdict(Decimal)
    for m in bundle.inventario.movimientos(producto_id=producto_id):
        if m.tipo not in _TIPOS_CONSUMO:
            continue
        d = _dia(m.fecha)
        if d is None or d < ini or d > hoy:
            continue
        por_dia[d] += abs(_dec(m.cantidad))
    return por_dia


def _regla_picos(bundle, cfg: ConfigShield, hoy: date) -> list[Alerta]:
    productos = {m.producto_id
                 for m in bundle.inventario.movimientos()
                 if m.tipo in _TIPOS_CONSUMO}
    out = []
    for prod in sorted(productos):
        serie = _serie_consumo(bundle, prod, hoy, cfg.pico_ventana_dias)
        dias = sorted(serie)
        if len(dias) < cfg.pico_min_historia_dias + 1:
            continue  # sin historia suficiente: silencio
        reciente = dias[-1]
        base = [float(serie[d]) for d in dias[:-1]]
        x = float(serie[reciente])
        mediana = float(sorted(base)[len(base) // 2])
        if x <= 0:
            continue
        m = mad(base)
        z = (_MAD_ESCALA * (x - mediana) / m) if m > 0 else (
            float("inf") if x != mediana else 0.0)
        if z < cfg.pico_z_alerta:
            continue
        if x < cfg.pico_factor_materialidad * max(mediana, 1e-9):
            continue  # micro-variación en serie constante: ruido
        sev = ("critico" if z >= cfg.pico_z_critico else "alerta")
        out.append(Alerta(
            regla="movimiento_inusual", severidad=sev,
            fecha=hoy.isoformat(),
            titulo_es=(f"Pico de consumo en {prod}: {x:.0f} unidades el "
                       f"{reciente.isoformat()}"),
            titulo_en=(f"Consumption spike in {prod}: {x:.0f} units on "
                       f"{reciente.isoformat()}"),
            explicacion_es=(
                f"El {reciente.isoformat()} se consumieron {x:.0f} unidades "
                f"de {prod}, muy por encima de lo normal (mediana "
                f"{mediana:.1f}/día, z={z:.1f}). Puede ser una venta "
                f"grande, un error de registro o una fuga."),
            explicacion_en=(
                f"On {reciente.isoformat()}, {x:.0f} units of {prod} were "
                f"consumed, far above normal (median {mediana:.1f}/day, "
                f"z={z:.1f}). It could be a big sale, a recording error, "
                f"or shrinkage."),
            dato={"producto_id": prod, "fecha_pico": reciente.isoformat(),
                  "consumo_pico": f"{x:.2f}",
                  "mediana_diaria": f"{mediana:.2f}",
                  "z_robusto": f"{z:.2f}",
                  "dias_historia": len(dias) - 1},
            accion_es=("Revisa qué originó el pico (venta grande, merma o "
                       "error) y ajusta el plan de reposición."),
            accion_en=("Check what caused the spike (big sale, shrinkage, "
                       "or error) and adjust the replenishment plan.")))
    return out


# ---------------------------------------------------------------------
# R6 — Devoluciones / cancelaciones frecuentes (usa os_eventos)
# ---------------------------------------------------------------------
def _ev_fecha(ev: dict) -> date | None:
    f = ev.get("fecha")
    if f is None:
        return None  # modo memoria: sin fecha -> se incluye en ventana
    if isinstance(f, datetime):
        return f.date()
    return _dia(str(f))


def _regla_devoluciones(bundle, cfg: ConfigShield, hoy: date,
                        eventos: list[dict] | None) -> list[Alerta]:
    ini = hoy - timedelta(days=cfg.devoluciones_ventana_dias)
    dev_refs: set[str] = set()
    if eventos:
        for ev in eventos:
            if ev.get("tipo") != "devolucion":
                continue
            f = _ev_fecha(ev)
            if f is not None and (f < ini or f > hoy):
                continue
            dev_refs.add(ev.get("ref_id") or ev.get("evento_id") or "?")
    # Canceladas del motor sin evento (dedup por venta_id)
    for vid, reg in bundle.ventas._ventas.items():
        if reg["estado"] != "cancelada" or vid in dev_refs:
            continue
        f = _dia(reg["solicitud"].fecha)
        if f is not None and (f < ini or f > hoy):
            continue
        dev_refs.add(vid)
    ventas = 0
    for reg in bundle.ventas._ventas.values():
        if reg["estado"] != "confirmada":
            continue
        f = _dia(reg["solicitud"].fecha)
        if f is not None and (f < ini or f > hoy):
            continue
        ventas += 1
    if ventas < cfg.devoluciones_min_ventas or not dev_refs:
        return []  # muestra pequeña o cero devoluciones: silencio
    ratio = len(dev_refs) / ventas
    if ratio >= cfg.devoluciones_ratio_critico:
        sev = "critico"
    elif ratio >= cfg.devoluciones_ratio_alerta:
        sev = "alerta"
    else:
        return []
    return [Alerta(
        regla="devoluciones_frecuentes", severidad=sev,
        fecha=hoy.isoformat(),
        titulo_es=(f"Devoluciones altas: {ratio * 100:.1f}% de las ventas "
                   f"({len(dev_refs)} de {ventas})"),
        titulo_en=(f"High returns: {ratio * 100:.1f}% of sales "
                   f"({len(dev_refs)} of {ventas})"),
        explicacion_es=(
            f"En los últimos {cfg.devoluciones_ventana_dias} días hubo "
            f"{len(dev_refs)} devoluciones/cancelaciones sobre {ventas} "
            f"ventas ({ratio * 100:.1f}%). Un ratio alto suele indicar "
            f"problemas de calidad, descripciones engañosas o errores "
            f"en el despacho."),
        explicacion_en=(
            f"In the last {cfg.devoluciones_ventana_dias} days there "
            f"were {len(dev_refs)} returns/cancellations out of {ventas} "
            f"sales ({ratio * 100:.1f}%). A high ratio usually signals "
            f"quality issues, misleading listings, or fulfillment "
            f"errors."),
        dato={"ventana_dias": cfg.devoluciones_ventana_dias,
              "n_ventas": ventas, "n_devoluciones": len(dev_refs),
              "ratio": f"{ratio:.3f}",
              "referencias": sorted(dev_refs)[:50]},
        accion_es=("Revisa los motivos de devolución por producto y "
                   "corrige la causa raíz (calidad, fotos, empaque)."),
        accion_en=("Review return reasons by product and fix the root "
                   "cause (quality, photos, packaging)."))]


# ---------------------------------------------------------------------
# R7 — Concentración de clientes (riesgo de demanda)
# ---------------------------------------------------------------------
def _regla_clientes(bundle, cfg: ConfigShield, hoy: date) -> list[Alerta]:
    """Un cliente que concentra la facturación: si se va, deja de
    comprar o deja de pagar, el hueco es grande. Espejo de R3 con
    ventas confirmadas en vez de órdenes de compra."""
    ini = hoy - timedelta(days=cfg.cliente_ventana_dias)
    por_moneda: dict[str, dict[str, Decimal]] = defaultdict(
        lambda: defaultdict(Decimal))
    n_ventas = 0
    for reg in bundle.ventas._ventas.values():  # coord. persistencia
        if reg["estado"] != "confirmada":
            continue
        sol = reg["solicitud"]
        d = _dia(sol.fecha)
        if d is None or d < ini or d > hoy:
            continue
        por_moneda[sol.moneda][sol.cliente_id] += _dec(reg["total"])
        n_ventas += 1
    if n_ventas < cfg.cliente_min_ventas:
        return []  # muestra pequeña: silencio, no falso positivo
    out = []
    for moneda, clis in sorted(por_moneda.items()):
        if len(clis) < 2:
            continue  # un solo cliente registrado: no hay con qué comparar
        total = sum(clis.values(), Decimal("0"))
        top_id, top_monto = max(clis.items(), key=lambda kv: kv[1])
        share = top_monto / total if total > 0 else Decimal("0")
        if share >= cfg.cliente_share_critico:
            sev = "critico"
        elif share >= cfg.cliente_share_alerta:
            sev = "alerta"
        else:
            continue
        out.append(Alerta(
            regla="cliente_concentrado", severidad=sev,
            fecha=hoy.isoformat(),
            titulo_es=(f"Dependencia de cliente: {top_id} concentra "
                       f"el {share * 100:.0f}% de las ventas"),
            titulo_en=(f"Customer dependence: {top_id} accounts for "
                       f"{share * 100:.0f}% of sales"),
            explicacion_es=(
                f"En los últimos {cfg.cliente_ventana_dias} días, "
                f"{_dinero(top_monto, moneda)} de {_dinero(total, moneda)} "
                f"en ventas fueron a {top_id} ({share * 100:.0f}%). Si "
                f"ese cliente se va o deja de pagar, la facturación se "
                f"desploma."),
            explicacion_en=(
                f"In the last {cfg.cliente_ventana_dias} days, "
                f"{_dinero(top_monto, moneda)} of {_dinero(total, moneda)} "
                f"in sales went to {top_id} ({share * 100:.0f}%). If "
                f"that customer leaves or stops paying, revenue "
                f"collapses."),
            dato={"cliente_id": top_id, "moneda": moneda,
                  "share": f"{share:.3f}",
                  "monto_cliente": str(top_monto),
                  "monto_total": str(total),
                  "n_ventas": n_ventas,
                  "n_clientes": len(clis),
                  "desglose": [{"cliente_id": p, "monto": str(m)}
                               for p, m in sorted(
                                   clis.items(), key=lambda kv: kv[1],
                                   reverse=True)[:5]]},
            accion_es=("Diversifica la cartera: busca clientes nuevos y "
                       "reduce el peso de este cliente en las próximas "
                       "ventas."),
            accion_en=("Diversify the customer base: find new customers "
                       "and reduce this customer's weight in upcoming "
                       "sales.")))
    return out


# ---------------------------------------------------------------------
# R8 — Reposición que llega tarde (quiebre proyectado vs. OC abierta)
# ---------------------------------------------------------------------
_OC_ABIERTAS = ("enviada", "parcial")  # con entrega esperada futura


def _recepciones_esperadas(bundle, hoy: date) -> dict[str, date]:
    """{producto_id: fecha mínima de entrega esperada} entre órdenes
    abiertas con fecha válida futura."""
    recep: dict[str, date] = {}
    for oc in bundle.compras._ordenes.values():  # coord. persistencia
        if oc.estado not in _OC_ABIERTAS:
            continue
        f = _dia(oc.fecha_entrega_esperada)
        if f is None or f < hoy:
            continue
        for lin in oc.lineas or []:
            pid = getattr(lin, "producto_id", None)
            if pid and (pid not in recep or f < recep[pid]):
                recep[pid] = f
    return recep


def _regla_reposicion(bundle, cfg: ConfigShield, hoy: date) -> list[Alerta]:
    """Quiebre proyectado ANTERIOR a la llegada de la reposición.

    Difiere de R1 (stock_critico): R1 avisa por días de cobertura sin
    importar si hay reposición en camino; R8 avisa cuando SÍ hay una
    orden abierta pero su fecha de entrega esperada no alcanza: el
    negocio se quedará sin stock aunque la orden llegue "a tiempo".
    Sin órdenes abiertas con fecha válida: silencio (lo cubre R1).
    """
    recep = _recepciones_esperadas(bundle, hoy)
    if not recep:
        return []
    out = []
    for (prod, alm), stock in sorted(
            bundle.inventario.stock_empresa().items()):
        llegada = recep.get(prod)
        if llegada is None or stock <= 0:
            continue  # sin reposición esperada o ya en quiebre (R1)
        medio, _ = _consumo_diario(bundle, prod, alm, hoy,
                                   cfg.ventana_consumo_dias)
        if medio <= 0:
            continue  # sin ritmo medible: silencio
        dias = stock / medio
        fecha_quiebre = hoy + timedelta(days=float(dias))
        if fecha_quiebre >= llegada:
            continue  # la reposición llega antes del quiebre: bien
        sev = ("critico" if dias <= cfg.dias_stock_critico else "alerta")
        gap = (llegada - fecha_quiebre).days
        out.append(Alerta(
            regla="reposicion_tardia", severidad=sev,
            fecha=hoy.isoformat(),
            titulo_es=(f"Reposición tardía: {prod} en {alm} se agota "
                       f"el {fecha_quiebre.isoformat()}"),
            titulo_en=(f"Late replenishment: {prod} at {alm} runs out "
                       f"on {fecha_quiebre.isoformat()}"),
            explicacion_es=(
                f"Quedan {stock} unidades de {prod} en {alm} "
                f"({dias:.1f} días de cobertura) pero la próxima "
                f"reposición esperada llega el {llegada.isoformat()} "
                f"({gap} días DESPUÉS del quiebre proyectado). Habrá "
                f"ruptura de stock aunque la orden llegue según lo "
                f"previsto."),
            explicacion_en=(
                f"{stock} units of {prod} remain at {alm} "
                f"({dias:.1f} days of cover) but the next expected "
                f"replenishment arrives on {llegada.isoformat()} "
                f"({gap} days AFTER the projected stockout). There "
                f"will be a stockout even if the order arrives as "
                f"planned."),
            dato={"producto_id": prod, "almacen_id": alm,
                  "stock": str(stock),
                  "dias_cobertura": f"{dias:.1f}",
                  "fecha_quiebre_proyectada": fecha_quiebre.isoformat(),
                  "fecha_entrega_esperada": llegada.isoformat(),
                  "dias_sin_stock_proyectados": gap},
            accion_es=("Adelanta la entrega con el proveedor, busca "
                       "una compra local de emergencia o transfiere "
                       "stock desde otro almacén."),
            accion_en=("Expedite delivery with the supplier, find an "
                       "emergency local purchase, or transfer stock "
                       "from another warehouse.")))
    return out


# ---------------------------------------------------------------------
def evaluar(bundle, config: ConfigShield | None = None, hoy=None,
            eventos: list[dict] | None = None) -> list[Alerta]:
    """Evalúa todas las reglas sobre el bundle de UNA empresa.

    - bundle: OSBundle (aislamiento por construcción: solo ve los
      datos de su company_id).
    - hoy: "YYYY-MM-DD" | date | None (None = ahora UTC).
    - eventos: lista de dicts tipo os_eventos (para devoluciones).
      None = la regla de devoluciones usa solo el motor de ventas.

    Devuelve alertas ordenadas: crítico primero, luego alerta.
    """
    cfg = config or ConfigShield()
    h = _hoy(hoy)
    alertas = []
    alertas += _regla_stock(bundle, cfg, h)
    alertas += _regla_cxc(bundle, cfg, h)
    alertas += _regla_proveedor(bundle, cfg, h)
    alertas += _regla_credito(bundle, cfg, h)
    alertas += _regla_picos(bundle, cfg, h)
    alertas += _regla_devoluciones(bundle, cfg, h, eventos)
    alertas += _regla_clientes(bundle, cfg, h)
    alertas += _regla_reposicion(bundle, cfg, h)
    peso = {"critico": 0, "alerta": 1, "info": 2}
    alertas.sort(key=lambda a: (peso.get(a.severidad, 9), a.regla,
                                str(a.dato.get("producto_id") or
                                    a.dato.get("venta_id") or
                                    a.dato.get("cuenta_id") or
                                    a.dato.get("proveedor_id") or
                                    a.dato.get("cliente_id") or "")))
    return alertas
