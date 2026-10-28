"""Página privada: Contador IA — análisis informativo (SOLO LECTURA)."""
from __future__ import annotations

from factura_web import templates as T
from factura_core import contador_ia

esc, money = T.esc, T.money


def _cambio_chip(cambio_pct):
    if cambio_pct is None:
        return '<span class="muted">sin mes anterior</span>'
    d = cambio_pct
    flecha = "▲" if float(d) >= 0 else "▼"
    color = "#15803D" if float(d) >= 0 else "#B91C1C"
    return (f'<span style="color:{color};font-weight:700">{flecha} '
            f'{esc(d)}%</span>')


def _impacto_badge(impacto: int) -> str:
    estilos = {5: ("#B91C1C", "Alta"), 4: ("#C2410C", "Media-alta"),
               3: ("#A16207", "Media"), 2: ("#4D7C0F", "Baja"),
               1: ("#64748B", "Informativa")}
    color, txt = estilos.get(int(impacto), ("#64748B", ""))
    return (f'<span style="display:inline-block;padding:2px 10px;'
            f'border-radius:999px;background:{color}18;color:{color};'
            f'border:1px solid {color};font-size:.78rem;font-weight:700">'
            f'Impacto: {txt}</span>')


def pagina_contador_ia(sesion, panel: dict) -> str:
    r = panel["resumen"]
    m = panel["margen"]
    cxc = panel["cxc"]
    inv = panel["inventario"]
    gastos = panel["gastos"]

    h = (f'<div class="alert info"><b>Contador IA — análisis '
         f'informativo.</b> Todo lo que ves aquí se calculó de los datos '
         f'reales de tu empresa; es solo lectura y no modifica nada. '
         f'{esc(contador_ia.DISCLAIMER)}</div>')

    # --- KPIs del mes -------------------------------------------------------
    h += (f"<h2>Resumen de {esc(r['mes'])}</h2>"
          f'<div class="cards" style="margin-bottom:16px">'
          f'<div class="card accent"><div class="k">Ventas del mes</div>'
          f'<div class="v">{money(r["ventas_total"])}'
          f'<br><small>{_cambio_chip(r["cambio_pct"])} vs '
          f'{esc(r["mes_anterior_nombre"])}</small></div></div>'
          f'<div class="card"><div class="k">Facturas</div>'
          f'<div class="v">{r["n_facturas"]}</div></div>'
          f'<div class="card"><div class="k">Ticket promedio</div>'
          f'<div class="v">{money(r["ticket_promedio"])}</div></div>'
          f'<div class="card"><div class="k">Utilidad neta (aprox.)</div>'
          f'<div class="v">{money(m["utilidad_neta"])}</div></div>'
          f'<div class="card"><div class="k">Margen neto</div>'
          f'<div class="v">{esc(m["margen_neto_pct"] or "—")}'
          f'{"%" if m["margen_neto_pct"] else ""}</div>'
          f'<div class="foot"><small>{esc(m["nota"])}</small></div></div>'
          f'</div>')

    # --- tops ---------------------------------------------------------------
    fi = "".join(
        f"<tr><td>{esc(p['nombre'])}</td>"
        f"<td class='num'>{money(p['ingreso'])}</td></tr>"
        for p in panel["top_ingreso"])
    fu = "".join(
        f"<tr><td>{esc(p['nombre'])}</td>"
        f"<td class='num'>{esc(p['cantidad'])}</td></tr>"
        for p in panel["top_unidades"])
    fc = "".join(
        f"<tr><td>{esc(c['nombre'])}</td>"
        f"<td class='num'>{c['facturas']}</td>"
        f"<td class='num'>{money(c['total'])}</td></tr>"
        for c in panel["top_clientes"])
    h += (f'<div class="grid2">'
          f'<div><h3>Top 5 productos por ingreso</h3>'
          f'<div class="table-wrap"><table>'
          f"<thead><tr><th>Producto</th><th class='num'>Ingreso</th></tr>"
          f"</thead><tbody>"
          f"{fi or '<tr><td colspan=2>Sin ventas este mes.</td></tr>'}"
          f"</tbody></table></div></div>"
          f'<div><h3>Top 5 productos por unidades</h3>'
          f'<div class="table-wrap"><table>'
          f"<thead><tr><th>Producto</th><th class='num'>Unidades</th></tr>"
          f"</thead><tbody>"
          f"{fu or '<tr><td colspan=2>Sin ventas este mes.</td></tr>'}"
          f"</tbody></table></div></div></div>"
          f"<h3>Top 5 clientes que más compran</h3>"
          f'<div class="table-wrap"><table>'
          f"<thead><tr><th>Cliente</th><th class='num'>Facturas</th>"
          f"<th class='num'>Total</th></tr></thead><tbody>"
          f"{fc or '<tr><td colspan=3>Sin ventas este mes.</td></tr>'}"
          f"</tbody></table></div>")

    # --- CxC ----------------------------------------------------------------
    h += (f"<h2>Cuentas por cobrar</h2>"
          f'<div class="cards" style="margin-bottom:16px">'
          f'<div class="card"><div class="k">Total pendiente</div>'
          f'<div class="v">{money(cxc["total_pendiente"])}'
          f'<br><small>{cxc["n_pendientes"]} facturas</small></div></div>'
          f'<div class="card accent"><div class="k">Total vencido</div>'
          f'<div class="v">{money(cxc["total_vencido"])}'
          f'<br><small>{cxc["n_vencidas"]} facturas de '
          f'{cxc["n_clientes_vencidos"]} clientes</small></div></div>'
          f'<div class="card"><div class="k">Retraso promedio</div>'
          f'<div class="v">{cxc["dias_promedio_vencidos"]}'
          f'<small> días</small></div></div></div>'
          f'<div class="table-wrap"><table>'
          f"<thead><tr><th>Antigüedad</th><th class='num'>Monto</th></tr>"
          f"</thead><tbody>"
          + "".join(
              f"<tr><td>{esc(k)} días</td>"
              f"<td class='num'>{money(v)}</td></tr>"
              for k, v in cxc["antiguedad"].items())
          + f"</tbody></table></div>")

    # --- inventario ----------------------------------------------------------
    fa = "".join(
        f"<tr><td>{esc(a['nombre'])}</td>"
        f"<td class='num'>{esc(a['stock'])}</td>"
        f"<td class='num'>{esc(a['dias_stock'] if a['dias_stock'] is not None else '—')}</td>"
        f"<td>{esc(a['estado'])}</td></tr>"
        for a in inv["alertas"])
    h += (f"<h2>Inventario</h2>"
          f'<div class="cards" style="margin-bottom:16px">'
          f'<div class="card"><div class="k">Valorización (stock × costo)</div>'
          f'<div class="v">{money(inv["valorizacion_total"])}</div></div>'
          f'<div class="card"><div class="k">Productos con alerta</div>'
          f'<div class="v">{inv["n_alertas"]}'
          f'<br><small>{inv["n_agotados"]} agotados</small></div></div>'
          f'<div class="card"><div class="k">Stock sin moverse (30 días)</div>'
          f'<div class="v">{money(inv["valorizacion_inmovilizada"])}</div>'
          f"</div></div>"
          f'<div class="table-wrap"><table>'
          f"<thead><tr><th>Producto</th><th class='num'>Stock</th>"
          f"<th class='num'>Días de stock</th><th>Estado</th></tr></thead>"
          f"<tbody>"
          f"{fa or '<tr><td colspan=4>Sin alertas de inventario. 🎉</td></tr>'}"
          f"</tbody></table></div>")

    # --- gastos --------------------------------------------------------------
    fg = "".join(
        f"<tr><td>{esc(g['categoria'].capitalize())}</td>"
        f"<td class='num'>{money(g['total_mes'])}</td>"
        f"<td class='num'>{money(g['total_mes_anterior'])}</td>"
        f"<td class='num'>{(esc(g['cambio_pct']) + '%') if g['cambio_pct'] is not None else '—'}</td></tr>"
        for g in gastos["categorias"])
    h += (f"<h2>Gastos por categoría — {esc(gastos['mes'])}</h2>"
          f'<div class="table-wrap"><table>'
          f"<thead><tr><th>Categoría</th><th class='num'>Este mes</th>"
          f"<th class='num'>Mes anterior</th><th class='num'>Cambio</th>"
          f"</tr></thead><tbody>"
          f"{fg or '<tr><td colspan=4>Sin gastos registrados.</td></tr>'}"
          f"</tbody></table></div>")

    # --- recomendaciones ------------------------------------------------------
    h += "<h2>💡 Recomendaciones</h2>"
    recs = panel["recomendaciones"]
    if not recs:
        h += T.empty_state(
            "📊", "Aún no hay suficiente información",
            "Cuando registres más ventas y gastos, el Contador IA te "
            "dará recomendaciones basadas en tus números.")
    for i, rc in enumerate(recs, 1):
        h += (f'<div class="card" style="margin-bottom:12px;'
              f'border-left:4px solid #7C3AED">'
              f'<div style="display:flex;gap:12px;align-items:center;'
              f'flex-wrap:wrap">'
              f"<h3 style='margin:0'>{i}. {esc(rc['titulo'])}</h3>"
              f"{_impacto_badge(rc['impacto'])}</div>"
              f"<p>{esc(rc['explicacion'])}</p>"
              f"<p><b>👉 Acción sugerida:</b> {esc(rc['accion'])}</p>"
              f"<p class='muted' style='font-size:.78rem'>"
              f"<b>Dato:</b> {esc(rc['dato'])} · "
              f"{esc(rc['disclaimer'])}</p>"
              f"</div>")

    return T.base(sesion, "Contador IA", h, active="/contador-ia")
