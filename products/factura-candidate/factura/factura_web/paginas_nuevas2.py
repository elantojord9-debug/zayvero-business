"""Páginas privadas nuevas FASE 1 + FASE 4 (usan T.base con sidebar)."""
from __future__ import annotations

import secrets

from decimal import Decimal

import config
from factura_web import templates as T

esc, money = T.esc, T.money


def pagina_403(sesion: dict, mensaje: str) -> str:
    return T.base(sesion, "Sin permiso",
                  T.alerta_err("Acceso denegado: " + mensaje) +
                  "<p><a href='/panel'>Volver al panel</a></p>")


def pagina_trial_vencido(sesion: dict, dias: int) -> str:
    cuerpo = f"""
<div class="alert err"><b>Período de prueba vencido</b> (hace {-dias} días).
Tu cuenta está en <b>solo lectura</b>: puedes ver tus datos, pero no crear
ni modificar nada.</div>
<div class="card"><h2 style="margin-top:0">¿Cómo reactivo mi cuenta?</h2>
<p>Escríbenos por WhatsApp al <b>+1 829-512-1552</b> o abre un ticket en
<a href="/soporte">Soporte</a> y te ayudamos a continuar con el plan que
prefieras.</p>
<p class="sub">Precios provisionales: Básico precio pendiente/mes · Negocio precio pendiente/mes
· Pro precio pendiente/mes.</p></div>"""
    return T.base(sesion, "Prueba vencida", cuerpo, active="/suscripcion")


# --- caja --------------------------------------------------------------------

def pagina_caja(sesion, turno, movs, hist, msg="") -> str:
    h = T.alerta_ok(msg)
    csrf = esc(sesion["csrf"])
    if turno:
        esp = sum((Decimal(str(m["monto"])) for m in movs), Decimal("0"))
        fm = "".join(
            f"<tr><td>{T.fecha_do(m['creado_en'])}</td>"
            f"<td>{esc(m['tipo'])}</td><td>{esc(m['motivo'])}</td>"
            f"<td class='num'>{money(m['monto'])}</td></tr>" for m in movs)
        h += f"""
<div class="alert ok"><b>Turno abierto</b> desde
{T.fecha_do(turno['abierta_en'])} ·
Esperado en caja: <b>{money(esp)}</b></div>
<div class="grid2">
<div class="form-card">
  <h2 style="margin-top:0">Registrar movimiento</h2>
  <form method="post" action="/caja/movimiento">
    <input type="hidden" name="idempotencia" value="{secrets.token_urlsafe(24)}">
    <input type="hidden" name="csrf" value="{csrf}">
    <div class="field"><label for="cj-tipo">Tipo</label>
      <select id="cj-tipo" name="tipo"><option value="ingreso">Ingreso</option>
      <option value="gasto">Gasto / retiro</option>
      <option value="venta">Venta en efectivo</option></select></div>
    <div class="field"><label for="cj-monto">Monto <span class="req" aria-hidden="true">*</span> (use negativo para salidas)</label>
      <input type="number" id="cj-monto" step="0.01" name="monto" required
             inputmode="decimal"></div>
    <div class="field"><label for="cj-motivo">Motivo</label>
      <input type="text" id="cj-motivo" name="motivo" maxlength="120"></div>
    <button class="btn block" type="submit">Registrar</button>
  </form>
</div>
<div class="form-card">
  <h2 style="margin-top:0">Cerrar turno</h2>
  <form method="post" action="/caja/cerrar"
        onsubmit="return confirm('¿Cerrar el turno con este conteo?')">
    <input type="hidden" name="csrf" value="{csrf}">
    <div class="field"><label for="cj-final">Conteo físico (efectivo en caja) <span class="req" aria-hidden="true">*</span></label>
      <input type="number" id="cj-final" step="0.01" name="monto_final" required
             inputmode="decimal"></div>
    <button class="btn violet block" type="submit">Cerrar turno</button>
  </form>
</div>
</div>
<h2>Movimientos del turno</h2>
<div class="table-wrap"><table>
<thead><tr><th>Hora</th><th>Tipo</th><th>Motivo</th>
<th class="num">Monto</th></tr></thead>
<tbody>{fm or '<tr><td colspan="4">Sin movimientos.</td></tr>'}</tbody></table></div>"""
    else:
        h += f"""
<div class="alert err"><b>No hay turno abierto.</b> El POS no puede vender
hasta que se abra la caja.</div>
<div class="form-card" style="max-width:520px">
  <h2 style="margin-top:0">Abrir turno de caja</h2>
  <form method="post" action="/caja/abrir">
    <input type="hidden" name="csrf" value="{csrf}">
    <div class="field"><label for="cj-ini">Monto inicial (efectivo en caja) <span class="req" aria-hidden="true">*</span></label>
      <input type="number" id="cj-ini" step="0.01" name="monto_inicial" required
             value="0" inputmode="decimal"></div>
    <button class="btn block" type="submit">Abrir caja</button>
  </form>
</div>"""
    fh = "".join(
        f"<tr><td>{t['id']}</td>"
        f"<td>{T.fecha_do(t['abierta_en'])}</td>"
        f"<td>{T.fecha_do(t['cerrada_en'])}</td>"
        f"<td class='num'>{money(t['monto_inicial'])}</td>"
        f"<td class='num'>{money(t['monto_final']) if t['estado']=='cerrada' else '—'}</td>"
        f"<td class='num'>{money(t['diferencia']) if t['estado']=='cerrada' else '—'}</td>"
        f"<td>{T.badge(t['estado'])}</td></tr>" for t in hist)
    h += (f"<h2>Historial de turnos</h2>"
          f'<div class="table-wrap"><table>'
          f"<thead><tr><th>#</th><th>Apertura</th><th>Cierre</th>"
          f"<th class='num'>Inicial</th><th class='num'>Final</th>"
          f"<th class='num'>Diferencia</th><th>Estado</th></tr></thead>"
          f"<tbody>{fh or '<tr><td colspan=7>Sin turnos.</td></tr>'}</tbody></table></div>")
    return T.base(sesion, "Caja", h, active="/caja")


