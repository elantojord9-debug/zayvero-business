"""BLOQUE IA · 5 — Centro de Decisiones.

Reúne en una vista única los hallazgos de Inspector, Shield y
Advisor (+ propuestas del Autopilot): problema, causa probable,
evidencia, impacto estimado, acción recomendada, responsable
sugerido y estado.

Estados: pendiente -> aprobada | rechazada; aprobada -> ejecutada.
"ejecutada" la registra un humano cuando realiza la acción fuera del
sistema (el Centro no ejecuta nada por sí mismo).

Persistencia: registro en memoria por company_id (no duradero; el
respaldo en PG es una decisión pendiente del propietario).
"""
from __future__ import annotations

import os as _os
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone

sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from .base import hoy as _hoy_fn, q1, verificar_aislamiento  # noqa: E402

FUENTES = ("inspector", "shield", "advisor", "autopilot")
ESTADOS = ("pendiente", "aprobada", "rechazada", "ejecutada")
_TRANSICIONES = {"pendiente": ("aprobada", "rechazada"),
                 "aprobada": ("ejecutada",),
                 "rechazada": (), "ejecutada": ()}


class ErrorDecisiones(Exception):
    """Error de dominio del Centro de Decisiones."""


def _responsable(tipo: str) -> str:
    t = (tipo or "").lower()
    if any(k in t for k in ("stock", "reorden", "quiebre", "reponer",
                            "inventario")):
        return "encargado de compras"
    if any(k in t for k in ("cxc", "cobro", "credito", "cliente")):
        return "cobranzas"
    if "proveedor" in t:
        return "compras"
    if "precio" in t:
        return "gerencia"
    if "devolucion" in t:
        return "operaciones / calidad"
    if "gasto" in t:
        return "administración"
    return "gerencia"


@dataclass
class Decision:
    id: str
    fuente: str                       # inspector | shield | advisor | autopilot
    problema: str
    causa_probable: str
    evidencia: dict = field(default_factory=dict)
    impacto_estimado: dict | None = None
    accion_recomendada: str = ""
    responsable_sugerido: str = "gerencia"
    estado: str = "pendiente"
    creada_at: str = ""
    actualizada_at: str = ""
    historial: list = field(default_factory=list)  # [{at,por,de,a,nota}]

    def to_dict(self) -> dict:
        return {"id": self.id, "fuente": self.fuente,
                "problema": self.problema,
                "causa_probable": self.causa_probable,
                "evidencia": self.evidencia,
                "impacto_estimado": self.impacto_estimado,
                "accion_recomendada": self.accion_recomendada,
                "responsable_sugerido": self.responsable_sugerido,
                "estado": self.estado, "creada_at": self.creada_at,
                "actualizada_at": self.actualizada_at,
                "historial": self.historial}


_LOCK = threading.RLock()
_STORE: dict[str, dict[str, Decision]] = {}


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _store(company_id: str) -> dict[str, Decision]:
    with _LOCK:
        return _STORE.setdefault(company_id, {})


def listar(company_id: str, estado: str | None = None,
           fuente: str | None = None) -> list[Decision]:
    if estado is not None and estado not in ESTADOS:
        raise ErrorDecisiones(f"estado inválido: {estado}")
    if fuente is not None and fuente not in FUENTES:
        raise ErrorDecisiones(f"fuente inválida: {fuente}")
    with _LOCK:
        ds = list(_store(company_id).values())
    if estado:
        ds = [d for d in ds if d.estado == estado]
    if fuente:
        ds = [d for d in ds if d.fuente == fuente]
    peso = {"pendiente": 0, "aprobada": 1, "rechazada": 2, "ejecutada": 3}
    return sorted(ds, key=lambda d: (peso.get(d.estado, 9), d.id))


def cambiar_estado(company_id: str, decision_id: str, estado: str,
                    usuario: str, nota: str = "") -> Decision:
    """Cambia el estado registrando quién, cuándo y la transición."""
    if estado not in ESTADOS:
        raise ErrorDecisiones(f"estado inválido: {estado}")
    if not usuario:
        raise ErrorDecisiones("usuario es obligatorio")
    with _LOCK:
        store = _store(company_id)
        d = store.get(decision_id)
        if d is None:
            raise ErrorDecisiones(f"decisión inexistente: {decision_id}")
        if estado not in _TRANSICIONES[d.estado]:
            raise ErrorDecisiones(
                f"transición inválida: {d.estado} -> {estado}")
        anterior = d.estado
        d.estado = estado
        d.actualizada_at = _ahora_iso()
        d.historial.append({"at": d.actualizada_at, "por": usuario,
                            "de": anterior, "a": estado, "nota": nota})
        return d


