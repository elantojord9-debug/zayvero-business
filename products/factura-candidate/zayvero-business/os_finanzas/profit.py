"""LOTE 5 — Profit y Margin Guard (modelo P&L corregido 2026-10-13).

LibroFinanzas (devengo, UNA empresa) — ÚNICA fuente de verdad del P&L:
- registrar_venta: ingreso + COGS (al costo promedio vigente del Lote 3/5).
  La venta se cuenta UNA vez aquí; el cobro va a Tesorería (no se duplica).
- Gastos operativos: UNA sola fuente — el RegistroGastos conectado vía
  conectar_gastos() (o el parámetro `gastos=` del constructor). Cada gasto
  se cuenta UNA vez (idempotencia por gasto_id): registrar_gasto() delega
  al registro y un reintento con el mismo gasto_id NO duplica. Sin registro
  conectado, el libro usa su lista interna (modo autónomo: tests y usos
  directos; el registro conectado siempre tiene precedencia).
- registrar_nota_credito: devoluciones que restan del ingreso, una sola vez
  (no borra la venta: trazabilidad completa).
- Descuentos por línea (LineaVenta.descuento_pct, 0-100) restan del ingreso.
- Impuestos: DATO EXPLÍCITO del contador (registrar_impuesto). El OS no
  estima, no proyecta y no aplica tasas fiscales: no hay tasas certificadas
  en el sistema. Sin impuestos registrados en el alcance, utilidad_neta es
  la cadena "datos insuficientes" (nunca se inventa).
- rentabilidad(): P&L agrupado por moneda. NUNCA mezcla monedas: la
  conversión exige tasa + fecha explícitas y queda registrada en el cálculo.

Modelo por moneda (vista sin filtros):
  ingresos_brutos − descuentos − devoluciones = ingresos_netos
  ingresos_netos − cogs           = utilidad_bruta
  utilidad_bruta − gastos_operativos = utilidad_operativa
  utilidad_operativa − impuestos  = utilidad_neta (o "datos insuficientes")
  márgenes bruto/operativo/neto = utilidad / ingresos_netos × 100.

Vistas filtradas (honestidad dimensional):
- producto_id: vista de contribución (ingresos/cogs/bruta del producto).
  Los gastos operativos y los impuestos NO son atribuibles por producto:
  no se prorratean (prorratear inventaría una regla); utilidad operativa,
  impuestos, utilidad neta y sus márgenes quedan en "datos insuficientes"
  con una nota que lo explica.
- sucursal_id: los gastos SÍ son atribuibles por sucursal (GastoDetallado
  lleva sucursal_id) y la utilidad operativa se calcula; los impuestos no
  tienen dimensión de sucursal -> utilidad neta "datos insuficientes".

Margin Guard: alerta si margen < umbral o si una venta se hizo bajo costo.
"""
from __future__ import annotations

import sys
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from oschema.contract import require_iso_currency  # noqa: E402
from .costo import CostoInventario  # noqa: E402
from .contratos import convertir  # noqa: E402


def _parse(f: str):
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


#: Valor honesto cuando el cálculo no tiene datos suficientes.
DATO_INSUFICIENTE = "datos insuficientes"


class ErrorProfit(Exception):
    pass


@dataclass
class LineaVenta:
    producto_id: str
    cantidad: str
    precio_unitario: str
    almacen_id: str = ""
    sucursal_id: str = ""
    descuento_pct: str = "0"  # descuento de línea, 0-100 (resta del ingreso)


@dataclass
class VentaFinanciera:
    venta_id: str
    company_id: str
    lineas: list = field(default_factory=list)
    moneda: str = "DOP"
    fecha: str = ""
    cogs_total: str = "0"  # calculado al registrar

    def total(self) -> Decimal:
        tot = Decimal("0")
        for l in self.lineas:
            desc = _dec(l.descuento_pct or "0") / 100
            tot += (_dec(l.cantidad) * _dec(l.precio_unitario)
                    * (1 - desc))
        return tot