# --- devoluciones --------------------------------------------------------------

def pagina_devoluciones(sesion, devoluciones, ventas_recientes, msg="") -> str:
    h = T.alerta_ok(msg)
    csrf = esc(sesion["csrf"])
    opts = "".join(
        f"<option value='{v['id']}'>{esc(v['numero'])} — {money(v['total'])}</option>"
        for v in ventas_recientes)
    h += f"""
<div class="form-card wide" style="margin-bottom:18px">
  <h2 style="margin-top:0">Nueva nota de devolución</h2>
  <p class="sub">Devuelve stock al inventario sin modificar la venta original.
  El motivo es obligatorio.</p>
  <form method="post" action="/devoluciones/nueva">
    <input type="hidden" name="idempotencia" value="{secrets.token_urlsafe(24)}">
    <input type="hidden" name="csrf" value="{csrf}">
    <div class="grid2">
      <div class="field"><label for="dv-venta">Venta origen <span class="req" aria-hidden="true">*</span></label>
        <select id="dv-venta" name="venta_id" required>{opts or '<option value="">— sin ventas —</option>'}</select></div>
      <div class="field"><label for="dv-motivo">Motivo <span class="req" aria-hidden="true">*</span></label>
        <input type="text" id="dv-motivo" name="motivo" required maxlength="160"
               placeholder="Ej.: producto defectuoso"></div>
    </div>
    <div class="field"><label for="dv-items">Ítems (uno por línea: producto_id,cantidad)</label>
      <textarea id="dv-items" name="items" rows="3" placeholder="12,2&#10;15,1"></textarea></div>
    <button class="btn violet" type="submit">Crear nota de devolución</button>
  </form>
</div>
<h2>Notas de devolución</h2>"""
    filas = "".join(
        f"<tr><td>{esc(d['numero'])}</td><td>{esc(d.get('venta_numero') or '')}</td>"
        f"<td>{esc(d['motivo'])}</td><td class='num'>{money(d['total'])}</td>"
        f"<td>{T.fecha_do(d['creada_en'])}<form method='post' action='/devoluciones/{d['id']}/reembolsar'>"
        f"<input type='hidden' name='csrf' value='{esc(sesion['csrf'])}'>"
        f"<input type='hidden' name='idempotencia' value='{secrets.token_urlsafe(24)}'>"
        f"<input type='number' name='monto' min='0.01' step='0.01' placeholder='Reembolso entregado' required>"
        f"<select name='metodo'><option value='efectivo'>Efectivo entregado</option><option value='transferencia'>Transferencia ya realizada</option></select>"
        f"<button type='submit'>Registrar reembolso</button></form></td></tr>"
        for d in devoluciones)
    if devoluciones:
        h += (f'<div class="table-wrap"><table>'
              f"<thead><tr><th>Número</th><th>Venta</th><th>Motivo</th>"
              f"<th class='num'>Total</th><th>Fecha</th></tr></thead>"
              f"<tbody>{filas}</tbody></table></div>")
    else:
        h += T.empty_state(
            "↩️", "Sin devoluciones",
            "Cuando un cliente devuelva un producto, la nota aparecerá aquí "
            "y el stock volverá al inventario automáticamente.")
    return T.base(sesion, "Devoluciones", h, active="/devoluciones")


