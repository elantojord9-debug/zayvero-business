"""Páginas nuevas FASE 1 + FASE 4 + FASE 7 (stdlib, sin motores externos).

- Públicas (sin sesión): landing, registro, recuperar, restablecer,
  privacidad, condiciones, gracias, superadmin.
- Privadas: usan T.base (sidebar) y respetan la matriz de permisos.
"""
from __future__ import annotations

import config
from factura_web import templates as T

esc, money = T.esc, T.money


# ============ páginas públicas (standalone) ==================================

def _head_publico(titulo: str, extra: str = "") -> str:
    return f"""<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{esc(titulo)} — ZAYVERO FACTURA</title>
<link rel="stylesheet" href="/static/style.css">
{extra}
</head>
<body>"""


def landing(csrf: str = "") -> str:
    planes = ""
    for slug, p in config.PLANES.items():
        feats = "".join(f"<li>{esc(f)}</li>" for f in p["features"])
        planes += (f"<div class='plan'><h3>{esc(p['nombre'])}</h3>"
                   f"<div class='price'>Precio por definir"
                   f"<small>/mes</small></div>"
                   f"<p class='sub'>{esc(p['descripcion'])}</p>"
                   f"<ul>{feats}</ul>"
                   f"<a class='btn ghost' href='#demo'>Solicitar demo</a></div>")
    return _head_publico("Facturación simple para tu negocio en RD") + f"""
<div class="land">
<header class="land-nav">
  <div class="land-brand"><strong class="brand-wordmark">ZAYVERO <small>FACTURA</small></strong></div>
  <nav><a href="#caracteristicas">Características</a>
  <a href="#planes">Planes</a><a href="#faq">Preguntas</a>
  <a href="#demo" class="btn sm">Solicitar demo</a>
  <a href="/login" class="btn ghost sm">Entrar</a></nav>
</header>

<section class="hero">
  <div class="hero-inner">
    <h1>Factura en segundos.<br>Cobra sin enredos.</h1>
    <p class="slogan-hero">“Fiel en lo mucho, fiel en lo poco” <span>· Lucas 16:10</span></p>
    <p class="lead">ZAYVERO FACTURA es el sistema de facturación pensado para
    colmados, tiendas y PYMEs de República Dominicana: punto de venta,
    inventario, cuentas por cobrar y reportes — todo en español y en pesos.</p>
    <div class="cta-row">
      <a class="btn big" href="#demo">Solicitar demo gratis</a>
      <a class="btn ghost big" href="/login">Probar la demo en vivo</a>
    </div>
    <p class="sub">14 días de prueba · Sin tarjeta · Soporte en español</p>
  </div>
</section>

<section class="land-sec" id="caracteristicas">
  <h2>Todo lo que tu negocio necesita para vender</h2>
  <div class="feat-grid">
    <div class="feat"><h3>🧾 Punto de venta (POS)</h3>
      <p>Cobra rápido en caja con control de turno: apertura, conteo y cierre
      con diferencia calculada automáticamente.</p></div>
    <div class="feat"><h3>📦 Inventario</h3>
      <p>Stock en tiempo real, alertas de stock bajo, ajustes con motivo e
      importación masiva desde CSV.</p></div>
    <div class="feat"><h3>💳 Cuentas por cobrar</h3>
      <p>Vende a crédito y lleva el balance de cada cliente sin hojas de
      cálculo.</p></div>
    <div class="feat"><h3>📊 Reportes</h3>
      <p>Ventas por día, ITBIS débito y productos más vendidos. Exporta a CSV
      y Excel, o imprime en PDF.</p></div>
    <div class="feat"><h3>🏪 Multisucursal</h3>
      <p>Gestiona varias sucursales y elige la sucursal al vender
      (plan Pro).</p></div>
    <div class="feat"><h3>🔌 e-CF en simulación</h3>
      <p>Practica el flujo de comprobantes electrónicos con el simulador
      local integrado. <b>Sin validez fiscal ante la DGII.</b></p></div>
  </div>
</section>

<section class="land-sec alt" id="planes">
  <h2>Planes simples, en pesos</h2>
  <p class="sub" style="text-align:center">Precios <b>provisionales</b> de
  lanzamiento — pueden cambiar. Este prototipo no procesa cobros.</p>
  <div class="plan-cards">{planes}</div>
</section>

<section class="land-sec" id="demo">
  <h2>Prueba la demo en vivo</h2>
  <div class="grid2">
    <div class="card">
      <h3 style="margin-top:0">Acceso demo</h3>
      <p>Entra con la cuenta de demostración y explora el sistema con datos
      ficticios:</p>
      <div class="kv"><dt>Usuario</dt><dd>admin@demo.do</dd>
      <dt>Contraseña</dt><dd>Demo1234!</dd></div>
      <p style="margin-top:14px"><a class="btn" href="/login">Abrir demo</a>
      <a class="btn ghost" href="/registro">Crear mi cuenta gratis</a></p>
    </div>
    <div class="card">
      <h3 style="margin-top:0">Solicitar demo guiada</h3>
      <form method="post" action="/solicitar-demo">
        <input type="hidden" name="csrf" value="{csrf}">
        <div class="field"><label>Nombre *</label>
          <input type="text" name="nombre" required maxlength="80"></div>
        <div class="grid2">
          <div class="field"><label>Negocio</label>
            <input type="text" name="empresa" maxlength="80"></div>
          <div class="field"><label>Teléfono / WhatsApp</label>
            <input type="text" name="telefono" maxlength="30"></div>
        </div>
        <div class="field"><label>Email *</label>
          <input type="email" name="email" required maxlength="80"></div>
        <div class="field"><label>¿Qué necesita tu negocio?</label>
          <textarea name="mensaje" rows="3" maxlength="500"></textarea></div>
        <button class="btn violet" type="submit">Solicitar demo</button>
      </form>
    </div>
  </div>
</section>

<section class="land-sec alt" id="faq">
  <h2>Preguntas frecuentes</h2>
  <details><summary>¿Necesito internet para usarlo?</summary>
  <p>Sí, ZAYVERO FACTURA funciona en la nube: accede desde cualquier
  computadora o celular con navegador.</p></details>
  <details><summary>¿Emite facturas electrónicas válidas ante la DGII?</summary>
  <p><b>No.</b> El módulo e-CF es un <b>simulador</b> para practicar el flujo
  (borrador → validación → aprobación → transmisión simulada). No está
  certificado por la DGII ni genera comprobantes con validez fiscal.</p></details>
  <details><summary>¿Cómo funciona la prueba gratis?</summary>
  <p>Al registrarte obtienes {config.TRIAL_DIAS} días de prueba del plan
  Básico. Sin tarjeta de crédito.</p></details>
  <details><summary>¿Mis datos están seguros?</summary>
  <p>Cada empresa ve solo sus datos (aislamiento multiempresa), las
  contraseñas se guardan con hash seguro y las acciones críticas quedan en
  el registro de auditoría.</p></details>
  <details><summary>¿Puedo importar mis clientes y productos?</summary>
  <p>Sí: pega tu CSV en la sección Importar y el sistema valida cada fila
  antes de crearlos.</p></details>
</section>

<section class="land-sec" id="contacto">
  <h2>Contacto</h2>
  <p>Escríbenos por WhatsApp al <b>+1 829-512-1552</b> o deja tu solicitud
  en el formulario de demo y te contactamos.</p>
</section>

<footer class="land-foot">
  <div><b>ZAYVERO FACTURA</b> · prototipo — datos 100% ficticios.</div>
  <div><a href="/privacidad">Privacidad</a> · <a href="/condiciones">Condiciones</a>
  · <a href="/login">Entrar</a></div>
</footer>
</div>
</body>
</html>"""


