"""Plantillas HTML (stdlib, sin motores externos) — español, responsive."""
from __future__ import annotations

import secrets

import html
from decimal import Decimal

import config


def esc(s) -> str:
    return html.escape(str(s if s is not None else ""))


def money(v) -> str:
    try:
        d = Decimal(str(v))
    except Exception:
        d = Decimal("0")
    return f"RD$ {d:,.2f}"


def fecha_do(s, con_hora: bool = True) -> str:
    """Fecha ISO → formato dominicano DD/MM/AAAA [HH:MM]."""
    if not s:
        return "—"
    t = str(s).strip().replace("T", " ")
    partes = (t.split(" ", 1) + [""])[:2]
    fecha, hora = partes[0], partes[1]
    f = fecha
    d = fecha.split("-")
    if len(d) == 3 and len(d[0]) == 4:
        f = f"{d[2]}/{d[1]}/{d[0]}"
    if con_hora and len(hora) >= 5:
        return f"{f} {hora[:5]}"
    return f


def empty_state(icono: str, titulo: str, texto: str,
                cta_href: str = "", cta_txt: str = "") -> str:
    """Bloque amable para listas vacías."""
    cta = (f'<p><a class="btn" href="{cta_href}">{esc(cta_txt)}</a></p>'
           if cta_href and cta_txt else "")
    return (f'<div class="empty"><span class="icon" aria-hidden="true">'
            f'{icono}</span><h3>{esc(titulo)}</h3><p>{esc(texto)}</p>'
            f'{cta}</div>')


NAV = [
    ("/panel", "Panel", "panel"),
    ("/pos", "POS", "pos"),
    ("/caja", "Caja", "caja"),
    ("/facturas", "Facturas", "ver_ventas"),
    ("/cotizaciones", "Cotizaciones", "ver_ventas"),
    ("/devoluciones", "Devoluciones", "devoluciones"),
    ("/clientes", "Clientes", "clientes_ver"),
    ("/inventario", "Inventario", "inventario_ver"),
    ("/sucursales", "Sucursales", "sucursales_ver"),
    ("/cxc", "Cuentas por cobrar", "cxc_ver"),
    ("/gastos", "Gastos e ingresos", "gastos_ver"),
    ("/proveedores", "Proveedores", "proveedores_ver"),
    ("/reportes", "Reportes", "reportes"),
    ("/contador-ia", "Contador IA", "contador_ia_ver"),
    ("/importar", "Importar", "importar"),
    ("/ecf", "e-CF (simulación)", "ecf_ver"),
    ("/auditoria", "Auditoría", "auditoria"),
    ("/suscripcion", "Suscripción", "suscripcion"),
    ("/soporte", "Soporte", "soporte"),
    ("/config", "Configuración", "config"),
]

# Alias antiguos → nuevos (el servidor redirige)
NAV_ALIAS = {"/": "/panel"}


def base(sesion: dict, titulo: str, cuerpo: str, active: str = "",
         extra_head: str = "", crumbs=None) -> str:
    rol = sesion.get("r", "")
    items = []
    for href, nombre, permiso in NAV:
        if not config.tiene_permiso(rol, permiso):
            continue
        cls = ' class="active"' if href == active else ""
        items.append(f'<a href="{href}"{cls}>{nombre}</a>')
    nav = "\n".join(items)
    # Banner de trial (FASE 4): aviso cuando quedan pocos días o venció.
    trial_banner = ""
    dias = sesion.get("trial_dias")
    if isinstance(dias, int):
        if dias < 0:
            trial_banner = (f'<div class="trial-banner danger">Prueba vencida '
                            f'hace {-dias} días — cuenta en solo lectura. '
                            f'<a href="/suscripcion">Ver suscripción</a></div>')
        elif dias <= 3:
            trial_banner = (f'<div class="trial-banner">Quedan {dias} días de '
                            f'prueba gratis. <a href="/suscripcion">Ver '
                            f'suscripción</a></div>')
    cuerpo = '<div class="sim-banner">GESTIÓN ADMINISTRATIVA — SIN VALOR FISCAL</div>' + trial_banner + cuerpo
    # Breadcrumbs opcionales: lista de (href, texto) o texto (página actual).
    crumb_html = ""
    if crumbs:
        lis = []
        for c in crumbs:
            if isinstance(c, (tuple, list)):
                lis.append(
                    f'<li><a href="{c[0]}">{esc(c[1])}</a></li>')
            else:
                lis.append(
                    f'<li><span aria-current="page">{esc(c)}</span></li>')
        crumb_html = ('<nav aria-label="Ruta de navegación">'
                      '<ol class="crumbs">' + "".join(lis) +
                      "</ol></nav>")
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(titulo)} — ZAYVERO FACTURA</title>
<link rel="stylesheet" href="/static/style.css">
<meta name="csrf-token" content="{esc(sesion.get('csrf', ''))}">
{extra_head}
</head>
<body>
<div class="layout">
<aside class="sidebar" id="sidebar" aria-label="Navegación principal">
  <div class="brand">
    <strong class="brand-wordmark">ZAYVERO <small>FACTURA</small></strong>
  </div>
  <nav class="nav">{nav}</nav>
  <div class="side-foot">Rol: {esc(rol)}<br>
  <a href="/logout" style="color:#9fb3d1">Cerrar sesión</a></div>
</aside>
<button class="nav-overlay" id="navOverlay" aria-label="Cerrar menú" tabindex="-1"></button>
<div class="main">
  <div class="topbar">
    <button class="nav-toggle" id="navToggle" aria-label="Abrir menú de navegación"
            aria-expanded="false" aria-controls="sidebar">
      <span class="bars" aria-hidden="true"></span></button>
    <div class="title"><h1>{esc(titulo)}</h1></div>
    <div class="who">{esc(sesion.get("empresa_nombre", ""))}</div>
  </div>
  <div class="content">{crumb_html}{cuerpo}</div>
</div>
</div>
<script>
(function(){{
  /* Drawer de navegación en móvil */
  var t=document.getElementById('navToggle'),o=document.getElementById('navOverlay');
  function close(){{document.body.classList.remove('nav-open');
    if(t)t.setAttribute('aria-expanded','false');}}
  if(t){{t.addEventListener('click',function(){{
    var op=document.body.classList.toggle('nav-open');
    t.setAttribute('aria-expanded',op?'true':'false');}});}}
  if(o){{o.addEventListener('click',close);}}
  document.addEventListener('keydown',function(e){{if(e.key==='Escape')close();}});
  /* Confirmación genérica: <form data-confirm="¿Seguro?"> */
  document.querySelectorAll('[data-confirm]').forEach(function(el){{
    el.addEventListener('submit',function(ev){{
      if(!confirm(el.getAttribute('data-confirm')))ev.preventDefault();}});
  }});
  /* Búsqueda client-side en tablas (insensible a tildes):
     <input data-table-search="id-tabla1 id-tabla2"> */
  function norm(s){{return s.toLowerCase().normalize('NFD')
    .replace(/[\\u0300-\\u036f]/g,'');}}
  document.querySelectorAll('[data-table-search]').forEach(function(inp){{
    var ids=inp.getAttribute('data-table-search').split(/\\s+/);
    var rows=[];
    ids.forEach(function(id){{
      var tbl=document.getElementById(id);
      if(tbl)rows=rows.concat(Array.prototype.slice.call(
        tbl.querySelectorAll('tbody tr')));}});
    if(!rows.length)return;
    var textos=rows.map(function(r){{return norm(r.textContent);}});
    inp.addEventListener('input',function(){{
      var q=norm(inp.value.trim());
      rows.forEach(function(r,i){{
        r.classList.toggle('row-hidden', q!==''&&textos[i].indexOf(q)<0);}});
    }});
  }});
}})();
</script>
</body>
</html>"""


def login_page(error: str = "", csrf: str = "") -> str:
    err = f'<div class="alert err">{esc(error)}</div>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Entrar — ZAYVERO FACTURA</title>
<link rel="stylesheet" href="/static/style.css">
</head>
<body>
<div class="login-wrap">
  <div class="login-card">
    <strong class="brand-wordmark">ZAYVERO <small>FACTURA</small></strong>
    <h1>ZAYVERO FACTURA</h1>
    <p class="slogan">“Fiel en lo mucho, fiel en lo poco” <span>· Lucas 16:10</span></p>
    <p>Sistema de facturación para empresas de RD</p>
    {err}
    <form method="post" action="/login">
      <input type="hidden" name="csrf" value="{esc(csrf)}">
      <div class="field">
        <label for="email">Correo electrónico</label>
        <input type="email" id="email" name="email" required autofocus
               placeholder="usuario@empresa.do">
      </div>
      <div class="field">
        <label for="password">Contraseña</label>
        <input type="password" id="password" name="password" required>
      </div>
      <button class="btn" type="submit" style="width:100%">Entrar</button>
    </form>
  </div>
</div>
</body>
</html>"""


