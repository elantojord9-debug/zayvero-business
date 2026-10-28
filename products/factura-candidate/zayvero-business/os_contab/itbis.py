"""BLOQUE CONTABILIDAD — Preparación ITBIS / NCF / e-CF (República Dominicana).

AVISO EN CADA SALIDA: "preparación local, no certificado DGII". El OS
organiza los números para el contador; NO declara impuestos ni emite
comprobantes fiscales válidos.

- ITBIS: la tasa es un PARÁMETRO EXPLÍCITO del contador (por defecto la
  tasa general de referencia "18", documentada como referencia vigente
  a 2026 — verifíquela con su contador/DGII). Débito = ITBIS de ventas
  no exentas; crédito = ITBIS de compras con comprobante; a pagar =
  débito − crédito (negativo = saldo a favor).
- NCF: secuencias SINTÉTICAS por tipo de comprobante, con control de
  rangos (desde/hasta, sin solapes, sin reutilizar). Todo NCF emitido
  aquí lleva la marca "SINTÉTICO — no válido ante la DGII".
- reporte_contador(): junta ITBIS + NCF emitidos por tipo en un resumen
  listo para llevar al contador.

Tipos NCF soportados (nomenclatura DGII): B01 crédito fiscal, B02
consumo, B03 débito, B04 crédito, B11 informal, B12 único de ingresos,
B13 gastos menores, B14 regímenes especiales, B15 gubernamental,
B16 exportaciones.

Caso trabajado (ver tests, tasa 18):
  ventas: 100_000 DOP (no exenta) + 20_000 DOP exenta.
  compras: 50_000 DOP con ITBIS.
  débito = 18_000; crédito = 9_000; a pagar = 9_000.
  rango B02 1–100, emitir 2 -> B0200000001, B0200000002; disponibles 98.
"""
from __future__ import annotations

import re
import sys
import os
import threading
from dataclasses import dataclass, field
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from .errores import ErrorContab, NoEncontrado  # noqa: E402

AVISO_NO_CERTIFICADO = (
    "PREPARACIÓN LOCAL — NO CERTIFICADO DGII: estos números organizan "
    "la información para su contador. El OS no declara impuestos ni "
    "emite comprobantes fiscales válidos; valide todo antes de declarar.")

AVISO_NCF_SINTETICO = (
    "NCF SINTÉTICO — secuencia de prueba interna, NO válida ante la DGII.")

TASA_REFERENCIA = "18"  # tasa general de referencia en RD (verificar vigencia)
TASA_NOTA = ("tasa provista como parámetro; por defecto la tasa general "
             "de referencia en RD (18 % a 2026). Confírmela con su "
             "contador/DGII: el OS no certifica tasas fiscales.")

TIPOS_NCF = {
    "B01": "Factura de Crédito Fiscal",
    "B02": "Factura de Consumo",
    "B03": "Nota de Débito",
    "B04": "Nota de Crédito",
    "B11": "Comprobante de Compras a Proveedores Informales",
    "B12": "Registro Único de Ingresos",
    "B13": "Comprobante de Gastos Menores",
    "B14": "Comprobante de Regímenes Especiales",
    "B15": "Comprobante Gubernamental",
    "B16": "Factura de Exportación",
}
_TIPO_RE = re.compile(r"^B(0[1-4]|1[1-6])$")


class ErrorITBIS(ErrorContab):
    pass


def _en_rango(fecha: str, desde: str | None, hasta: str | None) -> bool:
    f = (fecha or "")[:10]
    return (not desde or f >= desde) and (not hasta or f <= hasta)


