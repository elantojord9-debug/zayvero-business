"""Rotación y Dead Stock — análisis sobre movimientos, sin inventar datos.

- Rotación: tasa diaria de salidas, días de cobertura y clasificación.
  NO pronostica demanda: entrega los insumos que Stock Predictor necesitará
  (tasa_diaria, dias_cobertura, historial de salidas).
- Dead Stock: productos con stock > 0 sin salidas recientes, diferenciando
  3 causas: sin_movimientos / sin_salidas / baja_rotacion.

Todas las fechas en ISO-8601; `hoy` inyectable para pruebas deterministas.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

from oschema.contract import TipoMovimiento
from oschema.models import _dec


def _parse_fecha(f: str) -> datetime:
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


@dataclass
class AnalisisRotacion:
    producto_id: str
    almacen_id: str
    stock_actual: str
    unidades_salida_periodo: str
    tasa_diaria: str
    dias_cobertura: str | None   # None = sin salidas (cobertura infinita)
    clasificacion: str           # alta | media | baja | sin_salidas | sin_movimientos

    def to_dict(self):
        return {
            "producto_id": self.producto_id, "almacen_id": self.almacen_id,
            "stock_actual": self.stock_actual,
            "unidades_salida_periodo": self.unidades_salida_periodo,
            "tasa_diaria": self.tasa_diaria, "dias_cobertura": self.dias_cobertura,
            "clasificacion": self.clasificacion,
        }


def analizar_rotacion(engine, dias: int = 90, hoy: str | None = None
                      ) -> list[AnalisisRotacion]:
    """Analiza rotación por (producto, almacén) en los últimos `dias`."""
    ahora = _parse_fecha(hoy) if hoy else datetime.now(timezone.utc)
    movs = engine.movimientos()
    claves = {(m.producto_id, m.almacen_id) for m in movs}
    out: list[AnalisisRotacion] = []
    for prod, alm in sorted(claves):
        # Ventana: movimientos con fecha dentro de los últimos `dias`
        en_ventana = [m for m in movs
                      if (m.producto_id, m.almacen_id) == (prod, alm)
                      and (ahora - _parse_fecha(m.fecha)).days <= dias]
        salidas = sum(-_dec(m.cantidad) for m in en_ventana
                      if m.tipo == TipoMovimiento.SALIDA)
        stock = engine.stock(prod, alm)
        if not en_ventana:
            clas = "sin_movimientos"
        elif salidas == 0:
            clas = "sin_salidas"
        else:
            tasa = salidas / dias
            cobertura = (stock / tasa) if tasa > 0 else None
            if cobertura is not None and cobertura < 30:
                clas = "alta"
            elif cobertura is not None and cobertura <= 180:
                clas = "media"
            else:
                clas = "baja"
        tasa_diaria = (salidas / dias) if salidas else Decimal("0")
        cobertura = (stock / tasa_diaria) if tasa_diaria > 0 else None
        out.append(AnalisisRotacion(
            producto_id=prod, almacen_id=alm, stock_actual=str(stock),
            unidades_salida_periodo=str(salidas), tasa_diaria=str(tasa_diaria),
            dias_cobertura=str(round(cobertura, 1)) if cobertura is not None else None,
            clasificacion=clas))
    return out


@dataclass
class HallazgoDeadStock:
    producto_id: str
    almacen_id: str
    stock_actual: str
    dias_sin_salida: int | None
    causa: str  # sin_movimientos | sin_salidas | baja_rotacion
    mensaje: str

    def to_dict(self):
        return {
            "producto_id": self.producto_id, "almacen_id": self.almacen_id,
            "stock_actual": self.stock_actual,
            "dias_sin_salida": self.dias_sin_salida, "causa": self.causa,
            "mensaje": self.mensaje,
        }


def detectar_dead_stock(engine, dias_inmovil: int = 90, hoy: str | None = None
                        ) -> list[HallazgoDeadStock]:
    """Productos con stock > 0 sin salidas en los últimos `dias_inmovil` días.

    Causas diferenciadas:
    - sin_movimientos: ningún movimiento en el período (abandono total).
    - sin_salidas: hay entradas/ajustes pero nada sale (sobrestock o sin demanda).
    - baja_rotacion: hay salidas pero la cobertura supera el umbral.
    """
    ahora = _parse_fecha(hoy) if hoy else datetime.now(timezone.utc)
    movs = engine.movimientos()
    claves = {(m.producto_id, m.almacen_id) for m in movs}
    out: list[HallazgoDeadStock] = []
    for prod, alm in sorted(claves):
        stock = engine.stock(prod, alm)
        if stock <= 0:
            continue
        rel = [m for m in movs if (m.producto_id, m.almacen_id) == (prod, alm)]
        en_ventana = [m for m in rel
                      if (ahora - _parse_fecha(m.fecha)).days <= dias_inmovil]
        salidas = [m for m in en_ventana if m.tipo == TipoMovimiento.SALIDA]
        if not en_ventana:
            causa, dias = "sin_movimientos", None
        elif not salidas:
            causa = "sin_salidas"
            dias = min((ahora - _parse_fecha(m.fecha)).days for m in rel)
        else:
            # Hay salidas pero cobertura excesiva
            total_sal = sum(-_dec(m.cantidad) for m in salidas)
            tasa = total_sal / dias_inmovil
            cobertura = (stock / tasa) if tasa > 0 else Decimal("999999")
            if cobertura > dias_inmovil:
                causa = "baja_rotacion"
                dias = (ahora - _parse_fecha(
                    max(salidas, key=lambda m: m.fecha).fecha)).days
            else:
                continue
        out.append(HallazgoDeadStock(
            producto_id=prod, almacen_id=alm, stock_actual=str(stock),
            dias_sin_salida=dias, causa=causa,
            mensaje=f"{prod} en {alm}: stock {stock} inmovilizado ({causa})"))
    return out
