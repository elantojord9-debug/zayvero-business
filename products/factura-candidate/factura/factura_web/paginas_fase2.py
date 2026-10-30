"""Páginas privadas nuevas FASE 2: gastos/ingresos, proveedores y CxC vencidas."""
from __future__ import annotations

from datetime import date

import config
from factura_web import templates as T

esc, money = T.esc, T.money


def _opts_categorias(cats, elegida: str) -> str:
    return "".join(
        f"<option value='{c}'{' selected' if c == elegida else ''}>"
        f"{esc(c.capitalize())}</option>" for c in cats)


def _opts_metodos(elegido: str) -> str:
    return "".join(
        f"<option value='{m}'{' selected' if m == elegido else ''}>"
        f"{esc(m.capitalize())}</option>" for m in config.METODOS_PAGO)


def _opts_sucursales(sucursales, elegida) -> str:
    opts = "<option value=''>— sin sucursal —</option>"
    for s in sucursales or []:
        sel = " selected" if str(s["id"]) == str(elegida or "") else ""
        opts += f"<option value='{s['id']}'{sel}>{esc(s['nombre'])}</option>"
    return opts


def _opts_proveedores(proveedores, elegido) -> str:
    opts = "<option value=''>— sin proveedor —</option>"
    for p in proveedores or []:
        sel = " selected" if str(p["id"]) == str(elegido or "") else ""
        opts += (f"<option value='{p['id']}'{sel}>"
                 f"{esc(p['nombre'])}</option>")
    return opts


# --- gastos e ingresos ---------------------------------------------------------

def pagina_gastos(sesion, gastos, ingresos, total_gastos, total_ingresos,
                  desde="", hasta="", categoria="", msg="") -> str:
    csrf = esc(sesion["csrf"])
    h = T.alerta_ok(msg)
    puede = config.tiene_permiso(sesion.get("rol", ""), "gastos_gestion")
    cats = [""] + list(config.CATEGORIAS_GASTO)
    opts_cat = "".join(
        f"<option value='{c}'{' selected' if c == categoria else ''}>"
        f"{esc(c.capitalize()) if c else 'Todas las categorías'}</option>"
        for c in cats)
    h += f"""
<div class="toolbar">
  {('<a class="btn" href="/gastos/nuevo?tipo=gasto">+ Gasto</a>'
    '<a class="btn violet" href="/gastos/nuevo?tipo=ingreso">+ Ingreso</a>')
    if puede else ''}
  <div class="table-search">
    <label class="sr-only" for="q-gastos">Buscar movimientos</label>
    <input type="search" id="q-gastos" data-table-search="tbl-gastos tbl-ingresos"
           placeholder="Buscar por descripción o categoría…">
  </div>
  <span class="spacer"></span>
  <a class="btn ghost sm" href="/exportar/gastos.csv">Gastos CSV</a>
  <a class="btn ghost sm" href="/exportar/gastos.xls">Gastos Excel</a>
  <form method="get" action="/gastos" class="inline-form">
    <div class="field"><label for="g-desde">Desde</label>
      <input type="date" id="g-desde" name="desde" value="{esc(desde)}"></div>
    <div class="field"><label for="g-hasta">Hasta</label>
      <input type="date" id="g-hasta" name="hasta" value="{esc(hasta)}"></div>
    <div class="field"><label for="g-cat">Categoría</label>
      <select id="g-cat" name="categoria">{opts_cat}</select></div>
    <button class="btn" type="submit">Filtrar</button>
  </form>
</div>
<div class="cards" style="margin-bottom:16px">
  <div class="card"><div class="k">Total gastos (filtro)</div>
    <div class="v">{money(total_gastos)}</div></div>
  <div class="card"><div class="k">Otros ingresos (filtro)</div>
    <div class="v">{money(total_ingresos)}</div></div>
</div>
<h2>Gastos</h2>"""
    fg = "".join(
        f"<tr><td>{T.fecha_do(g['fecha'], False)}</td>"
        f"<td>{esc(g['categoria'].capitalize())}</td>"
        f"<td>{esc(g['descripcion'])}</td>"
        f"<td class='num'>{money(g['monto'])}</td>"
        f"<td>{esc(g['metodo'])}</td>"
        + (f"<td class='actions'><a class='btn small ghost' href='/gastos/{g['id']}/editar"
             f"?tipo=gasto'>Editar</a> "
             f"<form method='post' action='/gastos/{g['id']}/eliminar'"
             f" style='display:inline' onsubmit=\"return confirm('¿Eliminar"
             f" este gasto?')\">"
             f"<input type='hidden' name='csrf' value='{csrf}'>"
             f"<input type='hidden' name='tipo' value='gasto'>"
             f"<button class='btn small danger' type='submit'>"
             f"Eliminar</button></form></td>" if puede else "<td></td>")
        + "</tr>" for g in gastos)
    h += (f'<div class="table-wrap"><table id="tbl-gastos">'
          f"<thead><tr><th>Fecha</th><th>Categoría</th><th>Descripción</th>"
          f"<th class='num'>Monto</th><th>Método</th><th></th></tr></thead>"
          f"<tbody>{fg or '<tr><td colspan=6>Sin gastos en el filtro.</td></tr>'}"
          f"</tbody></table></div>")
    if not gastos and not ingresos:
        h += T.empty_state(
            "💸", "Aún no registras movimientos",
            "Lleva el control de lo que sale y entra de tu negocio: "
            "registra gastos e ingresos que no son ventas.",
            "/gastos/nuevo?tipo=gasto", "+ Registrar mi primer gasto")
    h += "<h2>Otros ingresos (no-venta)</h2>"
    fi = "".join(
        f"<tr><td>{T.fecha_do(g['fecha'], False)}</td>"
        f"<td>{esc(g['categoria'].capitalize())}</td>"
        f"<td>{esc(g['descripcion'])}</td>"
        f"<td class='num'>{money(g['monto'])}</td>"
        f"<td>{esc(g['metodo'])}</td>"
        + (f"<td class='actions'><a class='btn small ghost' href='/gastos/{g['id']}/editar"
             f"?tipo=ingreso'>Editar</a> "
             f"<form method='post' action='/gastos/{g['id']}/eliminar'"
             f" style='display:inline' onsubmit=\"return confirm('¿Eliminar"
             f" este ingreso?')\">"
             f"<input type='hidden' name='csrf' value='{csrf}'>"
             f"<input type='hidden' name='tipo' value='ingreso'>"
             f"<button class='btn small danger' type='submit'>"
             f"Eliminar</button></form></td>" if puede else "<td></td>")
        + "</tr>" for g in ingresos)
    if ingresos or gastos:
        h += (f'<div class="table-wrap"><table id="tbl-ingresos">'
              f"<thead><tr><th>Fecha</th><th>Categoría</th><th>Descripción</th>"
              f"<th class='num'>Monto</th><th>Método</th><th></th></tr></thead>"
              f"<tbody>{fi or '<tr><td colspan=6>Sin ingresos registrados.</td></tr>'}"
              f"</tbody></table></div>")
    return T.base(sesion, "Gastos e ingresos", h, active="/gastos")


