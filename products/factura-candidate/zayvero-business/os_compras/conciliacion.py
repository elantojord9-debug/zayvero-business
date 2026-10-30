"""Conciliación orden → recepción → factura (con pagos).

Por orden (o global) detecta con evidencia:
- Unidades faltantes (ordenado > recibido) y sobrantes.
- Precios distintos a lo pactado (factura vs orden).
- Productos facturados/recibidos no ordenados.
- Facturas duplicadas (mismo proveedor + número).
- Retrasos: última recepción posterior a fecha_entrega_esperada.
- Pagos indebidos: pago sin factura vinculada, pago mayor a lo
  facturado, pago anterior a la primera recepción, pago de factura
  duplicada.

Los pagos se llevan en `RegistroPagos` (memoria por empresa): cada pago
referencia factura_id y/o compra_id con monto, moneda y fecha. La
conciliación cruza: orden (pactado) vs recepciones (recibido) vs
facturas (cobrado) vs pagos (pagado).
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from .ordenes import EstadoOC  # noqa: E402
from .cruce import cruzar  # noqa: E402
from .facturas import RegistroFacturas, FacturaDuplicada  # noqa: E402


def _parse(f: str) -> datetime:
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc)


class ErrorConciliacion(Exception):
    pass


@dataclass
class PagoProveedor:
    pago_id: str
    company_id: str
    proveedor_id: str
    monto: str
    moneda: str = "DOP"
    fecha: str = ""
    factura_id: str = ""        # factura que cancela (puede estar vacío)
    compra_id: str = ""         # orden vinculada (puede estar vacío)
    metodo: str = ""

    def validate(self):
        for f in ("pago_id", "company_id", "proveedor_id"):
            if not getattr(self, f):
                raise ValueError(f"{f} es obligatorio")
        if _dec(self.monto) <= 0:
            raise ValueError("monto debe ser > 0")

    def to_dict(self):
        return {"pago_id": self.pago_id, "company_id": self.company_id,
                "proveedor_id": self.proveedor_id, "monto": str(_dec(self.monto)),
                "moneda": self.moneda, "fecha": self.fecha,
                "factura_id": self.factura_id, "compra_id": self.compra_id,
                "metodo": self.metodo}


class RegistroPagos:
    _instancias: dict[str, "RegistroPagos"] = {}
    _glob_lock = threading.Lock()

    def __init__(self, company_id: str):
        self.company_id = company_id
        self._lock = threading.RLock()
        self._pagos: dict[str, PagoProveedor] = {}

    @classmethod
    def por_empresa(cls, company_id: str) -> "RegistroPagos":
        with cls._glob_lock:
            return cls._instancias.setdefault(company_id, cls(company_id))

    def registrar(self, p: PagoProveedor) -> PagoProveedor:
        with self._lock:
            p.validate()
            if p.company_id != self.company_id:
                raise ErrorConciliacion("pago de otra empresa")
            if p.pago_id in self._pagos:
                raise ErrorConciliacion(f"pago duplicado: {p.pago_id}")
            self._pagos[p.pago_id] = p
            return p

    def listar(self, proveedor_id: str | None = None,
               compra_id: str | None = None) -> list[PagoProveedor]:
        with self._lock:
            return [p for p in self._pagos.values()
                    if (proveedor_id is None
                        or p.proveedor_id == proveedor_id)
                    and (compra_id is None or p.compra_id == compra_id)]


def conciliar_orden(compras, facturas: RegistroFacturas,
                    pagos: RegistroPagos, compra_id: str,
                    hoy: str | None = None) -> dict:
    """Concilia UNA orden: 3 vías + retraso + pagos."""
    orden = compras.get_orden(compra_id)
    if orden is None:
        raise ErrorConciliacion(f"orden inexistente: {compra_id}")
    ahora = _parse(hoy) if hoy else datetime.now(timezone.utc)

    recs = compras.recepciones_de(compra_id)
    recibido: dict[str, Decimal] = {}
    fechas_rec: list[str] = []
    for r in recs:
        if r.fecha:
            fechas_rec.append(r.fecha)
        for l in r.lineas:
            recibido[l.producto_id] = recibido.get(
                l.producto_id, Decimal("0")) + _dec(l.cantidad)

    facs = facturas.listar(compra_id=compra_id)
    # Facturas duplicadas: mismo proveedor+numero con distinto factura_id
    vistos: dict[tuple[str, str], str] = {}
    duplicadas: list[dict] = []
    for f in facs:
        k = (f.proveedor_id, f.numero)
        if k in vistos:
            duplicadas.append({
                "tipo": "factura_duplicada", "severidad": "alta",
                "proveedor_id": f.proveedor_id, "numero": f.numero,
                "factura_ids": [vistos[k], f.factura_id],
                "detalle": f"factura {f.proveedor_id} #{f.numero} "
                           f"registrada 2 veces ({vistos[k]}, "
                           f"{f.factura_id})"})
        else:
            vistos[k] = f.factura_id

    difs = []
    for f in facs:
        for d in cruzar(orden,
                        {"lineas": [l.to_dict() for l in f.lineas]},
                        recibido):
            dd = d.to_dict()
            dd["factura_id"] = f.factura_id
            dd["factura_numero"] = f.numero
            difs.append(dd)

    # Sin facturas vinculadas: el faltante se detecta orden vs recepción.
    if not facs and orden.estado in (EstadoOC.ENVIADA, EstadoOC.PARCIAL):
        for lin in orden.lineas:
            cant_ord, cant_rec = _dec(lin.cantidad), recibido.get(
                lin.producto_id, Decimal("0"))
            if cant_rec < cant_ord:
                difs.append({
                    "tipo": "faltante", "severidad": "media",
                    "producto_id": lin.producto_id,
                    "detalle": f"ordenado {cant_ord}, recibido {cant_rec} "
                               f"(sin factura vinculada aún)",
                    "ordenado": str(cant_ord), "recibido": str(cant_rec),
                    "facturado": ""})

    # Retraso
    retraso = None
    if orden.fecha_entrega_esperada and fechas_rec:
        delta = (_parse(max(fechas_rec)) - _parse(
            orden.fecha_entrega_esperada)).total_seconds() / 86400
        if delta > 0:
            retraso = {"tipo": "retraso_entrega", "severidad": "media",
                       "dias_retraso": round(delta, 1),
                       "esperada": orden.fecha_entrega_esperada,
                       "ultima_recepcion": max(fechas_rec)}

    # Pagos indebidos
    total_facturado = sum((f.total() for f in facs), Decimal("0"))
    pagos_ord = [p for p in pagos.listar(compra_id=compra_id)]
    pagos_fac = [p for p in pagos.listar()
                 if p.factura_id in {f.factura_id for f in facs}]
    vistos_p = {p.pago_id for p in pagos_ord}
    pagos_rel = pagos_ord + [p for p in pagos_fac if p.pago_id not in vistos_p]
    total_pagado = sum((_dec(p.monto) for p in pagos_rel), Decimal("0"))
    pagos_indebidos = []
    for p in pagos_rel:
        if not p.factura_id and not facs:
            pagos_indebidos.append({
                "tipo": "pago_sin_factura", "severidad": "alta",
                "pago_id": p.pago_id, "monto": str(_dec(p.monto)),
                "detalle": f"pago {p.pago_id} por {p.monto} {p.moneda} sin "
                           f"factura registrada para la orden {compra_id}"})
        if p.fecha and fechas_rec and p.fecha < min(fechas_rec):
            pagos_indebidos.append({
                "tipo": "pago_antes_de_recepcion", "severidad": "media",
                "pago_id": p.pago_id, "fecha_pago": p.fecha,
                "primera_recepcion": min(fechas_rec),
                "detalle": f"pago {p.pago_id} el {p.fecha}, antes de la "
                           f"primera recepción ({min(fechas_rec)})"})
    if total_pagado > total_facturado and facs:
        pagos_indebidos.append({
            "tipo": "pago_mayor_a_facturado", "severidad": "alta",
            "total_pagado": str(total_pagado),
            "total_facturado": str(total_facturado),
            "exceso": str(total_pagado - total_facturado),
            "detalle": f"pagado {total_pagado} > facturado {total_facturado}"})

    discrepancias = [d for d in difs] + duplicadas + pagos_indebidos
    if retraso:
        discrepancias.append(retraso)
    severidades = [d["severidad"] for d in discrepancias]
    return {
        "compra_id": compra_id, "proveedor_id": orden.proveedor_id,
        "estado_orden": orden.estado, "moneda": orden.moneda,
        "total_ordenado": str(orden.total()),
        "total_recibido_cantidades": {
            pid: str(c) for pid, c in sorted(recibido.items())},
        "facturas": [f.to_dict() for f in facs],
        "total_facturado": str(total_facturado),
        "pagos": [p.to_dict() for p in pagos_rel],
        "total_pagado": str(total_pagado),
        "saldo_pendiente": str(total_facturado - total_pagado),
        "limpia": not discrepancias,
        "discrepancias": discrepancias,
        "resumen_severidad": {
            s: severidades.count(s) for s in ("alta", "media", "baja")},
    }


def conciliar_todo(compras, facturas: RegistroFacturas,
                   pagos: RegistroPagos, hoy: str | None = None) -> dict:
    """Conciliación global: resumen por orden + duplicadas cruzadas."""
    ordenes = []
    for o in compras.listar_ordenes():
        try:
            r = conciliar_orden(compras, facturas, pagos, o.compra_id, hoy)
        except ErrorConciliacion as e:
            r = {"compra_id": o.compra_id, "error": str(e)}
        ordenes.append({"compra_id": o.compra_id,
                        "limpia": r.get("limpia"),
                        "discrepancias": len(r.get("discrepancias", [])),
                        "resumen_severidad": r.get("resumen_severidad", {})})
    con_problemas = [o for o in ordenes if not o.get("limpia")]
    return {"total_ordenes": len(ordenes),
            "limpias": len(ordenes) - len(con_problemas),
            "con_discrepancias": len(con_problemas),
            "ordenes": ordenes}


# ---- contrato de rutas -------------------------------------------------
def _ctx_company(ctx) -> str:
    c = getattr(ctx, "company_id", "") or ""
    if not c:
        raise ValueError("ctx sin company_id")
    return c


def _check(bundle, ctx):
    c = _ctx_company(ctx)
    if bundle.compras.company_id != c:
        raise ValueError("empresa ajena")
    return (c, RegistroFacturas.por_empresa(c), RegistroPagos.por_empresa(c))


def _fn_conciliacion(bundle, ctx, qob):
    c, facs, pagos = _check(bundle, ctx)
    q = qob or {}
    if q.get("compra_id"):
        r = conciliar_orden(bundle.compras, facs, pagos,
                            str(q["compra_id"]), hoy=q.get("hoy"))
        r["company_id"] = c
        return r
    r = conciliar_todo(bundle.compras, facs, pagos, hoy=q.get("hoy"))
    r["company_id"] = c
    return r


def _fn_pagos(bundle, ctx, qob):
    c, _, pagos = _check(bundle, ctx)
    q = qob or {}
    if isinstance(q, dict) and q.get("_metodo") == "POST":
        p = PagoProveedor(
            pago_id=str(q["pago_id"]), company_id=c,
            proveedor_id=str(q["proveedor_id"]), monto=str(q["monto"]),
            moneda=str(q.get("moneda", "DOP")), fecha=str(q.get("fecha", "")),
            factura_id=str(q.get("factura_id", "")),
            compra_id=str(q.get("compra_id", "")),
            metodo=str(q.get("metodo", "")))
        pagos.registrar(p)
        return {"ok": True, "pago": p.to_dict()}
    return {"company_id": c,
            "pagos": [p.to_dict() for p in pagos.listar(
                proveedor_id=q.get("proveedor_id"),
                compra_id=q.get("compra_id"))]}


def rutas():
    return {
        "compras/conciliacion": _fn_conciliacion,
        "compras/pagos": _fn_pagos,
    }