def calcular_itbis(ventas: list[dict], compras: list[dict],
                   tasa_pct: str = TASA_REFERENCIA,
                   desde: str | None = None, hasta: str | None = None) -> dict:
    """ITBIS débito/crédito por moneda, aritmética exacta con Decimal.

    ventas/compras: [{monto, moneda, fecha, exento(bool)}].
    """
    tasa = _dec(tasa_pct)
    if tasa < 0 or tasa > 100:
        raise ErrorITBIS(f"tasa_pct fuera de rango (0-100): {tasa_pct}")
    por_mon: dict[str, dict[str, Decimal]] = {}

    def acc(mon):
        return por_mon.setdefault(mon, {"base_ventas": Decimal("0"),
                                        "debito": Decimal("0"),
                                        "base_compras": Decimal("0"),
                                        "credito": Decimal("0")})

    for v in ventas or []:
        if not _en_rango(str(v.get("fecha", "")), desde, hasta):
            continue
        mon = str(v.get("moneda") or "")
        if not mon:
            raise ErrorITBIS("venta sin moneda: no se puede agrupar")
        monto = _dec(str(v.get("monto", "0")))
        a = acc(mon)
        a["base_ventas"] += monto
        if not v.get("exento"):
            a["debito"] += monto * tasa / 100
    for c in compras or []:
        if not _en_rango(str(c.get("fecha", "")), desde, hasta):
            continue
        mon = str(c.get("moneda") or "")
        if not mon:
            raise ErrorITBIS("compra sin moneda: no se puede agrupar")
        monto = _dec(str(c.get("monto", "0")))
        a = acc(mon)
        a["base_compras"] += monto
        if not c.get("exento"):
            a["credito"] += monto * tasa / 100

    def _s(d: Decimal) -> str:
        # Como profit._str_monto: sin notación científica ni ceros de más.
        q = round(d, 2)
        s = format(q.normalize(), "f")
        return s if s not in ("", "-") else "0"

    detalle = {}
    for mon in sorted(por_mon):
        a = por_mon[mon]
        a_pagar = a["debito"] - a["credito"]
        detalle[mon] = {
            "moneda": mon,
            "base_ventas": _s(a["base_ventas"]),
            "itbis_debito_ventas": _s(a["debito"]),
            "base_compras": _s(a["base_compras"]),
            "itbis_credito_compras": _s(a["credito"]),
            "itbis_a_pagar": _s(a_pagar),
            "saldo_a_favor": _s(-a_pagar) if a_pagar < 0 else "0",
        }
    return {"ok": True,
            "tasa_pct": str(tasa),
            "tasa_nota": TASA_NOTA,
            "desde": desde, "hasta": hasta,
            "por_moneda": detalle,
            "aviso": AVISO_NO_CERTIFICADO}


@dataclass
class RangoNCF:
    tipo: str
    desde: int
    hasta: int
    siguiente: int
    emitidos: list = field(default_factory=list)  # [{ncf, concepto}]

    def disponibles(self) -> int:
        return max(0, self.hasta - self.siguiente + 1)

    def to_dict(self) -> dict:
        return {"tipo": self.tipo,
                "descripcion": TIPOS_NCF[self.tipo],
                "desde": self.desde, "hasta": self.hasta,
                "siguiente": self.siguiente,
                "disponibles": self.disponibles(),
                "emitidos_n": len(self.emitidos),
                "aviso": AVISO_NCF_SINTETICO}


