"""BLOQUE IA · 4 — Autopilot (modo PROPUESTA solamente).

Genera propuestas de acciones (reordenar, ajustar precio, revisar
proveedor) a partir de los hallazgos de Inspector/Predict/Shield y
del Price Advisor. Cada propuesta registra quién la aprueba o rechaza
y cuándo.

REGLA DURA — el Autopilot NUNCA ejecuta: este módulo no contiene
ningún código que mute el bundle, llame a servicios de escritura
(ventas, compras, inventario, tesorería, gastos), toque la red ni
programe trabajos. La ejecución de una propuesta aprobada la hace un
humano (o el módulo `aprobaciones/`) FUERA de este módulo. Hay una
prueba que verifica por inspección del código fuente que no existe
ruta de ejecución (tests/test_ia_autopilot.py).

Estados: pendiente -> aprobada | rechazada. Terminales: aprobada,
rechazada. La "ejecución" no es un estado del Autopilot: cuando el
humano ejecuta, lo registra en el Centro de Decisiones
(os_ia/decisiones.py).

Persistencia: registro en memoria por company_id (igual que
eventos_memoria del bundle: no duradero; el respaldo en PG es una
decisión pendiente del propietario).
"""
from __future__ import annotations

import os as _os
import sys
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402

from .base import dia, hoy as _hoy_fn, q1, verificar_aislamiento  # noqa: E402

TIPOS = ("reordenar", "ajustar_precio", "revisar_proveedor",
         "revisar_producto")
ESTADOS = ("pendiente", "aprobada", "rechazada")
DECISIONES = ("aprobar", "rechazar")


class ErrorAutopilot(Exception):
    """Error de dominio del Autopilot (propuesta inexistente, doble
    decisión, transición inválida)."""


@dataclass
class Propuesta:
    id: str
    tipo: str                        # uno de TIPOS
    titulo_es: str
    titulo_en: str
    descripcion_es: str
    descripcion_en: str
    payload: dict = field(default_factory=dict)  # parámetros propuestos
    fuente: dict = field(default_factory=dict)   # {"modulo":..,"ref_id":..}
    estado: str = "pendiente"
    creada_at: str = ""
    creada_por: str = "autopilot"
    decidida_por: str = ""
    decidida_at: str = ""
    motivo_decision: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "tipo": self.tipo,
                "titulo_es": self.titulo_es, "titulo_en": self.titulo_en,
                "descripcion_es": self.descripcion_es,
                "descripcion_en": self.descripcion_en,
                "payload": self.payload, "fuente": self.fuente,
                "estado": self.estado, "creada_at": self.creada_at,
                "creada_por": self.creada_por,
                "decidida_por": self.decidida_por,
                "decidida_at": self.decidida_at,
                "motivo_decision": self.motivo_decision}


_LOCK = threading.RLock()
# company_id -> {propuesta_id: Propuesta} (memoria, no duradero)
_STORE: dict[str, dict[str, Propuesta]] = {}


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _store(company_id: str) -> dict[str, Propuesta]:
    with _LOCK:
        return _STORE.setdefault(company_id, {})


def listar(company_id: str, estado: str | None = None) -> list[Propuesta]:
    """Propuestas de UNA empresa (aislamiento por company_id)."""
    if estado is not None and estado not in ESTADOS:
        raise ErrorAutopilot(f"estado inválido: {estado}")
    with _LOCK:
        props = list(_store(company_id).values())
    if estado:
        props = [p for p in props if p.estado == estado]
    return sorted(props, key=lambda p: p.creada_at)


