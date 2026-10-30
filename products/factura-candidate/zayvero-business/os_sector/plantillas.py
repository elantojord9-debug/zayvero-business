"""BLOQUE SECTOR — Plantillas sectoriales (datos, no código de negocio).

Cada plantilla es CONFIGURACIÓN: dice qué mirar, con qué fórmula y de
qué fuente REAL del OS sale cada KPI. La plantilla NO inventa datos,
NO diagnostica y NO certifica umbrales: los umbrales marcados como
origen "os" vienen de valores por defecto reales del código del OS
(documentados en cada umbral); los marcados "sugerido" son criterios
operativos del sector que cada empresa debe ajustar.

Fuentes válidas (FUENTES_VALIDAS): identificadores de capacidades
reales auditadas en el código del OS (2026-10-13):
  os_finanzas/profit.py .......... LibroFinanzas.rentabilidad(),
                                   evaluar_margenes (Margin Guard, umbral
                                   por defecto 15%)
  os_finanzas/tesoreria.py ....... Tesoreria.posiciones_por_moneda()
  os_finanzas/gastos.py ........... RegistroGastos.todos()
  os_finanzas/expense_hunter.py ... cazar_desperdicios()
  os_ia/inspector.py .............. ventas_anomalas (z>=3 alerta, z>=5
                                   crítico, >=16 días de historia),
                                   gastos_anomalos (ratio >=1.5 vs
                                   promedio de 3 meses),
                                   comparacion_precios_compra
                                   (dif >=10%, 180 días),
                                   comparacion_periodos,
                                   perdidas_inventario (90 días,
                                   alerta si >=10 uds)
  shield/reglas.py + config.py .... stock_critico (alerta <15 días,
                                   crítico <7), cxc_vencidas (mora 30/90,
                                   monto mínimo 100), proveedor_concentrado
                                   y cliente_concentrado (40%/60%),
                                   credito_sin_cobro (45/90 días),
                                   movimiento_inusual (z>=3, >=15 días),
                                   devoluciones_frecuentes (5%/10%,
                                   >=10 ventas), reposicion_tardia
  os_crm/cobranzas.py ............. Cobranzas.aging() (tramos 30/60/90+)
  os_contab/rutas.py .............. presupuestos/ejecucion,
                                   rentabilidad/producto (contribución;
                                   gastos NO prorrateados),
                                   rentabilidad/sucursal,
                                   cashflow/proyeccion (determinista),
                                   cxc/resumen, cxp/resumen
  os_inventory/rotacion.py ........ analizar_rotacion(),
                                   detectar_dead_stock (90 días inmóvil)

validar_plantilla() verifica que toda referencia sea real: KPI.fuente
y umbral.fuente deben estar en FUENTES_VALIDAS, y todo umbral.kpi_id
debe existir en la plantilla.
"""
from __future__ import annotations

FUENTES_VALIDAS = frozenset({
    "LibroFinanzas.rentabilidad",
    "MarginGuard.evaluar_margenes",
    "Tesoreria.posiciones_por_moneda",
    "RegistroGastos.todos",
    "ExpenseHunter.cazar_desperdicios",
    "Inspector.ventas_anomalas",
    "Inspector.gastos_anomalos",
    "Inspector.comparacion_precios_compra",
    "Inspector.comparacion_periodos",
    "Inspector.perdidas_inventario",
    "Shield.stock_critico",
    "Shield.cxc_vencidas",
    "Shield.proveedor_concentrado",
    "Shield.credito_sin_cobro",
    "Shield.movimiento_inusual",
    "Shield.devoluciones_frecuentes",
    "Shield.cliente_concentrado",
    "Shield.reposicion_tardia",
    "Cobranzas.aging",
    "Contab.presupuestos/ejecucion",
    "Contab.rentabilidad/producto",
    "Contab.rentabilidad/sucursal",
    "Contab.cashflow/proyeccion",
    "Contab.cxc/resumen",
    "Contab.cxp/resumen",
    "Inventario.analizar_rotacion",
    "Inventario.detectar_dead_stock",
})