def alerta_ok(msg: str) -> str:
    return f'<div class="alert ok">{esc(msg)}</div>' if msg else ""


def alerta_err(msg: str) -> str:
    return f'<div class="alert err">{esc(msg)}</div>' if msg else ""


def badge(estado: str) -> str:
    cls = "b-" + esc(estado)
    return f'<span class="badge {cls}">{esc(estado)}</span>'


def sim_banner() -> str:
    return ("""<div class="sim-banner">SIMULACIÓN — SIN VALIDEZ FISCAL ANTE DGII"""
            """<small>Este módulo genera comprobantes de PRUEBA con respuestas """
            """sintéticas del simulador local. No está certificado por la DGII """
            """ni por ningún proveedor. No lo use para facturación real.</small>"""
            """</div>""")


# --- panel -------------------------------------------------------------------

def pagina_panel(sesion, resumen, cxc_total, stock_bajo, msg="") -> str:
    rol = sesion.get("r", "")
    btn_pos = ('<a class="btn" href="/pos">Abrir caja (POS)</a>'
               if config.tiene_permiso(rol, "pos") else "")
    btn_fact = ('<a class="btn violet" href="/facturas/nueva">Nueva factura</a>'
                if config.tiene_permiso(rol, "crear_ventas") else "")
    btn_cot = ('<a class="btn ghost" href="/cotizaciones/nueva">Nueva cotización</a>'
               if config.tiene_permiso(rol, "crear_ventas") else "")
    btn_rep = ('<a class="btn ghost" href="/reportes">Ver reportes</a>'
               if config.tiene_permiso(rol, "reportes") else "")
    h = alerta_ok(msg)
    h += f"""
<div class="cards">
  <div class="card accent">
    <div class="k">Ventas de hoy ({esc(resumen['hoy'].get('dia',''))})</div>
    <div class="v">{money(resumen['hoy']['total'])} <small>/ {resumen['hoy']['cantidad']} ventas</small></div>
  </div>
  <div class="card">
    <div class="k">Cuentas por cobrar</div>
    <div class="v">{money(cxc_total)}</div>
  </div>
  <div class="card">
    <div class="k">Productos con stock bajo</div>
    <div class="v">{len(stock_bajo)}</div>
  </div>
  <div class="card">
    <div class="k">Últimos 30 días</div>
    <div class="v">{money(resumen['ultimos_30d_total'])} <small>/ {resumen['ultimos_30d_cantidad']} ventas</small></div>
  </div>
</div>
<h2>Accesos rápidos</h2>
<div class="toolbar">
  {btn_pos}
  {btn_fact}
  {btn_cot}
  {btn_rep}
</div>"""
    if stock_bajo:
        filas = "".join(
            f"<tr><td>{esc(p['sku'])}</td><td>{esc(p['nombre'])}</td>"
            f"<td class='num'>{esc(p['stock'])}</td>"
            f"<td class='num'>{esc(p.get('stock_minimo', config.STOCK_BAJO_UMBRAL))}</td>"
            f"<td>{badge(p.get('estado', 'bajo'))}</td></tr>"
            for p in stock_bajo[:10])
        h += f"""<h2>Alertas de inventario</h2>
<p class="sub">Productos con stock igual o menor a su mínimo configurado.
<a href="/inventario">Gestionar inventario</a></p>
<div class="table-wrap"><table>
<thead><tr><th>SKU</th><th>Producto</th><th class="num">Stock</th>
<th class="num">Mínimo</th><th>Estado</th></tr></thead>
<tbody>{filas}</tbody></table></div>"""
    return base(sesion, "Panel", h, active="/panel")


# --- POS ---------------------------------------------------------------------

def pagina_pos(sesion, productos, q="", msg="", err="", sucursales=(),
               caja_abierta=True) -> str:
    h = alerta_ok(msg) + alerta_err(err)
    if not caja_abierta:
        h += ('<div class="alert err"><b>Caja cerrada.</b> Debe '
              '<a href="/caja">abrir el turno de caja</a> antes de vender.'
              '</div>')
    opts_suc = "".join(
        f"<option value='{s['id']}'>{esc(s['nombre'])}</option>"
        for s in (sucursales or []))
    h += f"""
<div class="pos-head no-print">
  <div class="field" style="margin:0"><label>Sucursal de la venta</label>
    <select id="sucursal">{opts_suc}</select></div>
  <a class="btn ghost sm" href="/caja">Ir a caja</a>
</div>"""
    filas = ""
    for p in productos[:60]:
        stock_txt = "—" if str(p.get("tipo") or "producto") == "servicio" \
            else esc(p['stock'])
        filas += (
            f"<tr><td>{esc(p['sku'])}</td><td>{esc(p['nombre'])}</td>"
            f"<td class='num'>{money(p['precio'])}</td>"
            f"<td class='num'>{stock_txt}</td>"
            f"<td><button class='btn small' data-add='{p['id']}' "
            f"data-nombre='{esc(p['nombre'])}' data-precio='{esc(p['precio'])}' "
            f"data-exento='{p['itbis_exento']}' data-stock='{esc(p['stock'])}' "
            f"data-tipo='{esc(p.get('tipo') or 'producto')}'>"
            f"Agregar</button></td></tr>")
    h += f"""
<div class="grid2">
<div>
  <form method="get" action="/pos" class="inline-form" style="margin-bottom:12px">
    <label class="sr-only" for="pos-q">Buscar producto o SKU</label>
    <input type="search" id="pos-q" name="q" value="{esc(q)}"
           placeholder="Buscar producto o SKU…" style="flex:1;min-width:180px" autofocus>
    <button class="btn" type="submit">Buscar</button>
  </form>
  <div class="table-wrap"><table>
  <thead><tr><th>SKU</th><th>Producto</th><th class="num">Precio</th>
  <th class="num">Stock</th><th></th></tr></thead>
  <tbody>{filas or '<tr><td colspan="5">Sin resultados.</td></tr>'}</tbody></table></div>
</div>
<div>
  <div class="card">
    <div class="k">Ticket actual</div>
    <div id="ticket"><p class="sub">Agregue productos con el botón “Agregar”.</p></div>
    <div id="totales" class="pos-totals" aria-live="polite"></div>
    <div class="field" style="margin-top:12px">
      <label for="metodo">Método de pago</label>
      <select id="metodo">
        <option value="efectivo">Efectivo</option>
        <option value="tarjeta">Tarjeta</option>
        <option value="transferencia">Transferencia</option>
      </select>
    </div>
    <button class="btn block" id="cobrar" type="button">Cobrar</button>
    <button class="btn ghost block" id="limpiar" type="button" style="margin-top:8px">Limpiar ticket</button>
    <div id="pos-msg" style="margin-top:10px" aria-live="polite"></div>
  </div>
  <div id="ticket-print"></div>
</div>
</div>
"""
    h += """<script>
const TASA = 18;
let ticket = [];

function operationKey(path, body){
  const storageKey='factura.pending:'+path;
  const previous=JSON.parse(sessionStorage.getItem(storageKey)||'null');
  if(previous && previous.body===body) return previous.key;
  if(previous) throw new Error('Hay una operación pendiente. Reintente el ticket original antes de cambiarlo.');
  const key=crypto.randomUUID();
  sessionStorage.setItem(storageKey,JSON.stringify({key,body}));
  return key;
}
async function sendOperation(path,payload,csrf){
  const body=JSON.stringify(payload);
  const key=operationKey(path,body);
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,'Idempotency-Key':key},body});
  const j=await r.json();
  if(r.ok && j.ok) sessionStorage.removeItem('factura.pending:'+path);
  else if(r.status>=400 && r.status<500) sessionStorage.removeItem('factura.pending:'+path);
  return j;
}

function escapeHtml(value){ const el=document.createElement('span'); el.textContent=String(value); return el.innerHTML; }
function fmt(n){ return 'RD$ ' + Number(n).toLocaleString('es-DO', {minimumFractionDigits:2, maximumFractionDigits:2}); }
function render(){
  const box = document.getElementById('ticket');
  if (!ticket.length){ box.innerHTML = '<p class="sub">Agregue productos con el botón “Agregar”.</p>'; document.getElementById('totales').innerHTML=''; return; }
  let rows = ticket.map((t,i)=>`<tr><td>${escapeHtml(t.nombre)}</td><td class="num">${t.cant}</td><td class="num">${fmt(t.precio)}</td><td class="num">${fmt(t.cant*t.precio)}</td><td><button class="btn small danger" onclick="quitar(${i})">×</button></td></tr>`).join('');
  box.innerHTML = `<div class="table-wrap"><table><tr><th>Producto</th><th class="num">Cant</th><th class="num">Precio</th><th class="num">Subt</th><th></th></tr>${rows}</table></div>`;
  let sub=0, itb=0;
  ticket.forEach(t=>{ const s=t.cant*t.precio; sub+=s; if(!t.exento) itb+=s*TASA/100; });
  document.getElementById('totales').innerHTML =
    `<div class="kv" style="margin-top:10px"><dt>Subtotal</dt><dd class="num">${fmt(sub)}</dd>`
    +`<dt>ITBIS (18%)</dt><dd class="num">${fmt(itb)}</dd>`
    +`<dt><b>Total</b></dt><dd class="num"><b>${fmt(sub+itb)}</b></dd></div>`;
}
function quitar(i){ ticket.splice(i,1); render(); }
document.querySelectorAll('[data-add]').forEach(b=>b.addEventListener('click',()=>{
  const id=+b.dataset.add, nombre=b.dataset.nombre, precio=+b.dataset.precio;
  const exento=b.dataset.exento==='1', stock=+b.dataset.stock;
  const esServicio=(b.dataset.tipo||'producto')==='servicio';
  const ex=ticket.find(t=>t.id===id);
  if(ex){ if(!esServicio && ex.cant+1>stock){ alert('Stock insuficiente'); return; } ex.cant++; }
  else { if(!esServicio && stock<1){ alert('Sin stock'); return; } ticket.push({id,nombre,precio,cant:1,exento}); }
  render();
}));
document.getElementById('limpiar').onclick=()=>{ ticket=[]; render(); };
document.getElementById('cobrar').onclick=async()=>{
  if(!ticket.length){ alert('El ticket está vacío'); return; }
  const csrf=document.querySelector('meta[name=csrf-token]').content;
  const button=document.getElementById('cobrar');
  if(button.disabled) return;
  button.disabled=true;
  let j;
  try { j=await sendOperation('/api/pos/cobrar',{items:ticket.map(t=>({producto_id:t.id,nombre:t.nombre,cantidad:t.cant,precio:t.precio,itbis_exento:t.exento})),metodo_pago:document.getElementById('metodo').value,sucursal_id:document.getElementById('sucursal').value},csrf); }
  catch(e){ document.getElementById('pos-msg').textContent='No se pudo confirmar. Reintente el mismo ticket: '+e.message; return; }
  finally { button.disabled=false; }
  const box=document.getElementById('pos-msg');
  if(!j.ok){ box.innerHTML=`<div class="alert err">${escapeHtml(j.error)}</div>`; return; }
  box.innerHTML=`<div class="alert ok">Venta ${j.numero} cobrada: ${fmt(j.total)}</div>`;
  document.getElementById('ticket-print').innerHTML =
    `<div class="ticket" style="margin-top:14px"><h3>Ticket ${j.numero}</h3><p>SIN VALOR FISCAL</p>`
    + j.items.map(t=>`<div>${t.cantidad} × ${escapeHtml(t.nombre)} — ${fmt(t.cantidad*t.precio)}</div>`).join('')
    + `<hr><div><b>Total: ${fmt(j.total)}</b> (${j.metodo_pago})</div>`
    + `<div class="no-print" style="margin-top:10px"><button class="btn small" onclick="window.print()">Imprimir ticket</button> `
    + `<a class="btn small ghost" href="/facturas/${j.id}">Ver factura</a></div></div>`;
  ticket=[]; render();
};
render();
</script>"""
    return base(sesion, "POS / Caja", h, active="/pos")