def form_gasto(sesion, tipo, g=None, sucursales=(), proveedores=(),
               err="") -> str:
    g = g or {}
    tipo = "ingreso" if tipo == "ingreso" else "gasto"
    csrf = esc(sesion["csrf"])
    titulo = ("Nuevo ingreso" if tipo == "ingreso" else "Nuevo gasto")
    if g.get("id"):
        titulo = ("Editar ingreso" if tipo == "ingreso" else "Editar gasto")
        action = f"/gastos/{g['id']}/editar"
    else:
        action = "/gastos/nuevo"
    cats = (config.CATEGORIAS_INGRESO if tipo == "ingreso"
            else config.CATEGORIAS_GASTO)
    prov_row = ""
    if tipo == "gasto":
        prov_row = f"""
  <div class="field"><label for="fg-prov">Proveedor (opcional)</label>
    <select id="fg-prov" name="proveedor_id">
      {_opts_proveedores(proveedores, g.get('proveedor_id'))}</select></div>"""
    h = T.alerta_err(err) + f"""
<div class="form-card">
<form method="post" action="{action}">
  <input type="hidden" name="csrf" value="{csrf}">
  <input type="hidden" name="tipo" value="{tipo}">
  <div class="grid2">
    <div class="field"><label for="fg-fecha">Fecha <span class="req" aria-hidden="true">*</span></label>
      <input type="date" id="fg-fecha" name="fecha"
             value="{esc(g.get('fecha') or date.today().isoformat())}"
             required></div>
    <div class="field"><label for="fg-cat">Categoría <span class="req" aria-hidden="true">*</span></label>
      <select id="fg-cat" name="categoria">
        {_opts_categorias(cats, g.get('categoria') or '')}</select></div>
  </div>
  <div class="field"><label for="fg-desc">Descripción <span class="req" aria-hidden="true">*</span></label>
    <input type="text" id="fg-desc" name="descripcion"
           value="{esc(g.get('descripcion', ''))}" required maxlength="160"
           placeholder="Ej. Pago de alquiler de octubre"></div>
  <div class="grid2">
    <div class="field"><label for="fg-monto">Monto (RD$) <span class="req" aria-hidden="true">*</span></label>
      <input type="number" id="fg-monto" step="0.01" min="0.01" name="monto"
             value="{esc(g.get('monto', ''))}" required inputmode="decimal"></div>
    <div class="field"><label for="fg-met">Método <span class="req" aria-hidden="true">*</span></label>
      <select id="fg-met" name="metodo">{_opts_metodos(g.get('metodo') or 'efectivo')}
      </select></div>
  </div>
  <div class="grid2">
    <div class="field"><label for="fg-suc">Sucursal</label>
      <select id="fg-suc" name="sucursal_id">
        {_opts_sucursales(sucursales, g.get('sucursal_id'))}</select></div>
    {prov_row if tipo == 'gasto' else ''}
  </div>
  <div class="field"><label for="fg-comp">Comprobante / nota</label>
    <input type="text" id="fg-comp" name="comprobante"
           value="{esc(g.get('comprobante', ''))}" maxlength="80"
           placeholder="N.º de factura, recibo…"></div>
  <div class="form-actions">
    <button class="btn" type="submit">Guardar</button>
    <a class="btn ghost" href="/gastos">Cancelar</a>
  </div>
</form>
</div>"""
    return T.base(sesion, titulo, h, active="/gastos",
                  crumbs=[("/gastos", "Gastos e ingresos"), titulo])


