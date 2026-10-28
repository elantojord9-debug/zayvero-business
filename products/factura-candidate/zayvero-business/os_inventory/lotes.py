"""Lotes/seriales, vencimientos, devoluciones y garantías.

Trazabilidad por lote: cada lote sabe su producto, almacén, cantidad
inicial/actual, vencimiento y recepción de origen. El consumo sigue FEFO
(primero vence, primero sale) por defecto o FIFO.

Devoluciones:
- De cliente (DEVOLUCION_IN): reingresa a stock. Impacto en costo: el
  costo reingresado = cantidad × costo promedio vigente (vía puerto
  `FuenteFinanzas`/bundle.costo). La reversión del ingreso en el P&L la
  hace Ventas (cancelación con nota de crédito); aquí se reporta el
  impacto de costo y stock.
- A proveedor (DEVOLUCION_OUT): sale de stock. Impacto: reducción del
  costo del inventario por el costo promedio vigente.

Garantías: registro de garantías por producto/serie/lote con vigencia;
`reclamar` marca la garantía usada y enlaza la devolución generada.

PERSISTENCIA: registro en memoria por empresa (igual que el engine en
modo memoria). Para durabilidad usar el backend PG del OSBundle (issue
#1); el módulo está diseñado sin estado global cruzado entre empresas.
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.contract import TipoMovimiento  # noqa: E402
from oschema.models import _dec  # noqa: E402


def _parse_fecha(f: str) -> datetime:
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc)


def _hoy(hoy: str | None) -> datetime:
    return _parse_fecha(hoy) if hoy else datetime.now(timezone.utc)


class ErrorLotes(Exception):
    pass


class LoteDuplicado(ErrorLotes):
    pass


class LoteInexistente(ErrorLotes):
    pass


class StockLoteInsuficiente(ErrorLotes):
    pass


@dataclass
class Lote:
    lote_id: str
    company_id: str
    producto_id: str
    almacen_id: str
    cantidad_inicial: str
    cantidad_actual: str
    fecha_vencimiento: str = ""      # ISO; "" = no perecedero
    fecha_fabricacion: str = ""
    recepcion_ref: str = ""          # recepción/orden de origen
    estado: str = "activo"           # activo | agotado | vencido_retirado

    def validate(self):
        for f in ("lote_id", "company_id", "producto_id", "almacen_id"):
            if not getattr(self, f):
                raise ValueError(f"{f} es obligatorio")
        if _dec(self.cantidad_inicial) <= 0:
            raise ValueError("cantidad_inicial debe ser > 0")
        if _dec(self.cantidad_actual) < 0:
            raise ValueError("cantidad_actual no puede ser negativa")
        if self.fecha_vencimiento:
            _parse_fecha(self.fecha_vencimiento)

    def dias_para_vencer(self, hoy: str | None = None) -> int | None:
        if not self.fecha_vencimiento:
            return None
        return (_parse_fecha(self.fecha_vencimiento) - _hoy(hoy)).days

    def to_dict(self):
        return {
            "lote_id": self.lote_id, "company_id": self.company_id,
            "producto_id": self.producto_id, "almacen_id": self.almacen_id,
            "cantidad_inicial": str(_dec(self.cantidad_inicial)),
            "cantidad_actual": str(_dec(self.cantidad_actual)),
            "fecha_vencimiento": self.fecha_vencimiento,
            "fecha_fabricacion": self.fecha_fabricacion,
            "recepcion_ref": self.recepcion_ref, "estado": self.estado,
        }


@dataclass
class Garantia:
    garantia_id: str
    company_id: str
    producto_id: str
    serie_o_lote: str
    venta_ref: str
    fecha_inicio: str
    meses_vigencia: int
    estado: str = "activa"            # activa | reclamada | expirada
    reclamo_ref: str = ""

    def validate(self):
        for f in ("garantia_id", "company_id", "producto_id", "venta_ref"):
            if not getattr(self, f):
                raise ValueError(f"{f} es obligatorio")
        _parse_fecha(self.fecha_inicio)
        if self.meses_vigencia <= 0:
            raise ValueError("meses_vigencia debe ser > 0")

    def vence_el(self) -> str:
        ini = _parse_fecha(self.fecha_inicio)
        # Suma de meses calendario aproximada a 30.44 días; documentado.
        from datetime import timedelta
        return (ini + timedelta(days=30.44 * self.meses_vigencia)
                ).strftime("%Y-%m-%dT%H:%M:%SZ")

    def vigente(self, hoy: str | None = None) -> bool:
        return self.estado == "activa" and _hoy(hoy) <= _parse_fecha(
            self.vence_el())

    def to_dict(self):
        return {
            "garantia_id": self.garantia_id, "company_id": self.company_id,
            "producto_id": self.producto_id, "serie_o_lote": self.serie_o_lote,
            "venta_ref": self.venta_ref, "fecha_inicio": self.fecha_inicio,
            "meses_vigencia": self.meses_vigencia, "estado": self.estado,
            "reclamo_ref": self.reclamo_ref, "vence_el": self.vence_el(),
        }


class RegistroLotes:
    """Trazabilidad de lotes y garantías de UNA empresa (memoria)."""

    _instancias: dict[str, "RegistroLotes"] = {}
    _glob_lock = threading.Lock()

    def __init__(self, company_id: str):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        self.company_id = company_id
        self._lock = threading.RLock()
        self._lotes: dict[str, Lote] = {}
        self._garantias: dict[str, Garantia] = {}
        self._devoluciones: list[dict] = []

    @classmethod
    def por_empresa(cls, company_id: str) -> "RegistroLotes":
        with cls._glob_lock:
            return cls._instancias.setdefault(
                company_id, cls(company_id))

    # ---- lotes ---------------------------------------------------------
    def registrar_lote(self, lote: Lote) -> Lote:
        with self._lock:
            lote.validate()
            if lote.company_id != self.company_id:
                raise ErrorLotes("lote de otra empresa")
            if lote.lote_id in self._lotes:
                raise LoteDuplicado(lote.lote_id)
            self._lotes[lote.lote_id] = lote
            return lote

    def ficha_lote(self, lote_id: str) -> Lote | None:
        with self._lock:
            return self._lotes.get(lote_id)

    def lotes_de(self, producto_id: str | None = None,
                 almacen_id: str | None = None) -> list[Lote]:
        with self._lock:
            return [l for l in self._lotes.values()
                    if (producto_id is None or l.producto_id == producto_id)
                    and (almacen_id is None or l.almacen_id == almacen_id)]

    def consumir(self, producto_id: str, almacen_id: str, cantidad: str,
                 politica: str = "FEFO") -> list[dict]:
        """Descuenta stock de lotes (FEFO: vence primero; FIFO: más viejo).

        Devuelve la asignación [{lote_id, cantidad}]. No toca el engine:
        el llamante registra el movimiento de inventario correspondiente.
        """
        if politica not in ("FEFO", "FIFO"):
            raise ValueError("politica debe ser FEFO o FIFO")
        resto = _dec(cantidad)
        if resto <= 0:
            raise ValueError("cantidad debe ser > 0")
        with self._lock:
            cands = [l for l in self.lotes_de(producto_id, almacen_id)
                     if l.estado == "activo" and _dec(l.cantidad_actual) > 0]
            if politica == "FEFO":
                cands.sort(key=lambda l: (l.fecha_vencimiento or "9999",
                                          l.lote_id))
            else:
                cands.sort(key=lambda l: (l.fecha_fabricacion or "9999",
                                          l.lote_id))
            # Atómico: validar disponibilidad total ANTES de mutar.
            total = sum((_dec(l.cantidad_actual) for l in cands),
                        Decimal("0"))
            if total < resto:
                raise StockLoteInsuficiente(
                    f"{producto_id}@{almacen_id}: hay {total} en lotes, "
                    f"piden {cantidad}")
            asign: list[dict] = []
            for l in cands:
                if resto <= 0:
                    break
                disp = _dec(l.cantidad_actual)
                toma = min(disp, resto)
                l.cantidad_actual = str(disp - toma)
                if _dec(l.cantidad_actual) == 0:
                    l.estado = "agotado"
                asign.append({"lote_id": l.lote_id,
                              "cantidad": str(toma)})
                resto -= toma
            return asign

    def alertas_vencimiento(self, dias_alerta: int = 30,
                            hoy: str | None = None) -> list[dict]:
        """Lotes vencidos o próximos a vencer con stock > 0."""
        out = []
        with self._lock:
            for l in self._lotes.values():
                if _dec(l.cantidad_actual) <= 0:
                    continue
                d = l.dias_para_vencer(hoy)
                if d is None:
                    continue
                if d < 0:
                    out.append({"lote_id": l.lote_id,
                                "producto_id": l.producto_id,
                                "almacen_id": l.almacen_id,
                                "cantidad_actual": str(_dec(l.cantidad_actual)),
                                "fecha_vencimiento": l.fecha_vencimiento,
                                "dias_para_vencer": d,
                                "severidad": "critica",
                                "mensaje": f"lote {l.lote_id} VENCIDO hace "
                                           f"{-d} días con "
                                           f"{l.cantidad_actual} uds en stock"})
                elif d <= dias_alerta:
                    out.append({"lote_id": l.lote_id,
                                "producto_id": l.producto_id,
                                "almacen_id": l.almacen_id,
                                "cantidad_actual": str(_dec(l.cantidad_actual)),
                                "fecha_vencimiento": l.fecha_vencimiento,
                                "dias_para_vencer": d,
                                "severidad": "alerta" if d <= 7 else "aviso",
                                "mensaje": f"lote {l.lote_id} vence en {d} "
                                           f"días ({l.cantidad_actual} uds)"})
        out.sort(key=lambda a: a["dias_para_vencer"])
        return out

    def retirar_vencidos(self, hoy: str | None = None) -> list[dict]:
        """Marca vencidos los lotes ya caducados (no los elimina: auditoría).

        Devuelve la lista marcada para que el llamante registre el AJUSTE
        de inventario correspondiente.
        """
        marcados = []
        with self._lock:
            for l in self._lotes.values():
                d = l.dias_para_vencer(hoy)
                if (d is not None and d < 0 and l.estado == "activo"
                        and _dec(l.cantidad_actual) > 0):
                    marcados.append(l.to_dict())
                    l.estado = "vencido_retirado"
        return marcados

    # ---- garantías ------------------------------------------------------
    def registrar_garantia(self, g: Garantia) -> Garantia:
        with self._lock:
            g.validate()
            if g.company_id != self.company_id:
                raise ErrorLotes("garantía de otra empresa")
            if g.garantia_id in self._garantias:
                raise ErrorLotes(f"garantía duplicada: {g.garantia_id}")
            self._garantias[g.garantia_id] = g
            return g

    def garantias(self, solo_vigentes: bool = False,
                  hoy: str | None = None) -> list[Garantia]:
        with self._lock:
            gs = list(self._garantias.values())
            if solo_vigentes:
                gs = [g for g in gs if g.vigente(hoy)]
            return gs

    def reclamar_garantia(self, garantia_id: str, reclamo_ref: str,
                          hoy: str | None = None) -> Garantia:
        with self._lock:
            g = self._garantias.get(garantia_id)
            if g is None:
                raise ErrorLotes(f"garantía inexistente: {garantia_id}")
            if g.estado != "activa":
                raise ErrorLotes(
                    f"garantía {garantia_id} en estado {g.estado}")
            if not g.vigente(hoy):
                g.estado = "expirada"
                raise ErrorLotes(
                    f"garantía {garantia_id} expirada el {g.vence_el()}")
            g.estado = "reclamada"
            g.reclamo_ref = reclamo_ref
            return g

    # ---- devoluciones ----------------------------------------------------
    def registrar_devolucion(self, engine, costo, tipo: str,
                             producto_id: str, almacen_id: str, cantidad: str,
                             motivo: str = "", lote_id: str | None = None,
                             umbral_aprobacion: str = "0",
                             fecha: str | None = None) -> dict:
        """Devolución de cliente (reingreso) o a proveedor (salida).

        Impacto en costo: cantidad × costo promedio vigente (bundle.costo).
        Devuelve el registro con impacto y banderas de aprobación.
        """
        if tipo not in ("cliente", "proveedor"):
            raise ValueError("tipo debe ser cliente|proveedor")
        if not motivo:
            raise ValueError("motivo es obligatorio (trazabilidad)")
        cant = _dec(cantidad)
        if cant <= 0:
            raise ValueError("cantidad debe ser > 0")
        stock_antes = engine.stock(producto_id, almacen_id)
        costo_u = _dec(costo.costo_promedio(producto_id, almacen_id, "DOP")
                       if hasattr(costo, "costo_promedio") else "0")
        # costo_promedio exige moneda; DOP por defecto documentado
        impacto = cant * costo_u
        if tipo == "cliente":
            mov_id = engine.devolucion_cliente(
                producto_id, almacen_id, str(cant), motivo=motivo,
                referencia_id=lote_id or "", fecha=fecha)
            efecto = "reingreso a stock"
        else:
            mov_id = engine.devolucion_proveedor(
                producto_id, almacen_id, str(cant), motivo=motivo,
                referencia_id=lote_id or "", fecha=fecha)
            efecto = "salida de stock (devuelto al proveedor)"
        stock_despues = engine.stock(producto_id, almacen_id)
        reg = {
            "tipo": tipo, "producto_id": producto_id,
            "almacen_id": almacen_id, "cantidad": str(cant),
            "lote_id": lote_id or "", "motivo": motivo,
            "movimiento_id": mov_id, "efecto": efecto,
            "stock_antes": str(stock_antes),
            "stock_despues": str(stock_despues),
            "costo_unitario_vigente": str(costo_u),
            "impacto_costo": str(impacto),
            "requiere_aprobacion": cant >= _dec(umbral_aprobacion)
            and _dec(umbral_aprobacion) > 0,
        }
        with self._lock:
            self._devoluciones.append(reg)
        return reg

    def devoluciones(self) -> list[dict]:
        with self._lock:
            return list(self._devoluciones)


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
    return c, RegistroLotes.por_empresa(c)


def _fn_lotes(bundle, ctx, qob):
    c, reg = _check(bundle, ctx)
    q = qob or {}
    if isinstance(q, dict) and q.get("_metodo") == "POST":
        lote = Lote(lote_id=str(q["lote_id"]), company_id=c,
                    producto_id=str(q["producto_id"]),
                    almacen_id=str(q.get("almacen_id", "principal")),
                    cantidad_inicial=str(q["cantidad"]),
                    cantidad_actual=str(q["cantidad"]),
                    fecha_vencimiento=str(q.get("fecha_vencimiento", "")),
                    fecha_fabricacion=str(q.get("fecha_fabricacion", "")),
                    recepcion_ref=str(q.get("recepcion_ref", "")))
        reg.registrar_lote(lote)
        return {"ok": True, "lote": lote.to_dict()}
    lotes = reg.lotes_de(q.get("producto_id"), q.get("almacen_id"))
    return {"company_id": c, "lotes": [l.to_dict() for l in lotes]}


def _fn_lotes_alertas(bundle, ctx, qob):
    c, reg = _check(bundle, ctx)
    q = qob or {}
    return {"company_id": c,
            "alertas": reg.alertas_vencimiento(
                dias_alerta=int(q.get("dias_alerta", 30)),
                hoy=q.get("hoy"))}


def _fn_devoluciones(bundle, ctx, qob):
    c, reg = _check(bundle, ctx)
    q = qob or {}
    if isinstance(q, dict) and q.get("_metodo") == "POST":
        r = reg.registrar_devolucion(
            bundle.inventario, bundle.costo, str(q["tipo"]),
            str(q["producto_id"]), str(q.get("almacen_id", "principal")),
            str(q["cantidad"]), motivo=str(q.get("motivo", "")),
            lote_id=q.get("lote_id"),
            umbral_aprobacion=str(q.get("umbral_aprobacion", "0")),
            fecha=q.get("fecha"))
        return {"ok": True, "devolucion": r,
                "nota": "si requiere_aprobacion, la capa de ruta debe "
                        "emitir 202 pendiente_aprobacion (aprobaciones)"}
    return {"company_id": c, "devoluciones": reg.devoluciones()}


def _fn_garantias(bundle, ctx, qob):
    c, reg = _check(bundle, ctx)
    q = qob or {}
    if isinstance(q, dict) and q.get("_metodo") == "POST":
        if q.get("accion") == "reclamar":
            g = reg.reclamar_garantia(str(q["garantia_id"]),
                                      str(q.get("reclamo_ref", "")),
                                      hoy=q.get("hoy"))
            return {"ok": True, "garantia": g.to_dict()}
        g = Garantia(garantia_id=str(q["garantia_id"]), company_id=c,
                     producto_id=str(q["producto_id"]),
                     serie_o_lote=str(q.get("serie_o_lote", "")),
                     venta_ref=str(q["venta_ref"]),
                     fecha_inicio=str(q["fecha_inicio"]),
                     meses_vigencia=int(q["meses_vigencia"]))
        reg.registrar_garantia(g)
        return {"ok": True, "garantia": g.to_dict()}
    gs = reg.garantias(solo_vigentes=bool(q.get("solo_vigentes")),
                       hoy=q.get("hoy"))
    return {"company_id": c, "garantias": [g.to_dict() for g in gs]}


def rutas():
    return {
        "inventario/lotes": _fn_lotes,
        "inventario/lotes/alertas": _fn_lotes_alertas,
        "inventario/devoluciones": _fn_devoluciones,
        "inventario/garantias": _fn_garantias,
    }