# --- facturas ------------------------------------------------------------------

def pagina_facturas(sesion, ventas, msg="", err="") -> str:
    h = alerta_ok(msg) + alerta_err(err)
    h += """<div class="toolbar">
      <div class="table-search">
        <label class="sr-only" for="q-facturas">Buscar facturas</label>
        <input type="search" id="q-facturas" data-table-search="tbl-facturas"
               placeholder="Buscar por número, cliente o estado…">
      </div>
      <span class="spacer"></span>
      <a class="btn" href="/facturas/nueva">+ Nueva factura</a>
    </div>"""
    if ventas:
        filas = "".join(
            f"<tr><td><a href='/facturas/{v['id']}'>{esc(v['numero'])}</a></td>"
            f"<td>{esc(v['tipo'])}</td>"
            f"<td>{esc(v.get('cliente_nombre') or '')}</td>"
            f"<td class='num'>{money(v['total'])}</td>"
            f"<td>{badge(v['estado'])}</td>"
            f"<td>{fecha_do(v['creada'])}</td></tr>"
            for v in ventas)
        h += (f'<div class="table-wrap"><table id="tbl-facturas">'
              f"<thead><tr><th>Número</th><th>Tipo</th><th>Cliente</th>"
              f"<th class='num'>Total</th><th>Estado</th><th>Creada</th></tr></thead>"
              f"<tbody>{filas}</tbody></table></div>")
    else:
        h += empty_state(
            "🧾", "Aún no has emitido facturas",
            "Crea tu primera factura en segundos: elige el cliente, agrega "
            "los productos y listo.",
            "/facturas/nueva", "+ Emitir mi primera factura")
    return base(sesion, "Facturas", h, active="/facturas")


def pagina_factura_nueva(sesion, clientes, productos, tasa, err="",
                         sucursales=()) -> str:
    h = alerta_err(err)
    opts_suc = "".join(
        f"<option value='{s['id']}'>{esc(s['nombre'])}</option>"
        for s in (sucursales or []))
    opts_cli = "".join(
        f"<option value='{c['id']}'>{esc(c['nombre'])}"
        f"{' · RNC '+esc(c['rnc']) if c['rnc'] else ''}</option>"
        for c in clientes)
    opts_prod = "".join(
        f"<option value='{p['id']}' data-nombre='{esc(p['nombre'])}' "
        f"data-precio='{esc(p['precio'])}' data-exento='{p['itbis_exento']}' "
        f"data-stock='{esc(p['stock'])}'>{esc(p['nombre'])} — {money(p['precio'])}</option>"
        for p in productos)
    h += f"""
<div class="form-card wide">
  <div class="field">
    <label for="cliente">Cliente</label>
    <select id="cliente"><option value="">— Consumidor final —</option>{opts_cli}</select>
  </div>
  <div class="field">
    <label for="prod">Agregar producto</label>
    <div style="display:flex;gap:8px">
      <select id="prod" style="flex:1"><option value="">— Seleccionar —</option>{opts_prod}</select>
      <label class="sr-only" for="cant">Cantidad</label>
      <input type="number" id="cant" value="1" min="1" style="width:80px">
      <button class="btn" type="button" id="add">Agregar</button>
    </div>
  </div>
  <div id="items"></div>
  <div class="field" style="max-width:200px">
    <label for="desc">Descuento global (RD$)</label>
    <input type="number" id="desc" value="0" min="0" step="0.01">
  </div>
  <div id="tot"></div>
  <div class="field" style="max-width:280px">
    <label for="sucursal">Sucursal</label>
    <select id="sucursal">{opts_suc}</select>
  </div>
  <div class="field">
    <label for="metodo">Cobro</label>
    <select id="metodo">
      <option value="">Dejar pendiente (a crédito)</option>
      <option value="efectivo">Cobrar ahora: efectivo</option>
      <option value="tarjeta">Cobrar ahora: tarjeta</option>
      <option value="transferencia">Cobrar ahora: transferencia</option>
    </select>
  </div>
  <div class="form-actions">
    <button class="btn" id="guardar" type="button">Emitir factura</button>
    <a class="btn ghost" href="/facturas">Cancelar</a>
  </div>
  <div id="msg" style="margin-top:10px"></div>
</div>
"""
    h += """<script>
const TASA = __TASA__;
let items = [];

function operationKey(path, body){
  const storageKey='factura.pending:'+path;
  const previous=JSON.parse(sessionStorage.getItem(storageKey)||'null');
  if(previous && previous.body===body) return previous.key;
  if(previous) throw new Error('Hay una operación pendiente. Reintente el ticket original antes de cambiarlo.');
  const key=crypto.randomUUID();
  sessionStorage.setItem(storageKey,JSON.stringify({key,body}));
  return key;
}
async function sendOperation(path,payload,csrf){
  const body=JSON.stringify(payload);
  const key=operationKey(path,body);
  const r=await fetch(path,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':csrf,'Idempotency-Key':key},body});
  const j=await r.json();
  if(r.ok && j.ok) sessionStorage.removeItem('factura.pending:'+path);
  else if(r.status>=400 && r.status<500) sessionStorage.removeItem('factura.pending:'+path);
  return j;
}

function escapeHtml(value){ const el=document.createElement('span'); el.textContent=String(value); return el.innerHTML; }
function fmt(n){ return 'RD$ ' + Number(n).toLocaleString('es-DO', {minimumFractionDigits:2, maximumFractionDigits:2}); }
function render(){
  const box=document.getElementById('items');
  box.innerHTML = items.length
    ? `<div class="table-wrap"><table><tr><th>Producto</th><th class="num">Cant</th><th class="num">Precio</th><th class="num">Subtotal</th><th></th></tr>`
      + items.map((t,i)=>`<tr><td>${escapeHtml(t.nombre)}</td><td class="num">${t.cantidad}</td><td class="num">${fmt(t.precio)}</td><td class="num">${fmt(t.cantidad*t.precio)}</td><td><button class="btn small danger" onclick="items.splice(${i},1);render()">×</button></td></tr>`).join('') + `</table></div>`
    : '<p class="sub">Sin ítems.</p>';
  let sub=0, itb=0;
  items.forEach(t=>{ const s=t.cantidad*t.precio - (t.descuento||0); sub+=s; if(!t.itbis_exento) itb+=s*TASA/100; });
  const dg=+document.getElementById('desc').value||0;
  document.getElementById('tot').innerHTML =
    `<div class="kv"><dt>Subtotal</dt><dd class="num">${fmt(sub)}</dd><dt>ITBIS</dt><dd class="num">${fmt(itb)}</dd><dt>Descuento</dt><dd class="num">${fmt(dg)}</dd><dt><b>Total</b></dt><dd class="num"><b>${fmt(sub+itb-dg)}</b></dd></div>`;
}
document.getElementById('add').onclick=()=>{
  const s=document.getElementById('prod'); if(!s.value) return;
  const o=s.selectedOptions[0], cant=+document.getElementById('cant').value||1;
  items.push({producto_id:+s.value, nombre:o.dataset.nombre, cantidad:cant, precio:+o.dataset.precio, itbis_exento:o.dataset.exento==='1', descuento:0});
  render();
};
document.getElementById('desc').oninput=render;
document.getElementById('guardar').onclick=async()=>{
  if(!items.length){ alert('Agregue al menos un ítem'); return; }
  const csrf=document.querySelector('meta[name=csrf-token]').content;
  const button=document.getElementById('guardar');
  if(button.disabled) return;
  button.disabled=true;
  let j;
  try { j=await sendOperation('/api/facturas',{cliente_id:document.getElementById('cliente').value||null, items, descuento:+document.getElementById('desc').value||0, metodo_pago:document.getElementById('metodo').value||null, sucursal_id:document.getElementById('sucursal').value||null},csrf); }
  catch(e){ document.getElementById('msg').textContent='No se pudo confirmar. Reintente: '+e.message; return; }
  finally { button.disabled=false; }
  if(!j.ok){ document.getElementById('msg').innerHTML=`<div class="alert err">${escapeHtml(j.error)}</div>`; return; }
  location.href='/facturas/'+j.id;
};
render();
</script>""".replace("__TASA__", str(tasa))
    return base(sesion, "Nueva factura", h, active="/facturas",
                crumbs=[("/facturas", "Facturas"), "Nueva factura"])


