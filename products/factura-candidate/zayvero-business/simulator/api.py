"""EXTRA 2 (sprint 12-oct-2026) — API del Simulator.

`POST /api/v1/os/simulator/escenario`

Seguridad (mismo patrón que `webapp/os_routes.py::handle_os`):
- Módulo OS habilitado (503 si no).
- Rate limiting ANTES de cualquier trabajo (cubetas de escritura,
  por ser POST) → 429 + Retry-After.
- Auth (401) y aislamiento multiempresa: `company_id` siempre del
  ctx (anti-IDOR); el bundle leído es el de esa empresa.
- Permiso `os.write` (403 si falta) + CSRF (403 si falla), como el
  resto de POSTs de `/api/v1/os/*`.
- Cuerpo > tope → 413 sin leer el body.

DIFERENCIA CLAVE con las rutas de mutación: el simulador NUNCA
escribe. No pasa por `os_persist/tx.py::mutar_os`, no llama a
`auditar()`, no reclama Idempotency-Key. Solo lee el bundle y
calcula. (Pasarlo por mutar_os tomaría el lock serializado por
empresa y abriría una transacción PG para una operación de pura
lectura.)

Contrato del body:
    {"tipo": "compra"|"precio"|"quiebre"|"rentabilidad", <parámetros>}
Ver docs/simulator.md para el contrato completo de cada tipo.

Respuestas: {"ok": True, "data": {...}} / {"ok": False, "error": ...}
con los mismos códigos que el resto de la API OS.
"""
from __future__ import annotations

import os as _os
import sys

sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from .engine import Simulador, ErrorSimulador  # noqa: E402


# P1 issue #3 (13-oct-2026) — los defaults de escenario son sintéticos y
# NO calibrados con datos reales: se etiquetan como demo en la respuesta.
# Misma convención que catalogo/config.py (nota_demo / demo_por_defecto).
_DEFAULTS_DEMO = {
    "almacen_id": "principal",
    "moneda": "DOP",
    "ventana_demanda_dias": 30,
    "horizonte_dias": 30,
    "dias_cobertura_objetivo": 30,
    "escenarios": "{optimista: 1.15, base: 1.0, pesimista: 0.85}",
}


def _nota_defaults_demo(body: dict, claves: tuple) -> str | None:
    """Devuelve la nota demo si algún parámetro usó el valor por defecto."""
    usados = [k for k in claves
              if body.get(k) is None and k in _DEFAULTS_DEMO]
    if not usados:
        return None
    det = ", ".join("%s=%s" % (k, _DEFAULTS_DEMO[k]) for k in usados)
    return ("Valores demo (sintéticos, NO calibrados con datos reales): %s. "
            "Ajústelos en la petición para escenarios personalizados."
            % det)


