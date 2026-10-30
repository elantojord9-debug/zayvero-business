"""Integridad de inventario: sin negativos silenciosos, ajustes y
transferencias con trazabilidad.

Reglas:
1. NINGUNA salida resta en silencio sin stock: `salida_con_guardia`
   intenta la salida; si el engine la bloquea (StockNegativo) NO se
   registra el movimiento y se genera una ALERTA + un EVENTO en el
   registro de integridad (no se pierde el intento).
2. Ajustes con motivo obligatorio: `ajuste_auditado` exige motivo no
   vacío y deja stock antes/después.
3. Transferencias atómicas y trazadas: `transferencia_auditada` usa
   `engine.transferir` (OUT+IN con misma referencia_id) y registra
   ambas patas.
4. `verificar_integridad`: barrido que detecta stocks negativos
   (posibles si permitir_negativo=True), movimientos huérfanos y
   transferencias incompletas (OUT sin IN con misma referencia).

El registro de integridad es por empresa, en memoria (igual que el
engine en modo memoria). La capa de ruta (webapp) debe replicar estos
eventos a os_eventos con `auditar(company_id, "ajuste", ...)` /
`auditar(company_id, "devolucion", ...)` dentro de `mutar_os` para
durabilidad en PG; aquí el registro es el respaldo en memoria y la
fuente de lectura de la ruta `inventario/integridad`.
"""
from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from os_inventory.engine import StockNegativo  # noqa: E402
from oschema.contract import TipoMovimiento  # noqa: E402
from oschema.models import _dec  # noqa: E402


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class RegistroIntegridad:
    """Alertas y eventos de integridad de UNA empresa (memoria)."""

    _instancias: dict[str, "RegistroIntegridad"] = {}
    _glob_lock = threading.Lock()

    def __init__(self, company_id: str):
        self.company_id = company_id
        self._lock = threading.RLock()
        self._eventos: list[dict] = []

    @classmethod
    def por_empresa(cls, company_id: str) -> "RegistroIntegridad":
        with cls._glob_lock:
            return cls._instancias.setdefault(company_id, cls(company_id))

    def _agregar(self, ev: dict) -> dict:
        ev = dict(ev)
        ev.setdefault("fecha", _ahora_iso())
        ev.setdefault("company_id", self.company_id)
        with self._lock:
            self._eventos.append(ev)
            del self._eventos[:-500]  # acotado, igual que eventos_memoria
        return ev

    def alerta(self, tipo: str, detalle: dict) -> dict:
        return self._agregar({"clase": "alerta", "tipo": tipo,
                              "detalle": detalle})

    def evento(self, tipo: str, detalle: dict) -> dict:
        return self._agregar({"clase": "evento", "tipo": tipo,
                              "detalle": detalle})

    def listar(self, clase: str | None = None,
               tipo: str | None = None) -> list[dict]:
        with self._lock:
            return [e for e in self._eventos
                    if (clase is None or e["clase"] == clase)
                    and (tipo is None or e["tipo"] == tipo)]


def salida_con_guardia(engine, registro: RegistroIntegridad,
                       producto_id: str, almacen_id: str, cantidad: str,
                       actor: str = "", motivo: str = "",
                       **kw) -> dict:
    """Salida que NUNCA resta en silencio.

    Éxito: registra el movimiento y deja evento con stock antes/después.
    Sin stock: NO registra nada, genera alerta `salida_bloqueada` con la
    evidencia (stock actual, cantidad intentada) y devuelve ok=False.
    """
    stock_antes = engine.stock(producto_id, almacen_id)
    try:
        mov_id = engine.salida(producto_id, almacen_id, cantidad,
                               motivo=motivo or f"salida {actor}".strip(),
                               **kw)
    except StockNegativo as e:
        alerta = registro.alerta("salida_bloqueada", {
            "producto_id": producto_id, "almacen_id": almacen_id,
            "cantidad_intentada": str(_dec(cantidad)),
            "stock_actual": str(stock_antes),
            "actor": actor, "motivo": motivo,
            "causa": str(e),
            "accion_sugerida": "registrar entrada, ajustar con motivo, o "
                               "crear orden de compra (ver compras/asesor)",
        })
        return {"ok": False, "error": "stock_insuficiente",
                "detalle": str(e), "alerta": alerta}
    stock_despues = engine.stock(producto_id, almacen_id)
    ev = registro.evento("salida", {
        "producto_id": producto_id, "almacen_id": almacen_id,
        "cantidad": str(_dec(cantidad)), "movimiento_id": mov_id,
        "actor": actor, "motivo": motivo,
        "stock_antes": str(stock_antes), "stock_despues": str(stock_despues),
    })
    return {"ok": True, "movimiento_id": mov_id, "evento": ev}