MODULOS_VALIDOS = frozenset({
    "inventario", "compras", "ventas", "crm", "finanzas", "gastos",
    "tesoreria", "contab", "empleados", "shield", "ia_inspector",
    "docs", "saas",
})

SEVERIDADES = ("info", "alerta", "critico")

SECTORES = ("tienda", "restaurante", "clinica", "servicios")

NOMBRES_SECTOR = {
    "tienda": "Tienda / Electrodomésticos",
    "restaurante": "Restaurante",
    "clinica": "Clínica",
    "servicios": "Servicios",
}

# Datos del OS que un KPI puede requerir para calcularse. "sin_datos"
# en el panel significa: el bundle de la empresa aún no tiene registros
# de este tipo (la plantilla NO rellena nada).
DATOS_VALIDOS = frozenset({
    "ventas", "compras", "inventario", "gastos", "cxc", "cxp",
    "tesoreria", "presupuestos",
})


def _k(id, nombre, formula, fuente, requiere, frecuencia, nota=""):
    return {"id": id, "nombre": nombre, "formula": formula,
            "fuente": fuente, "requiere": list(requiere),
            "frecuencia": frecuencia, "nota": nota}


def _u(id, kpi_id, nombre, condicion, severidad, fuente, origen,
       detalle_origen=""):
    return {"id": id, "kpi_id": kpi_id, "nombre": nombre,
            "condicion": condicion, "severidad": severidad,
            "fuente": fuente, "origen": origen,
            "detalle_origen": detalle_origen}


def _c(id, tarea, detalle, modulo):
    return {"id": id, "tarea": tarea, "detalle": detalle,
            "modulo": modulo}