def decidir(company_id: str, propuesta_id: str, decision: str,
            usuario: str, motivo: str = "") -> Propuesta:
    """Aprueba o rechaza una propuesta, registrando quién y cuándo.

    - decision: "aprobar" | "rechazar".
    - Doble decisión -> ErrorAutopilot (la propuesta ya tiene dueño).
    - NO ejecuta nada: solo cambia el estado y audita la decisión.
    """
    if decision not in DECISIONES:
        raise ErrorAutopilot(
            f"decisión inválida: use {', '.join(DECISIONES)}")
    if not usuario:
        raise ErrorAutopilot("usuario es obligatorio")
    with _LOCK:
        store = _store(company_id)
        p = store.get(propuesta_id)
        if p is None:
            raise ErrorAutopilot(
                f"propuesta inexistente: {propuesta_id}")
        if p.estado != "pendiente":
            raise ErrorAutopilot(
                f"la propuesta ya fue {p.estado} por {p.decidida_por} "
                f"el {p.decidida_at}")
        p.estado = "aprobada" if decision == "aprobar" else "rechazada"
        p.decidida_por = usuario
        p.decidida_at = _ahora_iso()
        p.motivo_decision = motivo or ""
        return p


def _guardar(company_id: str, p: Propuesta) -> Propuesta:
    """Guarda si no existe (idempotente por id determinista)."""
    with _LOCK:
        store = _store(company_id)
        if p.id in store:
            return store[p.id]
        store[p.id] = p
        return p


# ---------------------------------------------------------------------
# Generadores (solo lectura del bundle; solo crean propuestas)
# ---------------------------------------------------------------------
def _gen_reordenes(bundle, h) -> list[Propuesta]:
    """Riesgo de faltante (predict) -> propuesta de reorden."""
    from . import predict as _predict
    out = []
    for (prod, alm) in sorted(bundle.inventario.stock_empresa()):
        r = _predict.riesgo_faltante(bundle, prod, alm,
                                     lead_time_dias=7, fecha=h)
        if r.get("estado") != "ok" or not r.get("riesgo"):
            continue
        p = _dec(r["pronostico_diario"])
        stock = _dec(r["stock_actual"])
        sugerida = max(Decimal("0"), p * 30 - stock)
        out.append(Propuesta(
            id=f"prop:reordenar:{prod}:{alm}",
            tipo="reordenar",
            titulo_es=f"Reordenar {prod} en {alm}",
            titulo_en=f"Reorder {prod} at {alm}",
            descripcion_es=(
                f"Predict proyecta quiebre el "
                f"{r['fecha_quiebre_proyectada']} "
                f"(cobertura {r['dias_cobertura']} días). Se propone "
                f"ordenar {sugerida:.0f} uds (≈30 días de demanda "
                f"pronosticada menos stock actual)."),
            descripcion_en=(
                f"Predict projects a stockout on "
                f"{r['fecha_quiebre_proyectada']} "
                f"({r['dias_cobertura']} days of cover). Proposing to "
                f"order {sugerida:.0f} units (≈30 days of forecasted "
                f"demand minus current stock)."),
            payload={"producto_id": prod, "almacen_id": alm,
                     "cantidad_sugerida": f"{sugerida:.2f}",
                     "stock_actual": str(stock),
                     "pronostico_diario": f"{p:.4f}",
                     "fecha_quiebre_proyectada":
                         r["fecha_quiebre_proyectada"],
                     "lead_time_dias": 7},
            fuente={"modulo": "predict", "ref_id": "riesgo_faltante"},
            creada_at=_ahora_iso()))
    return out


def _gen_precios(bundle, h) -> list[Propuesta]:
    """Veredictos subir/bajar del Price Advisor -> propuesta (requiere
    aprobación humana por diseño del sistema)."""
    from price_advisor.engine import AsesorPrecios
    out = []
    try:
        data = AsesorPrecios(bundle).recomendar(
            moneda="DOP", hoy=h.isoformat(), lang="es")
    except Exception:
        return []
    for it in data.get("resultados", []):
        if it.get("estado") != "recomendado":
            continue
        veredicto = it.get("veredicto")
        if veredicto not in ("subir", "bajar"):
            continue
        res = it.get("resultados", {})
        pid, alm = it["producto_id"], it["almacen_id"]
        out.append(Propuesta(
            id=f"prop:ajustar_precio:{pid}:{alm}",
            tipo="ajustar_precio",
            titulo_es=(f"Ajustar precio de {pid} ({veredicto})"),
            titulo_en=f"Adjust price of {pid} ({veredicto})",
            descripcion_es=it.get("explicacion_es", ""),
            descripcion_en=it.get("explicacion_en", ""),
            payload={"producto_id": pid, "almacen_id": alm,
                     "moneda": it.get("moneda"),
                     "precio_recomendado": res.get("precio_recomendado"),
                     "precio_actual_referencia": it.get("insumos", {}).get(
                         "precio_actual_referencia"),
                     "veredicto": veredicto,
                     "nota": ("requiere aprobación humana; cambio >25% "
                              "fluye a aprobaciones")},
            fuente={"modulo": "price_advisor",
                    "ref_id": f"{pid}:{alm}"},
            creada_at=_ahora_iso()))
    return out