def ajuste_auditado(engine, registro: RegistroIntegridad, producto_id: str,
                    almacen_id: str, cantidad: str, motivo: str,
                    actor: str = "", **kw) -> dict:
    """Ajuste con motivo obligatorio y stock antes/después."""
    if not motivo or not motivo.strip():
        raise ValueError("el ajuste exige motivo (trazabilidad)")
    stock_antes = engine.stock(producto_id, almacen_id)
    mov_id = engine.ajuste(producto_id, almacen_id, cantidad,
                           motivo=motivo, **kw)
    stock_despues = engine.stock(producto_id, almacen_id)
    ev = registro.evento("ajuste", {
        "producto_id": producto_id, "almacen_id": almacen_id,
        "cantidad": str(_dec(cantidad)), "movimiento_id": mov_id,
        "actor": actor, "motivo": motivo,
        "stock_antes": str(stock_antes), "stock_despues": str(stock_despues),
    })
    return {"ok": True, "movimiento_id": mov_id, "evento": ev}


def transferencia_auditada(engine, registro: RegistroIntegridad,
                           producto_id: str, origen_id: str, destino_id: str,
                           cantidad: str, motivo: str = "",
                           actor: str = "", **kw) -> dict:
    """Transferencia atómica (OUT+IN, misma referencia) con trazabilidad."""
    s_antes_o = engine.stock(producto_id, origen_id)
    s_antes_d = engine.stock(producto_id, destino_id)
    out_id, in_id = engine.transferir(
        producto_id, origen_id, destino_id, cantidad,
        motivo=motivo or "transferencia entre almacenes", **kw)
    ref = engine.get(out_id).referencia_id
    ev = registro.evento("transferencia", {
        "producto_id": producto_id, "origen_id": origen_id,
        "destino_id": destino_id, "cantidad": str(_dec(cantidad)),
        "movimiento_out": out_id, "movimiento_in": in_id,
        "referencia_id": ref, "actor": actor, "motivo": motivo,
        "stock_origen_antes": str(s_antes_o),
        "stock_origen_despues": str(engine.stock(producto_id, origen_id)),
        "stock_destino_antes": str(s_antes_d),
        "stock_destino_despues": str(engine.stock(producto_id, destino_id)),
    })
    return {"ok": True, "movimiento_out": out_id, "movimiento_in": in_id,
            "referencia_id": ref, "evento": ev}


def verificar_integridad(engine, compras=None) -> list[dict]:
    """Barrido de integridad. Devuelve hallazgos (vacío = íntegro)."""
    hallazgos: list[dict] = []
    # 1. Stocks negativos (solo posibles con permitir_negativo=True).
    for (prod, alm), stock in sorted(engine.stock_empresa().items()):
        if stock < 0:
            hallazgos.append({
                "tipo": "stock_negativo", "severidad": "alta",
                "producto_id": prod, "almacen_id": alm,
                "stock": str(stock),
                "detalle": f"{prod}@{alm} en {stock}: negativo; revisar "
                           "ajustes y salidas sin guardia",
            })
    # 2. Transferencias incompletas: OUT sin IN con misma referencia.
    movs = engine.movimientos()
    refs: dict[str, dict[str, int]] = {}
    for m in movs:
        if m.tipo in (TipoMovimiento.TRANSFERENCIA_OUT,
                      TipoMovimiento.TRANSFERENCIA_IN) and m.referencia_id:
            r = refs.setdefault(m.referencia_id, {"out": 0, "in": 0})
            if m.tipo == TipoMovimiento.TRANSFERENCIA_OUT:
                r["out"] += 1
            else:
                r["in"] += 1
    for ref, c in sorted(refs.items()):
        if c["out"] != c["in"]:
            hallazgos.append({
                "tipo": "transferencia_incompleta", "severidad": "alta",
                "referencia_id": ref,
                "detalle": f"transferencia {ref}: {c['out']} OUT vs "
                           f"{c['in']} IN",
            })
    # 3. Movimientos con referencia a compra inexistente (si hay compras).
    if compras is not None:
        for m in movs:
            if m.tipo == TipoMovimiento.ENTRADA and m.referencia_id:
                if compras.get_orden(m.referencia_id) is None:
                    hallazgos.append({
                        "tipo": "entrada_huerfana", "severidad": "media",
                        "movimiento_id": m.movimiento_id,
                        "producto_id": m.producto_id,
                        "detalle": f"entrada {m.movimiento_id} referencia a "
                                   f"compra inexistente {m.referencia_id}",
                    })
    return hallazgos