# =====================================================================
# TIENDA / ELECTRODOMÉSTICOS
# =====================================================================
_TIENDA = {
    "id": "tienda",
    "nombre": NOMBRES_SECTOR["tienda"],
    "descripcion": ("Comercio minorista de productos físicos "
                    "(electrodomésticos, ferretería, tiendas). Foco: "
                    "margen por producto, rotación de inventario y "
                    "cobranza."),
    "prioridad_rd": "alta",
    "kpis": [
        _k("k-ventas-mes", "Ventas del mes vs. mes anterior",
           "(ventas_mes_actual − ventas_mes_anterior) / "
           "ventas_mes_anterior × 100, por moneda",
           "Inspector.comparacion_periodos", ["ventas"], "mensual",
           "Informativo: variación sin juicio de causa."),
        _k("k-margen-bruto", "Margen bruto por producto",
           "(ingreso − COGS) / ingreso × 100 por producto. Vista de "
           "CONTRIBUCIÓN: gastos operativos e impuestos NO se prorratean "
           "por producto (quedan en 'datos insuficientes').",
           "Contab.rentabilidad/producto", ["ventas", "inventario"],
           "mensual"),
        _k("k-margen-guard", "Alerta de margen bajo umbral",
           "evaluar_margenes(libro, umbral_pct): alerta si margen < "
           "umbral o si una venta se hizo bajo costo.",
           "MarginGuard.evaluar_margenes", ["ventas", "inventario"],
           "semanal"),
        _k("k-cobertura", "Días de cobertura de inventario",
           "stock / consumo_diario_promedio (ventana 30 días).",
           "Shield.stock_critico", ["inventario"], "semanal"),
        _k("k-rotacion", "Rotación y dead stock",
           "analizar_rotacion(engine, 90 días); detectar_dead_stock: "
           "productos sin movimiento en 90 días.",
           "Inventario.analizar_rotacion", ["inventario"], "mensual",
           "fuente alternativa para dead stock: "
           "Inventario.detectar_dead_stock"),
        _k("k-cxc-mora", "CxC vencidas por tramo",
           "Aging de cuentas por cobrar en tramos 30/60/90+ días.",
           "Cobranzas.aging", ["cxc"], "semanal"),
        _k("k-gasto-anomalo", "Gasto anormal por categoría",
           "gasto_mes_actual ≥ 1.5 × promedio de los 3 meses anteriores "
           "(exige los 3 meses con dato).",
           "Inspector.gastos_anomalos", ["gastos"], "mensual"),
        _k("k-precio-compra", "Diferencia de precio entre proveedores",
           "Mismo producto comprado a 2+ proveedores con precios que "
           "difieren ≥ 10% en 180 días (promedios ponderados).",
           "Inspector.comparacion_precios_compra", ["compras"],
           "trimestral"),
        _k("k-cashflow", "Proyección de caja 90 días",
           "Determinista: CxC con vencimiento + CxP con vencimiento + "
           "gastos programados + saldo inicial (dato explícito). No "
           "pronostica ventas.",
           "Contab.cashflow/proyeccion", ["cxc", "cxp", "tesoreria"],
           "mensual"),
        _k("k-devoluciones", "Ratio devoluciones / ventas",
           "devoluciones / ventas en 90 días (usa os_eventos).",
           "Shield.devoluciones_frecuentes", ["ventas"], "mensual"),
        _k("k-concentracion", "Concentración de clientes y proveedores",
           "Share del mayor cliente/proveedor sobre facturación/compras "
           "(365 días).",
           "Shield.cliente_concentrado", ["ventas", "compras"],
           "trimestral",
           "fuente alternativa para proveedores: "
           "Shield.proveedor_concentrado"),
    ],
    "umbrales": [
        _u("u-margen", "k-margen-guard", "Margen bajo umbral",
           "margen < umbral_pct", "alerta",
           "MarginGuard.evaluar_margenes", "os",
           "umbral por defecto 15% en os_finanzas/profit.py: "
           "evaluar_margenes(libro, umbral_pct='15')"),
        _u("u-cobertura", "k-cobertura", "Stock bajo",
           "cobertura < 15 días (alerta) / < 7 días (crítico)", "alerta",
           "Shield.stock_critico", "os",
           "dias_stock_alerta=15, dias_stock_critico=7 en "
           "shield/config.py::ConfigShield"),
        _u("u-mora", "k-cxc-mora", "Mora de clientes",
           "vencida > 30 días (alerta) / > 90 días (crítico); se ignoran "
           "saldos < 100", "alerta", "Shield.cxc_vencidas", "os",
           "dias_mora_alerta=30, dias_mora_critico=90, "
           "cxc_monto_minimo='100' en shield/config.py"),
        _u("u-gasto", "k-gasto-anomalo", "Gasto anormal",
           "ratio ≥ 1.5× vs promedio 3 meses", "alerta",
           "Inspector.gastos_anomalos", "os",
           "RATIO_GASTO_ANORMAL=1.5, MESES_BASE_GASTO=3 en "
           "os_ia/inspector.py"),
        _u("u-devol", "k-devoluciones", "Devoluciones altas",
           "ratio ≥ 5% (alerta) / ≥ 10% (crítico), con ≥ 10 ventas en "
           "90 días", "alerta", "Shield.devoluciones_frecuentes", "os",
           "devoluciones_ratio_alerta=0.05, _critico=0.10, "
           "devoluciones_min_ventas=10 en shield/config.py"),
        _u("u-concentracion", "k-concentracion", "Concentración riesgosa",
           "un cliente/proveedor ≥ 40% (alerta) / ≥ 60% (crítico)",
           "alerta", "Shield.cliente_concentrado", "os",
           "cliente_share_alerta=0.40, _critico=0.60, "
           "cliente_min_ventas=10 en shield/config.py (proveedores: "
           "mismos umbrales, ≥5 órdenes)"),
    ],
    "modulos_recomendados": [
        "inventario", "compras", "ventas", "crm", "finanzas", "gastos",
        "tesoreria", "contab", "shield", "ia_inspector",
    ],
    "checklist": [
        _c("c-catalogo", "Cargar el catálogo con costo unitario",
           "Cada producto con su costo promedio (os_finanzas/costo.py). "
           "Sin costo no hay margen ni COGS.", "inventario"),
        _c("c-proveedores", "Registrar proveedores y órdenes de compra",
           "Permite comparar precios entre proveedores y detectar "
           "concentración.", "compras"),
        _c("c-precios", "Definir precios de venta por producto",
           "Margin Guard necesita precio vs costo para alertar ventas "
           "bajo costo.", "ventas"),
        _c("c-gastos", "Registrar gastos por categoría cada mes",
           "Base del gasto anormal y del P&L. Mínimo 3 meses de historia "
           "para que el Inspector compare.", "gastos"),
        _c("c-cxc", "Registrar ventas a crédito con vencimiento",
           "Sin vencimiento no hay aging ni alertas de mora.", "crm"),
        _c("c-presupuesto", "Crear presupuestos por categoría",
           "os_contab presupuestos/ejecucion avisa sobreejecución.",
           "contab"),
    ],
}