def pagina_factura_detalle(sesion, v, empresa, pagos, puede_anular,
                           msg="", err="") -> str:
    h = alerta_ok(msg) + alerta_err(err)
    filas = "".join(
        f"<tr><td>{esc(it['nombre'])}</td><td class='num'>{esc(it['cantidad'])}</td>"
        f"<td class='num'>{money(it['precio'])}</td>"
        f"<td class='num'>{money(it['subtotal'])}</td>"
        f"<td class='num'>{money(it['itbis'])}</td></tr>" for it in v["items"])
    filas_pagos = "".join(
        f"<tr><td>{esc(p['metodo'])}</td><td class='num'>{money(p['monto'])}</td>"
        f"<td>{fecha_do(p['creada'])}</td></tr>"
        for p in pagos)
    h += f"""
<div class="toolbar no-print">
  <a class="btn ghost" href="/facturas">← Volver</a>
  <span class="spacer"></span>
  <button class="btn" onclick="window.print()">🖨 Imprimir / PDF</button>
  {f'''<form method="post" action="/facturas/{v['id']}/anular" style="display:inline"
        onsubmit="return confirm('¿Anular esta factura? No se puede deshacer.')">
        <input type="hidden" name="csrf" value="{esc(sesion['csrf'])}">
        <button class="btn danger" type="submit">Anular factura</button>
       </form>''' if puede_anular else ''}
</div>
<div class="doc"><p class="fiscal-notice">DOCUMENTO ADMINISTRATIVO — SIN VALOR FISCAL</p>
  <div class="doc-head">
    <div class="doc-brand">
      <img src="/static/logo-isotipo.png" alt="Logo de la empresa">
      <div><div class="name">{esc(empresa['nombre'])}</div>
      <p class="sub">RNC {esc(empresa['rnc'])} · {esc(empresa['direccion'])} · {esc(empresa['telefono'])}</p></div>
    </div>
    <div class="doc-meta">{badge(v['estado'])}<br>
      <b>{esc(v['numero'])}</b> ({esc(v['tipo'])})<br>
      {fecha_do(v['creada'])}</div>
  </div>
  <div class="kv">
    <dt>Cliente</dt><dd>{esc(v.get('cliente_nombre') or 'Consumidor final')}</dd>
    {f"<dt>e-CF</dt><dd><a href='/ecf/{v['ecf_id']}' class='no-print'>Ver comprobante (simulación)</a></dd>" if v.get('ecf_id') else ""}
  </div>
  <h2>Detalle</h2>
  <div class="table-wrap"><table>
  <thead><tr><th>Ítem</th><th class="num">Cant</th><th class="num">Precio</th>
  <th class="num">Subtotal</th><th class="num">ITBIS</th></tr></thead>
  <tbody>{filas}</tbody></table></div>
  <div class="kv doc-totals">
    <dt>Subtotal</dt><dd class="num">{money(v['subtotal'])}</dd>
    <dt>ITBIS</dt><dd class="num">{money(v['itbis'])}</dd>
    <dt>Descuento</dt><dd class="num">{money(v['descuento'])}</dd>
    <dt><b>Total</b></dt><dd class="num"><b>{money(v['total'])}</b></dd>
  </div>
  <h2>Pagos</h2>
  <div class="table-wrap"><table><thead><tr><th>Método</th><th class="num">Monto</th><th>Fecha</th></tr></thead>
  <tbody>{filas_pagos or '<tr><td colspan="3">Sin pagos registrados.</td></tr>'}</tbody></table></div>
  <p class="sub doc-foot">Documento generado por ZAYVERO FACTURA (prototipo).</p>
</div>"""
    return base(sesion, f"Factura {v['numero']}", h, active="/facturas",
                crumbs=[("/facturas", "Facturas"), f"Factura {v['numero']}"])


# --- cotizaciones --------------------------------------------------------------

def pagina_cotizaciones(sesion, cots, msg="", err="") -> str:
    h = alerta_ok(msg) + alerta_err(err)
    h += """<div class="toolbar"><a class="btn" href="/cotizaciones/nueva">+ Nueva cotización</a></div>"""
    if cots:
        filas = "".join(
            f"<tr><td>{esc(c['numero'])}</td>"
            f"<td class='num'>{money(c['total'])}</td>"
            f"<td>{badge(c['estado'])}</td>"
            f"<td>{fecha_do(c['creada'])}</td>"
            f"<td class='actions'>{f'''<form method="post" action="/cotizaciones/{c['id']}/convertir" style="display:inline" onsubmit="return confirm('¿Convertir a factura?')"><input type="hidden" name="csrf" value="{esc(sesion['csrf'])}"><button class="btn small violet" type="submit">Convertir a factura</button></form>''' if c['estado']=='vigente' else ''}</td></tr>"
            for c in cots)
        h += (f'<div class="table-wrap"><table>'
              f"<thead><tr><th>Número</th><th class='num'>Total</th><th>Estado</th>"
              f"<th>Creada</th><th></th></tr></thead>"
              f"<tbody>{filas}</tbody></table></div>")
    else:
        h += empty_state(
            "📝", "Aún no tienes cotizaciones",
            "Prepara presupuestos para tus clientes y conviértelos en "
            "factura con un clic cuando acepten.",
            "/cotizaciones/nueva", "+ Crear mi primera cotización")
    return base(sesion, "Cotizaciones", h, active="/cotizaciones")


