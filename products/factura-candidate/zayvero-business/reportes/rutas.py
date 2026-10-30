"""SPRINT 5 PRIORIDAD 4 — Ruta GET /api/v1/os/reportes/<modulo>.<formato>.

REGLA DE PERMISOS (documentada en docs/sprint5-p4-reportes.md):
- Base: todo reporte exige `os.read` (verificado por handle_os).
- Módulos SENSIBLES (`finanzas`, `cobranzas`: P&L, gastos detallados y
  saldos por cliente) exigen además `os.write` → solo owner/admin.
  viewer/analyst reciben 403.
- Módulos operativos (inventario, compras, shield): `os.read` basta.

Aislamiento: company_id siempre del ctx (anti-IDOR); los datos salen
del bundle de la empresa autenticada.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from reportes import MODULOS, FORMATOS
from reportes.datos import generar, nombre_archivo
from reportes.formatos import generar as generar_formato, CONTENT_TYPES

# Módulos con datos financieros sensibles: requieren os.write.
SENSIBLES = frozenset({"finanzas", "cobranzas"})


def _q1(query, name, default=""):
    v = query.get(name)
    return v[0] if v else default


def _send_json(handler, status, payload):
    from webapp import server as srv
    srv._send_json(handler, status, payload)


def _send_file(handler, body: bytes, content_type: str, filename: str):
    """Descarga binaria con Content-Disposition attachment."""
    from tenant import security as sec_mod
    handler.send_response(200)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header(
        "Content-Disposition", f'attachment; filename="{filename}"')
    for k, v in sec_mod.security_headers(
            hsts=sec_mod.hsts_enabled()).items():
        handler.send_header(k, v)
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)
    return True


def manejar(handler, store, bundle, ctx, sub, query) -> bool:
    """Devuelve True si la ruta fue manejada (siempre es el caso)."""
    resto = sub[len("reportes/"):]
    if "." not in resto:
        _send_json(handler, 404, {"ok": False,
                                  "error": "formato requerido: "
                                           "<modulo>.<csv|xlsx|pdf>"})
        return True
    modulo, formato = resto.rsplit(".", 1)
    if modulo not in MODULOS:
        _send_json(handler, 404, {"ok": False,
                                  "error": "módulo inválido: use " +
                                           ", ".join(MODULOS)})
        return True
    if formato not in FORMATOS:
        _send_json(handler, 404, {"ok": False,
                                  "error": "formato inválido: use " +
                                           ", ".join(FORMATOS)})
        return True
    if modulo in SENSIBLES:
        # viewer/analyst tienen os.read pero no os.write -> 403.
        from tenant.authorization import require_permission, PermissionDenied
        try:
            require_permission(store, ctx, "os.write", sub)
        except PermissionDenied:
            _send_json(handler, 403, {"ok": False,
                                      "error": "acceso denegado: este "
                                               "reporte requiere rol "
                                               "owner o admin"})
            return True
    lang = _q1(query, "lang", "es")
    if lang not in ("es", "en"):
        _send_json(handler, 400, {"ok": False,
                                  "error": "lang debe ser es o en"})
        return True
    hoy = _q1(query, "hoy") or None
    if hoy:
        from datetime import datetime
        try:
            datetime.strptime(hoy, "%Y-%m-%d")
        except ValueError:
            _send_json(handler, 400, {"ok": False,
                                      "error": "hoy debe ser YYYY-MM-DD"})
            return True
    try:
        rep = generar(modulo, bundle, ctx.company_id, lang=lang, hoy=hoy)
        payload = generar_formato(rep, formato)
    except Exception as e:
        handler.log_message("reportes falló %s.%s: %s", modulo, formato, e)
        _send_json(handler, 500, {"ok": False,
                                  "error": "no se pudo generar el reporte"})
        return True
    filename = nombre_archivo(modulo, formato, lang, rep.generado)
    return _send_file(handler, payload, CONTENT_TYPES[formato], filename)