# --- proveedores ---------------------------------------------------------------

def pagina_proveedores(sesion, proveedores, msg="") -> str:
    csrf = esc(sesion["csrf"])
    puede = config.tiene_permiso(sesion.get("rol", ""), "proveedores_gestion")
    h = T.alerta_ok(msg)
    if puede:
        h += ('<div class="toolbar"><a class="btn" href="/proveedores/nuevo">'
              '+ Proveedor</a></div>')
    filas = "".join(
        f"<tr><td>{esc(p['nombre'])}</td>"
        f"<td>{esc(p['rnc'])}</td>"
        f"<td>{esc(p['telefono'])}</td>"
        f"<td>{esc(p['email'])}</td>"
        f"<td class='num'>{money(p['balance'])}</td>"
        + (f"<td><a class='btn small ghost' href='/proveedores/{p['id']}"
             f"/editar'>Editar</a> "
             f"<form method='post' action='/proveedores/{p['id']}/eliminar'"
             f" style='display:inline' onsubmit=\"return confirm('¿Dar de"
             f" baja este proveedor? Se conserva su historial.')\">"
             f"<input type='hidden' name='csrf' value='{csrf}'>"
             f"<button class='btn small danger' type='submit'>Baja</button>"
             f"</form></td>" if puede else "<td></td>")
        + "</tr>" for p in proveedores)
    if proveedores:
        h += (f'<div class="table-wrap"><table>'
              f"<thead><tr><th>Nombre</th><th>RNC</th><th>Teléfono</th>"
              f"<th>Email</th><th class='num'>Balance</th><th></th></tr></thead>"
              f"<tbody>{filas}</tbody></table></div>")
    else:
        h += T.empty_state(
            "🏭", "Aún no tienes proveedores",
            "Registra a quienes te suplen mercancía para llevar su balance "
            "y tus compras.",
            "/proveedores/nuevo" if puede else "", "+ Registrar mi primer proveedor")
    return T.base(sesion, "Proveedores", h, active="/proveedores")