class RegistroNCF:
    """Secuencias NCF SINTÉTICAS con control de rangos, por empresa."""

    def __init__(self, company_id: str):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        self.company_id = company_id
        self._lock = threading.RLock()
        self._rangos: dict[str, RangoNCF] = {}

    def crear_rango(self, tipo: str, desde: int, hasta: int) -> dict:
        tipo = (tipo or "").upper().strip()
        if not _TIPO_RE.match(tipo) or tipo not in TIPOS_NCF:
            raise ErrorITBIS(
                f"tipo NCF inválido: {tipo} "
                f"(válidos: {', '.join(sorted(TIPOS_NCF))})")
        try:
            d, h = int(desde), int(hasta)
        except (TypeError, ValueError):
            raise ErrorITBIS("desde/hasta deben ser enteros")
        if d <= 0 or h < d:
            raise ErrorITBIS(
                f"rango inválido: desde={desde} hasta={hasta}")
        with self._lock:
            if tipo in self._rangos:
                raise ErrorITBIS(
                    f"ya existe un rango para {tipo} "
                    f"({self._rangos[tipo].desde}-"
                    f"{self._rangos[tipo].hasta}); cierre o amplíe el "
                    f"existente antes de crear otro")
            self._rangos[tipo] = RangoNCF(tipo=tipo, desde=d, hasta=h,
                                          siguiente=d)
            return {**self._rangos[tipo].to_dict(), "ok": True,
                    "sintetico": True}

    def emitir(self, tipo: str, concepto: str = "") -> dict:
        tipo = (tipo or "").upper().strip()
        with self._lock:
            r = self._rangos.get(tipo)
            if r is None:
                raise NoEncontrado(
                    f"sin rango NCF para {tipo}: cree el rango primero")
            if r.siguiente > r.hasta:
                raise ErrorITBIS(
                    f"rango {tipo} agotado ({r.desde}-{r.hasta}): "
                    f"solicite un rango nuevo")
            ncf = f"{tipo}{r.siguiente:08d}"
            r.emitidos.append({"ncf": ncf,
                               "concepto": (concepto or "").strip(),
                               "estado": "emitido",
                               "sintetico": True})
            r.siguiente += 1
            return {"ok": True, "ncf": ncf, "tipo": tipo,
                    "concepto": (concepto or "").strip(),
                    "sintetico": True, "aviso": AVISO_NCF_SINTETICO,
                    "disponibles_restantes": r.disponibles()}

    def rangos(self) -> list[dict]:
        with self._lock:
            return [r.to_dict() for _, r in sorted(self._rangos.items())]

    def emitidos(self, tipo: str | None = None) -> list[dict]:
        with self._lock:
            out = []
            for t in sorted(self._rangos):
                if tipo and t != tipo.upper():
                    continue
                out.extend(self._rangos[t].emitidos)
            return out

    def documento(self, numero: str) -> dict:
        """Vista de documento fiscal (memoria), marcada NO CERTIFICADA."""
        numero = (numero or "").strip().upper()
        with self._lock:
            for r in self._rangos.values():
                for e in r.emitidos:
                    if e["ncf"] == numero:
                        return {
                            "ok": True,
                            "documento": "COMPROBANTE FISCAL — "
                                         "DOCUMENTO DE PRUEBA INTERNA",
                            "certificacion_dgii": "NO CERTIFICADO DGII",
                            "numero": e["ncf"],
                            "tipo": e["ncf"][:3],
                            "descripcion_tipo":
                                TIPOS_NCF[e["ncf"][:3]],
                            "concepto": e.get("concepto", ""),
                            "estado": e.get("estado", "emitido"),
                            "motivo_anulacion":
                                e.get("motivo_anulacion", ""),
                            "sintetico": True,
                            "aviso": AVISO_NCF_SINTETICO,
                            "aviso_fiscal": AVISO_NO_CERTIFICADO,
                        }
            raise NoEncontrado(f"NCF inexistente: {numero}")

    def anular(self, numero: str, motivo: str = "") -> dict:
        """Anula un NCF emitido (memoria). El número queda reservado."""
        numero = (numero or "").strip().upper()
        if not numero:
            raise ErrorITBIS("numero es obligatorio")
        with self._lock:
            for r in self._rangos.values():
                for e in r.emitidos:
                    if e["ncf"] == numero:
                        if e.get("estado") == "anulado":
                            raise ErrorITBIS(
                                f"el NCF {numero} ya está anulado")
                        e["estado"] = "anulado"
                        e["motivo_anulacion"] = (motivo or "").strip()
                        return {"ok": True, "ncf": e["ncf"],
                                "tipo": e["ncf"][:3],
                                "concepto": e.get("concepto", ""),
                                "estado": "anulado",
                                "motivo_anulacion": e["motivo_anulacion"],
                                "sintetico": True,
                                "aviso": AVISO_NCF_SINTETICO}
            raise NoEncontrado(f"NCF inexistente: {numero}")


def reporte_contador(itbis: dict, ncf: RegistroNCF) -> dict:
    """Resumen para llevar al contador: ITBIS + NCF por tipo."""
    por_tipo: dict[str, int] = {}
    for e in ncf.emitidos():
        t = e["ncf"][:3]
        por_tipo[t] = por_tipo.get(t, 0) + 1
    return {"ok": True,
            "titulo": "Resumen para el contador (organización interna)",
            "itbis": itbis,
            "ncf_por_tipo": {t: {"descripcion": TIPOS_NCF[t],
                                 "emitidos": n}
                             for t, n in sorted(por_tipo.items())},
            "ncf_rangos": ncf.rangos(),
            "aviso": AVISO_NO_CERTIFICADO,
            "aviso_ncf": AVISO_NCF_SINTETICO}