def pagina_registro(err: str = "", csrf: str = "") -> str:
    e = f'<div class="alert err">{esc(err)}</div>' if err else ""
    return _head_publico("Crear cuenta") + f"""
<div class="login-wrap">
  <div class="login-card" style="max-width:560px">
    <strong class="brand-wordmark">ZAYVERO <small>FACTURA</small></strong>
    <h1>Crea tu cuenta</h1>
    <p>{config.TRIAL_DIAS} días de prueba gratis · Sin tarjeta</p>
    {e}
    <form method="post" action="/registro">
      <input type="hidden" name="csrf" value="{esc(csrf)}">
      <h3>Tu empresa</h3>
      <div class="field"><label>Nombre de la empresa *</label>
        <input type="text" name="nombre" required maxlength="120"></div>
      <div class="grid2">
        <div class="field"><label>RNC * (9 u 11 dígitos)</label>
          <input type="text" name="rnc" required maxlength="13"
                 placeholder="130123456"></div>
        <div class="field"><label>Teléfono</label>
          <input type="text" name="telefono" maxlength="30"></div>
      </div>
      <div class="field"><label>Dirección</label>
        <input type="text" name="direccion" maxlength="160"></div>
      <h3>Tu usuario administrador</h3>
      <div class="field"><label>Email *</label>
        <input type="email" name="email" required maxlength="80"></div>
      <div class="field"><label>Contraseña * (mínimo 12 caracteres)</label>
        <input type="password" name="password" required minlength="12"></div>
      <button class="btn violet" type="submit" style="width:100%">
        Crear cuenta y empezar prueba</button>
    </form>
    <p class="sub" style="text-align:center;margin-top:12px">
      ¿Ya tienes cuenta? <a href="/login">Entrar</a></p>
  </div>
</div>
</body>
</html>"""