def trazar_referencia(engine, referencia_id: str) -> list[dict]:
    """Todos los movimientos con una referencia (trazabilidad)."""
    return [m.to_dict() for m in engine.movimientos()
            if m.referencia_id == referencia_id]


# ---- contrato de rutas -------------------------------------------------
def _ctx_company(ctx) -> str:
    c = getattr(ctx, "company_id", "") or ""
    if not c:
        raise ValueError("ctx sin company_id")
    return c


def _check(bundle, ctx):
    c = _ctx_company(ctx)
    if bundle.inventario.company_id != c:
        raise ValueError("empresa ajena")
    return c, RegistroIntegridad.por_empresa(c)


def _fn_integridad(bundle, ctx, qob):
    c, reg = _check(bundle, ctx)
    q = qob or {}
    hallazgos = verificar_integridad(bundle.inventario, bundle.compras)
    return {"company_id": c, "integro": not hallazgos,
            "hallazgos": hallazgos,
            "alertas": reg.listar(clase="alerta", tipo=q.get("tipo")),
            "eventos": reg.listar(clase="evento", tipo=q.get("tipo")),
            "nota_auditoria": "la capa de ruta debe replicar estos eventos "
                              "a os_eventos con auditar(company_id, tipo, "
                              "actor, ref_id, detalle) dentro de mutar_os"}


def _fn_salida(bundle, ctx, qob):
    c, reg = _check(bundle, ctx)
    q = dict(qob or {})
    return salida_con_guardia(
        bundle.inventario, reg, str(q["producto_id"]),
        str(q.get("almacen_id", "principal")), str(q["cantidad"]),
        actor=str(getattr(ctx, "user_id", "") or q.get("actor", "")),
        motivo=str(q.get("motivo", "")), fecha=q.get("fecha"))


def _fn_ajuste(bundle, ctx, qob):
    c, reg = _check(bundle, ctx)
    q = dict(qob or {})
    return ajuste_auditado(
        bundle.inventario, reg, str(q["producto_id"]),
        str(q.get("almacen_id", "principal")), str(q["cantidad"]),
        motivo=str(q.get("motivo", "")),
        actor=str(getattr(ctx, "user_id", "") or q.get("actor", "")),
        fecha=q.get("fecha"))


def _fn_transferencia(bundle, ctx, qob):
    c, reg = _check(bundle, ctx)
    q = dict(qob or {})
    return transferencia_auditada(
        bundle.inventario, reg, str(q["producto_id"]),
        str(q["origen_id"]), str(q["destino_id"]), str(q["cantidad"]),
        motivo=str(q.get("motivo", "")),
        actor=str(getattr(ctx, "user_id", "") or q.get("actor", "")),
        fecha=q.get("fecha"))


def _fn_movimientos(bundle, ctx, qob):
    c, _ = _check(bundle, ctx)
    q = qob or {}
    if q.get("referencia_id"):
        movs = trazar_referencia(bundle.inventario, str(q["referencia_id"]))
        return {"company_id": c, "referencia_id": str(q["referencia_id"]),
                "movimientos": movs}
    movs = bundle.inventario.movimientos(
        producto_id=q.get("producto_id"), almacen_id=q.get("almacen_id"))
    if q.get("tipo"):
        movs = [m for m in movs if m.tipo == q["tipo"]]
    if q.get("desde"):
        movs = [m for m in movs if m.fecha >= str(q["desde"])]
    if q.get("hasta"):
        movs = [m for m in movs if m.fecha <= str(q["hasta"])]
    return {"company_id": c, "total": len(movs),
            "movimientos": [m.to_dict() for m in sorted(
                movs, key=lambda m: m.fecha or "")]}


def rutas():
    return {
        "inventario/integridad": _fn_integridad,
        "inventario/integridad/salida": _fn_salida,
        "inventario/integridad/ajuste": _fn_ajuste,
        "inventario/integridad/transferencia": _fn_transferencia,
        "inventario/movimientos": _fn_movimientos,
    }