# =====================================================================
# RESTAURANTE
# =====================================================================
_RESTAURANTE = {
    "id": "restaurante",
    "nombre": NOMBRES_SECTOR["restaurante"],
    "descripcion": ("Restaurantes, cafeterías y fondas. Foco: costo de "
                    "insumos (food cost), merma diaria y ritmo de ventas. "
                    "El OS no calcula recetas/porciones: el food cost es "
                    "una aproximación operativa con insumos registrados."),
    "prioridad_rd": "alta",
    "kpis": [
        _k("k-ventas-dia", "Ventas del día vs. mediana diaria",
           "z robusto (MAD) del día vs. serie de 120 días; alerta si "
           "|z| ≥ 3 (crítico si ≥ 5), con ≥ 16 días de historia.",
           "Inspector.ventas_anomalas", ["ventas"], "diaria"),
        _k("k-foodcost", "Costo de insumos / ventas (food cost %)",
           "COGS e insumos del período / ventas del período × 100. "
           "APROXIMACIÓN: el OS no modela recetas ni porciones estándar; "
           "requiere registrar insumos con su costo y las ventas del "
           "período.",
           "LibroFinanzas.rentabilidad", ["ventas", "compras", "gastos"],
           "semanal",
           "alternativa por insumo: "
           "Inspector.comparacion_precios_compra"),
        _k("k-merma", "Merma de inventario",
           "Ajustes negativos de stock (merma/daño/pérdida) en 90 días, "
           "valorizados a costo promedio.",
           "Inspector.perdidas_inventario", ["inventario"], "semanal"),
        _k("k-consumo-pico", "Pico de consumo de insumos",
           "Consumo del día vs. histórico (z robusto, ≥ 15 días de "
           "historia).",
           "Shield.movimiento_inusual", ["inventario"], "semanal"),
        _k("k-gasto-anomalo", "Gasto anormal por categoría",
           "gasto_mes_actual ≥ 1.5 × promedio de los 3 meses anteriores. "
           "Útil para luz, agua, gas, alquiler.",
           "Inspector.gastos_anomalos", ["gastos"], "mensual"),
        _k("k-precio-insumo", "Precio de insumos entre proveedores",
           "Mismo insumo a 2+ proveedores con diferencia ≥ 10% en "
           "180 días.",
           "Inspector.comparacion_precios_compra", ["compras"],
           "mensual"),
        _k("k-cashflow", "Proyección de caja 90 días",
           "Determinista desde CxC/CxP programados. No pronostica ventas.",
           "Contab.cashflow/proyeccion", ["cxc", "cxp", "tesoreria"],
           "mensual"),
        _k("k-cxc-mora", "CxC vencidas (clientes corporativos/eventos)",
           "Aging en tramos 30/60/90+ días.",
           "Cobranzas.aging", ["cxc"], "semanal"),
    ],
    "umbrales": [
        _u("u-merma", "k-merma", "Merma relevante",
           "merma ≥ 10 uds en 90 días (alerta); menos queda en info",
           "alerta", "Inspector.perdidas_inventario", "os",
           "severidad 'alerta' si uds >= 10 en os_ia/inspector.py::_i5"),
        _u("u-foodcost", "k-foodcost", "Food cost alto",
           "costo insumos / ventas > 35% (alerta)", "alerta",
           "LibroFinanzas.rentabilidad", "sugerido",
           "criterio operativo del sector (configurable por empresa); "
           "el OS no certifica este umbral"),
        _u("u-consumo", "k-consumo-pico", "Consumo inusual",
           "z ≥ 3 (alerta) / z ≥ 5 (crítico), con ≥ 15 días de historia "
           "y día ≥ 2× la mediana", "alerta",
           "Shield.movimiento_inusual", "os",
           "pico_z_alerta=3.0, pico_z_critico=5.0, "
           "pico_min_historia_dias=15, pico_factor_materialidad=2.0 en "
           "shield/config.py"),
        _u("u-gasto", "k-gasto-anomalo", "Gasto anormal",
           "ratio ≥ 1.5× vs promedio 3 meses", "alerta",
           "Inspector.gastos_anomalos", "os",
           "RATIO_GASTO_ANORMAL=1.5 en os_ia/inspector.py"),
        _u("u-credito", "k-cxc-mora", "Crédito sin cobro",
           "venta a crédito sin ningún pago tras 45 días (alerta) / "
           "90 días (crítico)", "alerta", "Shield.credito_sin_cobro",
           "os",
           "credito_dias_alerta=45, credito_dias_critico=90 en "
           "shield/config.py"),
    ],
    "modulos_recomendados": [
        "inventario", "compras", "ventas", "crm", "finanzas", "gastos",
        "tesoreria", "contab", "shield", "ia_inspector",
    ],
    "checklist": [
        _c("c-insumos", "Registrar insumos con costo unitario",
           "Arroz, carnes, aceite, etc. con su costo. Sin esto no hay "
           "food cost ni merma valorizada.", "inventario"),
        _c("c-venta-diaria", "Registrar la venta de cada día",
           "El Inspector necesita ≥ 16 días con venta para detectar "
           "picos/caídas.", "ventas"),
        _c("c-merma", "Anotar la merma como ajuste negativo",
           "Daño, vencimiento o pérdida se registra como ajuste "
           "negativo de stock, no se borra.", "inventario"),
        _c("c-gastos-fijos", "Registrar gastos fijos mensuales",
           "Alquiler, luz, agua, gas, nómina: base del gasto anormal.",
           "gastos"),
        _c("c-proveedores", "Comparar precios de insumos clave",
           "El mismo insumo a 2+ proveedores activa la comparación de "
           "precios.", "compras"),
    ],
}