def pagina_recuperar(err: str = "", csrf: str = "") -> str:
    e = f'<div class="alert err">{esc(err)}</div>' if err else ""
    return _head_publico("Recuperar acceso") + f"""
<div class="login-wrap">
  <div class="login-card">
    <strong class="brand-wordmark">ZAYVERO <small>FACTURA</small></strong>
    <h1>Recuperar acceso</h1>
    <p>Escribe tu correo y generaremos un enlace de restablecimiento.</p>
    {e}
    <form method="post" action="/recuperar">
      <input type="hidden" name="csrf" value="{esc(csrf)}">
      <div class="field"><label>Email *</label>
        <input type="email" name="email" required></div>
      <button class="btn" type="submit" style="width:100%">Generar enlace</button>
    </form>
    <p class="sub" style="text-align:center;margin-top:12px">
      <a href="/login">Volver al login</a></p>
  </div>
</div>
</body>
</html>"""


def pagina_recuperar_enviado() -> str:
    """Respuesta GENÉRICA anti-enumeración (P0 #1): idéntica exista o no
    el correo. El token NUNCA vuelve al HTML: viaja por el despachador
    de correo (en demo, a .demo-bandeja/; en producción, por SMTP)."""
    return _head_publico("Revisa tu correo") + """
<div class="login-wrap">
  <div class="login-card">
    <strong class="brand-wordmark">ZAYVERO <small>FACTURA</small></strong>
    <h1>Revisa tu correo</h1>
    <p>Si el correo está registrado, enviamos un enlace de
    restablecimiento (válido 30 minutos, un solo uso).</p>
    <p class="sub">En modo demo el enlace queda en la bandeja local
    <code>.demo-bandeja/</code> del proyecto; en producción llega por
    email.</p>
    <p class="sub" style="text-align:center;margin-top:12px">
      <a href="/login">Volver al login</a></p>
  </div>
</div>
</body>
</html>"""


def pagina_restablecer(token: str, err: str = "", csrf: str = "") -> str:
    e = f'<div class="alert err">{esc(err)}</div>' if err else ""
    return _head_publico("Restablecer contraseña") + f"""
<div class="login-wrap">
  <div class="login-card">
    <strong class="brand-wordmark">ZAYVERO <small>FACTURA</small></strong>
    <h1>Nueva contraseña</h1>
    {e}
    <form method="post" action="/restablecer">
      <input type="hidden" name="token" value="{esc(token)}">
      <input type="hidden" name="csrf" value="{esc(csrf)}">
      <div class="field"><label>Nueva contraseña * (mínimo 12 caracteres)</label>
        <input type="password" name="password" required minlength="12"></div>
      <button class="btn" type="submit" style="width:100%">Cambiar contraseña</button>
    </form>
  </div>
</div>
</body>
</html>"""


def pagina_gracias_solicitud() -> str:
    return _head_publico("Solicitud recibida") + """
<div class="login-wrap">
  <div class="login-card" style="text-align:center">
    <strong class="brand-wordmark">ZAYVERO <small>FACTURA</small></strong>
    <h1>¡Gracias!</h1>
    <p>Recibimos tu solicitud de demo. Te contactaremos pronto.</p>
    <p><a class="btn" href="/">Volver al inicio</a>
    <a class="btn ghost" href="/login">Probar la demo</a></p>
  </div>
</div>
</body>
</html>"""


