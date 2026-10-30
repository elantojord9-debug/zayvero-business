"""BLOQUE E — Garantías (2026-10-13). Núcleo del registro.

AUDITORÍA PREVIA (ver docs/bloque-garantias.md §0):
- os_inventory/lotes.py tiene una Garantia FUNCIONAL (no es solo texto):
  registro con vigencia y reclamar() que marca usada/enlaza devolución,
  con ruta inventario/garantias. Es primitiva: sin términos, sin ciclo
  de reclamo, sin costo, sin margen, sin alertas.
- os_crm/devoluciones.py maneja devoluciones de clientes con impacto en
  P&L (nota de crédito): la resolución "devolucion" de un reclamo de
  garantía puede delegar en él; aquí NO se duplica.
- os_finanzas/profit.py NO expone hook de garantías: el costo real del
  reclamo se cablea como gasto operativo (categoría "garantia",
  idempotente por reclamo) en RegistroGastos; rentabilidad() lo incluye
  en gastos_operativos sin cambiar profit.py. Documentado en §5.

MODELO:
- TerminoGarantia: condiciones comerciales por producto (y opcionalmente
  por lote): duración en meses, cobertura, condiciones, base de inicio.
- TarjetaGarantia: activación de un término para una venta concreta
  (cliente + producto + venta + serie/lote opcional), con vencimiento en
  MESES CALENDARIO (no la aproximación de 30.44 días de lotes.py).
- ReclamoGarantia: ciclo intake → diagnóstico → resolución. Una
  reclamación por tarjeta (igual que lotes.reclamar_garantia).
  Resoluciones: reparacion | reemplazo | devolucion | rechazado.

REGLAS:
- Todo es por empresa: company_id obligatorio; servicios de otra
  empresa se rechazan.
- Sin venta confirmada no hay activación; sin tarjeta activa y vigente
  no hay reclamo.
- Costos nunca negativos; costo_maximo_por_reclamo del término es techo
  duro en resolver().
- resolver() exige diagnóstico previo; si no está cubierto, la única
  resolución válida es "rechazado" con costo 0.
- El gasto operativo se emite SOLO al cerrar con costo > 0, con
  gasto_id "gar-<reclamo_id>" (idempotente: reintentos no duplican).
"""
from __future__ import annotations

import calendar
import sys
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from oschema.contract import require_iso_currency  # noqa: E402


class ErrorGarantia(Exception):
    """Error de dominio del bloque (el wiring lo traduce a 400)."""


class NoEncontrado(ErrorGarantia):
    """Recurso inexistente (el wiring lo traduce a 404)."""


class PermisoDenegado(ErrorGarantia):
    """Permiso insuficiente (el wiring lo traduce a 403)."""


# Coberturas y resoluciones válidas (cerradas: evita texto libre).
COBERTURAS = ("fabricacion", "fabricacion_mano_obra", "extendida",
              "proveedor")
CAUSAS = ("fabricacion", "mal_uso", "desgaste_normal", "no_determinada")
RESOLUCIONES = ("reparacion", "reemplazo", "devolucion", "rechazado")
INICIOS = ("fecha_venta", "fecha_entrega")


def _parse_fecha(f: str) -> datetime:
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(
        tzinfo=timezone.utc)


def _hoy(hoy: str | None) -> datetime:
    return _parse_fecha(hoy) if hoy else datetime.now(timezone.utc)


def _sumar_meses(iso: str, n: int) -> str:
    """Suma n meses calendario a una fecha ISO (día ajustado al mes).

    Difiere de lotes.Garantia.vence_el (aproxima 30.44 días/mes): aquí
    el vencimiento es el mismo día del mes n meses después, que es lo
    que dicen las pólizas reales ("12 meses de garantía").
    """
    dt = _parse_fecha(iso)
    m = dt.month - 1 + n
    y, m = dt.year + m // 12, m % 12 + 1
    d = min(dt.day, calendar.monthrange(y, m)[1])
    return dt.replace(year=y, month=m, day=d).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