@dataclass
class ImpuestoRegistrado:
    """Impuesto como DATO EXPLÍCITO del contador (no estimado por el OS)."""
    impuesto_id: str
    moneda: str
    monto: str
    concepto: str = ""
    fecha: str = ""

    def validate(self):
        if not self.impuesto_id:
            raise ValueError("impuesto_id es obligatorio")
        require_iso_currency(self.moneda)
        if _dec(self.monto) < 0:
            raise ValueError("el impuesto no puede ser negativo")


def _pct(parte: Decimal | None, base: Decimal) -> str | None:
    if parte is None or base == 0:
        return None
    return str(round(parte / base * 100, 1))


def _str_monto(d: Decimal) -> str:
    """Monto como cadena sin notación científica ni ceros de más.

    Decimal("2000.0") -> "2000"; Decimal("12000") -> "12000".
    """
    s = format(d, "f")
    if "." in s:
        s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-") else "0"


# Campos numéricos del P&L que participan en una conversión de moneda.
_CAMPOS_CONVERTIBLES = (
    "ingresos_brutos", "descuentos", "devoluciones", "ingresos_netos",
    "ingresos", "cogs", "utilidad_bruta", "gastos_operativos", "gastos",
    "utilidad_operativa", "impuestos", "utilidad_neta",
)