def ejecutar_escenario(bundle, body: dict) -> dict:
    """Ejecuta el escenario sobre el bundle (SOLO lectura + cálculo).

    Lanza ErrorSimulador/ValueError/KeyError/TypeError ante body
    inválido (la ruta los traduce a 400).
    """
    if not isinstance(body, dict):
        raise ErrorSimulador("el cuerpo debe ser un objeto JSON")
    tipo = body.get("tipo")
    sim = Simulador(bundle)
    nota_demo = None
    if tipo == "compra":
        nota_demo = _nota_defaults_demo(
            body, ("almacen_id", "moneda", "ventana_demanda_dias"))
        res = sim.simular_compra(
            proveedor_id=body["proveedor_id"],
            almacen_id=str(body.get("almacen_id", "principal")),
            lineas=body["lineas"],
            moneda=str(body.get("moneda", "DOP")),
            ventana_demanda_dias=int(body.get("ventana_demanda_dias", 30)),
            hoy=body.get("hoy"))
    elif tipo == "precio":
        nota_demo = _nota_defaults_demo(
            body, ("almacen_id", "moneda", "horizonte_dias",
                   "ventana_demanda_dias", "escenarios"))
        res = sim.simular_precio(
            producto_id=str(body["producto_id"]),
            nuevo_precio=str(body["nuevo_precio"]),
            almacen_id=str(body.get("almacen_id", "principal")),
            moneda=str(body.get("moneda", "DOP")),
            horizonte_dias=int(body.get("horizonte_dias", 30)),
            ventana_demanda_dias=int(body.get("ventana_demanda_dias", 30)),
            escenarios=body.get("escenarios"),
            hoy=body.get("hoy"))
    elif tipo == "quiebre":
        nota_demo = _nota_defaults_demo(
            body, ("almacen_id", "moneda", "horizonte_dias",
                   "dias_cobertura_objetivo", "ventana_demanda_dias"))
        res = sim.simular_quiebre(
            almacen_id=body.get("almacen_id"),
            moneda=str(body.get("moneda", "DOP")),
            horizonte_dias=int(body.get("horizonte_dias", 30)),
            dias_cobertura_objetivo=int(
                body.get("dias_cobertura_objetivo", 30)),
            ventana_demanda_dias=int(body.get("ventana_demanda_dias", 30)),
            hoy=body.get("hoy"))
    elif tipo == "rentabilidad":
        nota_demo = _nota_defaults_demo(body, ("moneda",))
        res = sim.rentabilidad(
            producto_ids=body.get("producto_ids"),
            moneda=str(body.get("moneda", "DOP")),
            desde=body.get("desde"),
            hasta=body.get("hasta"),
            mapa_lineas=body.get("mapa_lineas"))
    else:
        raise ErrorSimulador(
            "tipo inválido: use compra, precio, quiebre o rentabilidad")
    if nota_demo and isinstance(res, dict):
        sup = res.get("supuestos")
        if isinstance(sup, list):
            res["supuestos"] = sup + [nota_demo]
    return res


def handle_simulator(handler, store, ctx, token, method, path, query):
    """Despachador del sub-path /api/v1/os/simulator/*.

    Firma idéntica a `handle_os` para que `webapp/server.py` lo use
    como alternativa. Devuelve True (ruta manejada).
    """
    # Importación perezosa: evita ciclos al cargar el servidor y
    # reutiliza el preámbulo de seguridad ya probado de os_routes.
    from webapp.os_routes import (_h, _need, _ok, _err, _send,
                                  _os_rate_limit_ok, _os_body_ok,
                                  _body_json)
    srv = _h()
    if _os.environ.get("ZAYVERO_OS_ENABLED", "0") != "1":
        return _send(handler, _err(handler, 503,
                                   "módulo Business OS no habilitado"))
    sub = path[len("/api/v1/os/"):]
    # BLOQUE 3: rate limiting antes de cualquier trabajo.
    if not _os_rate_limit_ok(handler, ctx, method):
        return True
    if method != "POST":
        return _send(handler, _err(handler, 405, "método no permitido"))
    if sub != "simulator/escenario":
        return _send(handler, _err(handler, 404, "recurso no encontrado"))
    # Permiso de escritura + CSRF: igual que el resto de POSTs OS.
    # (El simulador no muta, pero es una operación con cuerpo que
    # consume cómputo; el gate uniforme evita bypass por rol lector.)
    if not _need(handler, store, ctx, "os.write", sub):
        return True
    if not srv._require_csrf(handler, store, ctx, token,
                             resource=f"os:{sub}"):
        return True
    if not _os_body_ok(handler):
        return True

    # Aislamiento: company_id SIEMPRE del ctx (anti-IDOR).
    from webapp.os_api import get_bundle
    bundle = get_bundle(ctx.company_id)
    try:
        data = ejecutar_escenario(bundle, _body_json(handler))
    except (ErrorSimulador, KeyError, ValueError, TypeError) as e:
        return _send(handler, _err(handler, 400, f"escenario inválido: {e}"))
    # SIN mutar_os, SIN auditar: la simulación no persiste nada.
    return _send(handler, _ok(handler, data))