_BORRADOR_LEGAL = """<div class="alert err"><b>BORRADOR — pendiente revisión
legal.</b> Este texto es un borrador y no constituye asesoría legal. No
promete certificación fiscal ante la DGII ni ningún otro organismo.</div>"""


def pagina_privacidad() -> str:
    return _head_publico("Política de privacidad") + f"""
<div class="legal-wrap"><div class="legal-card">
  <h1>Política de privacidad</h1>
  {_BORRADOR_LEGAL}
  <p><b>Última actualización:</b> octubre 2026 · <b>Ámbito:</b> República Dominicana</p>
  <h2>1. Datos que recogemos</h2>
  <p>Al registrar su empresa recogemos: nombre y RNC de la empresa, datos de
  contacto, y las credenciales de sus usuarios (la contraseña se almacena
  únicamente como hash irreversible). Los datos operativos que usted carga
  (clientes, productos, ventas) pertenecen a su empresa.</p>
  <h2>2. Uso de los datos</h2>
  <p>Usamos sus datos solo para operar el servicio: facturación, reportes,
  soporte y comunicaciones del servicio. No vendemos ni alquilamos sus datos
  a terceros.</p>
  <h2>3. Aislamiento multiempresa</h2>
  <p>Cada empresa accede únicamente a sus propios datos. Las sesiones están
  firmadas y expiran automáticamente.</p>
  <h2>4. Sus derechos</h2>
  <p>Puede solicitar acceso, corrección o eliminación de sus datos
  escribiendo a nuestro canal de soporte. Atendemos solicitudes conforme a
  la Ley No. 172-13 de la República Dominicana sobre protección de datos.</p>
  <h2>5. Seguridad</h2>
  <p>Aplicamos hash seguro de contraseñas, control de roles y registro de
  auditoría de acciones críticas. Ningún sistema es infalible: le
  recomendamos usar contraseñas únicas y no compartirlas.</p>
  <p><a href="/">← Volver al inicio</a></p>
</div></div>
</body>
</html>"""


def pagina_condiciones() -> str:
    return _head_publico("Términos y condiciones") + f"""
<div class="legal-wrap"><div class="legal-card">
  <h1>Términos y condiciones del servicio</h1>
  {_BORRADOR_LEGAL}
  <p><b>Última actualización:</b> octubre 2026 · <b>Ámbito:</b> República Dominicana</p>
  <h2>1. El servicio</h2>
  <p>ZAYVERO FACTURA es un software de facturación, punto de venta,
  inventario y reportes para empresas de la República Dominicana. Se ofrece
  "como está" durante el período de prueba y bajo suscripción.</p>
  <h2>2. Prueba gratis</h2>
  <p>Las cuentas nuevas incluyen {config.TRIAL_DIAS} días de prueba del plan
  Básico, sin tarjeta de crédito. Al vencer, la cuenta pasa a <b>solo
  lectura</b> hasta regularizar su suscripción.</p>
  <h2>3. Precios</h2>
  <p>Los precios están pendientes de aprobación y no se publican todavía. Los
  planes se facturan en pesos dominicanos (RD$).</p>
  <h2>4. Facturación electrónica (e-CF)</h2>
  <p>El módulo e-CF incluido es un <b>simulador con fines de práctica</b>.
  <b>No genera comprobantes con validez fiscal ante la DGII</b> ni sustituye
  los requisitos de la Norma General 06-2018 y demás normativa aplicable. El
  usuario es responsable de su cumplimiento tributario.</p>
  <h2>5. Uso aceptable</h2>
  <p>Se prohíbe usar el servicio para actividades ilícitas, cargar datos de
  terceros sin autorización o intentar vulnerar la seguridad del sistema.
  Podemos suspender cuentas que incumplan estos términos.</p>
  <h2>6. Limitación de responsabilidad</h2>
  <p>En la máxima medida permitida por la ley, ZAYVERO FACTURA no será responsable
  por daños indirectos derivados del uso del servicio. Nuestra
  responsabilidad total no excederá lo pagado por el cliente en los últimos
  3 meses.</p>
  <p><a href="/">← Volver al inicio</a></p>
</div></div>
</body>
</html>"""