class LibroFinanzas:
    def __init__(self, company_id: str, costo: CostoInventario,
                 gastos=None):
        """gastos: RegistroGastos opcional (fuente única de verdad).

        Conéctalo siempre en el bundle de la empresa
        (webapp/os_api.py lo hace en OSBundle.crear).
        """
        if not company_id:
            raise ValueError("company_id es obligatorio")
        self.company_id = company_id
        self.costo = costo
        self._lock = threading.RLock()
        self._ventas: dict[str, VentaFinanciera] = {}
        # Lista interna: SOLO modo autónomo (sin RegistroGastos conectado).
        self._gastos: list = []  # GastoDetallado
        self._notas_credito: list = []  # [(venta_id, monto, moneda, fecha,
        #                                  cogs_revertido)]
        self._impuestos: dict[str, ImpuestoRegistrado] = {}
        self._registro_gastos = None
        if gastos is not None:
            self.conectar_gastos(gastos)

    # ---- cableado RegistroGastos -------------------------------------
    def conectar_gastos(self, registro) -> None:
        """Conecta el RegistroGastos como ÚNICA fuente de gastos.

        Desde aquí, rentabilidad() lee los gastos SOLO del registro y
        registrar_gasto() delega en él (idempotente por gasto_id).
        """
        from .gastos import RegistroGastos
        if not isinstance(registro, RegistroGastos):
            raise ErrorProfit("conectar_gastos exige un RegistroGastos")
        if registro.company_id != self.company_id:
            raise ErrorProfit("registro de gastos de otra empresa")
        with self._lock:
            self._registro_gastos = registro

    @property
    def registro_gastos(self):
        return self._registro_gastos

    def _gastos_fuente(self) -> list:
        reg = self._registro_gastos
        if reg is not None:
            return reg.todos()
        return list(self._gastos)

    # ---- registro ------------------------------------------------------
    def registrar_venta(self, v: VentaFinanciera) -> VentaFinanciera:
        """Registra la venta y calcula su COGS al promedio vigente."""
        with self._lock:
            if v.company_id != self.company_id:
                raise ErrorProfit("venta de otra empresa")
            if v.venta_id in self._ventas:
                raise ErrorProfit(f"venta duplicada: {v.venta_id}")
            require_iso_currency(v.moneda)
            for lin in v.lineas:
                d = _dec(lin.descuento_pct or "0")
                if d < 0 or d > 100:
                    raise ErrorProfit(
                        f"descuento_pct fuera de rango (0-100): "
                        f"{lin.descuento_pct}")
            cogs = Decimal("0")
            for lin in v.lineas:
                cogs += self.costo.aplicar_salida(
                    lin.producto_id, lin.almacen_id or "principal",
                    str(_dec(lin.cantidad)), v.moneda)
            v.cogs_total = str(cogs)
            self._ventas[v.venta_id] = v
            return v

    def registrar_nota_credito(self, venta_id: str, monto: str,
                               moneda: str, fecha: str = "",
                               cogs_revertido: str = "0"):
        """Revierte ingreso (y opcionalmente COGS) de una venta.

        No borra la venta: trazabilidad completa. rentabilidad() la resta
        UNA sola vez como devolución.
        """
        with self._lock:
            require_iso_currency(moneda)
            if _dec(monto) <= 0:
                raise ErrorProfit("la nota de crédito debe ser > 0")
            self._notas_credito.append((venta_id, _dec(monto), moneda, fecha,
                                        _dec(cogs_revertido)))

    def registrar_gasto(self, g) -> bool:
        """Registra un gasto operativo. Devuelve True si es nuevo.

        Con RegistroGastos conectado: delega en él — idempotencia por
        gasto_id (un reintento devuelve False y NO duplica el gasto en
        el P&L). Sin registro: lista interna (modo autónomo).
        """
        with self._lock:
            if g.gasto.company_id != self.company_id:
                raise ErrorProfit("gasto de otra empresa")
            if self._registro_gastos is not None:
                return self._registro_gastos.registrar_idempotente(g)
            g.validate()
            self._gastos.append(g)
            return True

    def registrar_impuesto(self, imp: ImpuestoRegistrado) -> bool:
        """Registra un impuesto como DATO del contador (no estimado).

        SUPUESTO DOCUMENTADO: el OS no calcula ni estima carga fiscal;
        no existen tasas fiscales certificadas en el sistema. Solo los
        impuestos registrados aquí entran al P&L; sin ellos, la utilidad
        neta es "datos insuficientes".
        """
        with self._lock:
            imp.validate()
            if imp.impuesto_id in self._impuestos:
                raise ErrorProfit(
                    f"impuesto duplicado: {imp.impuesto_id}")
            self._impuestos[imp.impuesto_id] = imp
            return True

    # ---- P&L ------------------------------------------------------------
    def rentabilidad(self, desde: str | None = None,
                     hasta: str | None = None,
                     producto_id: str | None = None,
                     sucursal_id: str | None = None,
                     convertir_a: str | None = None,
                     tasa: str | None = None,
                     fecha_tasa: str | None = None) -> dict:
        """P&L agrupado por moneda. Sin mezclar monedas.

        convertir_a: si se indica, convierte todo a esa moneda con la
        tasa + fecha EXPLÍCITAS (unidades de origen por 1 de destino,
        ver contratos.convertir); la conversión queda registrada en la
        salida. Sin tasa o sin fecha_tasa: ErrorProfit (no se adivina).
        """
        if convertir_a is not None:
            require_iso_currency(convertir_a)
            if not tasa or not fecha_tasa:
                raise ErrorProfit(
                    "convertir_a exige tasa y fecha_tasa explícitas")
            if _dec(tasa) <= 0:
                raise ErrorProfit("tasa debe ser > 0")
        crudo = self._pyl_por_moneda(desde, hasta, producto_id,
                                     sucursal_id)
        if convertir_a is None:
            return {m: self._formatear(m, v)
                    for m, v in sorted(crudo.items())}
        return self._convertir(crudo, convertir_a, str(tasa),
                               str(fecha_tasa))

    def _pyl_por_moneda(self, desde, hasta, producto_id,
                        sucursal_id) -> dict:
        def acc():
            return {"brutos": Decimal("0"), "descuentos": Decimal("0"),
                    "devoluciones": Decimal("0"), "cogs": Decimal("0"),
                    "gastos": Decimal("0"), "impuestos": Decimal("0"),
                    "tiene_impuestos": False}
        por_mon: dict[str, dict] = {}
        with self._lock:
            for v in self._ventas.values():
                if desde and v.fecha < desde:
                    continue
                if hasta and v.fecha > hasta:
                    continue
                total_v = v.total()
                for lin in v.lineas:
                    if producto_id and lin.producto_id != producto_id:
                        continue
                    if (sucursal_id and
                            (lin.sucursal_id or lin.almacen_id)
                            != sucursal_id):
                        continue
                    a = por_mon.setdefault(v.moneda, acc())
                    cant = _dec(lin.cantidad)
                    pu = _dec(lin.precio_unitario)
                    desc = _dec(lin.descuento_pct or "0") / 100
                    bruto_l = cant * pu
                    desc_l = bruto_l * desc
                    neto_l = bruto_l - desc_l
                    a["brutos"] += bruto_l
                    a["descuentos"] += desc_l
                    # COGS prorrateado por participación neta de la
                    # línea en la venta.
                    parte = (neto_l / total_v) if total_v else Decimal("0")
                    a["cogs"] += _dec(v.cogs_total) * parte
            # Notas de crédito: revierten ingreso y COGS. No llevan
            # línea ni sucursal: solo aplican a la vista sin esos filtros
            # (no se inventa atribución).
            for _, monto, mon, fecha, cr in self._notas_credito:
                if desde and fecha < desde:
                    continue
                if hasta and fecha > hasta:
                    continue
                if producto_id or sucursal_id:
                    continue
                a = por_mon.setdefault(mon, acc())
                a["devoluciones"] += monto
                a["cogs"] -= cr
            # Gastos operativos: UNA sola fuente (el RegistroGastos
            # conectado, o la lista interna en modo autónomo). Cada
            # gasto se cuenta UNA vez (el registro es un dict por
            # gasto_id).
            for g in self._gastos_fuente():
                if desde and g.gasto.fecha < desde:
                    continue
                if hasta and g.gasto.fecha > hasta:
                    continue
                if sucursal_id and g.sucursal_id != sucursal_id:
                    continue
                m = g.gasto.moneda or "SIN_MONEDA"
                por_mon.setdefault(m, acc())["gastos"] += _dec(
                    g.gasto.monto)
            # Impuestos (dato explícito del contador).
            for imp in self._impuestos.values():
                if desde and imp.fecha < desde:
                    continue
                if hasta and imp.fecha > hasta:
                    continue
                if producto_id or sucursal_id:
                    continue  # sin dimensión atribuible
                a = por_mon.setdefault(imp.moneda, acc())
                a["impuestos"] += _dec(imp.monto)
                a["tiene_impuestos"] = True

        out = {}
        for mon, a in por_mon.items():
            netos = a["brutos"] - a["descuentos"] - a["devoluciones"]
            bruta = netos - a["cogs"]
            if producto_id:
                # Vista de contribución: gastos, devoluciones e impuestos
                # no son atribuibles por producto (no se prorratean:
                # prorratear inventaría una regla).
                out[mon] = {
                    **a, "netos": netos, "bruta": bruta,
                    "operativa": None, "neta": None,
                    "nota": ("vista por producto: los gastos operativos, "
                             "las devoluciones y los impuestos no son "
                             "atribuibles por producto y no se prorratean; "
                             "la utilidad operativa/neta no es calculable "
                             "aquí"),
                }
            else:
                operativa = bruta - a["gastos"]
                neta = (operativa - a["impuestos"]
                        if a["tiene_impuestos"] else None)
                nota = None
                if sucursal_id:
                    nota = ("vista por sucursal: las devoluciones y los "
                            "impuestos no tienen dimensión de sucursal; "
                            "la utilidad neta no es calculable aquí")
                out[mon] = {**a, "netos": netos, "bruta": bruta,
                            "operativa": operativa, "neta": neta,
                            "nota": nota}
        return out

    @staticmethod
    def _formatear(mon: str, a: dict) -> dict:
        netos, bruta = a["netos"], a["bruta"]
        operativa, neta = a["operativa"], a["neta"]
        out = {
            "moneda": mon,
            "ingresos_brutos": _str_monto(a["brutos"]),
            "descuentos": _str_monto(a["descuentos"]),
            "devoluciones": _str_monto(a["devoluciones"]),
            "ingresos_netos": _str_monto(netos),
            "ingresos": _str_monto(netos),  # alias de compatibilidad
            "cogs": _str_monto(a["cogs"]),
            "utilidad_bruta": _str_monto(bruta),
            "gastos_operativos": _str_monto(a["gastos"]),
            "gastos": _str_monto(a["gastos"]),  # alias de compatibilidad
            "utilidad_operativa": (_str_monto(operativa)
                                   if operativa is not None
                                   else DATO_INSUFICIENTE),
            "impuestos": (_str_monto(a["impuestos"])
                          if a["tiene_impuestos"]
                          else DATO_INSUFICIENTE),
            "utilidad_neta": (_str_monto(neta) if neta is not None
                              else DATO_INSUFICIENTE),
            "margen_bruto_pct": _pct(bruta, netos),
            "margen_operativo_pct": _pct(operativa, netos),
            "margen_neto_pct": _pct(neta, netos),
        }
        if a.get("nota"):
            out["nota"] = a["nota"]
        return out

    def _convertir(self, crudo: dict, destino: str, tasa: str,
                   fecha_tasa: str) -> dict:
        """Convierte el P&L a `destino` con tasa+fecha explícitas."""
        ag: dict[str, Decimal] = {}
        origenes = []
        notas = []
        for mon, a in crudo.items():
            f = self._formatear(mon, a)
            origenes.append(mon)
            if f.get("nota"):
                notas.append(f["nota"])
            for k in _CAMPOS_CONVERTIBLES:
                v = f[k]
                if v == DATO_INSUFICIENTE:
                    continue
                # Misma moneda: identidad (la tasa solo convierte
                # monedas distintas; convertir USD->USD no divide).
                conv = v if mon == destino else convertir(
                    v, mon, destino, tasa)
                ag[k] = ag.get(k, Decimal("0")) + _dec(conv)
        netos = ag.get("ingresos_netos", Decimal("0"))
        bruta = ag.get("utilidad_bruta", Decimal("0"))
        op = ag.get("utilidad_operativa")
        ne = ag.get("utilidad_neta")
        out = {
            "moneda": destino,
            **{k: _str_monto(ag.get(k, Decimal("0")))
               for k in _CAMPOS_CONVERTIBLES},
            "margen_bruto_pct": _pct(bruta, netos),
            "margen_operativo_pct": _pct(op, netos),
            "margen_neto_pct": _pct(ne, netos),
            "conversion": {
                "tasa": tasa,
                "fecha_tasa": fecha_tasa,
                "monedas_origen": sorted(origenes),
                "nota": ("conversión con tasa explícita; los importes "
                         "originales por moneda se conservan en la "
                         "vista sin convertir"),
            },
        }
        # Campos no computables por falta de datos: honestidad.
        if "utilidad_operativa" not in ag:
            out["utilidad_operativa"] = DATO_INSUFICIENTE
        if "impuestos" not in ag:
            out["impuestos"] = DATO_INSUFICIENTE
        if "utilidad_neta" not in ag:
            out["utilidad_neta"] = DATO_INSUFICIENTE
        if notas:
            out["nota"] = " | ".join(sorted(set(notas)))
        return {destino: out}