def pagina_ajustes(sesion, ajustes, productos, msg="") -> str:
    h = T.alerta_ok(msg)
    csrf = esc(sesion["csrf"])
    opts = "".join(
        f"<option value='{p['id']}'>{esc(p['sku'])} — {esc(p['nombre'])}"
        f" (stock {esc(p['stock'])})</option>" for p in productos)
    h += f"""
<div class="form-card wide" style="margin-bottom:18px">
  <h2 style="margin-top:0">Ajuste manual de inventario</h2>
  <form method="post" action="/inventario/ajuste">
    <input type="hidden" name="csrf" value="{csrf}">
    <div class="grid2">
      <div class="field"><label for="aj-prod">Producto <span class="req" aria-hidden="true">*</span></label>
        <select id="aj-prod" name="producto_id" required>{opts}</select></div>
      <div class="field"><label for="aj-stock">Nuevo stock <span class="req" aria-hidden="true">*</span></label>
        <input type="number" id="aj-stock" step="0.01" min="0" name="stock"
               required inputmode="decimal"></div>
    </div>
    <div class="field"><label for="aj-motivo">Motivo <span class="req" aria-hidden="true">*</span></label>
      <input type="text" id="aj-motivo" name="motivo" required maxlength="160"
             placeholder="Ej.: conteo físico, merma, corrección"></div>
    <button class="btn" type="submit">Aplicar ajuste</button>
  </form>
</div>
<h2>Historial de ajustes</h2>"""
    filas = "".join(
        f"<tr><td>{T.fecha_do(a['creado_en'])}</td>"
        f"<td>{esc(a.get('producto_nombre') or a['producto_id'])}</td>"
        f"<td class='num'>{esc(a['stock_antes'])}</td>"
        f"<td class='num'>{esc(a['stock_despues'])}</td>"
        f"<td>{esc(a['motivo'])}</td></tr>" for a in ajustes)
    h += (f'<div class="table-wrap"><table>'
          f"<thead><tr><th>Fecha</th><th>Producto</th>"
          f"<th class='num'>Antes</th><th class='num'>Después</th><th>Motivo</th></tr></thead>"
          f"<tbody>{filas or '<tr><td colspan=5>Sin ajustes.</td></tr>'}</tbody></table></div>")
    return T.base(sesion, "Ajustes de inventario", h, active="/inventario",
                  crumbs=[("/inventario", "Inventario"), "Ajustes"])


# --- sucursales ------------------------------------------------------------------