# =====================================================================
# CLÍNICA
# =====================================================================
_CLINICA = {
    "id": "clinica",
    "nombre": NOMBRES_SECTOR["clinica"],
    "descripcion": ("Clínicas dentales, médicas y de servicios de salud. "
                    "Foco: ingresos por servicio, cobranza a pacientes y "
                    "control de gastos operativos. El OS no maneja "
                    "historiales clínicos: solo la parte comercial y "
                    "financiera."),
    "prioridad_rd": "media",
    "kpis": [
        _k("k-ingresos-servicio", "Ingresos netos por servicio",
           "Ingresos − descuentos − devoluciones por servicio. Vista de "
           "CONTRIBUCIÓN: gastos operativos NO se prorratean por "
           "servicio.",
           "Contab.rentabilidad/producto", ["ventas"], "mensual",
           "cada servicio se modela como 'producto' del OS"),
        _k("k-utilidad-operativa", "Utilidad operativa por moneda",
           "ingresos_netos − COGS − gastos_operativos. Sin impuestos "
           "registrados, la utilidad neta queda en 'datos "
           "insuficientes' (el OS no estima tasas).",
           "LibroFinanzas.rentabilidad", ["ventas", "gastos"], "mensual"),
        _k("k-cxc-mora", "CxC de pacientes vencidas",
           "Aging de cuentas por cobrar en tramos 30/60/90+ días.",
           "Cobranzas.aging", ["cxc"], "semanal"),
        _k("k-credito-sin-cobro", "Cuentas a crédito sin pago",
           "Venta a crédito sin ningún pago tras N días.",
           "Shield.credito_sin_cobro", ["cxc", "ventas"], "semanal"),
        _k("k-gasto-periodo", "Gastos vs. mes anterior",
           "Variación informativa del gasto total por moneda.",
           "Inspector.comparacion_periodos", ["gastos"], "mensual"),
        _k("k-presupuesto", "Ejecución de presupuesto por categoría",
           "% consumido del presupuesto y alertas de sobreejecución.",
           "Contab.presupuestos/ejecucion", ["gastos", "presupuestos"],
           "mensual"),
        _k("k-cashflow", "Proyección de caja 90 días",
           "Determinista desde CxC/CxP programados.",
           "Contab.cashflow/proyeccion", ["cxc", "cxp", "tesoreria"],
           "mensual"),
    ],
    "umbrales": [
        _u("u-mora", "k-cxc-mora", "Mora de pacientes",
           "vencida > 30 días (alerta) / > 90 días (crítico); saldos < "
           "100 se ignoran", "alerta", "Shield.cxc_vencidas", "os",
           "dias_mora_alerta=30, dias_mora_critico=90, "
           "cxc_monto_minimo='100' en shield/config.py"),
        _u("u-credito", "k-credito-sin-cobro", "Crédito sin cobro",
           "sin pago tras 45 días (alerta) / 90 días (crítico)", "alerta",
           "Shield.credito_sin_cobro", "os",
           "credito_dias_alerta=45, credito_dias_critico=90 en "
           "shield/config.py"),
        _u("u-gasto", "k-gasto-periodo", "Gasto anormal por categoría",
           "ratio ≥ 1.5× vs promedio 3 meses", "alerta",
           "Inspector.gastos_anomalos", "os",
           "RATIO_GASTO_ANORMAL=1.5 en os_ia/inspector.py"),
        _u("u-presupuesto", "k-presupuesto", "Sobreejecución",
           "ejecución > 100% del presupuesto (o cerca del límite)",
           "alerta", "Contab.presupuestos/ejecucion", "os",
           "os_contab presupuestos: alertas de sobreejecución en "
           "ejecucion()"),
    ],
    "modulos_recomendados": [
        "ventas", "crm", "finanzas", "gastos", "tesoreria", "contab",
        "empleados", "shield", "ia_inspector",
    ],
    "checklist": [
        _c("c-servicios", "Catálogo de servicios con precios",
           "Limpieza, ortodoncia, consulta, etc. como 'productos' con "
           "precio de venta.", "ventas"),
        _c("c-pacientes", "Registrar pacientes como clientes",
           "Base de la cobranza y del aging.", "crm"),
        _c("c-politica-cobro", "Definir política de cobro",
           "Vencimientos por servicio y recordatorios "
           "(Cobranzas.aging + promesas de pago).", "crm"),
        _c("c-gastos", "Registrar gastos operativos mensuales",
           "Nómina, alquiler, insumos, servicios: mínimo 3 meses para "
           "comparar.", "gastos"),
        _c("c-presupuesto", "Presupuesto mensual por categoría",
           "La ejecución avisa antes de pasarse.", "contab"),
        _c("c-turnos", "Turnos del personal",
           "os_empleados: registro de personal y tareas/turnos.",
           "empleados"),
    ],
}