@dataclass
class AlertaMargen:
    tipo: str  # margen_bajo | venta_bajo_costo
    venta_id: str
    producto_id: str
    detalle: str

    def to_dict(self):
        return {"tipo": self.tipo, "venta_id": self.venta_id,
                "producto_id": self.producto_id, "detalle": self.detalle}


def evaluar_margenes(libro: LibroFinanzas, umbral_pct: str = "15") -> list[AlertaMargen]:
    """Margin Guard: margen bajo el umbral y ventas bajo costo."""
    out: list[AlertaMargen] = []
    umbral = _dec(umbral_pct)
    for v in libro._ventas.values():
        total = v.total()
        cogs = _dec(v.cogs_total)
        if total > 0:
            margen = (total - cogs) / total * 100
            if margen < umbral:
                out.append(AlertaMargen(
                    tipo="margen_bajo", venta_id=v.venta_id, producto_id="*",
                    detalle=f"venta {v.venta_id}: margen {round(margen, 1)}% "
                            f"< umbral {umbral}%"))
        for lin in v.lineas:
            costo_u = _dec(libro.costo.costo_promedio(
                lin.producto_id, lin.almacen_id or "principal", v.moneda))
            if costo_u > 0 and _dec(lin.precio_unitario) < costo_u:
                out.append(AlertaMargen(
                    tipo="venta_bajo_costo", venta_id=v.venta_id,
                    producto_id=lin.producto_id,
                    detalle=f"{lin.producto_id}: vendido a "
                            f"{lin.precio_unitario} bajo costo {costo_u}"))
    return out