def _nueva(company_id: str, id_: str, fuente: str, problema: str,
           causa: str, evidencia: dict, impacto: dict | None,
           accion: str, tipo_resp: str) -> None:
    with _LOCK:
        store = _store(company_id)
        if id_ in store:
            return  # idempotente: conserva el estado ya decidido
        ahora = _ahora_iso()
        store[id_] = Decision(
            id=id_, fuente=fuente, problema=problema,
            causa_probable=causa, evidencia=evidencia,
            impacto_estimado=impacto, accion_recomendada=accion,
            responsable_sugerido=_responsable(tipo_resp),
            creada_at=ahora, actualizada_at=ahora,
            historial=[{"at": ahora, "por": "sistema", "de": "-",
                        "a": "pendiente", "nota": "creada por consolidación"}])


def consolidar(bundle, fecha=None, eventos=None) -> list[Decision]:
    """Agrega Inspector + Shield + Advisor + Autopilot en decisiones.

    Solo lee el bundle y crea decisiones en estado pendiente; las ya
    existentes conservan su estado (idempotente).
    """
    from . import inspector as _inspector
    from . import shield as _shield
    from . import advisor as _advisor
    from . import autopilot as _autopilot

    h = _hoy_fn(fecha)
    cid = bundle.company_id

    for x in _inspector.inspeccionar(bundle, fecha=h):
        if x.suficiencia != "ok" or x.severidad == "info":
            continue
        _nueva(cid, f"dec:inspector:{x.id}", "inspector",
               problema=x.titulo_es,
               causa="detección automática del Inspector (ver evidencia)",
               evidencia=x.evidencia, impacto=x.impacto_estimado,
               accion=("Revisar la evidencia y definir acción correctiva; "
                       "si aplica, generar propuesta en Autopilot."),
               tipo_resp=x.tipo)

    for a in _shield.evaluar_todo(bundle, fecha=h, eventos=eventos):
        if a.severidad not in ("alerta", "critico"):
            continue
        _nueva(cid, f"dec:shield:{a.regla}:{a.fecha}:"
                    f"{a.dato.get('producto_id') or a.dato.get('venta_id') or a.dato.get('proveedor_id') or a.dato.get('cliente_id') or a.dato.get('cuenta_id') or '?'}",
               "shield", problema=a.titulo_es,
               causa=a.explicacion_es, evidencia=a.dato, impacto=None,
               accion=a.accion_es, tipo_resp=a.regla)

    for r in _advisor.recomendar(bundle, fecha=h).get("recomendaciones", []):
        if r.get("prioridad") not in ("alta", "media"):
            continue
        if r.get("estado") != "pendiente":
            continue
        _nueva(cid, f"dec:advisor:{r['id']}", "advisor",
               problema=r["titulo_es"],
               causa=r.get("causa_probable_es", ""),
               evidencia=r.get("evidencia", {}),
               impacto=r.get("impacto_posible"),
               accion=r.get("accion_recomendada_es", ""),
               tipo_resp=r.get("tipo", ""))

    for p in _autopilot.generar_propuestas(bundle, fecha=h):
        if p.estado != "pendiente":
            continue
        _nueva(cid, f"dec:autopilot:{p.id}", "autopilot",
               problema=p.titulo_es, causa=p.descripcion_es,
               evidencia={"propuesta_id": p.id, "payload": p.payload},
               impacto=None, accion=("Aprobar o rechazar la propuesta en "
                                     "ia/autopilot/decidir."),
               tipo_resp=p.tipo)

    return listar(cid)


# ---------------------------------------------------------------------
# Contrato de integración: rutas ia/decisiones/*
# ---------------------------------------------------------------------
def _decisiones(bundle, ctx, query):
    company_id = verificar_aislamiento(bundle, ctx)
    estado = q1(query, "estado")
    fuente = q1(query, "fuente")
    try:
        ds = listar(company_id, estado or None, fuente or None)
    except ErrorDecisiones as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"company_id": company_id,
                                 "n_decisiones": len(ds),
                                 "decisiones": [d.to_dict() for d in ds]}}


def _consolidar(bundle, ctx, body):
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    ds = consolidar(bundle, fecha=body.get("hoy"))
    return {"ok": True, "data": {"company_id": company_id,
                                 "n_decisiones": len(ds),
                                 "decisiones": [d.to_dict() for d in ds]}}


def _estado(bundle, ctx, body):
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    for campo in ("decision_id", "estado", "usuario"):
        if not body.get(campo):
            raise ValueError(f"{campo} es obligatorio")
    try:
        d = cambiar_estado(company_id, body["decision_id"],
                           body["estado"], body["usuario"],
                           body.get("nota", ""))
    except ErrorDecisiones as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"decision": d.to_dict()}}


def rutas():
    """Contrato ia/decisiones.

    GET  ia/decisiones/lista        (bundle, ctx, query)
    POST ia/decisiones/consolidar   (bundle, ctx, body)
    POST ia/decisiones/estado       (bundle, ctx, body)
    """
    return {"GET ia/decisiones/lista": _decisiones,
            "POST ia/decisiones/consolidar": _consolidar,
            "POST ia/decisiones/estado": _estado}
