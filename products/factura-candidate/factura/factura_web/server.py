"""Servidor web stdlib (http.server) de ZAYVERO FACTURA.

Sin frameworks: BaseHTTPRequestHandler + ThreadingHTTPServer.
Toda query filtra por empresa_id de la sesión (aislamiento multiempresa).
FASE 1/4/7: registro público, roles y permisos, caja, devoluciones,
sucursales, exportaciones, suscripción, importación CSV, auditoría visible,
trial/estados de empresa, onboarding, recuperación, soporte, legal,
superadmin local y landing pública.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import mimetypes
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import config
from factura_persist import db
from factura_auth import auth as auth_mod
from factura_auth import registro as registro_mod
from factura_auth import seguridad as seg_mod
from factura_core import (caja, clientes, contador_ia, devoluciones,
                          importacion, inventario, pagos, reportes, saas,
                          soporte, sucursales, ventas)
from factura_core import correo as correo_mod
from factura_core import gastos as gastos_mod
from factura_core import proveedores as prov_mod
from factura_ecf import service as ecf
from factura_auditoria import registrar as audit_reg, listar as audit_list
from factura_web import templates as T
from factura_web import paginas_nuevas as P1
from factura_web import paginas_nuevas2 as P2
from factura_web import paginas_fase2 as P3
from factura_web import paginas_contador as P4
from factura_web import export as export_mod

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

# Rutas públicas (sin sesión de empresa)
PUBLICAS_GET = {"/", "/login", "/logout", "/registro", "/recuperar",
                "/restablecer", "/privacidad", "/condiciones",
                "/solicitar-demo", "/superadmin", "/superadmin/login",
                "/superadmin/logout", "/planes", "/faq", "/guia"}
PUBLICAS_POST = {"/login", "/registro", "/recuperar", "/restablecer",
                 "/solicitar-demo", "/superadmin/login"}


# --- helpers ---------------------------------------------------------------

def _cookies(handler) -> dict:
    crudo = handler.headers.get("Cookie", "")
    out = {}
    for parte in crudo.split(";"):
        if "=" in parte:
            k, v = parte.strip().split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _sesion(handler) -> dict | None:
    crudo = _cookies(handler).get(config.SESSION_COOKIE)
    payload = auth_mod.leer_sesion(crudo)
    if not payload:
        return None
    # P0 #3: revocación del lado servidor (logout) e invalidez de
    # sesiones viejas tras cambio de contraseña (sess_version).
    if seg_mod.sesion_revocada(crudo):
        return None
    if not auth_mod.version_sesion_ok(payload):
        return None
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(
            cur, "SELECT id, nombre, itbis_tasa, e_cf_mode, plan, estado,"
                 " trial_hasta FROM factura_empresas WHERE id=%s",
            (payload["e"],))
        u = db.fetchone(cur, "SELECT activo, rol FROM factura_usuarios WHERE id=%s AND empresa_id=%s",
                        (payload["u"], payload["e"]))
    finally:
        conn.close()
    if not fila or not u or not u[0]:
        return None
    empresa = {"estado": fila[5], "trial_hasta": fila[6]}
    payload["rol"] = u[1]
    payload["empresa_nombre"] = fila[1]
    payload["empresa_tasa"] = fila[2]
    payload["empresa_ecf_mode"] = fila[3]
    payload["empresa_plan"] = fila[4]
    payload["empresa_estado"] = fila[5]
    payload["trial_dias"] = saas.trial_dias_restantes(empresa)
    payload["bloqueada"] = (fila[5] != "activa") or \
        saas.bloqueada_por_trial(empresa)
    payload["csrf"] = auth_mod.csrf_token(payload)
    return payload


def _audit(handler, ses, accion: str, entidad: str = "",
           entidad_id: int | None = None, detalle: dict | None = None):
    try:
        ip = handler.client_address[0] if handler.client_address else ""
    except Exception:
        ip = ""
    eid = ses["e"] if ses else None
    uid = ses["u"] if ses else None
    audit_reg(eid, uid, accion, entidad, entidad_id, detalle or {}, ip)


def _empresa_completa(empresa_id: int) -> dict:
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(cur, "SELECT * FROM factura_empresas WHERE id=%s",
                           (empresa_id,))
        return dict(fila)
    finally:
        conn.close()


def _leer_form(handler) -> dict:
    n = int(handler.headers.get("Content-Length", 0) or 0)
    crudo = handler.rfile.read(n).decode("utf-8", "replace")
    return {k: v[0] for k, v in urllib.parse.parse_qs(crudo).items()}


def _leer_json(handler) -> dict:
    n = int(handler.headers.get("Content-Length", 0) or 0)
    crudo = handler.rfile.read(n).decode("utf-8", "replace")
    try:
        return json.loads(crudo or "{}")
    except Exception:
        return {}


def _cookie_attr(nombre: str, valor: str, max_age: int) -> str:
    """Atributos de cookie de sesión: HttpOnly + SameSite=Lax siempre;
    Secure solo con FACTURA_HTTPS=1 (producción detrás de TLS; en local
    HTTP queda desactivado para no romper la demo)."""
    attr = (f"{nombre}={valor}; HttpOnly; Path=/; SameSite=Lax; "
            f"Max-Age={max_age}")
    if config.FACTURA_HTTPS:
        attr += "; Secure"
    return attr


def _ip_cliente(handler) -> str:
    try:
        return handler.client_address[0] if handler.client_address else ""
    except Exception:
        return ""


# --- superadmin local ---------------------------------------------------------

def _super_firmar(b64: str) -> str:
    secreto = auth_mod._secret()
    return hmac.new(secreto, ("super:" + b64).encode("ascii"),
                    hashlib.sha256).hexdigest()


def _super_crear() -> str:
    payload = {"super": 1,
               "exp": int(time.time()) + 4 * 3600}
    crudo = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(crudo).decode("ascii")
    return f"{b64}.{_super_firmar(b64)}"


def _super_leer(handler):
    valor = _cookies(handler).get(config.SUPERADMIN_COOKIE)
    if not valor or "." not in valor:
        return None
    if seg_mod.sesion_revocada(valor):
        return None
    b64, firma = valor.rsplit(".", 1)
    if not hmac.compare_digest(_super_firmar(b64), firma):
        return None
    try:
        payload = json.loads(base64.urlsafe_b64decode(b64.encode("ascii")))
    except Exception:
        return None
    if payload.get("exp", 0) < time.time() or not payload.get("super"):
        return None
    payload["csrf"] = auth_mod.csrf_token(
        {"u": 0, "e": 0, "exp": payload["exp"]})
    return payload


class Handler(BaseHTTPRequestHandler):
    server_version = "ZayveroFactura/0.2"

    # -- respuestas básicas -------------------------------------------------
    def _html(self, cuerpo: str, status: int = 200,
              set_cookies: list | None = None):
        data = cuerpo.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        for ck in set_cookies or []:
            self.send_header("Set-Cookie", ck)
        self.end_headers()
        self.wfile.write(data)

    def _emitir_anon(self):
        """Emite CSRF pre-sesión: (token_para_form, header Set-Cookie)."""
        token, firmado = auth_mod.csrf_anon_nuevo()
        attr = (f"{config.ANON_CSRF_COOKIE}={firmado}; HttpOnly; Path=/; "
                "SameSite=Lax; Max-Age=1800")
        if config.FACTURA_HTTPS:
            attr += "; Secure"
        return token, attr

    def _csrf_anon_ok(self, form: dict) -> bool:
        return auth_mod.csrf_anon_valido(
            _cookies(self).get(config.ANON_CSRF_COOKIE),
            form.get("csrf", ""))

    def _csrf_anon_403(self):
        return self._html(
            "<h1>403</h1><p>Sesión inválida (CSRF). "
            "<a href='/'>Volver</a></p>", 403)

    def _json(self, obj: dict, status: int = 200):
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _redirect(self, destino: str, cookie: str | None = None,
                  borrar: bool = False, super_cookie: str | None = None,
                  super_borrar: bool = False):
        self.send_response(302)
        self.send_header("Location", destino)
        if cookie is not None:
            self.send_header(
                "Set-Cookie",
                _cookie_attr(config.SESSION_COOKIE, cookie,
                             config.SESSION_HOURS * 3600))
        if borrar:
            self.send_header(
                "Set-Cookie", _cookie_attr(config.SESSION_COOKIE, "", 0))
        if super_cookie is not None:
            self.send_header(
                "Set-Cookie",
                _cookie_attr(config.SUPERADMIN_COOKIE, super_cookie,
                             4 * 3600))
        if super_borrar:
            self.send_header(
                "Set-Cookie",
                _cookie_attr(config.SUPERADMIN_COOKIE, "", 0))
        self.end_headers()

    def _not_found(self):
        self._html("<h1>404</h1><p>No encontrado. <a href='/'>Volver</a></p>",
                   404)

    def _sin_permiso(self, ses, que: str):
        return self._html(P2.pagina_403(
            ses, f"tu rol '{ses['rol']}' no puede: {que}."), 403)

    def _requiere(self, ses, permiso: str, que: str):
        """Devuelve True si puede continuar; si no, responde 403."""
        if not config.tiene_permiso(ses["rol"], permiso):
            self._sin_permiso(ses, que)
            return False
        return True

    def log_message(self, fmt, *args):  # log sobrio
        pass

    # -- GET -----------------------------------------------------------------
    def do_GET(self):
        ruta = urllib.parse.urlparse(self.path)
        path, qs = ruta.path, urllib.parse.parse_qs(ruta.query)

        if path == "/health":
            try:
                conn = db.connect()
                try:
                    db.fetchone(conn.cursor(), "SELECT 1")
                finally:
                    conn.close()
                return self._json({"ok": True, "modo": "administrativo_no_fiscal"})
            except Exception:
                return self._json({"ok": False}, 503)
        if path.startswith("/static/"):
            return self._static(path[len("/static/"):])
        if path == "/login":
            token, ck = self._emitir_anon()
            return self._html(T.login_page(csrf=token),
                              set_cookies=[ck])
        if path == "/logout":
            seg_mod.revocar_sesion(
                _cookies(self).get(config.SESSION_COOKIE))
            return self._redirect("/login", borrar=True)
        if path == "/registro":
            token, ck = self._emitir_anon()
            return self._html(P1.pagina_registro(csrf=token),
                              set_cookies=[ck])
        if path == "/recuperar":
            token, ck = self._emitir_anon()
            return self._html(P1.pagina_recuperar(csrf=token),
                              set_cookies=[ck])
        if path == "/restablecer":
            token = qs.get("token", [""])[0]
            if not token:
                csrf_t, ck = self._emitir_anon()
                return self._html(P1.pagina_recuperar(
                    "enlace inválido: falta el token", csrf=csrf_t),
                    set_cookies=[ck])
            csrf_t, ck = self._emitir_anon()
            return self._html(P1.pagina_restablecer(token, csrf=csrf_t),
                              set_cookies=[ck])
        if path == "/privacidad":
            return self._html(P1.pagina_privacidad())
        if path == "/condiciones":
            return self._html(P1.pagina_condiciones())
        if path == "/planes":
            return self._html(P1.pagina_planes())
        if path == "/faq":
            return self._html(P1.pagina_faq())
        if path == "/guia":
            return self._html(P1.pagina_guia())
        if path == "/solicitar-demo":
            return self._redirect("/#demo")
        if path == "/superadmin" or path == "/superadmin/login":
            sup = _super_leer(self)
            if sup:
                return self._redirect("/superadmin/panel")
            token, ck = self._emitir_anon()
            return self._html(P1.superadmin_login(csrf=token),
                              set_cookies=[ck])
        if path == "/superadmin/logout":
            seg_mod.revocar_sesion(
                _cookies(self).get(config.SUPERADMIN_COOKIE))
            return self._redirect("/superadmin/login", super_borrar=True)
        if path == "/superadmin/panel":
            sup = _super_leer(self)
            if not sup:
                return self._redirect("/superadmin/login")
            msg = qs.get("msg", [""])[0]
            return self._html(P1.superadmin_panel(
                saas.listar_empresas(), soporte.listar_solicitudes(),
                sup["csrf"], msg))

        ses = _sesion(self)
        if not ses:
            if path == "/":
                csrf_t, ck = self._emitir_anon()
                return self._html(P1.landing(csrf=csrf_t),
                                  set_cookies=[ck])
            return self._redirect("/login")
        if path == "/":
            path = "/panel"  # autenticado: la landing es solo pública
        try:
            return self._get_privado(ses, path, qs)
        except Exception as exc:  # pragma: no cover - red de seguridad
            return self._html(T.base(ses, "Error",
                                     T.alerta_err("No se pudo confirmar la operación. Consulte soporte antes de repetirla.") +
                                     "<p><a href='/panel'>Volver al panel</a></p>"),
                              500)

    def _get_privado(self, ses, path, qs):
        eid = ses["e"]
        msg = qs.get("msg", [""])[0]

        if path == "/panel":
            if not self._requiere(ses, "panel", "ver el panel"):
                return
            resumen = reportes.resumen_general(eid)
            return self._html(T.pagina_panel(
                ses, resumen, pagos.total_cxc(eid),
                inventario.stock_bajo(eid), msg))

        if path == "/bienvenida":
            if not self._requiere(ses, "bienvenida", "ver la bienvenida"):
                return
            return self._html(P2.pagina_bienvenida(
                ses, saas.estado_onboarding(eid), msg))

        if path == "/pos":
            if not self._requiere(ses, "pos", "usar el POS"):
                return
            q = qs.get("q", [""])[0]
            prods = inventario.listar(eid, q=q) if q else inventario.listar(eid)[:20]
            return self._html(T.pagina_pos(
                ses, prods, q, msg, "",
                sucursales.listar(eid, solo_activas=True),
                caja.turno_abierto(eid) is not None))

        if path == "/caja":
            if not self._requiere(ses, "caja", "gestionar la caja"):
                return
            turno = caja.turno_abierto(eid)
            movs = caja.movimientos(turno["id"], eid) if turno else []
            return self._html(P2.pagina_caja(
                ses, turno, movs, caja.historial(eid), msg))

        if path == "/facturas":
            if not self._requiere(ses, "ver_ventas", "ver facturas"):
                return
            vs = ventas.listar(eid)
            for v in vs:
                if v.get("cliente_id"):
                    cli = clientes.obtener(eid, v["cliente_id"])
                    v["cliente_nombre"] = cli["nombre"] if cli else ""
            return self._html(T.pagina_facturas(ses, vs, msg))
        if path == "/facturas/nueva":
            if not self._requiere(ses, "crear_ventas", "crear facturas"):
                return
            return self._html(T.pagina_factura_nueva(
                ses, clientes.listar(eid), inventario.listar(eid),
                ses["empresa_tasa"],
                sucursales=sucursales.listar(eid, solo_activas=True)))
        if path.startswith("/facturas/"):
            if not self._requiere(ses, "ver_ventas", "ver facturas"):
                return
            vid = path.split("/")[2]
            if vid.isdigit():
                v = ventas.obtener(eid, int(vid))
                if not v:
                    return self._not_found()
                if v.get("cliente_id"):
                    cli = clientes.obtener(eid, v["cliente_id"])
                    v["cliente_nombre"] = cli["nombre"] if cli else None
                puede = ses["rol"] == "admin" and v["estado"] != "anulada"
                return self._html(T.pagina_factura_detalle(
                    ses, v, _empresa_completa(eid),
                    pagos.pagos_de_venta(eid, int(vid)), puede, msg))
            return self._not_found()

        if path == "/cotizaciones":
            if not self._requiere(ses, "ver_ventas", "ver cotizaciones"):
                return
            return self._html(T.pagina_cotizaciones(
                ses, ventas.listar_cotizaciones(eid), msg))
        if path == "/cotizaciones/nueva":
            if not self._requiere(ses, "crear_ventas", "crear cotizaciones"):
                return
            return self._html(T.pagina_cotizacion_nueva(
                ses, clientes.listar(eid), inventario.listar(eid),
                ses["empresa_tasa"]))

        if path == "/devoluciones":
            if not self._requiere(ses, "devoluciones", "gestionar devoluciones"):
                return
            rec = [v for v in ventas.listar(eid, limite=50)
                   if v["estado"] != "anulada" and v["tipo"] in ("factura", "pos")]
            return self._html(P2.pagina_devoluciones(
                ses, devoluciones.listar(eid), rec, msg))

        if path == "/clientes":
            if not self._requiere(ses, "clientes_ver", "ver clientes"):
                return
            return self._html(T.pagina_clientes(ses, clientes.listar(eid), msg))
        if path == "/clientes/nuevo":
            if not self._requiere(ses, "clientes_gestion", "crear clientes"):
                return
            return self._html(T.form_cliente(ses))
        if path.startswith("/clientes/") and path.endswith("/editar"):
            if not self._requiere(ses, "clientes_gestion", "editar clientes"):
                return
            cid = path.split("/")[2]
            c = clientes.obtener(eid, int(cid)) if cid.isdigit() else None
            if not c:
                return self._not_found()
            return self._html(T.form_cliente(ses, c))

        if path == "/inventario":
            if not self._requiere(ses, "inventario_ver", "ver inventario"):
                return
            return self._html(T.pagina_inventario(
                ses, inventario.listar(eid, solo_activos=False), msg))
        if path == "/inventario/nuevo":
            if not self._requiere(ses, "inventario_gestion",
                                  "crear productos"):
                return
            return self._html(T.form_producto(ses))
        if path == "/inventario/ajustes":
            if not self._requiere(ses, "inventario_gestion",
                                  "ajustar inventario"):
                return
            return self._html(P2.pagina_ajustes(
                ses, devoluciones.historial_ajustes(eid),
                inventario.listar(eid, solo_activos=False), msg))
        if path.startswith("/inventario/") and path.endswith("/editar"):
            if not self._requiere(ses, "inventario_gestion",
                                  "editar productos"):
                return
            pid = path.split("/")[2]
            p = inventario.obtener(eid, int(pid)) if pid.isdigit() else None
            if not p:
                return self._not_found()
            return self._html(T.form_producto(ses, p))

        if path == "/sucursales":
            if not self._requiere(ses, "sucursales_ver", "ver sucursales"):
                return
            plan = config.PLANES.get(ses.get("empresa_plan") or "basico",
                                     config.PLANES["basico"])
            return self._html(P2.pagina_sucursales(
                ses, sucursales.listar(eid), plan["max_sucursales"], msg))

        if path == "/cxc":
            if not self._requiere(ses, "cxc_ver", "ver cuentas por cobrar"):
                return
            pend = pagos.cxc_pendientes(eid)
            return self._html(T.pagina_cxc(
                ses, pend, pagos.total_cxc(eid), msg,
                vencidas_n=len(pagos.cxc_vencidas(eid))))
        if path == "/cxc/vencidas":
            if not self._requiere(ses, "cxc_ver", "ver cuentas vencidas"):
                return
            return self._html(P3.pagina_cxc_vencidas(
                ses, pagos.cxc_vencidas(eid),
                pagos.resumen_vencidas_por_cliente(eid),
                pagos.total_vencido(eid), msg))

        if path == "/gastos":
            if not self._requiere(ses, "gastos_ver",
                                  "ver gastos e ingresos"):
                return
            desde = qs.get("desde", [""])[0]
            hasta = qs.get("hasta", [""])[0]
            cat = qs.get("categoria", [""])[0]
            return self._html(P3.pagina_gastos(
                ses,
                gastos_mod.listar(eid, "gasto", desde or None, hasta or None,
                                  cat or None),
                gastos_mod.listar(eid, "ingreso", desde or None, hasta or None),
                gastos_mod.total(eid, "gasto", desde or None, hasta or None),
                gastos_mod.total(eid, "ingreso", desde or None, hasta or None),
                desde, hasta, cat, msg))
        if path == "/gastos/nuevo":
            if not self._requiere(ses, "gastos_gestion",
                                  "registrar gastos e ingresos"):
                return
            tipo = qs.get("tipo", ["gasto"])[0]
            tipo = tipo if tipo in ("gasto", "ingreso") else "gasto"
            return self._html(P3.form_gasto(
                ses, tipo,
                sucursales=sucursales.listar(eid, solo_activas=True),
                proveedores=prov_mod.listar(eid)))
        if path.startswith("/gastos/") and path.endswith("/editar"):
            if not self._requiere(ses, "gastos_gestion",
                                  "editar gastos e ingresos"):
                return
            gid = path.split("/")[2]
            tipo = qs.get("tipo", ["gasto"])[0]
            tipo = tipo if tipo in ("gasto", "ingreso") else "gasto"
            g = gastos_mod.obtener(eid, tipo, int(gid)) if gid.isdigit() \
                else None
            if not g:
                return self._not_found()
            return self._html(P3.form_gasto(
                ses, tipo, g,
                sucursales=sucursales.listar(eid, solo_activas=True),
                proveedores=prov_mod.listar(eid)))

        if path == "/proveedores":
            if not self._requiere(ses, "proveedores_ver",
                                  "ver proveedores"):
                return
            return self._html(P3.pagina_proveedores(
                ses, prov_mod.listar(eid), msg))
        if path == "/proveedores/nuevo":
            if not self._requiere(ses, "proveedores_gestion",
                                  "crear proveedores"):
                return
            return self._html(P3.form_proveedor(ses))
        if path.startswith("/proveedores/") and path.endswith("/editar"):
            if not self._requiere(ses, "proveedores_gestion",
                                  "editar proveedores"):
                return
            pid = path.split("/")[2]
            p = prov_mod.obtener(eid, int(pid)) if pid.isdigit() else None
            if not p:
                return self._not_found()
            return self._html(P3.form_proveedor(ses, p))

        if path == "/reportes":
            if not self._requiere(ses, "reportes", "ver reportes"):
                return
            desde = qs.get("desde", [""])[0]
            hasta = qs.get("hasta", [""])[0]
            return self._html(T.pagina_reportes(
                ses, reportes.ventas_por_dia(eid, 30),
                reportes.resumen_itbis(eid, desde or None, hasta or None,
                                       ses["empresa_tasa"]),
                reportes.top_productos(eid, desde or None, hasta or None),
                desde, hasta,
                gastos=reportes.gastos_por_categoria(
                    eid, desde or None, hasta or None),
                er=reportes.estado_resultados(
                    eid, desde or None, hasta or None)))
        if path == "/contador-ia":
            if not self._requiere(ses, "contador_ia_ver",
                                  "ver el Contador IA"):
                return
            _audit(self, ses, "contador_ia.consulta", "contador_ia",
                   None, {"ruta": "/contador-ia"})
            return self._html(P4.pagina_contador_ia(
                ses, contador_ia.panel_contador(eid)))
        if path == "/reportes/imprimir":
            if not self._requiere(ses, "reportes", "ver reportes"):
                return
            tipo = qs.get("tipo", ["ventas"])[0]
            empresa = _empresa_completa(eid)
            if tipo == "inventario":
                cols, filas = export_mod.filas_inventario(
                    inventario.listar(eid, solo_activos=False))
                titulo = "Reporte de inventario"
            elif tipo == "cxc":
                cols, filas = export_mod.filas_cxc(pagos.cxc_pendientes(eid))
                titulo = "Cuentas por cobrar"
            elif tipo == "gastos":
                movs = (gastos_mod.listar(eid, "gasto", limite=2000) +
                        gastos_mod.listar(eid, "ingreso", limite=2000))
                cols, filas = export_mod.filas_gastos(movs)
                titulo = "Gastos e ingresos"
            elif tipo == "estado-resultados":
                cols, filas = export_mod.filas_estado_resultados(
                    reportes.estado_resultados(eid))
                titulo = "Estado de resultados"
            else:
                vs = ventas.listar(eid, limite=500)
                for v in vs:
                    if v.get("cliente_id"):
                        c = clientes.obtener(eid, v["cliente_id"])
                        v["cliente_nombre"] = c["nombre"] if c else ""
                cols, filas = export_mod.filas_ventas(vs)
                titulo = "Reporte de ventas"
            return self._html(P2.pagina_reporte_imprimible(
                ses, empresa, titulo, cols,
                [[("" if c is None else str(c)) for c in f] for f in filas],
                f"{len(filas)} registros"))

        if path.startswith("/exportar/"):
            if not self._requiere(ses, "exportar", "exportar reportes"):
                return
            return self._exportar(ses, path)

        if path == "/importar":
            if not self._requiere(ses, "importar", "importar datos"):
                return
            tipo = qs.get("tipo", ["clientes"])[0]
            if tipo not in ("clientes", "productos"):
                tipo = "clientes"
            return self._html(P2.pagina_importar(ses, tipo))

        if path == "/ecf":
            if not self._requiere(ses, "ecf_ver", "ver e-CF"):
                return
            ventas_f = [v for v in ventas.listar(eid, tipo="factura")
                        if v["estado"] != "anulada"][:50]
            ventas_p = [v for v in ventas.listar(eid, tipo="pos")
                        if v["estado"] != "anulada"][:50]
            return self._html(T.pagina_ecf(
                ses, ecf.listar(eid), ses["empresa_ecf_mode"],
                ventas_f + ventas_p, msg))
        if path.startswith("/ecf/"):
            if not self._requiere(ses, "ecf_ver", "ver e-CF"):
                return
            partes = path.split("/")
            if len(partes) == 3 and partes[2].isdigit():
                comp = ecf.obtener(eid, int(partes[2]))
                if not comp:
                    return self._not_found()
                return self._html(T.pagina_ecf_detalle(
                    ses, comp, ses["rol"] == "admin", msg))
            if len(partes) == 4 and partes[2].isdigit() and partes[3] == "xml":
                e_ncf, xml = ecf.xml_texto(eid, int(partes[2]))
                data = xml.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/xml")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{e_ncf}.xml"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            return self._not_found()

        if path == "/auditoria":
            if not self._requiere(ses, "auditoria", "ver la auditoría"):
                return
            acc = qs.get("accion", [""])[0].strip() or None
            return self._html(P2.pagina_auditoria(
                ses, audit_list(eid, 200, acc), acc or ""))

        if path == "/suscripcion":
            if not self._requiere(ses, "suscripcion", "ver la suscripción"):
                return
            return self._html(P2.pagina_suscripcion(
                ses, saas.uso_actual(eid), msg))

        if path == "/soporte":
            if not self._requiere(ses, "soporte", "usar soporte"):
                return
            return self._html(P2.pagina_soporte(
                ses, soporte.listar_tickets(eid),
                ses["rol"] == "admin", msg))

        if path == "/config":
            if not self._requiere(ses, "config", "ver la configuración"):
                return
            conn = db.connect()
            try:
                cur = conn.cursor()
                us = [dict(f) for f in db.fetchall(
                    cur, "SELECT id, email, rol, activo FROM factura_usuarios"
                         " WHERE empresa_id=%s ORDER BY email", (eid,))]
            finally:
                conn.close()
            return self._html(T.pagina_config(
                ses, _empresa_completa(eid), us, msg))
        if path == "/config/usuarios/nuevo":
            if not self._requiere(ses, "usuarios", "gestionar usuarios"):
                return
            return self._html(T.form_usuario(ses))

        return self._not_found()

    def _exportar(self, ses, path):
        """GET /exportar/<que>.<fmt> — CSV real o SpreadsheetML (.xls)."""
        eid = ses["e"]
        resto = path[len("/exportar/"):]
        if "." not in resto:
            return self._not_found()
        que, fmt = resto.rsplit(".", 1)
        empresa = _empresa_completa(eid)
        if que == "ventas":
            vs = ventas.listar(eid, limite=2000)
            for v in vs:
                if v.get("cliente_id"):
                    c = clientes.obtener(eid, v["cliente_id"])
                    v["cliente_nombre"] = c["nombre"] if c else ""
            cols, filas = export_mod.filas_ventas(vs)
            titulo = "ventas"
        elif que == "inventario":
            cols, filas = export_mod.filas_inventario(
                inventario.listar(eid, solo_activos=False))
            titulo = "inventario"
        elif que == "cxc":
            cols, filas = export_mod.filas_cxc(pagos.cxc_pendientes(eid))
            titulo = "cxc"
        elif que == "gastos":
            movs = (gastos_mod.listar(eid, "gasto", limite=5000) +
                    gastos_mod.listar(eid, "ingreso", limite=5000))
            cols, filas = export_mod.filas_gastos(movs)
            titulo = "gastos"
        elif que == "estado-resultados":
            cols, filas = export_mod.filas_estado_resultados(
                reportes.estado_resultados(eid))
            titulo = "estado-resultados"
        else:
            return self._not_found()
        if fmt == "csv":
            data = export_mod.a_csv(cols, filas)
            ctype = "text/csv; charset=utf-8"
            ext = "csv"
        elif fmt == "xls":
            data = export_mod.a_spreadsheetml(
                f"ZAYVERO FACTURA {titulo}", cols, filas)
            ctype = "application/vnd.ms-excel"
            ext = "xls"
        else:
            return self._not_found()
        nombre = (f"zayvero-{titulo}-{empresa.get('rnc','')}.{ext}")
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Disposition",
                         f'attachment; filename="{nombre}"')
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _static(self, nombre: str):
        seguro = os.path.normpath(nombre)
        if seguro.startswith("..") or "/" in seguro.replace("\\", "/") \
                and ".." in seguro:
            return self._not_found()
        ruta = os.path.join(STATIC_DIR, seguro)
        if not os.path.isfile(ruta) or not ruta.startswith(STATIC_DIR):
            return self._not_found()
        ctype = mimetypes.guess_type(ruta)[0] or "application/octet-stream"
        with open(ruta, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "max-age=3600")
        self.end_headers()
        self.wfile.write(data)

    # -- POST ----------------------------------------------------------------
    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            return self._json({"ok": False, "error": "Content-Length inválido"}, 400)
        if length < 0 or length > 2 * 1024 * 1024:
            self.close_connection = True
            return self._json({"ok": False, "error": "solicitud demasiado grande"}, 413)
        if self.headers.get("Transfer-Encoding"):
            self.close_connection = True
            return self._json({"ok": False, "error": "Transfer-Encoding no admitido"}, 400)
        self.connection.settimeout(15)
        ruta = urllib.parse.urlparse(self.path)
        path = ruta.path

        # Rutas públicas POST (todas exigen CSRF pre-sesión, P0 #3)
        if path == "/login":
            form = _leer_form(self)
            if not self._csrf_anon_ok(form):
                return self._csrf_anon_403()
            email = form.get("email", "")
            ip = _ip_cliente(self)
            clave = f"{email.strip().lower()}|{ip}"
            if seg_mod.bloqueado(seg_mod.T_LOGIN, clave,
                                 seg_mod.LOGIN_MAX_FALLOS,
                                 seg_mod.LOGIN_VENTANA_MIN):
                _audit(self, None, "login_bloqueado",
                       detalle={"email": email,
                                "motivo": "fuerza_bruta"})
                t, ck = self._emitir_anon()
                return self._html(
                    T.login_page("Demasiados intentos fallidos. Espere "
                                 "unos minutos e inténtelo de nuevo.",
                                 csrf=t), 429, set_cookies=[ck])
            usuario = auth_mod.autenticar(email, form.get("password", ""))
            if not usuario:
                seg_mod.registrar_intento(seg_mod.T_LOGIN, clave, ip)
                _audit(self, None, "login_fallido",
                       detalle={"email": email})
                t, ck = self._emitir_anon()
                return self._html(
                    T.login_page("Credenciales inválidas o usuario inactivo.",
                                 csrf=t), 401, set_cookies=[ck])
            empresa = auth_mod.empresa_de_usuario(usuario)
            if empresa and (empresa.get("estado") or "") == "suspendida":
                _audit(self, None, "login_bloqueado",
                       detalle={"email": email, "motivo": "suspendida"})
                t, ck = self._emitir_anon()
                return self._html(T.login_page(
                    "Cuenta suspendida. Contáctenos por WhatsApp "
                    "+1 829-512-1552 para reactivarla.", csrf=t), 403,
                    set_cookies=[ck])
            _audit(self, {"e": usuario["empresa_id"], "u": usuario["id"]},
                   "login", detalle={"email": usuario["email"]})
            return self._redirect("/bienvenida" if not
                                  (empresa or {}).get("onboarding_completo")
                                  else "/panel",
                                  cookie=auth_mod.crear_sesion(usuario))

        if path == "/registro":
            form = _leer_form(self)
            if not self._csrf_anon_ok(form):
                return self._csrf_anon_403()
            try:
                res = registro_mod.registrar_empresa(
                    form.get("nombre", ""), form.get("rnc", ""),
                    form.get("telefono", ""), form.get("direccion", ""),
                    form.get("email", ""), form.get("password", ""))
            except ValueError as exc:
                t, ck = self._emitir_anon()
                return self._html(P1.pagina_registro(str(exc), csrf=t), 400,
                                  set_cookies=[ck])
            _audit(self, {"e": res["empresa_id"],
                          "u": res["usuario"]["id"]},
                   "empresa_registrada",
                   entidad="empresa", entidad_id=res["empresa_id"],
                   detalle={"nombre": form.get("nombre", ""),
                            "trial_dias": res["trial_dias"]})
            return self._redirect("/bienvenida",
                                  cookie=auth_mod.crear_sesion(res["usuario"]))

        if path == "/recuperar":
            form = _leer_form(self)
            if not self._csrf_anon_ok(form):
                return self._csrf_anon_403()
            email = form.get("email", "").strip().lower()
            ip = _ip_cliente(self)
            # P0 #1: rate limit 5/hora por correo y por IP
            if (seg_mod.bloqueado(seg_mod.T_RECUPERAR_EMAIL, email,
                                  seg_mod.RECUPERAR_MAX_POR_HORA, 60)
                    or seg_mod.bloqueado(seg_mod.T_RECUPERAR_IP, ip,
                                         seg_mod.RECUPERAR_MAX_POR_HORA,
                                         60)):
                _audit(self, None, "recuperacion_bloqueada",
                       detalle={"email": email})
                return self._html(P1.pagina_recuperar_enviado(), 429)
            seg_mod.registrar_intento(seg_mod.T_RECUPERAR_EMAIL, email, ip)
            seg_mod.registrar_intento(seg_mod.T_RECUPERAR_IP, ip, ip)
            # Anti-enumeración: crear_token_reset devuelve None si el
            # correo no existe; la respuesta es IDÉNTICA en ambos casos y
            # el token NUNCA vuelve al HTML (va por el despachador).
            token = saas.crear_token_reset(email)
            if token:
                # P0: el enlace de restablecimiento NUNCA usa el encabezado
                # Host (inyectable por el atacante); el origen es fijo por
                # configuración (FACTURA_PUBLIC_ORIGIN / defecto local).
                enlace = (f"{config.origen_publico()}/restablecer"
                          f"?token={token}")
                try:
                    correo_mod.enviar_enlace_recuperacion(email, enlace)
                except Exception:
                    pass  # best-effort: la respuesta sigue siendo genérica
            _audit(self, None, "recuperacion_solicitada",
                   detalle={"email": email})
            return self._html(P1.pagina_recuperar_enviado())

        if path == "/restablecer":
            form = _leer_form(self)
            if not self._csrf_anon_ok(form):
                return self._csrf_anon_403()
            usuario = saas.consumir_token_reset(form.get("token", ""))
            if not usuario:
                t, ck = self._emitir_anon()
                return self._html(P1.pagina_restablecer(
                    form.get("token", ""),
                    "enlace inválido, vencido o ya usado", csrf=t), 400,
                    set_cookies=[ck])
            try:
                # cambiar_password incrementa sess_version: cierra TODAS
                # las sesiones vigentes del usuario (P0 #3).
                auth_mod.cambiar_password(usuario["id"],
                                          form.get("password", ""))
            except ValueError as exc:
                t, ck = self._emitir_anon()
                return self._html(P1.pagina_restablecer(
                    form.get("token", ""), str(exc), csrf=t), 400,
                    set_cookies=[ck])
            _audit(self, {"e": usuario["empresa_id"], "u": usuario["id"]},
                   "password_restablecida")
            t, ck = self._emitir_anon()
            return self._html(T.login_page(
                "Contraseña actualizada. Ya puede entrar.", csrf=t),
                set_cookies=[ck])

        if path == "/solicitar-demo":
            form = _leer_form(self)
            if not self._csrf_anon_ok(form):
                return self._csrf_anon_403()
            try:
                s = soporte.crear_solicitud(
                    form.get("nombre", ""), form.get("empresa", ""),
                    form.get("email", ""), form.get("telefono", ""),
                    form.get("mensaje", ""))
            except ValueError as exc:
                t, ck = self._emitir_anon()
                return self._html(P1.landing(csrf=t), 400,
                                  set_cookies=[ck])
            _audit(self, None, "solicitud_demo",
                   detalle={"email": s["email"], "nombre": s["nombre"]})
            return self._html(P1.pagina_gracias_solicitud())

        if path == "/superadmin/login":
            form = _leer_form(self)
            if not self._csrf_anon_ok(form):
                return self._csrf_anon_403()
            ip = _ip_cliente(self)
            # P0 #2: máximo 5 intentos por IP cada 15 min, bloqueo en BD.
            if seg_mod.bloqueado(seg_mod.T_SUPERADMIN, ip,
                                 seg_mod.SUPERADMIN_MAX_FALLOS,
                                 seg_mod.SUPERADMIN_VENTANA_MIN):
                _audit(self, None, "superadmin_bloqueado",
                       detalle={"motivo": "fuerza_bruta"})
                t, ck = self._emitir_anon()
                return self._html(P1.superadmin_login(
                    "Demasiados intentos. Espere 15 minutos.", csrf=t),
                    429, set_cookies=[ck])
            email_ok = hmac.compare_digest(
                form.get("email", "").strip(), config.SUPERADMIN_EMAIL)
            pass_ok = hmac.compare_digest(
                form.get("password", ""), config.SUPERADMIN_PASS)
            if email_ok and pass_ok:
                return self._redirect("/superadmin/panel",
                                      super_cookie=_super_crear())
            seg_mod.registrar_intento(seg_mod.T_SUPERADMIN, ip, ip)
            _audit(self, None, "superadmin_login_fallido", detalle={})
            t, ck = self._emitir_anon()
            return self._html(P1.superadmin_login("Credenciales inválidas.",
                                                 csrf=t), 401,
                              set_cookies=[ck])

        if path in PUBLICAS_POST:
            return self._not_found()

        # Superadmin: sesión separada, sin sesión de empresa.
        if path.startswith("/superadmin/"):
            sup = _super_leer(self)
            if not sup:
                return self._redirect("/superadmin/login")
            form = _leer_form(self)
            if not auth_mod.csrf_valido(
                    {"u": 0, "e": 0, "exp": sup["exp"]},
                    form.get("csrf", "")):
                return self._html("CSRF inválido", 403)
            return self._post_superadmin(sup, path, form)

        ses = _sesion(self)
        if not ses:
            if path.startswith("/api/"):
                return self._json({"ok": False, "error": "sesión requerida"}, 401)
            return self._redirect("/login")

        es_api = path.startswith("/api/")
        if es_api:
            token = self.headers.get("X-CSRF-Token", "")
        else:
            form = _leer_form(self)
            token = form.get("csrf", "")
        if not auth_mod.csrf_valido(ses, token):
            if es_api:
                return self._json({"ok": False, "error": "CSRF inválido"}, 403)
            return self._html(T.base(ses, "Error",
                                     T.alerta_err("Sesión inválida (CSRF).")),
                              403)

        # Trial vencido o empresa suspendida: solo lectura (salvo soporte).
        if ses.get("bloqueada") and path != "/soporte/nuevo":
            if es_api:
                return self._json(
                    {"ok": False,
                     "error": "prueba vencida: la cuenta está en solo lectura"},
                    403)
            return self._html(P2.pagina_trial_vencido(
                ses, ses.get("trial_dias") or 0), 403)

        try:
            if es_api:
                return self._post_api(ses, path)
            return self._post_form(ses, path, form)
        except ValueError as exc:
            msg = urllib.parse.quote(str(exc))
            if es_api:
                return self._json({"ok": False, "error": str(exc)}, 400)
            destino = {"facturas": "/facturas", "cotizaciones": "/cotizaciones",
                       "clientes": "/clientes", "inventario": "/inventario",
                       "cxc": "/cxc", "ecf": "/ecf", "config": "/config",
                       "caja": "/caja", "devoluciones": "/devoluciones",
                       "sucursales": "/sucursales",
                       "suscripcion": "/suscripcion",
                       "importar": "/importar", "soporte": "/soporte",
                       "auditoria": "/auditoria", "gastos": "/gastos",
                       "proveedores": "/proveedores",
                       "bienvenida": "/bienvenida"}.get(
                           path.strip("/").split("/")[0], "/panel")
            return self._redirect(f"{destino}?msg={msg}")
        except Exception as exc:  # pragma: no cover - red de seguridad
            if es_api:
                return self._json({"ok": False, "error": "No se pudo confirmar la operación. Reintente con la misma clave."}, 500)
            return self._html(T.base(ses, "Error",
                                     T.alerta_err("No se pudo confirmar la operación. Consulte soporte antes de repetirla.")), 500)

    def _post_superadmin(self, sup, path, form):
        ok = lambda m: self._redirect(  # noqa: E731
            "/superadmin/panel?msg=" + urllib.parse.quote(m))
        partes = path.strip("/").split("/")
        if partes[1] == "empresa" and partes[2] == "estado":
            saas.cambiar_estado(int(form.get("empresa_id", 0)),
                                form.get("estado", ""))
            return ok("Estado de empresa actualizado")
        if partes[1] == "empresa" and partes[2] == "trial":
            saas.extender_trial(int(form.get("empresa_id", 0)),
                                int(form.get("dias", 0)))
            return ok("Trial extendido")
        if partes[1] == "solicitud" and partes[2] == "estado":
            soporte.cambiar_estado_solicitud(int(form.get("id", 0)),
                                             form.get("estado", ""))
            return ok("Solicitud actualizada")
        return self._not_found()

    def _post_api(self, ses, path):
        eid = ses["e"]
        if not config.tiene_permiso(ses["rol"], "crear_ventas"):
            return self._json({"ok": False,
                               "error": "su rol no puede crear ventas"}, 403)
        datos = _leer_json(self)

        def _items_servidor(raw):
            """Re-deriva precio/exención desde el inventario (no confía en
            el cliente para impuestos ni precios)."""
            conn = db.connect()
            try:
                cur = conn.cursor()
                out = []
                for it in raw or []:
                    pid = it.get("producto_id")
                    if pid:
                        fila = db.fetchone(
                            cur, "SELECT nombre, precio, itbis_exento, stock"
                                 " FROM factura_productos"
                                 " WHERE id=%s AND empresa_id=%s AND activo=1",
                            (pid, eid))
                        if not fila:
                            raise ValueError(
                                "producto inválido en el ticket")
                        out.append({
                            "producto_id": pid,
                            "nombre": fila[0],
                            "cantidad": it.get("cantidad", 1),
                            "precio": fila[1],
                            "itbis_exento": bool(fila[2]),
                            "descuento": it.get("descuento", 0)})
                    else:
                        out.append(it)
                return out
            finally:
                conn.close()

        def _aplicar_sucursal(venta, datos_api):
            sid = datos_api.get("sucursal_id")
            if sid:
                ventas.asignar_sucursal(eid, venta["id"], int(sid))

        if path == "/api/pos/cobrar":
            if not config.tiene_permiso(ses["rol"], "pos"):
                return self._json({"ok": False,
                                   "error": "su rol no puede usar el POS"},
                                  403)
            metodo = datos.get("metodo_pago") or "efectivo"
            v = ventas.emitir_administrativa(
                eid, "pos", None, datos.get("items"),
                creada_por=ses["u"], metodo_pago=metodo,
                sucursal_id=datos.get("sucursal_id"),
                idempotencia=self.headers.get("Idempotency-Key"))
            _audit(self, ses, "venta_creada", "venta", v["id"],
                   {"numero": v["numero"], "tipo": "pos",
                    "total": str(v["total"])})
            return self._json({"ok": True, "id": v["id"], "numero": v["numero"],
                               "total": v["total"], "items": v["items"],
                               "metodo_pago": metodo})

        if path == "/api/facturas":
            cli = datos.get("cliente_id")
            v = ventas.emitir_administrativa(
                eid, "factura", int(cli) if cli else None,
                datos.get("items"),
                descuento_global=datos.get("descuento", 0),
                creada_por=ses["u"], metodo_pago=datos.get("metodo_pago") or None,
                sucursal_id=datos.get("sucursal_id"),
                idempotencia=self.headers.get("Idempotency-Key"))
            _audit(self, ses, "venta_creada", "venta", v["id"],
                   {"numero": v["numero"], "tipo": "factura",
                    "total": str(v["total"])})
            return self._json({"ok": True, "id": v["id"], "numero": v["numero"]})

        if path == "/api/cotizaciones":
            cli = datos.get("cliente_id")
            c = ventas.crear_cotizacion(
                eid, int(cli) if cli else None,
                _items_servidor(datos.get("items")), creada_por=ses["u"])
            _audit(self, ses, "cotizacion_creada", "cotizacion", c["id"],
                   {"numero": c["numero"]})
            return self._json({"ok": True, "id": c["id"], "numero": c["numero"]})

        return self._json({"ok": False, "error": "recurso no encontrado"}, 404)

    def _post_form(self, ses, path, form):
        eid = ses["e"]
        partes = path.strip("/").split("/")
        ok = lambda d: self._redirect(d)  # noqa: E731

        if partes[0] == "bienvenida" and len(partes) == 2 \
                and partes[1] == "listo":
            saas.marcar_onboarding(eid, True)
            return ok("/panel?msg=" + urllib.parse.quote(
                "¡Bienvenido! Su negocio está listo para facturar"))

        if partes[0] == "caja":
            if not self._requiere(ses, "caja", "gestionar la caja"):
                return
            if len(partes) == 2 and partes[1] == "abrir":
                t = caja.abrir(eid, ses["u"], form.get("monto_inicial", 0))
                _audit(self, ses, "caja_abierta", "caja", t["id"],
                       {"monto_inicial": str(form.get("monto_inicial", 0))})
                return ok("/caja?msg=" + urllib.parse.quote("Turno abierto"))
            if len(partes) == 2 and partes[1] == "cerrar":
                t = caja.cerrar(eid, ses["u"], form.get("monto_final", 0))
                _audit(self, ses, "caja_cerrada", "caja", t["id"],
                       {"monto_final": str(t["monto_final"]),
                        "diferencia": str(t["diferencia"])})
                return ok("/caja?msg=" + urllib.parse.quote(
                    f"Turno cerrado. Diferencia: RD$ {t['diferencia']}"))
            if len(partes) == 2 and partes[1] == "movimiento":
                m = caja.registrar_movimiento(
                    eid, form.get("tipo", "ingreso"), form.get("monto", 0),
                    form.get("motivo", ""), ses["u"],
                    idempotencia=form.get("idempotencia"))
                _audit(self, ses, "caja_movimiento", "caja_movimiento",
                       m["id"], {"tipo": m["tipo"], "monto": str(m["monto"])})
                return ok("/caja?msg=" + urllib.parse.quote(
                    "Movimiento registrado"))

        if partes[0] == "devoluciones":
            if not self._requiere(ses, "devoluciones",
                                  "gestionar devoluciones"):
                return
            if len(partes) == 3 and partes[2] == "reembolsar":
                devoluciones.reembolsar(eid, int(partes[1]), form.get("monto", "0"),
                    form.get("metodo", "efectivo"), ses["u"], form.get("idempotencia"))
                return ok("/devoluciones?msg=" + urllib.parse.quote("Reembolso registrado; no se ejecutó transferencia bancaria"))
            if len(partes) == 2 and partes[1] == "nueva":
                items = []
                for linea in (form.get("items", "") or "").splitlines():
                    if "," in linea:
                        pid, cant = linea.split(",", 1)
                        if pid.strip() and cant.strip():
                            items.append({"producto_id": int(pid.strip()),
                                          "cantidad": cant.strip()})
                d = devoluciones.crear_devolucion(
                    eid, int(form.get("venta_id", 0)), items,
                    form.get("motivo", ""), ses["u"],
                    idempotencia=form.get("idempotencia"))
                _audit(self, ses, "devolucion_creada", "devolucion",
                       d["id"], {"numero": d["numero"],
                                 "venta_id": d["venta_id"],
                                 "total": str(d["total"])})
                return ok("/devoluciones?msg=" + urllib.parse.quote(
                    f"Nota {d['numero']} creada"))

        if partes[0] == "inventario" and len(partes) == 2 \
                and partes[1] == "ajuste":
            if not self._requiere(ses, "inventario_gestion",
                                  "ajustar inventario"):
                return
            a = devoluciones.ajuste_manual(
                eid, int(form.get("producto_id", 0)),
                form.get("stock", 0), form.get("motivo", ""), ses["u"])
            _audit(self, ses, "inventario_ajustado", "ajuste", a["id"],
                   {"producto_id": a["producto_id"],
                    "antes": str(a["stock_antes"]),
                    "despues": str(a["stock_despues"])})
            return ok("/inventario/ajustes?msg=" + urllib.parse.quote(
                "Ajuste aplicado"))

        if partes[0] == "sucursales":
            if not self._requiere(ses, "sucursales_gestion",
                                  "gestionar sucursales"):
                return
            if len(partes) == 2 and partes[1] == "nueva":
                s = sucursales.crear(eid, form.get("nombre", ""),
                                     form.get("direccion", ""),
                                     form.get("telefono", ""))
                _audit(self, ses, "sucursal_creada", "sucursal", s["id"],
                       {"nombre": s["nombre"]})
                return ok("/sucursales?msg=" + urllib.parse.quote(
                    "Sucursal creada"))
            if len(partes) == 3 and partes[2] == "editar":
                s = sucursales.actualizar(
                    eid, int(partes[1]), form.get("nombre", ""),
                    form.get("direccion", ""), form.get("telefono", ""),
                    activa=form.get("activa", "1") == "1")
                _audit(self, ses, "sucursal_actualizada", "sucursal",
                       s["id"], {"nombre": s["nombre"]})
                return ok("/sucursales?msg=" + urllib.parse.quote(
                    "Sucursal actualizada"))

        if partes[0] == "suscripcion" and len(partes) == 2 \
                and partes[1] == "plan":
            if not self._requiere(ses, "suscripcion",
                                  "cambiar el plan"):
                return
            if db.es_modo_cloud():
                raise ValueError("cambios de plan requieren habilitación del propietario")
            saas.cambiar_plan(eid, form.get("plan", ""))
            _audit(self, ses, "plan_cambiado",
                   detalle={"plan": form.get("plan", "")})
            return ok("/suscripcion?msg=" + urllib.parse.quote(
                "Plan actualizado (sin cobro: prototipo)"))

        if partes[0] == "importar":
            if not self._requiere(ses, "importar", "importar datos"):
                return
            if len(partes) == 2 and partes[1] == "previsualizar":
                tipo = form.get("tipo", "clientes")
                try:
                    if tipo == "productos":
                        prev = importacion.previsualizar_productos(
                            form.get("csv", ""))
                    else:
                        tipo = "clientes"
                        prev = importacion.previsualizar_clientes(
                            form.get("csv", ""))
                except ValueError as exc:
                    return self._html(P2.pagina_importar(
                        ses, form.get("tipo", "clientes"), None, "",
                        str(exc)), 400)
                return self._html(P2.pagina_importar(ses, tipo, prev))
            if len(partes) == 2 and partes[1] == "confirmar":
                tipo = form.get("tipo", "clientes")
                filas = importacion.desempaquetar(form.get("datos", "[]"))
                if tipo == "productos":
                    res = importacion.confirmar_productos(eid, filas)
                else:
                    res = importacion.confirmar_clientes(eid, filas)
                _audit(self, ses, "importacion_confirmada",
                       detalle={"tipo": tipo, "creados": res["creados"]})
                destino = "/clientes" if tipo == "clientes" else "/inventario"
                return ok(destino + "?msg=" + urllib.parse.quote(
                    f"Importados {res['creados']} registros"
                    + (f" ({len(res['errores'])} errores)" if res["errores"]
                       else "")))

        if partes[0] == "soporte":
            if not self._requiere(ses, "soporte", "usar soporte"):
                return
            if len(partes) == 2 and partes[1] == "nuevo":
                t = soporte.crear_ticket(eid, ses["u"],
                                         form.get("asunto", ""),
                                         form.get("mensaje", ""))
                _audit(self, ses, "ticket_creado", "ticket", t["id"],
                       {"asunto": t["asunto"]})
                return ok("/soporte?msg=" + urllib.parse.quote(
                    "Ticket enviado. Le responderemos pronto."))
            if len(partes) == 3 and partes[2] == "cerrar":
                if ses["rol"] != "admin":
                    return ok("/soporte?msg=" + urllib.parse.quote(
                        "Solo un admin puede cerrar tickets"))
                soporte.cambiar_estado_ticket(eid, int(partes[1]), "cerrado")
                _audit(self, ses, "ticket_cerrado", "ticket",
                       int(partes[1]))
                return ok("/soporte?msg=" + urllib.parse.quote(
                    "Ticket cerrado"))

        if partes[0] == "facturas" and len(partes) == 3 and partes[2] == "anular":
            if ses["rol"] != "admin":
                return ok("/facturas?msg=" + urllib.parse.quote(
                    "Solo un admin puede anular"))
            v = ventas.anular(eid, int(partes[1]))
            _audit(self, ses, "venta_anulada", "venta", v["id"],
                   {"numero": v["numero"]})
            return ok("/facturas?msg=" + urllib.parse.quote("Factura anulada"))

        if partes[0] == "cotizaciones" and len(partes) == 3 \
                and partes[2] == "convertir":
            if not self._requiere(ses, "crear_ventas", "convertir cotizaciones"):
                return
            saas.verificar_limite_documentos(eid)
            v = ventas.convertir_cotizacion(eid, int(partes[1]),
                                            creada_por=ses["u"])
            _audit(self, ses, "venta_creada", "venta", v["id"],
                   {"numero": v["numero"], "origen": "cotizacion"})
            return ok(f"/facturas/{v['id']}?msg=" + urllib.parse.quote(
                "Cotización convertida a factura " + v["numero"]))

        if partes[0] == "clientes":
            if len(partes) == 2 and partes[1] == "nuevo":
                if not self._requiere(ses, "clientes_gestion",
                                      "crear clientes"):
                    return
                c = clientes.crear(eid, form.get("nombre", ""),
                                   form.get("rnc", ""),
                                   form.get("telefono", ""),
                                   form.get("direccion", ""))
                _audit(self, ses, "cliente_creado", "cliente", c["id"],
                       {"nombre": c["nombre"]})
                return ok("/clientes?msg=" + urllib.parse.quote("Cliente creado"))
            if len(partes) == 3 and partes[2] == "editar":
                if not self._requiere(ses, "clientes_gestion",
                                      "editar clientes"):
                    return
                clientes.actualizar(eid, int(partes[1]), form.get("nombre", ""),
                                    form.get("rnc", ""), form.get("telefono", ""),
                                    form.get("direccion", ""))
                return ok("/clientes?msg=" + urllib.parse.quote(
                    "Cliente actualizado"))

        if partes[0] == "inventario":
            kwargs = dict(
                sku=form.get("sku", ""), nombre=form.get("nombre", ""),
                precio=form.get("precio", 0), costo=form.get("costo", 0),
                itbis_exento="itbis_exento" in form,
                stock=form.get("stock", 0),
                tipo=form.get("tipo", "producto"),
                stock_minimo=form.get("stock_minimo",
                                      config.STOCK_BAJO_UMBRAL))
            if len(partes) == 2 and partes[1] == "nuevo":
                if not self._requiere(ses, "inventario_gestion",
                                      "crear productos"):
                    return
                inventario.crear(eid, activo=True, **kwargs)
                _audit(self, ses, "producto_creado",
                       detalle={"nombre": kwargs["nombre"]})
                return ok("/inventario?msg=" + urllib.parse.quote(
                    "Producto creado"))
            if len(partes) == 3 and partes[2] == "editar":
                if not self._requiere(ses, "inventario_gestion",
                                      "editar productos"):
                    return
                kwargs["activo"] = "activo" in form
                inventario.actualizar(eid, int(partes[1]), **kwargs)
                return ok("/inventario?msg=" + urllib.parse.quote(
                    "Producto actualizado"))

        if partes[0] == "gastos":
            if not self._requiere(ses, "gastos_gestion",
                                  "registrar gastos e ingresos"):
                return
            tipo = form.get("tipo", "gasto")
            if tipo not in ("gasto", "ingreso"):
                tipo = "gasto"
            if len(partes) == 2 and partes[1] == "nuevo":
                g = gastos_mod.crear(
                    eid, tipo, form.get("fecha", ""),
                    form.get("categoria", ""), form.get("descripcion", ""),
                    form.get("monto", 0), metodo=form.get("metodo",
                                                          "efectivo"),
                    sucursal_id=form.get("sucursal_id") or None,
                    proveedor_id=form.get("proveedor_id") or None,
                    comprobante=form.get("comprobante", ""),
                    creada_por=ses["u"])
                _audit(self, ses, f"{tipo}_creado", tipo, g["id"],
                       {"categoria": g["categoria"],
                        "monto": str(g["monto"])})
                return ok("/gastos?msg=" + urllib.parse.quote(
                    "Registro guardado"))
            if len(partes) == 3 and partes[2] == "editar":
                g = gastos_mod.actualizar(
                    eid, tipo, int(partes[1]), form.get("fecha", ""),
                    form.get("categoria", ""), form.get("descripcion", ""),
                    form.get("monto", 0), metodo=form.get("metodo",
                                                          "efectivo"),
                    sucursal_id=form.get("sucursal_id") or None,
                    proveedor_id=form.get("proveedor_id") or None,
                    comprobante=form.get("comprobante", ""))
                _audit(self, ses, f"{tipo}_actualizado", tipo, g["id"],
                       {"monto": str(g["monto"])})
                return ok("/gastos?msg=" + urllib.parse.quote(
                    "Registro actualizado"))
            if len(partes) == 3 and partes[2] == "eliminar":
                gastos_mod.eliminar(eid, tipo, int(partes[1]))
                _audit(self, ses, f"{tipo}_eliminado", tipo,
                       int(partes[1]))
                return ok("/gastos?msg=" + urllib.parse.quote(
                    "Registro eliminado"))

        if partes[0] == "proveedores":
            if not self._requiere(ses, "proveedores_gestion",
                                  "gestionar proveedores"):
                return
            if len(partes) == 2 and partes[1] == "nuevo":
                p = prov_mod.crear(
                    eid, form.get("nombre", ""), form.get("rnc", ""),
                    form.get("telefono", ""), form.get("email", ""),
                    form.get("direccion", ""), form.get("balance", 0))
                _audit(self, ses, "proveedor_creado", "proveedor", p["id"],
                       {"nombre": p["nombre"]})
                return ok("/proveedores?msg=" + urllib.parse.quote(
                    "Proveedor creado"))
            if len(partes) == 3 and partes[2] == "editar":
                p = prov_mod.actualizar(
                    eid, int(partes[1]), form.get("nombre", ""),
                    form.get("rnc", ""), form.get("telefono", ""),
                    form.get("email", ""), form.get("direccion", ""),
                    form.get("balance", 0),
                    activo="activo" in form)
                _audit(self, ses, "proveedor_actualizado", "proveedor",
                       p["id"], {"nombre": p["nombre"]})
                return ok("/proveedores?msg=" + urllib.parse.quote(
                    "Proveedor actualizado"))
            if len(partes) == 3 and partes[2] == "eliminar":
                prov_mod.eliminar(eid, int(partes[1]))
                _audit(self, ses, "proveedor_eliminado", "proveedor",
                       int(partes[1]))
                return ok("/proveedores?msg=" + urllib.parse.quote(
                    "Proveedor dado de baja"))

        if partes[0] == "cxc" and len(partes) == 3 and partes[2] == "pago":
            if not self._requiere(ses, "cxc_gestion", "registrar pagos"):
                return
            r = pagos.registrar_pago(eid, int(partes[1]), form.get("monto", 0),
                                     form.get("metodo", "efectivo"),
                                     creado_por=ses["u"],
                                     idempotencia=form.get("idempotencia"))
            _audit(self, ses, "pago_registrado", "venta", int(partes[1]),
                   {"monto": str(form.get("monto", 0)),
                    "metodo": form.get("metodo", "efectivo")})
            return ok("/cxc?msg=" + urllib.parse.quote("Pago registrado"))

        if partes[0] == "cxc" and len(partes) == 3 \
                and partes[2] == "vencimiento":
            if not self._requiere(ses, "cxc_gestion", "fijar vencimientos"):
                return
            ventas.establecer_vencimiento(eid, int(partes[1]),
                                          form.get("vencimiento", ""))
            _audit(self, ses, "vencimiento_fijado", "venta", int(partes[1]),
                   {"vencimiento": form.get("vencimiento", "")})
            return ok("/cxc/vencidas?msg=" + urllib.parse.quote(
                "Vencimiento actualizado"))

        if partes[0] == "ecf":
            if len(partes) == 2 and partes[1] == "nuevo":
                if not self._requiere(ses, "ecf_gestion", "crear e-CF"):
                    return
                if ses["empresa_ecf_mode"] == "disabled":
                    return ok("/ecf?msg=" + urllib.parse.quote(
                        "e-CF deshabilitado (modo disabled)"))
                comp = ecf.crear(eid, int(form.get("venta_id", 0)),
                                 form.get("tipo", "32"),
                                 actor=f"usuario:{ses['u']}",
                                 encf_original=form.get("encf_original", ""))
                _audit(self, ses, "ecf_creado", "ecf", comp["id"],
                       {"e_ncf": comp["e_ncf"]})
                return ok(f"/ecf/{comp['id']}?msg=" + urllib.parse.quote(
                    "Borrador " + comp["e_ncf"] + " creado"))
            if len(partes) == 3 and partes[2] in (
                    "validar", "aprobar", "emitir", "anular"):
                if partes[2] in ("aprobar", "emitir", "anular") \
                        and ses["rol"] != "admin":
                    return ok("/ecf?msg=" + urllib.parse.quote(
                        "Se requiere rol admin"))
                if partes[2] == "validar" and not self._requiere(
                        ses, "ecf_gestion", "validar e-CF"):
                    return
                actor = f"usuario:{ses['u']}"
                cid = int(partes[1])
                if partes[2] == "validar":
                    comp = ecf.validar(eid, cid, actor)
                    m = "Comprobante validado"
                elif partes[2] == "aprobar":
                    comp = ecf.aprobar(eid, cid, actor)
                    m = "Aprobación humana registrada"
                elif partes[2] == "emitir":
                    comp = ecf.emitir(eid, cid, actor)
                    m = ("Transmitido (simulación): " +
                         comp["comprobante"]["estado"])
                else:
                    comp = ecf.anular(eid, cid, actor,
                                      form.get("motivo", ""))
                    m = "Comprobante anulado"
                _audit(self, ses, f"ecf_{partes[2]}", "ecf", comp["id"],
                       {"e_ncf": comp["e_ncf"]})
                return ok(f"/ecf/{comp['id']}?msg=" + urllib.parse.quote(m))

        if partes[0] == "config" and ses["rol"] == "admin":
            if len(partes) == 2 and partes[1] == "empresa":
                tasa = (form.get("itbis_tasa") or "18").strip()
                try:
                    t = float(tasa)
                    assert 0 <= t <= 100
                except Exception:
                    raise ValueError("tasa ITBIS inválida")
                modo = form.get("e_cf_mode", "disabled")
                if modo not in config.ECF_MODES:
                    raise ValueError("modo e-CF inválido")
                conn = db.connect()
                try:
                    cur = conn.cursor()
                    actual = db.fetchone(
                        cur, "SELECT nombre, rnc, telefono, direccion"
                             " FROM factura_empresas WHERE id=%s", (eid,))
                    actual = dict(actual) if actual else {}
                    def _cons(clave):
                        v = (form.get(clave, "") or "").strip()
                        return v if v else (actual.get(clave) or "")
                    with db.tx(conn):
                        db.execute(
                            cur, "UPDATE factura_empresas SET nombre=%s, rnc=%s,"
                                 " telefono=%s, direccion=%s, itbis_tasa=%s,"
                                 " e_cf_mode=%s WHERE id=%s",
                            (_cons("nombre"), _cons("rnc"),
                             _cons("telefono"), _cons("direccion"),
                             str(t), modo, eid))
                finally:
                    conn.close()
                return ok("/config?msg=" + urllib.parse.quote(
                    "Empresa actualizada"))
            if len(partes) == 3 and partes[1] == "usuarios" \
                    and partes[2] == "nuevo":
                saas.verificar_limite_usuarios(eid)
                try:
                    u = auth_mod.crear_usuario(eid, form.get("email", ""),
                                               form.get("password", ""),
                                               form.get("rol", "cajero"))
                except ValueError as exc:
                    return self._html(T.form_usuario(ses, str(exc)), 400)
                _audit(self, ses, "usuario_creado", "usuario", u["id"],
                       {"email": u["email"], "rol": u["rol"]})
                return ok("/config?msg=" + urllib.parse.quote(
                    "Usuario creado"))
            if len(partes) == 4 and partes[1] == "usuarios" \
                    and partes[2] == "rol":
                uid = int(partes[3])
                if uid == ses["u"]:
                    raise ValueError("no puede cambiar su propio rol")
                u = auth_mod.cambiar_rol(eid, uid, form.get("rol", "cajero"))
                _audit(self, ses, "usuario_rol_cambiado", "usuario", uid,
                       {"rol": u["rol"]})
                return ok("/config?msg=" + urllib.parse.quote(
                    "Rol actualizado"))
            if len(partes) == 4 and partes[1] == "usuarios" \
                    and partes[2] in ("activar", "desactivar"):
                uid = int(partes[3])
                if uid == ses["u"]:
                    raise ValueError(
                        "no puede desactivarse a sí mismo")
                auth_mod.activar_desactivar(eid, uid,
                                            partes[2] == "activar")
                _audit(self, ses, "usuario_" + partes[2], "usuario", uid)
                return ok("/config?msg=" + urllib.parse.quote(
                    "Usuario " + ("activado" if partes[2] == "activar"
                                  else "desactivado")))

        return self._not_found()
