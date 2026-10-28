"""SPRINT 5 PRIORIDAD 4 — Extracción de datos para reportes.

Cada función `_rpt_<modulo>` lee los objetos reales del bundle OS de la
empresa (nada se inventa) y devuelve un `Reporte` con secciones,
columnas separadas RD$/US$, totales verificables y notas de criterio.

Si el módulo no tiene datos suficientes, el reporte lo indica con
`nota_vacia` en vez de inventar filas.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation


@dataclass
class Seccion:
    """Una tabla del reporte."""
    titulo: str
    columnas: list[str]
    filas: list[list[str]]
    totales: list[list[str]] = field(default_factory=list)
    nota: str = ""
    # Índices de columnas numéricas (formato moneda en XLSX).
    num_cols: set[int] = field(default_factory=set)


@dataclass
class Reporte:
    modulo: str
    lang: str  # "es" | "en"
    titulo: str
    generado: str  # YYYY-MM-DD
    empresa: str
    aviso: str  # "Datos sintéticos de demostración" (ES/EN)
    secciones: list[Seccion]
    nota_vacia: str = ""  # texto si el módulo no tiene datos
    vacio: bool = False


# ---------------------------------------------------------------------
# Textos ES/EN (la lógica de negocio no se duplica por idioma).
# ---------------------------------------------------------------------
_T = {
    "es": {
        "aviso": "Datos sintéticos de demostración — no corresponden a operaciones reales.",
        "generado": "Generado",
        "empresa": "Empresa",
        "nota": "Nota",
        "sin_datos": "Sin registros en este módulo para la empresa. Los datos no se inventan: cargue datos demo u opere el módulo para verlos aquí.",
        "total": "TOTAL",
        "moneda": "Moneda",
        "tit": {
            "inventario": "Reporte de inventario",
            "finanzas": "Reporte de finanzas",
            "compras": "Reporte de compras",
            "cobranzas": "Reporte de cobranzas",
            "shield": "Reporte de riesgos (Shield)",
        },
        "sec_inv": "Existencias por producto y almacén",
        "col_producto": "Producto", "col_almacen": "Almacén",
        "col_stock": "Stock (uds)",
        "col_cup_rd": "Costo prom. unit. (RD$)",
        "col_cup_us": "Costo prom. unit. (US$)",
        "col_val_rd": "Valor stock (RD$)",
        "col_val_us": "Valor stock (US$)",
        "nota_inv": "Fórmula: valor stock = stock × costo promedio unitario de la moneda indicada. Total = suma de las filas de cada columna. Las monedas no se mezclan: RD$ y US$ van en columnas separadas.",
        "sec_rent": "Rentabilidad por moneda",
        "col_inn_rd": "Ingresos netos (RD$)",
        "col_inn_us": "Ingresos netos (US$)",
        "col_cogs_rd": "Costos (RD$)", "col_cogs_us": "Costos (US$)",
        "col_ub_rd": "Utilidad bruta (RD$)", "col_ub_us": "Utilidad bruta (US$)",
        "col_gas_rd": "Gastos operativos (RD$)",
        "col_gas_us": "Gastos operativos (US$)",
        "col_uo_rd": "Utilidad operativa (RD$)",
        "col_uo_us": "Utilidad operativa (US$)",
        "col_imp_rd": "Impuestos (RD$)", "col_imp_us": "Impuestos (US$)",
        "col_un_rd": "Utilidad neta (RD$)", "col_un_us": "Utilidad neta (US$)",
        "nota_rent": "Criterio: ingresos netos = brutos − descuentos − devoluciones (las notas de crédito revierten ingreso y COGS, una sola vez); utilidad bruta = ingresos netos − costos (COGS prorrateado por línea de venta); utilidad operativa = bruta − gastos operativos (del RegistroGastos, cada gasto una sola vez); utilidad neta = operativa − impuestos. Total = suma de las filas de cada columna.",
        "nota_nd": "N/D = dato insuficiente: el OS no inventa el valor (p. ej. la utilidad neta exige impuestos registrados por el contador).",
        "sec_gastos": "Gastos registrados",
        "col_gid": "Gasto ID", "col_cat": "Categoría", "col_fecha": "Fecha",
        "col_suc": "Sucursal", "col_prov": "Proveedor",
        "col_monto_rd": "Monto (RD$)", "col_monto_us": "Monto (US$)",
        "nota_gastos": "Fórmula: total = suma de los montos de cada columna de moneda.",
        "sec_caja": "Saldos de caja (tesorería)",
        "col_saldo_rd": "Saldo (RD$)", "col_saldo_us": "Saldo (US$)",
        "nota_caja": "Criterio: saldo de caja por moneda según la tesorería de la empresa.",
        "sec_oc": "Órdenes de compra y sus líneas",
        "col_oc": "Compra ID", "col_proveedor": "Proveedor",
        "col_estado": "Estado", "col_emision": "Emisión",
        "col_cant": "Cantidad",
        "col_pu_rd": "Precio unit. (RD$)", "col_pu_us": "Precio unit. (US$)",
        "col_sub_rd": "Subtotal (RD$)", "col_sub_us": "Subtotal (US$)",
        "nota_oc": "Fórmula: subtotal = cantidad × precio unitario (en la moneda de la orden). Total = suma de subtotales por columna de moneda.",
        "sec_cob": "Cuentas por cobrar (aging)",
        "col_cuenta": "Cuenta", "col_cliente": "Cliente",
        "col_pend_rd": "Pendiente (RD$)", "col_pend_us": "Pendiente (US$)",
        "col_vence": "Vencimiento", "col_dias": "Días vencida",
        "col_tramo": "Tramo", "col_promesa": "Promesa de pago",
        "nota_cob": "Criterio: pendiente = monto − cobrado; tramo por días vencida: 0-30, 31-60, 61-90, 90+. Total = suma de pendientes por columna de moneda.",
        "sec_shield": "Alertas de riesgo",
        "col_regla": "Regla", "col_sev": "Severidad",
        "col_titulo": "Título", "col_expl": "Explicación",
        "nota_shield": "Criterio: cada fila es una alerta del motor de reglas Shield con su regla y severidad (crítico > alerta > info). Totales = conteo por severidad.",
        "resumen_sev": "Conteo por severidad",
    },
    "en": {
        "aviso": "Synthetic demo data — does not reflect real operations.",
        "generado": "Generated",
        "empresa": "Company",
        "nota": "Note",
        "sin_datos": "No records in this module for the company. Data is never invented: load demo data or operate the module to see it here.",
        "total": "TOTAL",
        "moneda": "Currency",
        "tit": {
            "inventario": "Inventory report",
            "finanzas": "Finance report",
            "compras": "Purchasing report",
            "cobranzas": "Collections report",
            "shield": "Risk report (Shield)",
        },
        "sec_inv": "Stock by product and warehouse",
        "col_producto": "Product", "col_almacen": "Warehouse",
        "col_stock": "Stock (units)",
        "col_cup_rd": "Avg. unit cost (RD$)",
        "col_cup_us": "Avg. unit cost (US$)",
        "col_val_rd": "Stock value (RD$)",
        "col_val_us": "Stock value (US$)",
        "nota_inv": "Formula: stock value = stock × average unit cost in the stated currency. Total = sum of each column's rows. Currencies are never mixed: RD$ and US$ use separate columns.",
        "sec_rent": "Profitability by currency",
        "col_inn_rd": "Net revenue (RD$)",
        "col_inn_us": "Net revenue (US$)",
        "col_cogs_rd": "Costs (RD$)", "col_cogs_us": "Costs (US$)",
        "col_ub_rd": "Gross profit (RD$)", "col_ub_us": "Gross profit (US$)",
        "col_gas_rd": "Operating expenses (RD$)",
        "col_gas_us": "Operating expenses (US$)",
        "col_uo_rd": "Operating profit (RD$)",
        "col_uo_us": "Operating profit (US$)",
        "col_imp_rd": "Taxes (RD$)", "col_imp_us": "Taxes (US$)",
        "col_un_rd": "Net profit (RD$)", "col_un_us": "Net profit (US$)",
        "nota_rent": "Criteria: net revenue = gross − discounts − returns (credit notes reverse revenue and COGS, counted once); gross profit = net revenue − costs (COGS prorated by sale line); operating profit = gross − operating expenses (from the expense ledger, each expense counted once); net profit = operating − taxes. Total = sum of each column's rows.",
        "nota_nd": "N/A = insufficient data: the OS never invents the value (e.g. net profit requires taxes recorded by the accountant).",
        "sec_gastos": "Registered expenses",
        "col_gid": "Expense ID", "col_cat": "Category", "col_fecha": "Date",
        "col_suc": "Branch", "col_prov": "Vendor",
        "col_monto_rd": "Amount (RD$)", "col_monto_us": "Amount (US$)",
        "nota_gastos": "Formula: total = sum of the amounts in each currency column.",
        "sec_caja": "Cash balances (treasury)",
        "col_saldo_rd": "Balance (RD$)", "col_saldo_us": "Balance (US$)",
        "nota_caja": "Criteria: cash balance per currency from the company's treasury.",
        "sec_oc": "Purchase orders and their lines",
        "col_oc": "Order ID", "col_proveedor": "Vendor",
        "col_estado": "Status", "col_emision": "Issued",
        "col_cant": "Quantity",
        "col_pu_rd": "Unit price (RD$)", "col_pu_us": "Unit price (US$)",
        "col_sub_rd": "Subtotal (RD$)", "col_sub_us": "Subtotal (US$)",
        "nota_oc": "Formula: subtotal = quantity × unit price (in the order's currency). Total = sum of subtotals per currency column.",
        "sec_cob": "Accounts receivable (aging)",
        "col_cuenta": "Account", "col_cliente": "Customer",
        "col_pend_rd": "Due (RD$)", "col_pend_us": "Due (US$)",
        "col_vence": "Due date", "col_dias": "Days overdue",
        "col_tramo": "Bucket", "col_promesa": "Payment promise",
        "nota_cob": "Criteria: due = amount − collected; bucket by days overdue: 0-30, 31-60, 61-90, 90+. Total = sum of due amounts per currency column.",
        "sec_shield": "Risk alerts",
        "col_regla": "Rule", "col_sev": "Severity",
        "col_titulo": "Title", "col_expl": "Explanation",
        "nota_shield": "Criteria: each row is a Shield rule-engine alert with its rule and severity (critical > warning > info). Totals = count per severity.",
        "resumen_sev": "Count by severity",
    },
}


def _dec(v) -> Decimal:
    try:
        if v is None or v == "":
            return Decimal("0")
        return Decimal(str(v))
    except (InvalidOperation, ValueError):
        return Decimal("0")


def _f2(d: Decimal) -> str:
    """Monto con 2 decimales, sin separadores de miles (parseable en CSV)."""
    q = d.quantize(Decimal("0.01"))
    return format(q, "f")


def _en_moneda(monto: str, moneda: str) -> tuple[str, str]:
    """(rd, usd): el monto va solo a su columna de moneda; la otra queda
    vacía. Monedas no RD$/US$ se ignoran (nunca se mezclan)."""
    d = _dec(monto)
    if moneda == "DOP":
        return _f2(d), ""
    if moneda == "USD":
        return "", _f2(d)
    return "", ""


def nombre_archivo(modulo: str, formato: str, lang: str, fecha: str) -> str:
    base = "reporte" if lang == "es" else "report"
    return f"{base}-{modulo}-{fecha}.{formato}"


def generar(modulo: str, bundle, company_id: str, lang: str = "es",
            hoy: str | None = None) -> Reporte:
    """Construye el Reporte del módulo con los datos reales del bundle."""
    if lang not in ("es", "en"):
        raise ValueError("lang debe ser es o en")
    T = _T[lang]
    fecha = datetime.now(timezone.utc).date().isoformat()
    rep = Reporte(
        modulo=modulo, lang=lang, titulo=T["tit"][modulo], generado=fecha,
        empresa=company_id, aviso=T["aviso"], secciones=[],
        nota_vacia=T["sin_datos"])
    fn = {"inventario": _rpt_inventario, "finanzas": _rpt_finanzas,
          "compras": _rpt_compras, "cobranzas": _rpt_cobranzas,
          "shield": _rpt_shield}[modulo]
    fn(rep, T, bundle, hoy=hoy)
    rep.vacio = not any(s.filas for s in rep.secciones)
    return rep


# ---------------------------------------------------------------------
# Inventario
# ---------------------------------------------------------------------
def _rpt_inventario(rep: Reporte, T: dict, bundle, hoy=None) -> None:
    stock = bundle.inventario.stock_empresa()
    cols = [T["col_producto"], T["col_almacen"], T["col_stock"],
            T["col_cup_rd"], T["col_cup_us"],
            T["col_val_rd"], T["col_val_us"]]
    filas, tu, vrd, vus = [], Decimal("0"), Decimal("0"), Decimal("0")
    for (prod, alm) in sorted(stock):
        st = _dec(stock[(prod, alm)])
        curd = _dec(bundle.costo.costo_promedio(prod, alm, "DOP"))
        cus = _dec(bundle.costo.costo_promedio(prod, alm, "USD"))
        filas.append([prod, alm, _f2(st), _f2(curd), _f2(cus),
                      _f2(st * curd), _f2(st * cus)])
        tu += st
        vrd += st * curd
        vus += st * cus
    totales = ([[T["total"], "", _f2(tu), "", "", _f2(vrd), _f2(vus)]]
               if filas else [])
    rep.secciones.append(Seccion(
        titulo=T["sec_inv"], columnas=cols, filas=filas, totales=totales,
        nota=T["nota_inv"],
        num_cols={2, 3, 4, 5, 6}))


# ---------------------------------------------------------------------
# Finanzas (SENSIBLE: owner/admin)
# ---------------------------------------------------------------------
def _rpt_finanzas(rep: Reporte, T: dict, bundle, hoy=None) -> None:
    # 2026-10-13: columnas del P&L corregido (una fuente de verdad).
    # "datos insuficientes" se muestra como N/D y no entra en totales.
    rent = bundle.libro.rentabilidad()
    pares = [
        ("ingresos_netos", T["col_inn_rd"], T["col_inn_us"]),
        ("cogs", T["col_cogs_rd"], T["col_cogs_us"]),
        ("utilidad_bruta", T["col_ub_rd"], T["col_ub_us"]),
        ("gastos_operativos", T["col_gas_rd"], T["col_gas_us"]),
        ("utilidad_operativa", T["col_uo_rd"], T["col_uo_us"]),
        ("impuestos", T["col_imp_rd"], T["col_imp_us"]),
        ("utilidad_neta", T["col_un_rd"], T["col_un_us"]),
    ]
    cols = [T["moneda"]]
    for _, crd, cus in pares:
        cols += [crd, cus]
    filas = []
    acc = [Decimal("0")] * (2 * len(pares))
    con_datos = [False] * (2 * len(pares))
    hay_nd = False
    for mon in sorted(rent):
        r = rent[mon]
        rd = mon == "DOP"
        fila = [mon]
        for i, (k, _, _) in enumerate(pares):
            v = r[k]
            if v == "datos insuficientes" or v is None:
                fila += ["N/D", ""] if rd else ["", "N/D"]
                hay_nd = True
                continue
            d = _dec(v)
            if rd:
                fila += [_f2(d), ""]
                acc[2 * i] += d
            else:
                fila += ["", _f2(d)]
                acc[2 * i + 1] += d
            con_datos[2 * i if rd else 2 * i + 1] = True
        filas.append(fila)
    tot = [T["total"]] + [_f2(a) if c else "N/D"
                         for a, c in zip(acc, con_datos)]
    nota = T["nota_rent"] + (" " + T["nota_nd"] if hay_nd else "")
    rep.secciones.append(Seccion(
        titulo=T["sec_rent"], columnas=cols, filas=filas,
        totales=[tot] if filas else [], nota=nota,
        num_cols=set(range(1, 2 * len(pares) + 1))))

    # Gastos registrados.
    gcols = [T["col_gid"], T["col_cat"], T["col_fecha"], T["col_suc"],
             T["col_monto_rd"], T["col_monto_us"], T["col_prov"]]
    gfilas, grd, gus = [], Decimal("0"), Decimal("0")
    for g in sorted(bundle.gastos.todos(),
                    key=lambda x: (x.gasto.fecha or "", x.gasto.gasto_id)):
        rd, us = _en_moneda(g.gasto.monto, g.gasto.moneda)
        gfilas.append([g.gasto.gasto_id, g.gasto.categoria,
                       (g.gasto.fecha or "")[:10], g.sucursal_id,
                       rd, us, g.gasto.proveedor_id or ""])
        grd += _dec(rd)
        gus += _dec(us)
    rep.secciones.append(Seccion(
        titulo=T["sec_gastos"], columnas=gcols, filas=gfilas,
        totales=[[T["total"], "", "", "", _f2(grd), _f2(gus), ""]]
        if gfilas else [], nota=T["nota_gastos"],
        num_cols={4, 5}))

    # Caja.
    ccols = [T["moneda"], T["col_saldo_rd"], T["col_saldo_us"]]
    cfilas = []
    for mon in ("DOP", "USD"):
        rd, us = _en_moneda(bundle.tesoreria.saldo(mon), mon)
        cfilas.append([mon, rd, us])
    if not any(_dec(f[1]) != 0 or _dec(f[2]) != 0 for f in cfilas):
        cfilas = []
    rep.secciones.append(Seccion(
        titulo=T["sec_caja"], columnas=ccols, filas=cfilas, nota=T["nota_caja"],
        num_cols={1, 2}))


# ---------------------------------------------------------------------
# Compras
# ---------------------------------------------------------------------
def _rpt_compras(rep: Reporte, T: dict, bundle, hoy=None) -> None:
    cols = [T["col_oc"], T["col_proveedor"], T["col_estado"],
            T["col_emision"], T["col_producto"], T["col_cant"],
            T["col_pu_rd"], T["col_pu_us"],
            T["col_sub_rd"], T["col_sub_us"]]
    filas, trd, tus = [], Decimal("0"), Decimal("0")
    ordenes = sorted(bundle.compras.listar_ordenes(),
                     key=lambda o: (o.fecha_emision or "", o.compra_id))
    for o in ordenes:
        for lin in o.lineas:
            cant = _dec(lin.cantidad)
            pu = _dec(lin.precio_unitario)
            sub = cant * pu
            purd, puus = _en_moneda(str(pu), o.moneda)
            srd, sus = _en_moneda(str(sub), o.moneda)
            filas.append([o.compra_id, o.proveedor_id, o.estado,
                          (o.fecha_emision or "")[:10], lin.producto_id,
                          _f2(cant), purd, puus, srd, sus])
            trd += _dec(srd)
            tus += _dec(sus)
    rep.secciones.append(Seccion(
        titulo=T["sec_oc"], columnas=cols, filas=filas,
        totales=[[T["total"], "", "", "", "", "", "", "",
                  _f2(trd), _f2(tus)]] if filas else [],
        nota=T["nota_oc"], num_cols={5, 6, 7, 8, 9}))


# ---------------------------------------------------------------------
# Cobranzas (SENSIBLE: owner/admin)
# ---------------------------------------------------------------------
def _rpt_cobranzas(rep: Reporte, T: dict, bundle, hoy=None) -> None:
    cols = [T["col_cuenta"], T["col_cliente"],
            T["col_pend_rd"], T["col_pend_us"],
            T["col_vence"], T["col_dias"], T["col_tramo"],
            T["col_promesa"]]
    filas, trd, tus = [], Decimal("0"), Decimal("0")
    for it in bundle.cobranzas.aging(hoy=hoy):
        rd, us = _en_moneda(it.pendiente, it.moneda)
        prom = ""
        if it.promesa:
            prom = (f"{it.promesa.get('fecha', '')} / "
                    f"{it.promesa.get('monto', '')}")
        filas.append([it.cuenta_id, it.cliente_id, rd, us,
                      (it.vencimiento or "")[:10], str(it.dias_vencida),
                      it.tramo, prom])
        trd += _dec(rd)
        tus += _dec(us)
    rep.secciones.append(Seccion(
        titulo=T["sec_cob"], columnas=cols, filas=filas,
        totales=[[T["total"], "", _f2(trd), _f2(tus), "", "", "", ""]]
        if filas else [], nota=T["nota_cob"],
        num_cols={2, 3, 5}))


# ---------------------------------------------------------------------
# Shield
# ---------------------------------------------------------------------
def _rpt_shield(rep: Reporte, T: dict, bundle, hoy=None) -> None:
    from shield.api import alertas_para_ruta
    query = {"hoy": [hoy]} if hoy else {}
    alertas = alertas_para_ruta(bundle, bundle.company_id, query)
    tkey = "titulo_es" if rep.lang == "es" else "titulo_en"
    ekey = "explicacion_es" if rep.lang == "es" else "explicacion_en"
    cols = [T["col_regla"], T["col_sev"], T["col_titulo"],
            T["col_expl"], T["col_fecha"]]
    filas = []
    sev = {"critico": 0, "alerta": 0, "info": 0}
    for a in alertas:
        filas.append([a["regla"], a["severidad"], a.get(tkey, ""),
                      a.get(ekey, ""), (a.get("fecha") or "")[:10]])
        if a["severidad"] in sev:
            sev[a["severidad"]] += 1
    totales = ([[T["total"], "", f"crítico: {sev['critico']}",
                 f"alerta: {sev['alerta']}", f"info: {sev['info']}"]]
               if rep.lang == "es" else
               [[T["total"], "", f"critical: {sev['critico']}",
                 f"warning: {sev['alerta']}", f"info: {sev['info']}"]]
               if filas else [])
    rep.secciones.append(Seccion(
        titulo=T["sec_shield"], columnas=cols, filas=filas,
        totales=totales, nota=T["nota_shield"]))
