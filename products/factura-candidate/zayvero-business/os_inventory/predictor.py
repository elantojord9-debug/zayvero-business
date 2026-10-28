"""Stock Predictor — pronóstico de demanda y cobertura por (producto, almacén).

REGLA DURA (roadmap): sin histórico suficiente NO se pronostica. Si los datos
no alcanzan el umbral, el pronóstico sale con
`suficiencia="datos_insuficientes"` y `riesgo="desconocido"`: nunca se
inventa una tasa ni un punto de reposición.

Insumos (todo real, de `os_inventory.engine`):
- SALIDAs (ventas/consumo) dentro de la ventana -> tasa diaria de consumo.
- Stock actual derivado de movimientos.

Cálculos (explicables, verificables a mano):
- tasa_diaria = unidades_salidas_en_ventana / dias_ventana
- dias_cobertura = stock_actual / tasa_diaria (None si tasa = 0)
- fecha_agotamiento_estimada = hoy + dias_cobertura
- stock_seguridad = tasa_diaria * dias_seguridad
- punto_reposicion = tasa_diaria * (lead_time_dias + dias_seguridad)
- riesgo: critico si cobertura < lead_time; alto si < lead_time+seguridad;
  medio si < 2*(lead_time+seguridad); bajo en otro caso.

Suficiencia: dias_observados = días de la ventana con al menos una SALIDA.
Si dias_observados < min_dias -> "datos_insuficientes".
Si no hay salidas en la ventana -> "sin_demanda" (cobertura infinita, no hay
qué pronosticar; punto de reposición None).
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.contract import TipoMovimiento  # noqa: E402
from oschema.models import _dec  # noqa: E402


def _parse_fecha(f: str) -> datetime:
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc)


@dataclass
class Pronostico:
    producto_id: str
    almacen_id: str
    stock_actual: str
    dias_ventana: int
    dias_observados: int
    unidades_salida_ventana: str
    tasa_diaria: str
    dias_cobertura: str | None
    fecha_agotamiento_estimada: str | None
    punto_reposicion: str | None
    stock_seguridad: str | None
    lead_time_dias: int
    lead_time_origen: str        # "proveedor" | "parametro" | "defecto"
    riesgo: str                  # critico|alto|medio|bajo|agotado|sin_demanda|desconocido
    suficiencia: str             # suficiente|datos_insuficientes|sin_demanda
    supuestos: list = field(default_factory=list)

    def to_dict(self):
        return {
            "producto_id": self.producto_id, "almacen_id": self.almacen_id,
            "stock_actual": self.stock_actual, "dias_ventana": self.dias_ventana,
            "dias_observados": self.dias_observados,
            "unidades_salida_ventana": self.unidades_salida_ventana,
            "tasa_diaria": self.tasa_diaria,
            "dias_cobertura": self.dias_cobertura,
            "fecha_agotamiento_estimada": self.fecha_agotamiento_estimada,
            "punto_reposicion": self.punto_reposicion,
            "stock_seguridad": self.stock_seguridad,
            "lead_time_dias": self.lead_time_dias,
            "lead_time_origen": self.lead_time_origen,
            "riesgo": self.riesgo, "suficiencia": self.suficiencia,
            "supuestos": self.supuestos,
        }


def predecir(engine, dias: int = 90, min_dias: int = 30,
             lead_time_dias: int = 7, dias_seguridad: int = 7,
             lead_time_por_producto: dict | None = None,
             hoy: str | None = None, producto_id: str | None = None,
             almacen_id: str | None = None) -> list[Pronostico]:
    """Pronostica por (producto, almacén). Ver docstring del módulo."""
    if dias <= 0:
        raise ValueError("dias debe ser > 0")
    if min_dias <= 0:
        raise ValueError("min_dias debe ser > 0")
    ahora = _parse_fecha(hoy) if hoy else datetime.now(timezone.utc)
    lt_prod = lead_time_por_producto or {}

    movs = engine.movimientos(producto_id=producto_id, almacen_id=almacen_id)
    claves = sorted({(m.producto_id, m.almacen_id) for m in movs})

    out: list[Pronostico] = []
    for prod, alm in claves:
        en_ventana = [
            m for m in movs
            if (m.producto_id, m.almacen_id) == (prod, alm)
            and (ahora - _parse_fecha(m.fecha)).days <= dias
            and (ahora - _parse_fecha(m.fecha)).days >= 0
        ]
        salidas = [m for m in en_ventana if m.tipo == TipoMovimiento.SALIDA]
        unidades = sum(-_dec(m.cantidad) for m in salidas)
        dias_obs = len({_parse_fecha(m.fecha).date() for m in salidas})
        stock = engine.stock(prod, alm)

        supuestos = [
            f"ventana={dias}d", f"min_dias={min_dias}",
            f"dias_seguridad={dias_seguridad}",
        ]
        if prod in lt_prod:
            lt, lt_origen = int(lt_prod[prod]), "proveedor"
            supuestos.append(f"lead_time={lt}d (proveedor, histórico real)")
        else:
            lt, lt_origen = int(lead_time_dias), "defecto"
            supuestos.append(
                f"lead_time={lt}d por defecto (configurable; sin histórico "
                "de proveedor para este producto)")

        if unidades == 0:
            # Sin salidas en la ventana: no hay demanda que pronosticar.
            out.append(Pronostico(
                producto_id=prod, almacen_id=alm, stock_actual=str(stock),
                dias_ventana=dias, dias_observados=dias_obs,
                unidades_salida_ventana=str(unidades), tasa_diaria="0",
                dias_cobertura=None, fecha_agotamiento_estimada=None,
                punto_reposicion=None, stock_seguridad=None,
                lead_time_dias=lt, lead_time_origen=lt_origen,
                riesgo="agotado" if stock <= 0 else "sin_demanda",
                suficiencia="sin_demanda",
                supuestos=supuestos + [
                    "sin salidas en la ventana: cobertura infinita, "
                    "no se calcula punto de reposición"]))
            continue

        if dias_obs < min_dias:
            # Histórico insuficiente: NO se pronostica, se declara.
            out.append(Pronostico(
                producto_id=prod, almacen_id=alm, stock_actual=str(stock),
                dias_ventana=dias, dias_observados=dias_obs,
                unidades_salida_ventana=str(unidades), tasa_diaria="0",
                dias_cobertura=None, fecha_agotamiento_estimada=None,
                punto_reposicion=None, stock_seguridad=None,
                lead_time_dias=lt, lead_time_origen=lt_origen,
                riesgo="desconocido", suficiencia="datos_insuficientes",
                supuestos=supuestos + [
                    f"datos insuficientes: {dias_obs} días con salidas < "
                    f"mínimo {min_dias}; no se inventa pronóstico"]))
            continue

        tasa = unidades / dias
        cobertura = stock / tasa if tasa > 0 else None
        dias_cob = round(float(cobertura), 1) if cobertura is not None else None
        if dias_cob is not None and cobertura is not None and cobertura >= 0:
            from datetime import timedelta
            agot = (ahora + timedelta(days=float(cobertura))).strftime(
                "%Y-%m-%d")
        else:
            agot = None
        seg = tasa * dias_seguridad
        pr = tasa * (lt + dias_seguridad)

        if cobertura is None or cobertura < 0:
            riesgo = "agotado"
        elif cobertura < lt:
            riesgo = "critico"
        elif cobertura < lt + dias_seguridad:
            riesgo = "alto"
        elif cobertura < 2 * (lt + dias_seguridad):
            riesgo = "medio"
        else:
            riesgo = "bajo"

        out.append(Pronostico(
            producto_id=prod, almacen_id=alm, stock_actual=str(stock),
            dias_ventana=dias, dias_observados=dias_obs,
            unidades_salida_ventana=str(unidades),
            tasa_diaria=str(round(tasa, 4)),
            dias_cobertura=str(dias_cob) if dias_cob is not None else None,
            fecha_agotamiento_estimada=agot,
            punto_reposicion=str(math.ceil(float(pr))),
            stock_seguridad=str(math.ceil(float(seg))),
            lead_time_dias=lt, lead_time_origen=lt_origen,
            riesgo=riesgo, suficiencia="suficiente", supuestos=supuestos))
    return out


# ---- contrato de rutas -------------------------------------------------
def _ctx_company(ctx) -> str:
    c = getattr(ctx, "company_id", "") or ""
    if not c:
        raise ValueError("ctx sin company_id")
    return c


def _fn_predictor(bundle, ctx, qob):
    company = _ctx_company(ctx)
    if bundle.inventario.company_id != company:
        raise ValueError("empresa ajena")
    q = qob or {}
    prs = predecir(
        bundle.inventario,
        dias=int(q.get("dias", 90)),
        min_dias=int(q.get("min_dias", 30)),
        lead_time_dias=int(q.get("lead_time_dias", 7)),
        dias_seguridad=int(q.get("dias_seguridad", 7)),
        hoy=q.get("hoy"),
        producto_id=q.get("producto_id"),
        almacen_id=q.get("almacen_id"),
    )
    return {"company_id": company,
            "pronosticos": [p.to_dict() for p in prs]}


def rutas():
    """Contrato: prefijo `inventario/`, fns (bundle, ctx, qob) -> dict."""
    return {"inventario/predictor": _fn_predictor}