def pagina_sucursales(sesion, sucursales, limite, msg="") -> str:
    h = T.alerta_ok(msg)
    csrf = esc(sesion["csrf"])
    h += f"""
<p class="sub">Su plan permite <b>{limite}</b> sucursal(es) activa(s).
La sucursal se elige al vender (facturas y POS).</p>
<div class="form-card wide" style="margin-bottom:18px">
  <h2 style="margin-top:0">Nueva sucursal</h2>
  <form method="post" action="/sucursales/nueva">
    <input type="hidden" name="csrf" value="{csrf}">
    <div class="grid2">
      <div class="field"><label for="sc-nombre">Nombre <span class="req" aria-hidden="true">*</span></label>
        <input type="text" id="sc-nombre" name="nombre" required maxlength="80"></div>
      <div class="field"><label for="sc-tel">Teléfono</label>
        <input type="tel" id="sc-tel" name="telefono" maxlength="30"></div>
    </div>
    <div class="field"><label for="sc-dir">Dirección</label>
      <input type="text" id="sc-dir" name="direccion" maxlength="160"></div>
    <button class="btn" type="submit">Crear sucursal</button>
  </form>
</div>
<h2>Sucursales</h2>"""
    filas = ""
    for s in sucursales:
        estado = "Sí" if s["activa"] else "No"
        filas += (f"<tr><td>{esc(s['nombre'])}</td><td>{esc(s['direccion'])}</td>"
                  f"<td>{esc(s['telefono'])}</td><td>{estado}</td><td>"
                  f"<form method='post' action='/sucursales/{s['id']}/editar'"
                  f" style='display:inline'>"
                  f"<input type='hidden' name='csrf' value='{csrf}'>"
                  f"<input type='text' name='nombre' value='{esc(s['nombre'])}'"
                  f" required maxlength='80' style='width:130px'> "
                  f"<select name='activa'><option value='1'"
                  f"{' selected' if s['activa'] else ''}>activa</option>"
                  f"<option value='0'"
                  f"{'' if s['activa'] else ' selected'}>inactiva</option></select> "
                  f"<button class='btn sm' type='submit'>Guardar</button></form>"
                  f"</td></tr>")
    if sucursales:
        h += (f'<div class="table-wrap"><table>'
              f"<thead><tr><th>Nombre</th><th>Dirección</th><th>Teléfono</th>"
              f"<th>Activa</th><th>Editar</th></tr></thead>"
              f"<tbody>{filas}</tbody></table></div>")
    else:
        h += T.empty_state(
            "🏪", "Aún no tienes sucursales",
            "Crea tu primera sucursal para elegirla al vender desde el POS "
            "o las facturas.")
    return T.base(sesion, "Sucursales", h, active="/sucursales")


# --- suscripción -------------------------------------------------------------------

def pagina_suscripcion(sesion, uso, msg="") -> str:
    h = T.alerta_ok(msg)
    csrf = esc(sesion["csrf"])

    def barra(usados, limite):
        pct = min(100, int(usados * 100 / limite)) if limite else 0
        cls = "bar-danger" if pct >= 90 else ("bar-warn" if pct >= 70 else "bar-ok")
        return (f"<div class='meter'><div class='{cls}' style='width:{pct}%'>"
                f"</div></div><span class='sub'>{usados} / {limite}</span>")

    dias = uso["trial_dias"]
    if dias is None:
        trial_html = "<p class='sub'>Cuenta sin período de prueba (heredada).</p>"
    elif dias >= 0:
        trial_html = (f"<div class='alert ok'><b>Quedan {dias} días de "
                      f"prueba</b> del plan {esc(uso['plan_nombre'])}.</div>")
    else:
        trial_html = (f"<div class='alert err'><b>Prueba vencida</b> hace "
                      f"{-dias} días. La cuenta está en solo lectura.</div>")
    cards = "".join(
        f"<div class='card'><div class='k'>{nombre}</div>"
        f"<div style='margin-top:10px'>{barra(u['usados'], u['limite'])}</div></div>"
        for nombre, u in (("Usuarios", uso["usuarios"]),
                          ("Sucursales", uso["sucursales"]),
                          ("Documentos del mes", uso["documentos_mes"])))
    planes = ""
    for slug, p in config.PLANES.items():
        cur = " current" if uso["plan"] == slug else ""
        planes += (f"<div class='plan{cur}'><h3>{esc(p['nombre'])}</h3>"
                   f"<div class='price'>Precio por definir</div>"
                   f"<p class='sub'>{usados_limite(p)}</p>"
                   f"<form method='post' action='/suscripcion/plan'>"
                   f"<input type='hidden' name='csrf' value='{csrf}'>"
                   f"<input type='hidden' name='plan' value='{slug}'>"
                   f"{'<b>Plan actual</b>' if cur else '<button class=\"btn ghost sm\" type=\"submit\">Cambiar a este plan</button>'}"
                   f"</form></div>")
    h += f"""
<h2>Mi suscripción</h2>
{trial_html}
<p>Estado de la cuenta: {T.badge(uso['estado'])}</p>
<h2>Uso vs. límites</h2>
<div class="cards">{cards}</div>
<h2>Cambiar de plan</h2>
<p class="sub">Precios <b>provisionales</b>. Este prototipo no procesa cobros:
el cambio de plan solo ajusta los límites.</p>
<div class="plan-cards">{planes}</div>"""
    return T.base(sesion, "Suscripción", h, active="/suscripcion")