def superadmin_login(err: str = "", csrf: str = "") -> str:
    e = f'<div class="alert err">{esc(err)}</div>' if err else ""
    return _head_publico("Superadmin") + f"""
<div class="login-wrap">
  <div class="login-card">
    <strong class="brand-wordmark">ZAYVERO <small>FACTURA</small></strong>
    <h1>Superadmin del sistema</h1>
    <p class="sub">Acceso local de administración de la plataforma.</p>
    {e}
    <form method="post" action="/superadmin/login">
      <input type="hidden" name="csrf" value="{esc(csrf)}">
      <div class="field"><label>Usuario</label>
        <input type="text" name="email" required autocomplete="off"></div>
      <div class="field"><label>Contraseña</label>
        <input type="password" name="password" required></div>
      <button class="btn" type="submit" style="width:100%">Entrar</button>
    </form>
  </div>
</div>
</body>
</html>"""


def superadmin_panel(empresas: list[dict], solicitudes: list[dict],
                     csrf: str, msg: str = "") -> str:
    h = T.alerta_ok(msg)
    filas = ""
    for e in empresas:
        dias = e.get("trial_dias")
        trial = "—" if dias is None else (f"{dias} días" if dias >= 0
                                          else f"vencido ({dias})")
        filas += (f"<tr><td>{e['id']}</td><td>{esc(e['nombre'])}</td>"
                  f"<td>{esc(e['rnc'])}</td><td>{esc(e['plan'])}</td>"
                  f"<td>{T.badge(e['estado'])}</td>"
                  f"<td>{esc(trial)}</td><td class='num'>{e['usuarios']}</td>"
                  f"<td><form method='post' action='/superadmin/empresa/estado'"
                  f" style='display:inline'>"
                  f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
                  f"<input type='hidden' name='empresa_id' value='{e['id']}'>"
                  f"<select name='estado'><option value='activa'>activa</option>"
                  f"<option value='suspendida'>suspendida</option></select> "
                  f"<button class='btn sm' type='submit'>Aplicar</button></form> "
                  f"<form method='post' action='/superadmin/empresa/trial'"
                  f" style='display:inline'>"
                  f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
                  f"<input type='hidden' name='empresa_id' value='{e['id']}'>"
                  f"<input type='number' name='dias' value='14' min='1' max='365'"
                  f" style='width:64px'> "
                  f"<button class='btn ghost sm' type='submit'>+días</button></form>"
                  f"</td></tr>")
    sols = "".join(
        f"<tr><td>{T.fecha_do(s['creada_en'])}</td>"
        f"<td>{esc(s['nombre'])}</td><td>{esc(s['empresa'])}</td>"
        f"<td>{esc(s['email'])}</td><td>{esc(s['telefono'])}</td>"
        f"<td>{T.badge(s['estado'])}</td>"
        f"<td><form method='post' action='/superadmin/solicitud/estado'"
        f" style='display:inline'>"
        f"<input type='hidden' name='csrf' value='{esc(csrf)}'>"
        f"<input type='hidden' name='id' value='{s['id']}'>"
        f"<select name='estado'><option value='nueva'>nueva</option>"
        f"<option value='contactada'>contactada</option>"
        f"<option value='descartada'>descartada</option></select> "
        f"<button class='btn sm' type='submit'>OK</button></form></td></tr>"
        for s in solicitudes)
    h += f"""
<h2>Empresas</h2>
<div class="table-wrap"><table>
<thead><tr><th>ID</th><th>Nombre</th><th>RNC</th><th>Plan</th><th>Estado</th>
<th>Trial</th><th class="num">Usuarios</th><th>Acciones</th></tr></thead>
<tbody>{filas or '<tr><td colspan="8">Sin empresas.</td></tr>'}</tbody></table></div>
<h2>Solicitudes de demo</h2>
<div class="table-wrap"><table>
<thead><tr><th>Fecha</th><th>Nombre</th><th>Empresa</th><th>Email</th>
<th>Teléfono</th><th>Estado</th><th>Acción</th></tr></thead>
<tbody>{sols or '<tr><td colspan="7">Sin solicitudes.</td></tr>'}</tbody></table></div>
<p><a class="btn ghost" href="/superadmin/logout">Cerrar sesión</a></p>"""
    return _head_publico("Superadmin") + f"""
<div class="legal-wrap"><div class="legal-card" style="max-width:1100px">
<h1>Superadmin del sistema</h1>{h}</div></div>
</body>
</html>"""


