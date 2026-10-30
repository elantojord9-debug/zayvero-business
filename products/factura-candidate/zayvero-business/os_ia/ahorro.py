"""BLOQUE IA · 6 — Medidor de ahorro.

Diferencia ahorro ESTIMADO vs. CONFIRMADO:
- Estimado: lo que un hallazgo o propuesta dice que se podría ahorrar
  (cálculo con supuestos explícitos, sin afirmar nada).
- Confirmado: lo que los datos REALES del sistema muestran al comparar
  antes/después de aplicar la medida.

REGLA DURA — nunca atribuir ahorros sin evidencia: `medir()` solo
confirma si existen registros fechados en AMBOS períodos; sin datos,
la medida queda "en_seguimiento" con nota de evidencia insuficiente.
Un ahorro no se confirma por estimación ni por declaración.

Fuentes de datos soportadas para la medición (lectura del bundle):
  "gastos:categoria=<cat>"  total de gastos de la categoría por moneda
  "gastos:total"            total de gastos por moneda

Persistencia: registro en memoria por company_id (no duradero; el
respaldo en PG es una decisión pendiente del propietario).
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

from .base import dia, q1, verificar_aislamiento  # noqa: E402

ESTADOS = ("estimada", "en_seguimiento", "confirmada", "descartada")


class ErrorAhorro(Exception):
    """Error de dominio del Medidor de ahorro."""


@dataclass
class Medida:
    id: str
    titulo: str
    descripcion: str = ""
    categoria: str = ""
    monto_estimado: str = "0"        # Decimal str
    moneda: str = "DOP"
    evidencia_estimacion: str = ""   # cómo se calculó el estimado
    estado: str = "estimada"
    periodo_antes: dict = field(default_factory=dict)    # {inicio, fin}
    periodo_despues: dict = field(default_factory=dict)  # {inicio, fin}
    fuente_datos: str = ""
    monto_antes: str | None = None
    monto_despues: str | None = None
    monto_confirmado: str | None = None
    creada_at: str = ""
    creada_por: str = "sistema"
    confirmada_at: str = ""
    nota: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "titulo": self.titulo,
                "descripcion": self.descripcion,
                "categoria": self.categoria,
                "monto_estimado": self.monto_estimado,
                "moneda": self.moneda,
                "evidencia_estimacion": self.evidencia_estimacion,
                "estado": self.estado,
                "periodo_antes": self.periodo_antes,
                "periodo_despues": self.periodo_despues,
                "fuente_datos": self.fuente_datos,
                "monto_antes": self.monto_antes,
                "monto_despues": self.monto_despues,
                "monto_confirmado": self.monto_confirmado,
                "creada_at": self.creada_at,
                "creada_por": self.creada_por,
                "confirmada_at": self.confirmada_at,
                "nota": self.nota}


_LOCK = threading.RLock()
_STORE: dict[str, dict[str, Medida]] = {}


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _store(company_id: str) -> dict[str, Medida]:
    with _LOCK:
        return _STORE.setdefault(company_id, {})


def registrar_estimacion(company_id: str, titulo: str, monto_estimado,
                         moneda: str = "DOP", descripcion: str = "",
                         categoria: str = "",
                         evidencia_estimacion: str = "",
                         creada_por: str = "sistema") -> Medida:
    """Registra un ahorro ESTIMADO (no afirma que ocurrió)."""
    if not titulo:
        raise ErrorAhorro("titulo es obligatorio")
    m = Medida(id=f"aho-{uuid.uuid4().hex[:12]}", titulo=titulo,
               descripcion=descripcion, categoria=categoria,
               monto_estimado=str(_dec(monto_estimado)), moneda=moneda,
               evidencia_estimacion=evidencia_estimacion,
               creada_at=_ahora_iso(), creada_por=creada_por)
    with _LOCK:
        _store(company_id)[m.id] = m
    return m


def programar_medicion(company_id: str, medida_id: str,
                       periodo_antes: dict, periodo_despues: dict,
                       fuente_datos: str) -> Medida:
    """Define los períodos antes/después y la fuente de datos reales."""
    for nombre, p in (("periodo_antes", periodo_antes),
                      ("periodo_despues", periodo_despues)):
        if not isinstance(p, dict) or not dia(p.get("inicio")) or \
                not dia(p.get("fin")):
            raise ErrorAhorro(f"{nombre} requiere inicio/fin YYYY-MM-DD")
    if not fuente_datos.startswith("gastos:"):
        raise ErrorAhorro(f"fuente_datos no soportada: {fuente_datos}")
    with _LOCK:
        m = _store(company_id).get(medida_id)
        if m is None:
            raise ErrorAhorro(f"medida inexistente: {medida_id}")
        if m.estado not in ("estimada", "en_seguimiento"):
            raise ErrorAhorro(
                f"la medida ya está {m.estado}; no se reprograma")
        m.periodo_antes = periodo_antes
        m.periodo_despues = periodo_despues
        m.fuente_datos = fuente_datos
        m.estado = "en_seguimiento"
        return m


def _total_gastos(bundle, inicio: str, fin: str,
                  categoria: str | None) -> tuple[dict[str, Decimal], int]:
    """Totales por moneda + nº de registros fechados en el período."""
    di, df = dia(inicio), dia(fin)
    tot: dict[str, Decimal] = {}
    n = 0
    for g in bundle.gastos.todos():
        d = dia(g.gasto.fecha)
        if d is None or d < di or d > df:
            continue
        if categoria and g.gasto.categoria != categoria:
            continue
        tot[g.gasto.moneda] = tot.get(g.gasto.moneda,
                                      Decimal("0")) + _dec(g.gasto.monto)
        n += 1
    return tot, n


def medir(company_id: str, medida_id: str, bundle) -> Medida:
    """Compara antes/después con datos REALES del sistema.

    - Sin registros fechados en alguno de los períodos: queda
      "en_seguimiento" con nota de evidencia insuficiente (NO confirma).
    - Con datos: ahorro = antes − después; si > 0 se confirma por el
      monto observado; si no, se descarta con nota honesta.
    """
    with _LOCK:
        m = _store(company_id).get(medida_id)
        if m is None:
            raise ErrorAhorro(f"medida inexistente: {medida_id}")
        if m.estado not in ("en_seguimiento", "estimada"):
            raise ErrorAhorro(f"la medida ya está {m.estado}")
        if not m.periodo_antes or not m.periodo_despues \
                or not m.fuente_datos:
            raise ErrorAhorro("la medida no tiene medición programada")
        categoria = None
        if m.fuente_datos.startswith("gastos:categoria="):
            categoria = m.fuente_datos.split("=", 1)[1]
        antes, n_antes = _total_gastos(
            bundle, m.periodo_antes["inicio"], m.periodo_antes["fin"],
            categoria)
        despues, n_despues = _total_gastos(
            bundle, m.periodo_despues["inicio"],
            m.periodo_despues["fin"], categoria)
        if n_antes == 0 or n_despues == 0:
            m.estado = "en_seguimiento"
            m.nota = ("evidencia insuficiente: sin registros fechados en "
                      f"uno de los períodos (antes={n_antes}, "
                      f"después={n_despues}); no se confirma ahorro")
            return m
        mon = m.moneda
        va = antes.get(mon, Decimal("0"))
        vd = despues.get(mon, Decimal("0"))
        m.monto_antes = str(va)
        m.monto_despues = str(vd)
        ahorro = va - vd
        if ahorro > 0:
            m.estado = "confirmada"
            m.monto_confirmado = str(ahorro)
            m.confirmada_at = _ahora_iso()
            m.nota = (f"ahorro confirmado con datos reales: {va} → {vd} "
                      f"{mon} ({n_antes} vs {n_despues} registros)")
        else:
            m.estado = "descartada"
            m.monto_confirmado = "0"
            m.nota = (f"no se observó ahorro: {va} → {vd} {mon}; la "
                      f"medida se descarta sin atribuir ahorros")
        return m


def listar(company_id: str, estado: str | None = None) -> list[Medida]:
    if estado is not None and estado not in ESTADOS:
        raise ErrorAhorro(f"estado inválido: {estado}")
    with _LOCK:
        ms = list(_store(company_id).values())
    if estado:
        ms = [m for m in ms if m.estado == estado]
    return sorted(ms, key=lambda m: m.creada_at)


def resumen(company_id: str) -> dict:
    """Totales estimado vs. confirmado por moneda (solo confirmadas)."""
    estimado: dict[str, Decimal] = {}
    confirmado: dict[str, Decimal] = {}
    n = {"estimada": 0, "en_seguimiento": 0, "confirmada": 0,
         "descartada": 0}
    with _LOCK:
        ms = list(_store(company_id).values())
    for m in ms:
        n[m.estado] = n.get(m.estado, 0) + 1
        estimado[m.moneda] = estimado.get(m.moneda, Decimal("0")) + \
            _dec(m.monto_estimado)
        if m.estado == "confirmada" and m.monto_confirmado:
            confirmado[m.moneda] = confirmado.get(m.moneda,
                                                  Decimal("0")) + \
                _dec(m.monto_confirmado)
    return {"company_id": company_id, "n_medidas": len(ms),
            "por_estado": n,
            "total_estimado": {k: str(v)
                               for k, v in sorted(estimado.items())},
            "total_confirmado": {k: str(v)
                                 for k, v in sorted(confirmado.items())},
            "advertencia": ("el total estimado no es ahorro real: solo "
                            "el confirmado con datos antes/después "
                            "cuenta como ahorro")}


# ---------------------------------------------------------------------
# Contrato de integración: rutas ia/ahorro/*
# ---------------------------------------------------------------------
def _medidas(bundle, ctx, query):
    company_id = verificar_aislamiento(bundle, ctx)
    estado = q1(query, "estado")
    try:
        ms = listar(company_id, estado or None)
    except ErrorAhorro as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"company_id": company_id,
                                 "n_medidas": len(ms),
                                 "medidas": [m.to_dict() for m in ms]}}


def _resumen(bundle, ctx, query):
    company_id = verificar_aislamiento(bundle, ctx)
    return {"ok": True, "data": resumen(company_id)}


def _registrar(bundle, ctx, body):
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    if not body.get("titulo") or body.get("monto_estimado") is None:
        raise ValueError("titulo y monto_estimado son obligatorios")
    try:
        m = registrar_estimacion(
            company_id, body["titulo"], body["monto_estimado"],
            moneda=body.get("moneda", "DOP"),
            descripcion=body.get("descripcion", ""),
            categoria=body.get("categoria", ""),
            evidencia_estimacion=body.get("evidencia_estimacion", ""),
            creada_por=body.get("creada_por", "sistema"))
    except ErrorAhorro as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"medida": m.to_dict()}}


def _programar(bundle, ctx, body):
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    for campo in ("medida_id", "periodo_antes", "periodo_despues",
                  "fuente_datos"):
        if not body.get(campo):
            raise ValueError(f"{campo} es obligatorio")
    try:
        m = programar_medicion(company_id, body["medida_id"],
                               body["periodo_antes"],
                               body["periodo_despues"],
                               body["fuente_datos"])
    except ErrorAhorro as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"medida": m.to_dict()}}


def _medir(bundle, ctx, body):
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    if not body.get("medida_id"):
        raise ValueError("medida_id es obligatorio")
    try:
        m = medir(company_id, body["medida_id"], bundle)
    except ErrorAhorro as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"medida": m.to_dict()}}


def rutas():
    """Contrato ia/ahorro.

    GET  ia/ahorro/medidas     (bundle, ctx, query)
    GET  ia/ahorro/resumen     (bundle, ctx, query)
    POST ia/ahorro/registrar   (bundle, ctx, body)
    POST ia/ahorro/programar   (bundle, ctx, body)
    POST ia/ahorro/medir       (bundle, ctx, body)
    """
    return {"GET ia/ahorro/medidas": _medidas,
            "GET ia/ahorro/resumen": _resumen,
            "POST ia/ahorro/registrar": _registrar,
            "POST ia/ahorro/programar": _programar,
            "POST ia/ahorro/medir": _medir}