def usados_limite(p: dict) -> str:
    return (f"{p['max_usuarios']} usuario(s) · {p['max_sucursales']} "
            f"sucursal(es) · {p['max_docs_mes']:,} docs/mes")


# --- auditoría ---------------------------------------------------------------------

def pagina_auditoria(sesion, eventos, filtro_accion="", msg="") -> str:
    h = T.alerta_ok(msg)
    h += f"""
<div class="toolbar no-print">
  <form method="get" action="/auditoria" class="inline-form">
    <div class="field"><label for="aud-accion">Filtrar por acción</label>
      <input type="search" id="aud-accion" name="accion" value="{esc(filtro_accion)}"
             placeholder="ej.: login, venta_creada"></div>
    <button class="btn" type="submit">Filtrar</button>
  </form>
</div>
<p class="sub">Registro inmutable: solo se agregan eventos, nunca se editan ni
borran.</p>"""
    filas = "".join(
        f"<tr><td>{T.fecha_do(e['creada_en'])}</td>"
        f"<td>{esc(e['accion'])}</td>"
        f"<td>{esc(e['entidad'])}{' #' + str(e['entidad_id']) if e['entidad_id'] else ''}</td>"
        f"<td>{esc(e['usuario_id'] or '—')}</td>"
        f"<td>{esc(e['ip'])}</td>"
        f"<td class='sub'>{esc((e['detalle'] or '')[:80])}</td></tr>"
        for e in eventos)
    h += (f'<div class="table-wrap"><table>'
          f"<thead><tr><th>Fecha</th><th>Acción</th><th>Entidad</th>"
          f"<th>Usuario</th><th>IP</th><th>Detalle</th></tr></thead>"
          f"<tbody>{filas or '<tr><td colspan=6>Sin eventos.</td></tr>'}</tbody></table></div>")
    return T.base(sesion, "Auditoría", h, active="/auditoria")


# --- importar ------------------------------------------------------------------------