# ============ FASE 8: páginas comerciales ====================================

def _nav_publico() -> str:
    return """<div class="land"><header class="land-nav">
  <div class="land-brand"><strong class="brand-wordmark">ZAYVERO <small>FACTURA</small></strong></div>
  <nav><a href="/planes">Planes</a><a href="/faq">Preguntas</a>
  <a href="/guia">Guía</a><a href="/#demo" class="btn sm">Solicitar demo</a>
  <a href="/login" class="btn ghost sm">Entrar</a></nav></header>"""


def _pie_publico() -> str:
    return """<footer class="land-foot">
  <div><b>ZAYVERO FACTURA</b> · prototipo — datos 100% ficticios.</div>
  <div><a href="/privacidad">Privacidad</a> · <a href="/condiciones">Condiciones</a>
  · <a href="/">Inicio</a></div></footer></div></body></html>"""


def pagina_planes() -> str:
    tarjetas = ""
    for slug, p in config.PLANES.items():
        feats = "".join(f"<li>{esc(f)}</li>" for f in p["features"])
        tarjetas += (f"<div class='plan'><h3>{esc(p['nombre'])}</h3>"
                     f"<div class='price'>Precio por definir"
                     f"<small>/mes</small></div>"
                     f"<p class='sub'>{esc(p['descripcion'])}</p><ul>{feats}</ul>"
                     f"<p class='hint'>Hasta {p['max_usuarios']} usuario(s) · "
                     f"{p['max_sucursales']} sucursal(es) · "
                     f"{p['max_docs_mes']:,} documentos/mes</p>"
                     f"<a class='btn ghost' href='/#demo'>Solicitar demo</a></div>")
    return _head_publico("Planes") + _nav_publico() + f"""
<section class="land-sec"><h2>Planes</h2>
<p class="lead">Precios provisionales en pesos dominicanos.
<b>Todavía no estamos cobrando</b>: al registrarte obtienes
{config.TRIAL_DIAS} días de prueba sin tarjeta de crédito.</p>
<div class="plans">{tarjetas}</div>
<p class="hint">¿No sabes cuál te conviene? Empieza con el Básico y sube de
plan cuando tu negocio crezca. Escríbenos por WhatsApp al
<b>+1 829-512-1552</b> y te asesoramos sin compromiso.</p>
</section>""" + _pie_publico()


_FAQ_ITEMS = [
    ("¿Necesito internet para usarlo?",
     "Sí. ZAYVERO FACTURA funciona en la nube: accede desde cualquier "
     "computadora o celular con navegador. No hay nada que instalar."),
    ("¿Emite facturas electrónicas válidas ante la DGII?",
     "<b>No.</b> El módulo e-CF es un <b>simulador</b> para practicar el flujo "
     "(borrador → validación → aprobación → transmisión simulada). No está "
     "certificado por la DGII ni genera comprobantes con validez fiscal. "
     "Las facturas del sistema son documentos administrativos."),
    ("¿Cómo funciona la prueba gratis?",
     "Al registrarte obtienes días de prueba del plan Básico, sin tarjeta de "
     "crédito y sin compromiso."),
    ("¿Mis datos están seguros?",
     "Cada empresa ve solo sus datos (aislamiento multiempresa), las "
     "contraseñas se guardan con hash seguro, las sesiones expiran y las "
     "acciones críticas quedan en el registro de auditoría."),
    ("¿Puedo importar mis clientes y productos?",
     "Sí: pega tu CSV en la sección Importar y el sistema valida cada fila "
     "antes de crearlos. También puedes agregarlos uno por uno."),
    ("¿Sirve para un colmado o tienda pequeña?",
     "Sí: el plan Básico está pensado para colmados y negocios pequeños — "
     "punto de venta rápido, inventario y reportes en español."),
    ("¿Puedo tener varias sucursales o empleados?",
     "Sí, desde el plan Negocio: sucursales con selector en ventas y "
     "empleados con roles (administrador, cajero, contador) y permisos."),
    ("¿Qué pasa si me quedo sin inventario?",
     "Puedes fijar un stock mínimo por producto y el panel te avisa cuando "
     "está bajo o agotado."),
    ("¿Puedo vender a crédito?",
     "Sí: las facturas sin pago completo generan cuentas por cobrar, puedes "
     "registrar abonos parciales y ver las vencidas por cliente."),
    ("¿Funciona en el celular?",
     "Sí: la interfaz es responsive y el POS está diseñado para pantallas "
     "táctiles."),
    ("¿Puedo cancelar cuando quiera?",
     "Sí. No hay contratos forzosos ni penalidades en esta etapa."),
    ("¿Hacen respaldo de mis datos?",
     "Sí: generamos respaldos verificados con SHA-256 y procedimiento de "
     "restauración probado."),
    ("¿El contador IA toma decisiones por mí?",
     "No. El contador IA solo <b>informa</b>: analiza tus datos y sugiere "
     "ideas. Nunca modifica impuestos, ni ejecuta pagos, ni cambia tus "
     "números."),
    ("¿Cuándo sale la versión oficial?",
     "Estamos en etapa de demostración y pruebas con empresas reales. "
     "Déjanos tu solicitud de demo y te avisamos."),
]