def pagina_cotizacion_nueva(sesion, clientes, productos, tasa) -> str:
    opts_cli = "".join(
        f"<option value='{c['id']}'>{esc(c['nombre'])}</option>" for c in clientes)
    opts_prod = "".join(
        f"<option value='{p['id']}' data-nombre='{esc(p['nombre'])}' "
        f"data-precio='{esc(p['precio'])}' data-exento='{p['itbis_exento']}'>"
        f"{esc(p['nombre'])} — {money(p['precio'])}</option>" for p in productos)
    h = f"""
<div class="form-card wide">
  <div class="field">
    <label for="cliente">Cliente</label>
    <select id="cliente"><option value="">— Sin cliente —</option>{opts_cli}</select>
  </div>
  <div class="field">
    <label for="prod">Agregar producto</label>
    <div style="display:flex;gap:8px">
      <select id="prod" style="flex:1"><option value="">— Seleccionar —</option>{opts_prod}</select>
      <label class="sr-only" for="cant">Cantidad</label>
      <input type="number" id="cant" value="1" min="1" style="width:80px">
      <button class="btn" type="button" id="add">Agregar</button>
    </div>
  </div>
  <div id="items"></div>
  <div id="tot"></div>
  <div class="form-actions">
    <button class="btn" id="guardar" type="button">Guardar cotización</button>
    <a class="btn ghost" href="/cotizaciones">Cancelar</a>
  </div>
  <div id="msg" style="margin-top:10px"></div>
</div>
"""
    h += """<script>
const TASA = __TASA__;
let items = [];
function escapeHtml(value){ const el=document.createElement('span'); el.textContent=String(value); return el.innerHTML; }
function fmt(n){ return 'RD$ ' + Number(n).toLocaleString('es-DO', {minimumFractionDigits:2, maximumFractionDigits:2}); }
function render(){
  const box=document.getElementById('items');
  box.innerHTML = items.length
    ? `<div class="table-wrap"><table><tr><th>Producto</th><th class="num">Cant</th><th class="num">Precio</th><th class="num">Subtotal</th><th></th></tr>`
      + items.map((t,i)=>`<tr><td>${escapeHtml(t.nombre)}</td><td class="num">${t.cantidad}</td><td class="num">${fmt(t.precio)}</td><td class="num">${fmt(t.cantidad*t.precio)}</td><td><button class="btn small danger" onclick="items.splice(${i},1);render()">×</button></td></tr>`).join('') + `</table></div>`
    : '<p class="sub">Sin ítems.</p>';
  let sub=0, itb=0;
  items.forEach(t=>{ const s=t.cantidad*t.precio; sub+=s; if(!t.itbis_exento) itb+=s*TASA/100; });
  document.getElementById('tot').innerHTML =
    `<div class="kv"><dt>Subtotal</dt><dd class="num">${fmt(sub)}</dd><dt>ITBIS</dt><dd class="num">${fmt(itb)}</dd><dt><b>Total</b></dt><dd class="num"><b>${fmt(sub+itb)}</b></dd></div>`;
}
document.getElementById('add').onclick=()=>{
  const s=document.getElementById('prod'); if(!s.value) return;
  const o=s.selectedOptions[0];
  items.push({producto_id:+s.value, nombre:o.dataset.nombre, cantidad:+document.getElementById('cant').value||1, precio:+o.dataset.precio, itbis_exento:o.dataset.exento==='1'});
  render();
};
document.getElementById('guardar').onclick=async()=>{
  if(!items.length){ alert('Agregue al menos un ítem'); return; }
  const csrf=document.querySelector('meta[name=csrf-token]').content;
  const r=await fetch('/api/cotizaciones',{method:'POST',
    headers:{'Content-Type':'application/json','X-CSRF-Token':csrf},
    body:JSON.stringify({cliente_id:document.getElementById('cliente').value||null, items})});
  const j=await r.json();
  if(!j.ok){ document.getElementById('msg').innerHTML=`<div class="alert err">${escapeHtml(j.error)}</div>`; return; }
  location.href='/cotizaciones';
};
render();
</script>""".replace("__TASA__", str(tasa))
    return base(sesion, "Nueva cotización", h, active="/cotizaciones",
                crumbs=[("/cotizaciones", "Cotizaciones"), "Nueva cotización"])


# --- clientes ------------------------------------------------------------------

def pagina_clientes(sesion, clientes, msg="", err="") -> str:
    h = alerta_ok(msg) + alerta_err(err)
    h += """<div class="toolbar">
      <div class="table-search">
        <label class="sr-only" for="q-clientes">Buscar clientes</label>
        <input type="search" id="q-clientes" data-table-search="tbl-clientes"
               placeholder="Buscar por nombre, RNC o teléfono…">
      </div>
      <span class="spacer"></span>
      <a class="btn" href="/clientes/nuevo">+ Nuevo cliente</a>
    </div>"""
    if clientes:
        filas = "".join(
            f"<tr><td>{esc(c['nombre'])}</td><td>{esc(c['rnc'] or '—')}</td>"
            f"<td>{esc(c['telefono'] or '')}</td>"
            f"<td class='actions'><a class='btn small ghost' "
            f"href='/clientes/{c['id']}/editar'>Editar</a></td></tr>"
            for c in clientes)
        h += (f'<div class="table-wrap"><table id="tbl-clientes">'
              f"<thead><tr><th>Nombre</th><th>RNC</th><th>Teléfono</th>"
              f"<th></th></tr></thead><tbody>{filas}</tbody></table></div>")
    else:
        h += empty_state(
            "👥", "Aún no tienes clientes",
            "Registra tu primer cliente para poder facturarle y llevar "
            "su historial de compras.",
            "/clientes/nuevo", "+ Crear mi primer cliente")
    return base(sesion, "Clientes", h, active="/clientes")


def form_cliente(sesion, c=None, err="") -> str:
    c = c or {}
    editando = bool(c.get("id"))
    h = alerta_err(err)
    action = f"/clientes/{c['id']}/editar" if editando else "/clientes/nuevo"
    h += f"""
<div class="form-card">
<form method="post" action="{action}">
  <input type="hidden" name="csrf" value="{esc(sesion['csrf'])}">
  <div class="field"><label for="fc-nombre">Nombre <span class="req" aria-hidden="true">*</span></label>
    <input type="text" id="fc-nombre" name="nombre" value="{esc(c.get('nombre',''))}" required
           autocomplete="name" maxlength="120"></div>
  <div class="field"><label for="fc-rnc">RNC (9 u 11 dígitos; vacío = consumidor final)</label>
    <input type="text" id="fc-rnc" name="rnc" value="{esc(c.get('rnc',''))}"
           placeholder="999888777" inputmode="numeric" maxlength="11"></div>
  <div class="field"><label for="fc-tel">Teléfono</label>
    <input type="tel" id="fc-tel" name="telefono" value="{esc(c.get('telefono',''))}"
           autocomplete="tel" maxlength="30"></div>
  <div class="field"><label for="fc-dir">Dirección</label>
    <input type="text" id="fc-dir" name="direccion" value="{esc(c.get('direccion',''))}"
           autocomplete="street-address" maxlength="160"></div>
  <div class="form-actions">
    <button class="btn" type="submit">Guardar cliente</button>
    <a class="btn ghost" href="/clientes">Cancelar</a>
  </div>
</form>
</div>"""
    titulo = "Editar cliente" if editando else "Nuevo cliente"
    return base(sesion, titulo, h, active="/clientes",
                crumbs=[("/clientes", "Clientes"), titulo])


# --- inventario ------------------------------------------------------------------

def pagina_inventario(sesion, productos, msg="", err="") -> str:
    h = alerta_ok(msg) + alerta_err(err)
    h += """<div class="toolbar">
      <div class="table-search">
        <label class="sr-only" for="q-productos">Buscar productos</label>
        <input type="search" id="q-productos" data-table-search="tbl-productos"
               placeholder="Buscar por nombre o SKU…">
      </div>
      <span class="spacer"></span>
      <a class="btn" href="/inventario/nuevo">+ Nuevo producto</a>
    </div>"""
    if productos:
        filas = ""
        for p in productos:
            es_serv = str(p.get("tipo") or "producto") == "servicio"
            tipo_txt = "Servicio" if es_serv else "Producto"
            minimo = Decimal(str(p.get("stock_minimo")
                                 or config.STOCK_BAJO_UMBRAL))
            bajo = (not es_serv) and Decimal(str(p["stock"])) <= minimo
            estilo = ' style="background:#FEF3C7"' if bajo else ""
            stock_txt = "—" if es_serv else f"<b>{esc(p['stock'])}</b>"
            filas += (
                f"<tr{estilo}><td>{esc(p['sku'])}</td><td>{esc(p['nombre'])}</td>"
                f"<td>{tipo_txt}</td>"
                f"<td class='num'>{money(p['precio'])}</td>"
                f"<td class='num'>{money(p['costo'])}</td>"
                f"<td>{'Sí' if p['itbis_exento'] else 'No'}</td>"
                f"<td class='num'>{stock_txt}</td>"
                f"<td class='num'>{esc(p.get('stock_minimo') or config.STOCK_BAJO_UMBRAL)}</td>"
                f"<td class='actions'><a class='btn small ghost' href='/inventario/{p['id']}/editar'>Editar</a></td></tr>")
        h += (f'<div class="table-wrap"><table id="tbl-productos">'
              f"<thead><tr><th>SKU</th><th>Producto</th><th>Tipo</th>"
              f"<th class='num'>Precio</th>"
              f"<th class='num'>Costo</th><th>Exento ITBIS</th>"
              f"<th class='num'>Stock</th><th class='num'>Mínimo</th><th></th></tr></thead>"
              f"<tbody>{filas}</tbody></table></div>"
              f"<p class='sub'>Las filas resaltadas tienen stock ≤ su mínimo "
              f"configurado. Los servicios no llevan control de stock.</p>")
    else:
        h += empty_state(
            "📦", "Tu inventario está vacío",
            "Agrega tu primer producto o servicio para empezar a vender "
            "desde el POS o con facturas.",
            "/inventario/nuevo", "+ Agregar mi primer producto")
    return base(sesion, "Inventario", h, active="/inventario")