def pagina_importar(sesion, tipo="clientes", preview=None, msg="", err="") -> str:
    h = T.alerta_ok(msg)
    if err:
        h += T.alerta_err(err)
    csrf = esc(sesion["csrf"])
    ejemplo = ("nombre,rnc,telefono,direccion\nJuan Pérez,40212345678,"
               "809-555-0101,Calle 1" if tipo == "clientes"
               else "sku,nombre,precio,costo,itbis_exento,stock\n"
                    "ARZ-001,Arroz 1 lb,68,52,no,120")
    h += f"""
<div class="toolbar">
  <a class="btn{' violet' if tipo=='clientes' else ' ghost'}" href="/importar?tipo=clientes">Clientes</a>
  <a class="btn{' violet' if tipo=='productos' else ' ghost'}" href="/importar?tipo=productos">Productos</a>
</div>"""
    if preview is None:
        h += f"""
<div class="form-card wide">
  <h2 style="margin-top:0">Paso 1 — Pegue el CSV de {esc(tipo)}</h2>
  <p class="sub">Separado por comas o punto y coma. Máximo 500 filas.</p>
  <form method="post" action="/importar/previsualizar">
    <input type="hidden" name="csrf" value="{csrf}">
    <input type="hidden" name="tipo" value="{esc(tipo)}">
    <div class="field"><label for="imp-csv">Contenido CSV</label>
      <textarea id="imp-csv" name="csv" rows="8" required
                placeholder="{esc(ejemplo)}" spellcheck="false"></textarea></div>
    <button class="btn" type="submit">Previsualizar y validar</button>
  </form>
</div>"""
    else:
        filas_ok = "".join(
            "<tr>" + "".join(f"<td>{esc(v)}</td>"
                             for v in f.values()) + "</tr>"
            for f in preview["filas"])
        cols = "".join(f"<th>{esc(c)}</th>"
                       for c in (preview["filas"][0].keys()
                                 if preview["filas"] else []))
        errs = "".join(
            f"<tr><td>{e['linea']}</td><td class='sub'>{esc(e['error'])}</td></tr>"
            for e in preview["errores"])
        import json as _json
        paquete = _json.dumps(preview["filas"], ensure_ascii=False)
        h += f"""
<h2>Paso 2 — Previsualización: {len(preview['filas'])} válidas,
{len(preview['errores'])} con error</h2>"""
        if preview["errores"]:
            h += (f"<h3>Filas con error (no se importarán)</h3><div class=\"table-wrap\"><table>"
                  f"<tr><th>Línea</th><th>Error</th></tr>{errs}</table></div>")
        if preview["filas"]:
            h += (f"<h3>Filas válidas</h3><div class=\"table-wrap\"><table><tr>{cols}</tr>{filas_ok}"
                  f"</table>"
                  f"<form method='post' action='/importar/confirmar'>"
                  f"<input type='hidden' name='csrf' value='{csrf}'>"
                  f"<input type='hidden' name='tipo' value='{esc(tipo)}'>"
                  f"<textarea name='datos' style='display:none'>"
                  f"{esc(paquete)}</textarea>"
                  f"<button class='btn violet' type='submit'>"
                  f"Confirmar importación ({len(preview['filas'])})</button> "
                  f"<a class='btn ghost' href='/importar?tipo={esc(tipo)}'>"
                  f"Cancelar</a></form>")
        else:
            h += "<p>No hay filas válidas para importar.</p>"
    return T.base(sesion, "Importar desde CSV", h, active="/importar")


# --- bienvenida (onboarding) -----------------------------------------------------------

def pagina_bienvenida(sesion, pasos, msg="") -> str:
    h = T.alerta_ok(msg)
    hechos = sum(1 for p in pasos if p["hecho"])
    pct = int(hechos * 100 / len(pasos))
    items = ""
    for p in pasos:
        marca = "✔" if p["hecho"] else "○"
        cls = "done" if p["hecho"] else ""
        items += (f"<div class='step {cls}'><span class='mark'>{marca}</span>"
                  f"<div><b>{esc(p['titulo'])}</b><br>"
                  f"<span class='sub'>{esc(p['detalle'])}</span></div>"
                  f"<span class='spacer'></span>"
                  f"<a class='btn{' ghost' if p['hecho'] else ''} sm'"
                  f" href='{p['enlace']}'>{'Revisar' if p['hecho'] else 'Hacer'}</a>"
                  f"</div>")
    h += f"""
<div class="card"><h2 style="margin-top:0">Bienvenido a ZAYVERO FACTURA 🎉</h2>
<p>Completa estos pasos para dejar tu negocio listo para facturar.</p>
<div class="meter big"><div class="bar-ok" style="width:{pct}%"></div></div>
<p class="sub">{hechos} de {len(pasos)} pasos completados</p></div>
<div class="steps">{items}</div>
<form method="post" action="/bienvenida/listo" style="margin-top:16px">
  <input type="hidden" name="csrf" value="{esc(sesion['csrf'])}">
  <button class="btn violet" type="submit">Terminar y ir al panel</button>
</form>"""
    return T.base(sesion, "Bienvenida", h, active="/bienvenida")