def _gen_proveedores(bundle, h) -> list[Propuesta]:
    """Alertas R10 de shield -> propuesta de revisión de proveedor."""
    from . import shield as _shield
    out = []
    for a in _shield.evaluar_extendido(bundle, fecha=h):
        if a.regla != "problemas_proveedor":
            continue
        prov = a.dato.get("proveedor_id", "?")
        sub = a.dato.get("subtipo", "?")
        out.append(Propuesta(
            id=f"prop:revisar_proveedor:{prov}:{sub}",
            tipo="revisar_proveedor",
            titulo_es=f"Revisar proveedor {prov} ({sub})",
            titulo_en=f"Review supplier {prov} ({sub})",
            descripcion_es=a.explicacion_es,
            descripcion_en=a.explicacion_en,
            payload={"proveedor_id": prov, "subtipo": sub,
                     "evidencia": a.dato},
            fuente={"modulo": "shield", "ref_id": a.regla},
            creada_at=_ahora_iso()))
    return out


def generar_propuestas(bundle, fecha=None) -> list[Propuesta]:
    """Genera (o recupera si ya existen) propuestas para la empresa.

    Solo LEE el bundle y crea propuestas en estado pendiente. No
    ejecuta ninguna operación financiera ni externa.
    """
    h = _hoy_fn(fecha)
    nuevas = []
    nuevas += _gen_reordenes(bundle, h)
    nuevas += _gen_precios(bundle, h)
    nuevas += _gen_proveedores(bundle, h)
    return [_guardar(bundle.company_id, p) for p in nuevas]


# ---------------------------------------------------------------------
# Contrato de integración: rutas ia/autopilot/*
# ---------------------------------------------------------------------
def _propuestas(bundle, ctx, query):
    company_id = verificar_aislamiento(bundle, ctx)
    estado = q1(query, "estado")
    if estado and estado not in ESTADOS:
        raise ValueError("estado inválido: use " + ", ".join(ESTADOS))
    props = listar(company_id, estado or None)
    return {"ok": True, "data": {"company_id": company_id,
                                 "n_propuestas": len(props),
                                 "propuestas": [p.to_dict()
                                                for p in props]}}


def _proponer(bundle, ctx, body):
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    props = generar_propuestas(bundle, fecha=body.get("hoy"))
    return {"ok": True, "data": {
        "company_id": company_id, "n_propuestas": len(props),
        "nota": ("modo propuesta: ninguna operación fue ejecutada; "
                  "cada propuesta requiere aprobación humana explícita"),
        "propuestas": [p.to_dict() for p in props]}}


def _decidir(bundle, ctx, body):
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    for campo in ("propuesta_id", "decision", "usuario"):
        if not body.get(campo):
            raise ValueError(f"{campo} es obligatorio")
    try:
        p = decidir(company_id, body["propuesta_id"], body["decision"],
                    body["usuario"], body.get("motivo", ""))
    except ErrorAutopilot as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"propuesta": p.to_dict(),
                                 "nota": ("decisión registrada; la "
                                          "ejecución la realiza un humano "
                                          "fuera del Autopilot")}}


def rutas():
    """Contrato ia/autopilot.

    GET ia/autopilot/propuestas            (bundle, ctx, query)
    POST ia/autopilot/proponer             (bundle, ctx, body)
    POST ia/autopilot/decidir              (bundle, ctx, body)
    """
    return {"GET ia/autopilot/propuestas": _propuestas,
            "POST ia/autopilot/proponer": _proponer,
            "POST ia/autopilot/decidir": _decidir}