def pagina_faq() -> str:
    items = "".join(
        f"<details><summary>{esc(q)}</summary><p>{r}</p></details>"
        for q, r in _FAQ_ITEMS)
    # Nota: las respuestas contienen HTML intencional (<b>), no escaparlas.
    return _head_publico("Preguntas frecuentes") + _nav_publico() + f"""
<section class="land-sec"><h2>Preguntas frecuentes</h2>
{items}
<p class="hint">¿Otra pregunta? Escríbenos por WhatsApp al
<b>+1 829-512-1552</b>.</p></section>""" + _pie_publico()


_GUIA_PASOS = [
    ("Crea tu cuenta",
     "Entra a /registro, completa los datos de tu empresa (nombre, RNC, "
     "teléfono, dirección) y crea tu usuario administrador. Obtienes días "
     "de prueba automáticamente.",
     "/registro"),
    ("Configura tu empresa",
     "En Configuración revisa la tasa de ITBIS (18% por defecto), tus "
     "sucursales y los usuarios con sus roles.",
     "/config"),
    ("Agrega tus productos",
     "En Inventario → Nuevo producto: SKU, nombre, precio, costo y stock "
     "inicial. Marca el stock mínimo para recibir alertas. Los servicios "
     "(sin inventario) se marcan como tipo servicio.",
     "/inventario/nuevo"),
    ("Agrega tus clientes",
     "En Clientes → Nuevo: nombre, RNC (opcional), teléfono y dirección. "
     "También puedes importarlos desde un CSV.",
     "/clientes/nuevo"),
    ("Haz tu primera venta en el POS",
     "Abre un turno de caja, entra al POS, agrega productos al ticket y "
     "cobra en efectivo, tarjeta o transferencia. Cierra el turno al final "
     "del día.",
     "/pos"),
    ("Factura a crédito y cobra después",
     "Crea facturas sin pago completo: se convierten en cuentas por cobrar. "
     "Registra abonos parciales y controla las vencidas por cliente.",
     "/cxc"),
    ("Revisa tus reportes y respalda",
     "En Reportes descarga ventas, gastos y estado de resultados en CSV o "
     "Excel. Pide tu respaldo verificado antes de cualquier cambio grande.",
     "/reportes"),
]


def pagina_guia() -> str:
    pasos = "".join(
        f"<div class='plan'><h3>Paso {i}: {esc(t)}</h3><p>{esc(d)}</p>"
        f"<a class='btn ghost sm' href='{esc(link)}'>Ir ahora</a></div>"
        for i, (t, d, link) in enumerate(_GUIA_PASOS, 1))
    return _head_publico("Guía de primeros pasos") + _nav_publico() + f"""
<section class="land-sec"><h2>Guía de primeros pasos</h2>
<p class="lead">De cero a tu primera venta en 7 pasos. Todo con datos de prueba primero, sin miedo a equivocarte.</p>
<div class="plans">{pasos}</div>
<p class="hint">Tip: practica el flujo completo con la empresa de demostración "
"antes de cargar tus datos reales.</p></section>""" + _pie_publico()