@dataclass
class TerminoGarantia:
    """Condiciones comerciales de garantía por producto (o lote)."""
    termino_id: str
    company_id: str
    producto_id: str
    duracion_meses: int
    cobertura: str = "fabricacion"
    condiciones: str = ""          # texto libre de la póliza
    inicio: str = "fecha_venta"    # fecha_venta | fecha_entrega
    lote_id: str = ""              # "" = aplica a todo el producto
    costo_maximo_por_reclamo: str = ""  # "" = sin techo
    proveedor_respalda: bool = False    # el proveedor cubre defectos

    def validate(self):
        for f in ("termino_id", "company_id", "producto_id"):
            if not getattr(self, f):
                raise ValueError(f"{f} es obligatorio")
        if int(self.duracion_meses) <= 0:
            raise ValueError("duracion_meses debe ser > 0")
        if self.cobertura not in COBERTURAS:
            raise ValueError(f"cobertura inválida: {self.cobertura} "
                             f"(válidas: {', '.join(COBERTURAS)})")
        if self.inicio not in INICIOS:
            raise ValueError(f"inicio inválido: {self.inicio}")
        if self.costo_maximo_por_reclamo:
            if _dec(self.costo_maximo_por_reclamo) <= 0:
                raise ValueError("costo_maximo_por_reclamo debe ser > 0")

    def to_dict(self):
        return {"termino_id": self.termino_id,
                "company_id": self.company_id,
                "producto_id": self.producto_id,
                "lote_id": self.lote_id,
                "duracion_meses": int(self.duracion_meses),
                "cobertura": self.cobertura,
                "condiciones": self.condiciones,
                "inicio": self.inicio,
                "costo_maximo_por_reclamo": self.costo_maximo_por_reclamo,
                "proveedor_respalda": self.proveedor_respalda}


@dataclass
class TarjetaGarantia:
    """Activación de un término para una venta concreta."""
    tarjeta_id: str
    company_id: str
    termino_id: str
    producto_id: str
    venta_id: str
    cliente_id: str
    fecha_inicio: str
    fecha_vencimiento: str
    cantidad: str = "1"
    serie_o_lote: str = ""
    estado: str = "activa"       # activa|reclamada|reemplazada|anulada|
                                 # expirada
    reclamo_ref: str = ""

    def vigente(self, hoy: str | None = None) -> bool:
        return (self.estado == "activa"
                and _hoy(hoy) <= _parse_fecha(self.fecha_vencimiento))

    def dias_para_vencer(self, hoy: str | None = None) -> int:
        return (_parse_fecha(self.fecha_vencimiento) - _hoy(hoy)).days

    def to_dict(self):
        return {"tarjeta_id": self.tarjeta_id,
                "company_id": self.company_id,
                "termino_id": self.termino_id,
                "producto_id": self.producto_id,
                "venta_id": self.venta_id, "cliente_id": self.cliente_id,
                "fecha_inicio": self.fecha_inicio,
                "fecha_vencimiento": self.fecha_vencimiento,
                "cantidad": str(_dec(self.cantidad)),
                "serie_o_lote": self.serie_o_lote,
                "estado": self.estado, "reclamo_ref": self.reclamo_ref}


@dataclass
class ReclamoGarantia:
    """Ciclo de un reclamo: intake → diagnóstico → resolución."""
    reclamo_id: str
    company_id: str
    tarjeta_id: str
    cliente_id: str
    producto_id: str
    venta_id: str
    fecha_reporte: str
    sintomas: str
    moneda: str = "DOP"
    serie_o_lote: str = ""
    diagnostico: str = ""
    causa: str = ""                    # ver CAUSAS
    cubierto: bool | None = None
    resolucion: str = ""               # ver RESOLUCIONES
    costo_mano_obra: str = "0"
    costo_repuestos: str = "0"
    costo_envio: str = "0"
    estado: str = "abierto"            # abierto|diagnosticado|cerrado
    fecha_cierre: str = ""
    gasto_emitido: bool = False

    def costo_total(self) -> Decimal:
        return (_dec(self.costo_mano_obra) + _dec(self.costo_repuestos)
                + _dec(self.costo_envio))

    def to_dict(self):
        return {"reclamo_id": self.reclamo_id,
                "company_id": self.company_id,
                "tarjeta_id": self.tarjeta_id,
                "cliente_id": self.cliente_id,
                "producto_id": self.producto_id, "venta_id": self.venta_id,
                "fecha_reporte": self.fecha_reporte,
                "sintomas": self.sintomas,
                "serie_o_lote": self.serie_o_lote,
                "moneda": self.moneda,
                "diagnostico": self.diagnostico, "causa": self.causa,
                "cubierto": self.cubierto,
                "resolucion": self.resolucion,
                "costo_mano_obra": str(_dec(self.costo_mano_obra)),
                "costo_repuestos": str(_dec(self.costo_repuestos)),
                "costo_envio": str(_dec(self.costo_envio)),
                "costo_total": str(self.costo_total()),
                "estado": self.estado, "fecha_cierre": self.fecha_cierre,
                "gasto_emitido": self.gasto_emitido}