# =====================================================================
# SERVICIOS
# =====================================================================
_SERVICIOS = {
    "id": "servicios",
    "nombre": NOMBRES_SECTOR["servicios"],
    "descripcion": ("Talleres, salones de belleza, técnicos y servicios "
                    "profesionales. Foco: rentabilidad por servicio, "
                    "cobranza y concentración de clientes."),
    "prioridad_rd": "media",
    "kpis": [
        _k("k-utilidad-servicio", "Contribución por servicio/trabajo",
           "Ingresos − COGS por servicio. Vista de CONTRIBUCIÓN: gastos "
           "operativos NO prorrateados.",
           "Contab.rentabilidad/producto", ["ventas"], "mensual"),
        _k("k-utilidad-sucursal", "P&L por sucursal/local",
           "Ingresos, COGS y gastos directos atribuidos por sucursal. "
           "Impuestos sin dimensión de sucursal: utilidad neta en 'datos "
           "insuficientes'.",
           "Contab.rentabilidad/sucursal", ["ventas", "gastos"],
           "mensual",
           "requiere gastos con sucursal_id (GastoDetallado)"),
        _k("k-margen-guard", "Alerta de margen bajo umbral",
           "evaluar_margenes(libro, umbral_pct): alerta si margen < "
           "umbral o venta bajo costo.",
           "MarginGuard.evaluar_margenes", ["ventas"], "semanal"),
        _k("k-cxc-mora", "CxC vencidas por tramo",
           "Aging en tramos 30/60/90+ días.",
           "Cobranzas.aging", ["cxc"], "semanal"),
        _k("k-credito-sin-cobro", "Trabajos a crédito sin pago",
           "Venta a crédito sin ningún pago tras N días.",
           "Shield.credito_sin_cobro", ["cxc", "ventas"], "semanal"),
        _k("k-gasto-anomalo", "Gasto anormal por categoría",
           "gasto_mes_actual ≥ 1.5 × promedio de los 3 meses anteriores.",
           "Inspector.gastos_anomalos", ["gastos"], "mensual"),
        _k("k-concentracion", "Concentración de clientes",
           "Share del mayor cliente sobre la facturación (365 días, "
           "≥ 10 ventas).",
           "Shield.cliente_concentrado", ["ventas"], "trimestral"),
        _k("k-cashflow", "Proyección de caja 90 días",
           "Determinista desde CxC/CxP programados.",
           "Contab.cashflow/proyeccion", ["cxc", "cxp", "tesoreria"],
           "mensual"),
    ],
    "umbrales": [
        _u("u-margen", "k-margen-guard", "Margen bajo umbral",
           "margen < umbral_pct", "alerta",
           "MarginGuard.evaluar_margenes", "os",
           "umbral por defecto 15% en os_finanzas/profit.py"),
        _u("u-mora", "k-cxc-mora", "Mora de clientes",
           "vencida > 30 días (alerta) / > 90 días (crítico); saldos < "
           "100 se ignoran", "alerta", "Shield.cxc_vencidas", "os",
           "dias_mora_alerta=30, dias_mora_critico=90 en "
           "shield/config.py"),
        _u("u-credito", "k-credito-sin-cobro", "Crédito sin cobro",
           "sin pago tras 45 días (alerta) / 90 días (crítico)", "alerta",
           "Shield.credito_sin_cobro", "os",
           "credito_dias_alerta=45, credito_dias_critico=90 en "
           "shield/config.py"),
        _u("u-concentracion", "k-concentracion", "Cliente dominante",
           "un cliente ≥ 40% de la facturación (alerta) / ≥ 60% "
           "(crítico)", "alerta", "Shield.cliente_concentrado", "os",
           "cliente_share_alerta=0.40, _critico=0.60 en "
           "shield/config.py"),
        _u("u-gasto", "k-gasto-anomalo", "Gasto anormal",
           "ratio ≥ 1.5× vs promedio 3 meses", "alerta",
           "Inspector.gastos_anomalos", "os",
           "RATIO_GASTO_ANORMAL=1.5 en os_ia/inspector.py"),
    ],
    "modulos_recomendados": [
        "ventas", "crm", "finanzas", "gastos", "tesoreria", "contab",
        "empleados", "shield", "ia_inspector",
    ],
    "checklist": [
        _c("c-servicios", "Catálogo de servicios con precio y costo",
           "Cada servicio con precio de venta y costo estimado "
           "(materiales/horas).", "ventas"),
        _c("c-clientes", "Registrar clientes y sus trabajos",
           "Base de la cobranza y de la concentración.", "crm"),
        _c("c-cotizacion", "Toda cotización aceptada → venta registrada",
           "Sin venta registrada no hay margen ni aging.", "ventas"),
        _c("c-vencimientos", "Ventas a crédito siempre con vencimiento",
           "Sin vencimiento no hay alertas de mora.", "crm"),
        _c("c-gastos", "Gastos mensuales por categoría",
           "Combustible, herramientas, alquiler, nómina.", "gastos"),
    ],
}