def form_producto(sesion, p=None, err="") -> str:
    p = p or {}
    h = alerta_err(err)
    action = f"/inventario/{p['id']}/editar" if p.get("id") else "/inventario/nuevo"
    chk_ex = "checked" if p.get("itbis_exento") else ""
    chk_ac = "checked" if p.get("activo", 1) else ""
    tipo = p.get("tipo") or "producto"
    opts_tipo = "".join(
        f"<option value='{t}'{' selected' if t == tipo else ''}>"
        f"{t.capitalize()}</option>" for t in config.TIPOS_PRODUCTO)
    editando = bool(p.get("id"))
    h += f"""
<div class="form-card">
<form method="post" action="{action}">
  <input type="hidden" name="csrf" value="{esc(sesion['csrf'])}">
  <div class="field"><label for="fp-sku">SKU</label>
    <input type="text" id="fp-sku" name="sku" value="{esc(p.get('sku',''))}"
           placeholder="ARZ-001" maxlength="40"></div>
  <div class="field"><label for="fp-nombre">Nombre <span class="req" aria-hidden="true">*</span></label>
    <input type="text" id="fp-nombre" name="nombre" value="{esc(p.get('nombre',''))}"
           required maxlength="120"></div>
  <div class="grid2">
    <div class="field"><label for="fp-tipo">Tipo <span class="req" aria-hidden="true">*</span></label>
      <select id="fp-tipo" name="tipo">{opts_tipo}</select>
      <p class="hint">Los servicios no mueven inventario.</p></div>
    <div class="field"><label for="fp-min">Stock mínimo (alerta)</label>
      <input type="number" id="fp-min" step="0.01" min="0" name="stock_minimo"
             value="{esc(p.get('stock_minimo', config.STOCK_BAJO_UMBRAL))}"></div>
  </div>
  <div class="grid2">
    <div class="field"><label for="fp-precio">Precio (RD$) <span class="req" aria-hidden="true">*</span></label>
      <input type="number" id="fp-precio" step="0.01" min="0" name="precio"
             value="{esc(p.get('precio','0'))}" required inputmode="decimal"></div>
    <div class="field"><label for="fp-costo">Costo (RD$)</label>
      <input type="number" id="fp-costo" step="0.01" min="0" name="costo"
             value="{esc(p.get('costo','0'))}" inputmode="decimal"></div>
  </div>
  <div class="field"><label for="fp-stock">Stock</label>
    <input type="number" id="fp-stock" step="0.01" min="0" name="stock"
           value="{esc(p.get('stock','0'))}" inputmode="decimal"></div>
  <div class="field"><label class="check-row"><input type="checkbox" name="itbis_exento" {chk_ex}>
    Exento de ITBIS</label></div>
  <div class="field"><label class="check-row"><input type="checkbox" name="activo" {chk_ac}>
    Activo</label></div>
  <div class="form-actions">
    <button class="btn" type="submit">Guardar producto</button>
    <a class="btn ghost" href="/inventario">Cancelar</a>
  </div>
</form>
</div>"""
    titulo = "Editar producto" if editando else "Nuevo producto"
    return base(sesion, titulo, h, active="/inventario",
                crumbs=[("/inventario", "Inventario"), titulo])


# --- CxC -------------------------------------------------------------------------

def pagina_cxc(sesion, pendientes, total, msg="", err="",
               vencidas_n=0) -> str:
    h = alerta_ok(msg) + alerta_err(err)
    h += f"""<div class="toolbar">
      <a class="btn ghost sm" href="/cxc/vencidas">⚠ Ver vencidas ({vencidas_n})</a>
    </div>
    <div class="cards" style="margin-bottom:16px"><div class="card">
      <div class="k">Total por cobrar</div><div class="v">{money(total)}</div></div></div>"""
    filas = "".join(
        f"<tr><td><a href='/facturas/{v['id']}'>{esc(v['numero'])}</a></td>"
        f"<td class='num'>{money(v['total'])}</td>"
        f"<td class='num'><b>{money(v['balance'])}</b></td>"
        f"<td>{fecha_do(v['creada'], False)}</td>"
        f"<td>{fecha_do(v.get('vencimiento'), False)}</td>"
        f"<td><form method='post' action='/cxc/{v['id']}/pago' class='inline-form'>"
        f"<input type='hidden' name='idempotencia' value='{secrets.token_urlsafe(24)}'>"
        f"<input type='hidden' name='csrf' value='{esc(sesion['csrf'])}'>"
        f"<label class='sr-only' for='monto-{v['id']}'>Monto del abono</label>"
        f"<input type='number' id='monto-{v['id']}' step='0.01' min='0.01' name='monto' placeholder='Monto' required style='width:110px'>"
        f"<label class='sr-only' for='met-{v['id']}'>Método de pago</label>"
        f"<select id='met-{v['id']}' name='metodo'><option value='efectivo'>Efectivo</option>"
        f"<option value='tarjeta'>Tarjeta</option><option value='transferencia'>Transferencia</option></select>"
        f"<button class='btn small' type='submit'>Registrar pago</button></form>"
        f"<form method='post' action='/cxc/{v['id']}/vencimiento' class='inline-form' style='margin-top:6px'>"
        f"<input type='hidden' name='csrf' value='{esc(sesion['csrf'])}'>"
        f"<label class='sr-only' for='ven-{v['id']}'>Fecha de vencimiento</label>"
        f"<input type='date' id='ven-{v['id']}' name='vencimiento' value='{esc((v.get('vencimiento') or '')[:10])}' style='width:140px'>"
        f"<button class='btn small ghost' type='submit'>Fijar vencimiento</button></form></td></tr>"
        for v in pendientes)
    if pendientes:
        h += (f'<div class="table-wrap"><table>'
              f"<thead><tr><th>Factura</th><th class='num'>Total</th><th class='num'>Balance</th>"
              f"<th>Fecha</th><th>Vencimiento</th><th>Registrar abono / vencimiento</th></tr></thead>"
              f"<tbody>{filas}</tbody></table></div>")
    else:
        h += empty_state(
            "💰", "Sin cuentas pendientes",
            "¡Todo cobrado! Cuando vendas a crédito, las facturas por cobrar "
            "aparecerán aquí.")
    return base(sesion, "Cuentas por cobrar", h, active="/cxc")


# --- reportes --------------------------------------------------------------------