class RegistroGarantias:
    """Garantías de UNA empresa (memoria; PG en la migración 017)."""

    def __init__(self, company_id: str, ventas=None, gastos=None,
                 lotes=None):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        for svc in (ventas, gastos, lotes):
            if svc is not None and getattr(svc, "company_id", None) \
                    != company_id:
                raise ValueError(
                    "todos los servicios deben ser de la misma empresa")
        self.company_id = company_id
        self._ventas = ventas        # os_crm.ventas.VentasService
        self._gastos = gastos        # os_finanzas.gastos.RegistroGastos
        self._lotes = lotes          # os_inventory.lotes.RegistroLotes
        self._lock = threading.RLock()
        self._terminos: dict[str, TerminoGarantia] = {}
        self._tarjetas: dict[str, TarjetaGarantia] = {}
        self._reclamos: dict[str, ReclamoGarantia] = {}

    # ---- términos ----------------------------------------------------
    def registrar_termino(self, t: TerminoGarantia) -> TerminoGarantia:
        with self._lock:
            t.validate()
            if t.company_id != self.company_id:
                raise ErrorGarantia("término de otra empresa")
            if t.termino_id in self._terminos:
                raise ErrorGarantia(
                    f"término duplicado: {t.termino_id}")
            if t.lote_id and self._lotes is not None:
                lote = self._lotes.ficha_lote(t.lote_id)
                if lote is None:
                    raise ErrorGarantia(
                        f"lote inexistente: {t.lote_id}")
                if lote.producto_id != t.producto_id:
                    raise ErrorGarantia(
                        f"el lote {t.lote_id} es de {lote.producto_id}, "
                        f"no de {t.producto_id}")
            self._terminos[t.termino_id] = t
            return t

    def terminos(self, producto_id: str | None = None) -> list[dict]:
        with self._lock:
            ts = list(self._terminos.values())
            if producto_id:
                ts = [t for t in ts if t.producto_id == producto_id]
            return [t.to_dict() for t in sorted(
                ts, key=lambda t: t.termino_id)]

    def _termino_para(self, producto_id: str,
                      lote_id: str = "") -> TerminoGarantia | None:
        """Término aplicable: el del lote si existe, si no el de producto."""
        if lote_id:
            for t in self._terminos.values():
                if (t.producto_id == producto_id
                        and t.lote_id == lote_id):
                    return t
        for t in self._terminos.values():
            if t.producto_id == producto_id and not t.lote_id:
                return t
        return None

    # ---- activación desde ventas --------------------------------------
    def activar_desde_venta(self, venta_id: str,
                            fecha_entrega: str = "") -> list[dict]:
        """Crea una tarjeta por línea de la venta con término aplicable.

        Lee la venta de os_crm.ventas (estado_venta); exige venta
        confirmada. La fecha de inicio es la fecha de la venta (o
        fecha_entrega si el término usa inicio=fecha_entrega).
        Idempotente por venta: una segunda activación devuelve las
        tarjetas ya creadas sin duplicar.
        """
        if self._ventas is None:
            raise ErrorGarantia(
                "registro sin servicio de ventas conectado")
        with self._lock:
            reg = self._ventas.estado_venta(venta_id)
            if reg is None:
                raise NoEncontrado(f"venta inexistente: {venta_id}")
            if reg.get("estado") != "confirmada":
                raise ErrorGarantia(
                    f"venta en estado {reg.get('estado')}: no activa "
                    "garantías")
            ya = [t for t in self._tarjetas.values()
                  if t.venta_id == venta_id]
            if ya:
                return [t.to_dict() for t in sorted(
                    ya, key=lambda t: t.tarjeta_id)]
            sol = reg["solicitud"]
            creadas = []
            for lin in sol.lineas:
                pid = lin["producto_id"]
                term = self._termino_para(pid)
                if term is None:
                    continue
                inicio = (fecha_entrega or sol.fecha
                          if term.inicio == "fecha_entrega"
                          else sol.fecha)
                if not inicio:
                    raise ErrorGarantia(
                        f"la venta {venta_id} no tiene fecha: no se "
                        "puede iniciar la garantía")
                tid = f"gt-{venta_id}-{pid}"
                if tid in self._tarjetas:  # defensa ante líneas repetidas
                    continue
                tj = TarjetaGarantia(
                    tarjeta_id=tid, company_id=self.company_id,
                    termino_id=term.termino_id, producto_id=pid,
                    venta_id=venta_id, cliente_id=sol.cliente_id,
                    fecha_inicio=inicio,
                    fecha_vencimiento=_sumar_meses(
                        inicio, int(term.duracion_meses)),
                    cantidad=str(_dec(lin["cantidad"])))
                self._tarjetas[tid] = tj
                creadas.append(tj.to_dict())
            return creadas

    def tarjetas(self, estado: str | None = None,
                 producto_id: str | None = None,
                 cliente_id: str | None = None) -> list[dict]:
        with self._lock:
            ts = list(self._tarjetas.values())
            if estado:
                ts = [t for t in ts if t.estado == estado]
            if producto_id:
                ts = [t for t in ts if t.producto_id == producto_id]
            if cliente_id:
                ts = [t for t in ts if t.cliente_id == cliente_id]
            return [t.to_dict() for t in sorted(
                ts, key=lambda t: t.tarjeta_id)]

    # ---- reclamos ------------------------------------------------------
    def registrar_reclamo(self, reclamo_id: str, tarjeta_id: str,
                          sintomas: str, moneda: str = "DOP",
                          serie_o_lote: str = "",
                          fecha_reporte: str = "") -> dict:
        with self._lock:
            if not (reclamo_id or "").strip():
                raise ErrorGarantia("reclamo_id es obligatorio")
            if reclamo_id in self._reclamos:
                raise ErrorGarantia(
                    f"reclamo duplicado: {reclamo_id}")
            if not (sintomas or "").strip():
                raise ErrorGarantia(
                    "el reclamo requiere los síntomas reportados")
            tj = self._tarjetas.get(tarjeta_id)
            if tj is None:
                raise NoEncontrado(
                    f"tarjeta inexistente: {tarjeta_id}")
            if tj.estado != "activa":
                raise ErrorGarantia(
                    f"tarjeta {tarjeta_id} en estado {tj.estado}: "
                    "no admite reclamos")
            if not tj.vigente(fecha_reporte or None):
                tj.estado = "expirada"
                raise ErrorGarantia(
                    f"garantía vencida el {tj.fecha_vencimiento}")
            require_iso_currency(moneda)
            fecha_reporte = fecha_reporte or datetime.now(
                timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            r = ReclamoGarantia(
                reclamo_id=reclamo_id, company_id=self.company_id,
                tarjeta_id=tarjeta_id, cliente_id=tj.cliente_id,
                producto_id=tj.producto_id, venta_id=tj.venta_id,
                fecha_reporte=fecha_reporte, sintomas=sintomas.strip(),
                moneda=moneda, serie_o_lote=serie_o_lote)
            self._reclamos[reclamo_id] = r
            return r.to_dict()

    def diagnosticar(self, reclamo_id: str, diagnostico: str,
                     causa: str, cubierto: bool) -> dict:
        with self._lock:
            r = self._exigir_reclamo(reclamo_id)
            if r.estado != "abierto":
                raise ErrorGarantia(
                    f"reclamo {reclamo_id} en estado {r.estado}: "
                    "ya fue diagnosticado")
            if causa not in CAUSAS:
                raise ErrorGarantia(
                    f"causa inválida: {causa} "
                    f"(válidas: {', '.join(CAUSAS)})")
            if not (diagnostico or "").strip():
                raise ErrorGarantia("el diagnóstico es obligatorio")
            r.diagnostico = diagnostico.strip()
            r.causa = causa
            r.cubierto = bool(cubierto)
            r.estado = "diagnosticado"
            return r.to_dict()

    def resolver(self, reclamo_id: str, resolucion: str,
                 costo_mano_obra: str = "0", costo_repuestos: str = "0",
                 costo_envio: str = "0",
                 fecha_cierre: str = "") -> dict:
        """Cierra el reclamo con su costo real.

        - Sin cobertura: solo "rechazado" y costo total 0.
        - Con cobertura: reparacion|reemplazo|devolucion; el costo total
          no puede superar el techo del término.
        - Al cerrar con costo > 0 emite el gasto operativo idempotente
          (gasto_id "gar-<reclamo_id>", categoría "garantia"); con costo
          0 no hay gasto que registrar (honestidad: no se inventa).
        - reemplazo → tarjeta "reemplazada"; devolucion → tarjeta
          "anulada" (la devolución contable la hace os_crm.devoluciones,
          aquí solo se marca); reparacion/rechazado → "reclamada".
        """
        with self._lock:
            r = self._exigir_reclamo(reclamo_id)
            if r.estado != "diagnosticado":
                raise ErrorGarantia(
                    f"reclamo {reclamo_id} en estado {r.estado}: "
                    "requiere diagnóstico previo")
            if resolucion not in RESOLUCIONES:
                raise ErrorGarantia(
                    f"resolución inválida: {resolucion} "
                    f"(válidas: {', '.join(RESOLUCIONES)})")
            costos = {"costo_mano_obra": _dec(costo_mano_obra),
                      "costo_repuestos": _dec(costo_repuestos),
                      "costo_envio": _dec(costo_envio)}
            for k, v in costos.items():
                if v < 0:
                    raise ErrorGarantia(f"{k} no puede ser negativo")
            total = sum(costos.values(), Decimal("0"))
            if not r.cubierto:
                if resolucion != "rechazado":
                    raise ErrorGarantia(
                        "sin cobertura la única resolución válida es "
                        "'rechazado'")
                if total != 0:
                    raise ErrorGarantia(
                        "un reclamo rechazado no puede tener costo")
            else:
                if resolucion == "rechazado":
                    raise ErrorGarantia(
                        "con cobertura no se puede rechazar: use "
                        "reparacion, reemplazo o devolucion")
                term = self._terminos.get(
                    self._tarjetas[r.tarjeta_id].termino_id)
                techo = (term.costo_maximo_por_reclamo if term else "")
                if techo and total > _dec(techo):
                    raise ErrorGarantia(
                        f"costo {total} supera el techo del término "
                        f"({techo}): requiere autorización")
            r.costo_mano_obra = str(costos["costo_mano_obra"])
            r.costo_repuestos = str(costos["costo_repuestos"])
            r.costo_envio = str(costos["costo_envio"])
            r.resolucion = resolucion
            r.estado = "cerrado"
            r.fecha_cierre = fecha_cierre or datetime.now(
                timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            # Estado final de la tarjeta.
            tj = self._tarjetas[r.tarjeta_id]
            tj.estado = {"reemplazo": "reemplazada",
                         "devolucion": "anulada"}.get(resolucion,
                                                      "reclamada")
            tj.reclamo_ref = reclamo_id
            # Costo real → gasto operativo (idempotente).
            if total > 0:
                r.gasto_emitido = self._emitir_gasto(r)
            d = r.to_dict()
            term = self._terminos.get(tj.termino_id)
            d["escalable_a_proveedor"] = bool(
                term and term.proveedor_respalda
                and r.causa == "fabricacion")
            if d["escalable_a_proveedor"]:
                d["nota_proveedor"] = (
                    "defecto de fabricación con respaldo del proveedor: "
                    "el reclamo a proveedor se gestiona en os_compras "
                    "con este reclamo_id como referencia")
            return d

    def _emitir_gasto(self, r: ReclamoGarantia) -> bool:
        """Registra el costo del reclamo como gasto operativo.

        Devuelve True si el gasto quedó registrado (False si el
        registro de gastos no está conectado: el costo queda calculado
        y documentado para el cableado del coordinador, ver §5 del doc).
        Idempotente por gasto_id: reintentos no duplican.
        """
        if self._gastos is None:
            return False
        from oschema.models import Gasto  # noqa: E402
        from os_finanzas.gastos import GastoDetallado  # noqa: E402
        g = GastoDetallado(
            gasto=Gasto(
                gasto_id=f"gar-{r.reclamo_id}",
                company_id=self.company_id, categoria="garantia",
                descripcion=(f"reclamo garantía {r.reclamo_id} "
                             f"({r.resolucion}, {r.producto_id})"),
                monto=str(r.costo_total()), moneda=r.moneda,
                fecha=r.fecha_cierre),
            centro_costo="garantias")
        self._gastos.registrar_idempotente(g)
        return True

    def reclamos(self, estado: str | None = None,
                 producto_id: str | None = None,
                 cliente_id: str | None = None) -> list[dict]:
        with self._lock:
            rs = list(self._reclamos.values())
            if estado:
                rs = [r for r in rs if r.estado == estado]
            if producto_id:
                rs = [r for r in rs if r.producto_id == producto_id]
            if cliente_id:
                rs = [r for r in rs if r.cliente_id == cliente_id]
            return [r.to_dict() for r in sorted(
                rs, key=lambda r: r.reclamo_id)]

    def _exigir_reclamo(self, reclamo_id: str) -> ReclamoGarantia:
        r = self._reclamos.get(reclamo_id)
        if r is None:
            raise NoEncontrado(f"reclamo inexistente: {reclamo_id}")
        return r

    # ---- costos e impacto en margen ----------------------------------
    def costos_por_periodo(self, desde: str | None = None,
                           hasta: str | None = None,
                           producto_id: str | None = None
                           ) -> dict[str, str]:
        """Costo real de garantías cerradas, agrupado por moneda.

        Fuente para el cableado a rentabilidad: si el registro de
        gastos está conectado, estos mismos montos ya están en
        gastos_operativos (categoría "garantia"); si no, este dict es
        el insumo documentado para el coordinador (§5 del doc).
        """
        tot: dict[str, Decimal] = {}
        with self._lock:
            for r in self._reclamos.values():
                if r.estado != "cerrado":
                    continue
                if desde and (r.fecha_cierre or "") < desde:
                    continue
                if hasta and (r.fecha_cierre or "") > hasta:
                    continue
                if producto_id and r.producto_id != producto_id:
                    continue
                tot[r.moneda] = (tot.get(r.moneda, Decimal("0"))
                                 + r.costo_total())
        return {m: str(v) for m, v in sorted(tot.items())}

    def impacto_margen(self, producto_id: str,
                       desde: str | None = None,
                       hasta: str | None = None) -> dict:
        """Margen de contribución del producto con y sin garantías.

        Usa la vista honesta rentabilidad(producto_id=) del libro
        (gastos operativos NO atribuibles por producto: no se
        prorratean) y resta el costo real de garantías del producto.
        Si el libro no está disponible o el producto no tiene ventas,
        los campos quedan en "datos insuficientes" (nunca inventados).
        """
        costos = self.costos_por_periodo(desde, hasta, producto_id)
        out = {"producto_id": producto_id, "desde": desde, "hasta": hasta,
               "costo_garantias_por_moneda": costos}
        libro = getattr(self, "_libro", None)
        if libro is None:
            out["nota"] = ("sin libro conectado: el costo de garantías "
                           "está calculado; el cableado a rentabilidad "
                           "lo hace el coordinador (ver docs §5)")
            return out
        try:
            vista = libro.rentabilidad(desde=desde, hasta=hasta,
                                       producto_id=producto_id)
        except Exception as e:  # defensa: nunca romper por el libro
            out["nota"] = f"rentabilidad no disponible: {e}"
            return out
        detalle = {}
        for mon, v in vista.items():
            if v.get("ingresos_netos") in (None, "datos insuficientes"):
                continue
            netos = _dec(v["ingresos_netos"])
            bruta = _dec(v["utilidad_bruta"])
            cg = _dec(costos.get(mon, "0"))
            bruta2 = bruta - cg
            detalle[mon] = {
                "ingresos_netos": v["ingresos_netos"],
                "utilidad_bruta_sin_garantias": v["utilidad_bruta"],
                "costo_garantias": str(cg),
                "utilidad_bruta_con_garantias": str(bruta2),
                "margen_bruto_sin_garantias_pct":
                    v.get("margen_bruto_pct"),
                "margen_bruto_con_garantias_pct": (
                    str(round(bruta2 / netos * 100, 1))
                    if netos > 0 else None),
                "nota": v.get("nota", "")}
        out["por_moneda"] = detalle
        return out

    def conectar_libro(self, libro) -> None:
        """Conecta el LibroFinanzas para impacto_margen (opcional)."""
        if getattr(libro, "company_id", None) != self.company_id:
            raise ErrorGarantia("libro de otra empresa")
        with self._lock:
            self._libro = libro

    # ---- historial -----------------------------------------------------
    def historial_cliente(self, cliente_id: str) -> dict:
        """Tarjetas + reclamos de un cliente (trazabilidad completa)."""
        with self._lock:
            tjs = [t.to_dict() for t in self._tarjetas.values()
                   if t.cliente_id == cliente_id]
            rcs = [r.to_dict() for r in self._reclamos.values()
                   if r.cliente_id == cliente_id]
            costo = sum((r.costo_total() for r in self._reclamos.values()
                         if r.cliente_id == cliente_id
                         and r.estado == "cerrado"), Decimal("0"))
            return {"cliente_id": cliente_id,
                    "tarjetas": sorted(tjs,
                                       key=lambda t: t["tarjeta_id"]),
                    "reclamos": sorted(rcs,
                                       key=lambda r: r["reclamo_id"]),
                    "costo_total_reclamos": str(costo)}

    def historial_producto(self, producto_id: str) -> dict:
        """Términos + tarjetas + reclamos + costos de un producto."""
        with self._lock:
            tms = [t.to_dict() for t in self._terminos.values()
                   if t.producto_id == producto_id]
            tjs = [t.to_dict() for t in self._tarjetas.values()
                   if t.producto_id == producto_id]
            rcs = [r.to_dict() for r in self._reclamos.values()
                   if r.producto_id == producto_id]
            cerrados = [r for r in self._reclamos.values()
                        if r.producto_id == producto_id
                        and r.estado == "cerrado"]
            costo = sum((r.costo_total() for r in cerrados),
                        Decimal("0"))
            por_causa: dict[str, int] = {}
            por_res: dict[str, int] = {}
            for r in cerrados:
                por_causa[r.causa or "?"] = por_causa.get(
                    r.causa or "?", 0) + 1
                por_res[r.resolucion or "?"] = por_res.get(
                    r.resolucion or "?", 0) + 1
            return {"producto_id": producto_id,
                    "terminos": sorted(
                        tms, key=lambda t: t["termino_id"]),
                    "tarjetas": sorted(
                        tjs, key=lambda t: t["tarjeta_id"]),
                    "reclamos": sorted(
                        rcs, key=lambda r: r["reclamo_id"]),
                    "reclamos_cerrados": len(cerrados),
                    "costo_total_reclamos": str(costo),
                    "por_causa": por_causa,
                    "por_resolucion": por_res}

    # ---- alertas de vencimiento (fuente para os_notif) -------------------
    def alertas_vencimiento(self, dias_alerta: int = 30,
                            hoy: str | None = None) -> list[dict]:
        """Tarjetas activas vencidas o próximas a vencer.

        Formato de fuente para os_notif (contrato en docs §6): cada
        alerta es un dict con fuente/tipo/severidad/mensaje + ids.
        NO muta estado (las expiradas se marcan al reclamar o con
        depurar_vencidas).
        """
        out = []
        with self._lock:
            for t in self._tarjetas.values():
                if t.estado != "activa":
                    continue
                d = t.dias_para_vencer(hoy)
                if d < 0:
                    sev, tipo = "critica", "garantia_vencida"
                    msg = (f"garantía {t.tarjeta_id} de {t.producto_id} "
                           f"VENCIÓ hace {-d} días "
                           f"({t.fecha_vencimiento[:10]})")
                elif d <= dias_alerta:
                    sev = "alerta" if d <= 7 else "aviso"
                    tipo = "garantia_proxima_a_vencer"
                    msg = (f"garantía {t.tarjeta_id} de {t.producto_id} "
                           f"vence en {d} días "
                           f"({t.fecha_vencimiento[:10]})")
                else:
                    continue
                out.append({"fuente": "os_garantias", "tipo": tipo,
                            "severidad": sev, "tarjeta_id": t.tarjeta_id,
                            "termino_id": t.termino_id,
                            "cliente_id": t.cliente_id,
                            "producto_id": t.producto_id,
                            "venta_id": t.venta_id,
                            "fecha_vencimiento": t.fecha_vencimiento,
                            "dias_para_vencer": d, "mensaje": msg})
        out.sort(key=lambda a: a["dias_para_vencer"])
        return out

    def depurar_vencidas(self, hoy: str | None = None) -> list[str]:
        """Marca 'expirada' las tarjetas activas ya vencidas (auditoría).

        No las elimina. Devuelve los ids marcados.
        """
        marcadas = []
        with self._lock:
            for t in self._tarjetas.values():
                if (t.estado == "activa"
                        and t.dias_para_vencer(hoy) < 0):
                    t.estado = "expirada"
                    marcadas.append(t.tarjeta_id)
        return sorted(marcadas)