PLANTILLAS = {
    "tienda": _TIENDA,
    "restaurante": _RESTAURANTE,
    "clinica": _CLINICA,
    "servicios": _SERVICIOS,
}


# =====================================================================
# Validación
# =====================================================================
def validar_plantilla(plantilla: dict) -> list[str]:
    """Devuelve la lista de problemas; vacía = plantilla válida.

    Verifica: estructura mínima, que cada KPI referencie una fuente
    real del OS (FUENTES_VALIDAS), que cada umbral referencie una
    fuente real y un KPI existente, y que los módulos del checklist
    sean módulos conocidos del OS.
    """
    problemas = []
    pid = plantilla.get("id", "?")
    for campo in ("id", "nombre", "descripcion", "kpis", "umbrales",
                  "modulos_recomendados", "checklist"):
        if campo not in plantilla:
            problemas.append(f"{pid}: falta campo '{campo}'")
    if problemas:
        return problemas
    kpi_ids = set()
    for k in plantilla["kpis"]:
        kid = k.get("id", "?")
        kpi_ids.add(kid)
        for campo in ("id", "nombre", "formula", "fuente", "requiere",
                      "frecuencia"):
            if campo not in k:
                problemas.append(f"{pid}/{kid}: falta campo '{campo}'")
        if k.get("fuente") not in FUENTES_VALIDAS:
            problemas.append(
                f"{pid}/{kid}: fuente desconocida "
                f"'{k.get('fuente')}' (no está en FUENTES_VALIDAS)")
        for d in k.get("requiere", []):
            if d not in DATOS_VALIDOS:
                problemas.append(
                    f"{pid}/{kid}: dato requerido desconocido '{d}'")
    for u in plantilla["umbrales"]:
        uid = u.get("id", "?")
        for campo in ("id", "kpi_id", "nombre", "condicion", "severidad",
                      "fuente", "origen"):
            if campo not in u:
                problemas.append(f"{pid}/{uid}: falta campo '{campo}'")
        if u.get("fuente") not in FUENTES_VALIDAS:
            problemas.append(
                f"{pid}/{uid}: fuente desconocida '{u.get('fuente')}'")
        if u.get("kpi_id") not in kpi_ids:
            problemas.append(
                f"{pid}/{uid}: kpi_id '{u.get('kpi_id')}' no existe")
        if u.get("severidad") not in SEVERIDADES:
            problemas.append(
                f"{pid}/{uid}: severidad inválida "
                f"'{u.get('severidad')}'")
        if u.get("origen") not in ("os", "sugerido"):
            problemas.append(
                f"{pid}/{uid}: origen debe ser 'os' o 'sugerido'")
    for m in plantilla["modulos_recomendados"]:
        if m not in MODULOS_VALIDOS:
            problemas.append(f"{pid}: módulo desconocido '{m}'")
    for c in plantilla["checklist"]:
        for campo in ("id", "tarea", "detalle", "modulo"):
            if campo not in c:
                problemas.append(
                    f"{pid}/{c.get('id', '?')}: falta campo '{campo}'")
        if c.get("modulo") not in MODULOS_VALIDOS:
            problemas.append(
                f"{pid}/{c.get('id', '?')}: módulo desconocido "
                f"'{c.get('modulo')}'")
    return problemas


def obtener_plantilla(sector_id: str) -> dict:
    """Plantilla completa por id; None si no existe."""
    return PLANTILLAS.get(sector_id)


def listar_plantillas() -> list[dict]:
    """Resumen de las 4 plantillas (sin detalle de KPIs)."""
    return [{"id": p["id"], "nombre": p["nombre"],
             "descripcion": p["descripcion"],
             "prioridad_rd": p["prioridad_rd"],
             "n_kpis": len(p["kpis"]),
             "n_umbrales": len(p["umbrales"]),
             "n_checklist": len(p["checklist"]),
             "modulos_recomendados": list(p["modulos_recomendados"])}
            for p in PLANTILLAS.values()]