def pagina_reportes(sesion, por_dia, itbis, top, desde="", hasta="",
                    gastos=None, er=None) -> str:
    filas_dia = "".join(
        f"<tr><td>{esc(d['dia'])}</td><td class='num'>{d['cantidad']}</td>"
        f"<td class='num'>{money(d['total'])}</td></tr>" for d in por_dia)
    filas_top = "".join(
        f"<tr><td>{esc(t['nombre'])}</td><td class='num'>{esc(t['cantidad'])}</td>"
        f"<td class='num'>{money(t['ingreso'])}</td></tr>" for t in top)
    h = f"""
<div class="toolbar">
  <span class="sub"><b>Exportar:</b></span>
  <a class="btn ghost sm" href="/exportar/ventas.csv">Ventas CSV</a>
  <a class="btn ghost sm" href="/exportar/ventas.xls">Ventas Excel</a>
  <a class="btn ghost sm" href="/exportar/inventario.csv">Inventario CSV</a>
  <a class="btn ghost sm" href="/exportar/inventario.xls">Inventario Excel</a>
  <a class="btn ghost sm" href="/exportar/cxc.csv">CxC CSV</a>
  <a class="btn ghost sm" href="/exportar/gastos.csv">Gastos CSV</a>
  <a class="btn ghost sm" href="/exportar/gastos.xls">Gastos Excel</a>
  <a class="btn ghost sm" href="/exportar/estado-resultados.csv">Estado de resultados CSV</a>
  <a class="btn ghost sm" href="/reportes/imprimir?tipo=ventas">🖨 PDF ventas</a>
  <a class="btn ghost sm" href="/reportes/imprimir?tipo=inventario">🖨 PDF inventario</a>
  <a class="btn ghost sm" href="/reportes/imprimir?tipo=gastos">🖨 PDF gastos</a>
  <a class="btn ghost sm" href="/reportes/imprimir?tipo=estado-resultados">🖨 PDF estado de resultados</a>
  <span class="spacer"></span>
  <form method="get" action="/reportes" class="inline-form">
    <div class="field"><label for="rep-desde">Desde</label>
      <input type="date" id="rep-desde" name="desde" value="{esc(desde)}"></div>
    <div class="field"><label for="rep-hasta">Hasta</label>
      <input type="date" id="rep-hasta" name="hasta" value="{esc(hasta)}"></div>
    <button class="btn" type="submit">Filtrar</button>
  </form>
</div>
<div class="grid2">
<div>
  <h2>Ventas por día</h2>
  <div class="table-wrap"><table>
  <thead><tr><th>Día</th><th class="num">Ventas</th><th class="num">Total</th></tr></thead>
  <tbody>{filas_dia or '<tr><td colspan="3">Sin ventas en el período.</td></tr>'}</tbody></table></div>
</div>
<div>
  <h2>Top productos</h2>
  <div class="table-wrap"><table>
  <thead><tr><th>Producto</th><th class="num">Cant.</th><th class="num">Ingreso</th></tr></thead>
  <tbody>{filas_top or '<tr><td colspan="3">Sin datos.</td></tr>'}</tbody></table></div>
</div>
</div>
<h2>ITBIS débito (motor os_contab.itbis · tasa {esc(itbis['tasa'])}%)</h2>
<div class="cards">
  <div class="card"><div class="k">Base gravada</div><div class="v">{money(itbis['base_gravada'])}</div></div>
  <div class="card"><div class="k">Base exenta</div><div class="v">{money(itbis['base_exenta'])}</div></div>
  <div class="card"><div class="k">Débito ITBIS</div><div class="v">{money(itbis['debito'])}</div></div>
  <div class="card accent"><div class="k">A pagar</div><div class="v">{money(itbis['a_pagar'])}</div></div>
</div>
<p class="sub">{esc(itbis['nota'])}</p>"""
    if gastos:
        fg = "".join(
            f"<tr><td>{esc(g['categoria'].capitalize())}</td>"
            f"<td class='num'>{g['cantidad']}</td>"
            f"<td class='num'>{money(g['total'])}</td></tr>"
            for g in gastos["gastos"])
        h += f"""
<h2>Gastos por categoría</h2>
<div class="table-wrap"><table>
<thead><tr><th>Categoría</th><th class="num">Movimientos</th>
<th class="num">Total</th></tr></thead>
<tbody>
{fg or '<tr><td colspan="3">Sin gastos en el período.</td></tr>'}
<tr><td><b>Total gastos</b></td><td></td>
<td class="num"><b>{money(gastos['total_gastos'])}</b></td></tr></tbody></table></div>
<p class="sub">Otros ingresos (no-venta) del período: {money(gastos['total_ingresos_no_venta'])}</p>"""
    if er:
        h += f"""
<h2>Estado de resultados</h2>
<div class="cards">
  <div class="card"><div class="k">Ventas ({er['cantidad_ventas']})</div>
    <div class="v">{money(er['ventas'])}</div></div>
  <div class="card"><div class="k">Costo de ventas</div>
    <div class="v">{money(er['costo_ventas'])}</div></div>
  <div class="card"><div class="k">Utilidad bruta</div>
    <div class="v">{money(er['utilidad_bruta'])}</div></div>
  <div class="card"><div class="k">Otros ingresos</div>
    <div class="v">{money(er['otros_ingresos'])}</div></div>
  <div class="card"><div class="k">Gastos</div>
    <div class="v">{money(er['gastos'])}</div></div>
  <div class="card accent"><div class="k">Utilidad neta</div>
    <div class="v">{money(er['utilidad_neta'])}</div></div>
</div>
<p class="sub">{esc(er['nota'])}</p>"""
    return base(sesion, "Reportes", h, active="/reportes")


# --- e-CF --------------------------------------------------------------------------

def pagina_ecf(sesion, comprobantes, modo, ventas_fact, msg="", err="") -> str:
    h = sim_banner() + alerta_ok(msg) + alerta_err(err)
    if modo == "disabled":
        h += ("""<div class="alert err"><b>Módulo e-CF deshabilitado.</b> """
              """La empresa tiene el modo fiscal en <b>disabled</b>: la """
              """transmisión está bloqueada. Para practicar con el simulador, """
              """un administrador puede cambiarlo a <b>mock</b> en """
              """Configuración (solo simulación, sin validez ante DGII)."""
              """</div>""")
    else:
        opts_v = "".join(
            f"<option value='{v['id']}'>{esc(v['numero'])} — {money(v['total'])}</option>"
            for v in ventas_fact)
        h += f"""
<div class="form-card" style="max-width:720px;margin-bottom:18px">
  <h2 style="margin-top:0">Crear comprobante (simulación)</h2>
  <form method="post" action="/ecf/nuevo">
    <input type="hidden" name="csrf" value="{esc(sesion['csrf'])}">
    <div class="grid2">
      <div class="field"><label>Venta origen</label>
        <select name="venta_id" required>{opts_v or '<option value="">— sin ventas —</option>'}</select></div>
      <div class="field"><label>Tipo e-CF</label>
        <select name="tipo">
          <option value="31">E31 — Crédito Fiscal</option>
          <option value="32">E32 — Consumo</option>
          <option value="33">E33 — Nota de Débito</option>
          <option value="34">E34 — Nota de Crédito</option>
        </select></div>
    </div>
    <div class="field"><label>e-NCF original (solo notas 33/34)</label>
      <input type="text" name="encf_original" placeholder="E310000000001"></div>
    <button class="btn violet" type="submit">Crear borrador</button>
  </form>
</div>"""
    filas = "".join(
        f"<tr><td><a href='/ecf/{c['id']}'>{esc(c['e_ncf'])}</a></td>"
        f"<td>E{c['tipo']} — {esc(c['tipo_nombre'])}</td>"
        f"<td>{badge(c['estado'])}</td><td>{esc(c['modo'])}</td>"
        f"<td>{fecha_do(c['creado_en'])}</td></tr>"
        for c in comprobantes)
    h += (f'<div class="table-wrap"><table>'
          f"<thead><tr><th>e-NCF</th><th>Tipo</th><th>Estado</th><th>Modo</th>"
          f"<th>Creado</th></tr></thead>"
          f"<tbody>{filas or '<tr><td colspan=5>Sin comprobantes.</td></tr>'}</tbody></table></div>")
    return base(sesion, "e-CF — Comprobantes electrónicos", h, active="/ecf")