def form_proveedor(sesion, p=None, err="") -> str:
    p = p or {}
    csrf = esc(sesion["csrf"])
    action = (f"/proveedores/{p['id']}/editar" if p.get("id")
              else "/proveedores/nuevo")
    chk = "checked" if p.get("activo", 1) else ""
    titulo = "Editar proveedor" if p.get("id") else "Nuevo proveedor"
    h = T.alerta_err(err) + f"""
<div class="form-card">
<form method="post" action="{action}">
  <input type="hidden" name="csrf" value="{csrf}">
  <div class="field"><label for="pv-nombre">Nombre <span class="req" aria-hidden="true">*</span></label>
    <input type="text" id="pv-nombre" name="nombre" value="{esc(p.get('nombre', ''))}"
           required maxlength="120" autocomplete="organization"></div>
  <div class="grid2">
    <div class="field"><label for="pv-rnc">RNC (opcional, 9 u 11 dígitos)</label>
      <input type="text" id="pv-rnc" name="rnc" value="{esc(p.get('rnc', ''))}"
             inputmode="numeric" maxlength="11"></div>
    <div class="field"><label for="pv-tel">Teléfono</label>
      <input type="tel" id="pv-tel" name="telefono"
             value="{esc(p.get('telefono', ''))}" autocomplete="tel" maxlength="30"></div>
  </div>
  <div class="field"><label for="pv-email">Email</label>
    <input type="email" id="pv-email" name="email" value="{esc(p.get('email', ''))}"
           autocomplete="email" maxlength="120"></div>
  <div class="field"><label for="pv-dir">Dirección</label>
    <input type="text" id="pv-dir" name="direccion"
           value="{esc(p.get('direccion', ''))}" maxlength="160"></div>
  <div class="grid2">
    <div class="field"><label for="pv-bal">Balance (RD$)</label>
      <input type="number" id="pv-bal" step="0.01" name="balance"
             value="{esc(p.get('balance', '0'))}" inputmode="decimal"></div>
    <div class="field"><label class="check-row"><input type="checkbox" name="activo" {chk}>
      Activo</label></div>
  </div>
  <div class="form-actions">
    <button class="btn" type="submit">Guardar proveedor</button>
    <a class="btn ghost" href="/proveedores">Cancelar</a>
  </div>
</form>
</div>"""
    return T.base(sesion, titulo, h, active="/proveedores",
                  crumbs=[("/proveedores", "Proveedores"), titulo])


# --- CxC vencidas ---------------------------------------------------------------

def pagina_cxc_vencidas(sesion, vencidas, resumen, total, msg="") -> str:
    h = T.alerta_ok(msg)
    h += f"""
<div class="toolbar">
  <a class="btn ghost sm" href="/cxc">← Todas las cuentas por cobrar</a>
</div>
<div class="cards" style="margin-bottom:16px">
  <div class="card accent"><div class="k">Total vencido</div>
    <div class="v">{money(total)}</div></div>
  <div class="card"><div class="k">Facturas vencidas</div>
    <div class="v">{len(vencidas)}</div></div>
</div>"""
    if resumen:
        fr = "".join(
            f"<tr><td>{esc(r['cliente_nombre'])}</td>"
            f"<td class='num'>{r['cantidad']}</td>"
            f"<td class='num'><b>{money(r['total'])}</b></td></tr>"
            for r in resumen)
        h += (f"<h2>Resumen por cliente</h2>"
              f'<div class="table-wrap"><table>'
              f"<thead><tr><th>Cliente</th><th class='num'>Facturas</th>"
              f"<th class='num'>Total vencido</th></tr></thead>"
              f"<tbody>{fr}</tbody></table></div>")
    filas = "".join(
        f"<tr><td><a href='/facturas/{v['id']}'>{esc(v['numero'])}</a></td>"
        f"<td>{esc(v.get('cliente_nombre', ''))}</td>"
        f"<td>{T.fecha_do(v.get('vencimiento'), False)}</td>"
        f"<td class='num'>{v.get('dias_vencidos', 0)}</td>"
        f"<td class='num'>{money(v['total'])}</td>"
        f"<td class='num'><b>{money(v['balance'])}</b></td>"
        f"<td class='actions'><a class='btn small' href='/facturas/{v['id']}'>Ver factura</a>"
        f"</td></tr>" for v in vencidas)
    h += (f"<h2>Facturas vencidas</h2>")
    if vencidas:
        h += (f'<div class="table-wrap"><table>'
              f"<thead><tr><th>Factura</th><th>Cliente</th><th>Vencimiento</th>"
              f"<th class='num'>Días vencida</th><th class='num'>Total</th>"
              f"<th class='num'>Balance</th><th></th></tr></thead>"
              f"<tbody>{filas}</tbody></table></div>")
    else:
        h += T.empty_state(
            "🎉", "Sin cuentas vencidas",
            "¡Vas al día! Ninguna factura pendiente pasó su fecha de "
            "vencimiento.")
    return T.base(sesion, "Cuentas vencidas", h, active="/cxc")