# --- soporte -----------------------------------------------------------------------------

def pagina_soporte(sesion, tickets, es_admin, msg="") -> str:
    h = T.alerta_ok(msg)
    csrf = esc(sesion["csrf"])
    h += f"""
<div class="form-card wide" style="margin-bottom:18px">
  <h2 style="margin-top:0">Abrir ticket de soporte</h2>
  <form method="post" action="/soporte/nuevo">
    <input type="hidden" name="csrf" value="{csrf}">
    <div class="field"><label for="sp-asunto">Asunto <span class="req" aria-hidden="true">*</span></label>
      <input type="text" id="sp-asunto" name="asunto" required maxlength="120"></div>
    <div class="field"><label for="sp-mensaje">Mensaje <span class="req" aria-hidden="true">*</span></label>
      <textarea id="sp-mensaje" name="mensaje" rows="4" required maxlength="2000"></textarea></div>
    <button class="btn" type="submit">Enviar ticket</button>
  </form>
</div>
<h2>Mis tickets</h2>"""
    filas = ""
    for t in tickets:
        accion = ""
        if es_admin and t["estado"] == "abierto":
            accion = (f"<form method='post' action='/soporte/{t['id']}/cerrar'"
                      f" style='display:inline' data-confirm='¿Cerrar este ticket?'>"
                      f"<input type='hidden' name='csrf' value='{csrf}'>"
                      f"<button class='btn ghost sm' type='submit'>Cerrar</button></form>")
        filas += (f"<tr><td>{t['id']}</td><td>{esc(t['asunto'])}</td>"
                  f"<td class='sub'>{esc(t['mensaje'][:90])}</td>"
                  f"<td>{T.badge(t['estado'])}</td>"
                  f"<td>{T.fecha_do(t['creado_en'])}</td>"
                  f"<td class='actions'>{accion}</td></tr>")
    if tickets:
        h += (f'<div class="table-wrap"><table>'
              f"<thead><tr><th>#</th><th>Asunto</th><th>Mensaje</th><th>Estado</th>"
              f"<th>Creado</th><th></th></tr></thead>"
              f"<tbody>{filas}</tbody></table></div>")
    else:
        h += T.empty_state(
            "💬", "Sin tickets de soporte",
            "¿Necesitas ayuda? Abre tu primer ticket y te respondemos "
            "en español lo antes posible.")
    return T.base(sesion, "Soporte", h, active="/soporte")


# --- vista imprimible (PDF vía navegador) ---------------------------------------------------

def pagina_reporte_imprimible(sesion, empresa, titulo, columnas, filas,
                              resumen="") -> str:
    from datetime import date as _date
    trs = "".join(
        "<tr>" + "".join(f"<td>{esc(c)}</td>" for c in f) + "</tr>"
        for f in filas)
    ths = "".join(f"<th>{esc(c)}</th>" for c in columnas)
    hoy = T.fecha_do(_date.today().isoformat(), False)
    cuerpo = f"""
<div class="toolbar no-print">
  <a class="btn ghost" href="/reportes">← Volver a reportes</a>
  <span class="spacer"></span>
  <button class="btn" onclick="window.print()">🖨 Imprimir / Guardar PDF</button>
</div>
<div class="print-head">
  <div class="doc-brand">
    <img src="/static/logo-isotipo.png" alt="Logo" width="44" height="44">
    <div><h1 style="margin:0">{esc(empresa.get('nombre',''))}</h1>
    <p class="sub">RNC {esc(empresa.get('rnc',''))} · {esc(titulo)} · {hoy}</p>
    {f'<p class="sub">{esc(resumen)}</p>' if resumen else ''}</div>
  </div>
</div>
<div class="table-wrap"><table class="print-table">
<thead><tr>{ths}</tr></thead><tbody>{trs}</tbody></table></div>
<p class="sub">Documento generado por ZAYVERO FACTURA (prototipo).</p>"""
    return T.base(sesion, titulo, cuerpo, active="/reportes")