def pagina_ecf_detalle(sesion, comp, es_admin, msg="", err="") -> str:
    h = sim_banner() + alerta_ok(msg) + alerta_err(err)
    c = comp["comprobante"]
    t = c["totales"]
    estado = c["estado"]
    h += f"""
<div class="toolbar no-print">
  <a class="btn ghost" href="/ecf">← Volver</a><span class="spacer"></span>
  <a class="btn ghost" href="/ecf/{comp['id']}/xml">Descargar XML</a>
</div>
<div class="grid2">
<div class="card">
  <h2 style="margin-top:0">{esc(comp['e_ncf'])} — {esc(comp['tipo_nombre'])}</h2>
  <div class="kv">
    <dt>Estado</dt><dd>{badge(estado)}</dd>
    <dt>Modo</dt><dd>{esc(c['modo'])}</dd>
    <dt>Emisor</dt><dd>{esc(c['emisor']['razon_social'])} (RNC {esc(c['emisor']['rnc'])})</dd>
    <dt>Receptor</dt><dd>{esc(c['receptor']['razon_social'])} (RNC {esc(c['receptor']['rnc'])})</dd>
    <dt>Subtotal</dt><dd class="num">{money(t['subtotal'])}</dd>
    <dt>ITBIS</dt><dd class="num">{money(t['itbis'])}</dd>
    <dt>Total</dt><dd class="num"><b>{money(t['total'])}</b></dd>
    <dt>Cadena de eventos</dt><dd>{'✔ intacta' if comp['historial_ok'] else '<b style="color:#dc2626">✘ ALTERADA</b>'}</dd>
  </div>
  <h2>Acciones</h2>
  <div class="toolbar">"""
    csrf = esc(sesion["csrf"])
    cid = comp["id"]
    if estado == "borrador":
        h += (f"<form method='post' action='/ecf/{cid}/validar'>"
              f"<input type='hidden' name='csrf' value='{csrf}'>"
              f"<button class='btn' type='submit'>1. Validar</button></form>")
    if estado == "validado" and es_admin:
        h += (f"<form method='post' action='/ecf/{cid}/aprobar'>"
              f"<input type='hidden' name='csrf' value='{csrf}'>"
              f"<button class='btn violet' type='submit' "
              f"onclick=\"return confirm('¿Aprueba este comprobante como responsable humano?')\">"
              f"2. Aprobar (humano)</button></form>")
    if estado == "pendiente_aprobacion" and es_admin:
        h += (f"<form method='post' action='/ecf/{cid}/emitir'>"
              f"<input type='hidden' name='csrf' value='{csrf}'>"
              f"<button class='btn' type='submit' "
              f"onclick=\"return confirm('Transmitir al simulador (MockProvider). Sin validez fiscal.')\">"
              f"3. Transmitir (simulación)</button></form>")
    if estado in ("aceptado", "observado", "rechazado") and es_admin:
        h += (f"<form method='post' action='/ecf/{cid}/anular' "
              f"onsubmit=\"return confirm('¿Anular este comprobante?')\">"
              f"<input type='hidden' name='csrf' value='{csrf}'>"
              f"<input type='text' name='motivo' placeholder='Motivo de anulación' required> "
              f"<button class='btn danger' type='submit'>Anular</button></form>")
    if estado in ("borrador", "validado") and not es_admin:
        h += "<p class='sub'>Las acciones de aprobación/transmisión requieren rol admin.</p>"
    h += "</div>"
    if not comp["transiciones"]:
        h += "<p class='sub'>Estado final: sin más transiciones.</p>"
    h += "</div>"
    evs = "".join(
        f"<div class='ev'><b>{esc(e['de'] or '—')} → {esc(e['a'])}</b> "
        f"<span class='sub'>{esc(e['actor'])} · {fecha_do(e['timestamp'])}</span><br>"
        f"{esc(e['detalle'])}<br><span class='hash'>hash: {esc(e['hash'][:24])}…</span></div>"
        for e in comp["historial_lista"])
    h += f"<div class='card'><h2 style='margin-top:0'>Historial (cadena hash)</h2><div class='timeline'>{evs or 'Sin eventos.'}</div></div></div>"
    return base(sesion, f"e-CF {esc(comp['e_ncf'])}", h, active="/ecf")


# --- config --------------------------------------------------------------------------

def pagina_config(sesion, empresa, usuarios, msg="", err="") -> str:
    h = alerta_ok(msg) + alerta_err(err)
    planes = ""
    for slug, p in config.PLANES.items():
        cur = " current" if empresa.get("plan") == slug else ""
        feats = "".join(f"<li>{esc(f)}</li>" for f in p["features"])
        planes += (f"<div class='plan{cur}'><h3>{esc(p['nombre'])}</h3>"
                   f"<div class='price'>{esc(p['moneda'])}{p['precio']:,}/mes</div>"
                   f"<p class='sub'>{esc(p['descripcion'])}</p>"
                   f"<p class='sub'>Hasta {p['usuarios']} usuarios · "
                   f"{'Multisucursal' if p['multisucursal'] else '1 sucursal'}</p>"
                   f"<ul>{feats}</ul>"
                   f"{'<b>Plan actual</b>' if cur else ''}</div>")
    h += f"""
<h2>Datos de la empresa</h2>
<div class="form-card" style="max-width:720px">
<form method="post" action="/config/empresa">
  <input type="hidden" name="csrf" value="{esc(sesion['csrf'])}">
  <div class="field"><label>Nombre *</label>
    <input type="text" name="nombre" value="{esc(empresa['nombre'])}" required></div>
  <div class="grid2">
    <div class="field"><label>RNC *</label>
      <input type="text" name="rnc" value="{esc(empresa['rnc'])}" required></div>
    <div class="field"><label>Teléfono</label>
      <input type="text" name="telefono" value="{esc(empresa['telefono'])}"></div>
  </div>
  <div class="field"><label>Dirección</label>
    <input type="text" name="direccion" value="{esc(empresa['direccion'])}"></div>
  <div class="grid2">
    <div class="field"><label>Tasa ITBIS (%)</label>
      <input type="number" step="0.01" min="0" max="100" name="itbis_tasa"
             value="{esc(empresa['itbis_tasa'])}"></div>
    <div class="field"><label>Modo e-CF</label>
      <select name="e_cf_mode">
        <option value="disabled"{' selected' if empresa['e_cf_mode']=='disabled' else ''}>disabled (bloqueado)</option>
        <option value="mock"{' selected' if empresa['e_cf_mode']=='mock' else ''}>mock (simulador local)</option>
      </select></div>
  </div>
  <p class="sub">El modo <b>mock</b> solo activa el simulador offline con respuestas
  sintéticas. No habilita facturación electrónica real ante la DGII.</p>
  <button class="btn" type="submit">Guardar cambios</button>
</form>
</div>
<h2>Plan (configurable, sin cobro real)</h2>
<div class="plan-cards">{planes}</div>
<p class="sub">Los planes se muestran como referencia comercial. Este prototipo no
cobra ni procesa pagos de suscripción.</p>
<h2>Usuarios</h2>
<div class="toolbar"><a class="btn" href="/config/usuarios/nuevo">Nuevo usuario</a></div>"""
    filas = ""
    for u in usuarios:
        propio = " (usted)" if u["id"] == sesion.get("u") else ""
        if propio:
            acc = "<span class='sub'>—</span>"
        else:
            opts_rol = "".join(
                f"<option value='{r}'{' selected' if u['rol'] == r else ''}>"
                f"{r}</option>" for r in config.ROLES)
            acc = (
                f"<form method='post' action='/config/usuarios/{u['id']}/rol'"
                f" style='display:inline'>"
                f"<input type='hidden' name='csrf' value='{esc(sesion['csrf'])}'>"
                f"<select name='rol'>{opts_rol}</select> "
                f"<button class='btn sm' type='submit'>Rol</button></form> "
                f"<form method='post' "
                f"action='/config/usuarios/{u['id']}/"
                f"{'desactivar' if u['activo'] else 'activar'}'"
                f" style='display:inline'>"
                f"<input type='hidden' name='csrf' value='{esc(sesion['csrf'])}'>"
                f"<button class='btn ghost sm' type='submit'>"
                f"{'Desactivar' if u['activo'] else 'Activar'}</button></form>")
        filas += (f"<tr><td>{esc(u['email'])}{propio}</td>"
                  f"<td>{esc(u['rol'])}</td>"
                  f"<td>{'Sí' if u['activo'] else 'No'}</td>"
                  f"<td>{acc}</td></tr>")
    h += (f'<div class="table-wrap"><table>'
          f"<thead><tr><th>Email</th><th>Rol</th><th>Activo</th>"
          f"<th>Acciones</th></tr></thead>"
          f"<tbody>{filas or '<tr><td colspan=4>Sin usuarios.</td></tr>'}</tbody></table></div>")
    h += ('<p class="sub">Roles: <b>admin</b> (todo) · <b>cajero</b> (POS, '
          'ventas, clientes; sin configuración ni reportes) · <b>contador</b> '
          '(reportes y lectura; sin caja ni configuración).</p>')
    return base(sesion, "Configuración", h, active="/config")


def form_usuario(sesion, err="") -> str:
    h = alerta_err(err)
    h += f"""
<div class="form-card">
<form method="post" action="/config/usuarios/nuevo">
  <input type="hidden" name="csrf" value="{esc(sesion['csrf'])}">
  <div class="field"><label for="fu-email">Email <span class="req" aria-hidden="true">*</span></label>
    <input type="email" id="fu-email" name="email" required
           autocomplete="email" maxlength="120"></div>
  <div class="field"><label for="fu-pass">Contraseña <span class="req" aria-hidden="true">*</span> (mínimo 12 caracteres)</label>
    <input type="password" id="fu-pass" name="password" required minlength="12"
           autocomplete="new-password"></div>
  <div class="field"><label for="fu-rol">Rol</label>
    <select id="fu-rol" name="rol"><option value="cajero">cajero — vende y cobra</option>
    <option value="contador">contador — reportes y lectura</option>
    <option value="admin">admin — acceso total</option></select></div>
  <div class="form-actions">
    <button class="btn" type="submit">Crear usuario</button>
    <a class="btn ghost" href="/config">Cancelar</a>
  </div>
</form>
</div>"""
    return base(sesion, "Nuevo usuario", h, active="/config",
                crumbs=[("/config", "Configuración"), "Nuevo usuario"])
