"""BLOQUE CONTABILIDAD — Conciliación bancaria SIMULADA.

REGLA DURA: el sistema NO se conecta a ningún banco. Las líneas de
extracto se cargan MANUALMENTE desde un CSV que pega el usuario; el
cotejo es contra los movimientos del libro (ventas/gastos registrados).
TODA salida lleva `"simulado": True` y el aviso correspondiente.

Estados por línea de extracto:
- conciliado:   hay contraparte en el libro (misma moneda, mismo monto,
                fecha a ≤1 día; la referencia, si ambas la traen, suma
                confianza pero no es obligatoria).
- discrepancia: misma referencia en el libro pero monto distinto, o
                monto igual pero fechas a >7 días (posible error).
- pendiente:    sin contraparte encontrada (solo en extracto o solo en
                el libro — ambas direcciones se reportan).

El operador puede marcar manualmente una línea (p. ej. tras revisar el
banco real) con `marcar()`; la marca manual queda registrada con actor
y nota, y también dice "simulado".

Caso trabajado (ver tests/test_contab_conciliacion.py):
  libro: venta v1 900 DOP el 2026-10-05 (ref "v1"); gasto g1 200 DOP el
  2026-10-06 (ref comprobante "FAC-9"); gasto g2 500 DOP sin extracto.
  extracto CSV:
    L1 2026-10-05, "cobro cliente", 900, DOP, v1    -> conciliado
    L2 2026-10-06, "pago proveedor", -200, DOP, FAC-9 -> conciliado
    L3 2026-10-07, "cargo banco", -50, DOP, ""      -> pendiente
    L4 2026-10-05, "cobro cliente", 850, DOP, v1    -> discrepancia
      (misma referencia, distinto monto)
"""
from __future__ import annotations

import csv
import io
import sys
import os
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402
from .errores import ErrorContab, NoEncontrado  # noqa: E402

AVISO_SIMULADO = (
    "CONCILIACIÓN SIMULADA: las líneas de extracto se cargaron manualmente "
    "desde un CSV; el sistema NO se conecta a ningún banco, NO valida "
    "saldos reales y NO mueve dinero. Úsela como lista de trabajo para "
    "revisar contra su banco real.")

ESTADOS = ("conciliado", "pendiente", "discrepancia")
TOLERANCIA_DIAS = 1       # fecha a ≤1 día para conciliar automático
DISCREPANCIA_DIAS = 7     # >7 días con mismo monto => posible error


class ErrorConciliacion(ErrorContab):
    pass


def _parse_fecha(f: str) -> datetime:
    return datetime.strptime(f[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


@dataclass
class LineaExtracto:
    linea_id: str
    fecha: str          # YYYY-MM-DD
    descripcion: str
    monto: str          # + entrada, − salida
    moneda: str
    referencia: str = ""
    estado: str = "pendiente"
    contraparte_id: str = ""   # mov_id del libro (si conciliado)
    marca_manual: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"linea_id": self.linea_id, "fecha": self.fecha,
                "descripcion": self.descripcion, "monto": str(_dec(self.monto)),
                "moneda": self.moneda, "referencia": self.referencia,
                "estado": self.estado,
                "contraparte_id": self.contraparte_id,
                "marca_manual": dict(self.marca_manual)}


@dataclass
class MovimientoLibro:
    """Movimiento del libro para cotejar: ventas (+) y gastos (−)."""
    mov_id: str
    fecha: str          # YYYY-MM-DD
    descripcion: str
    monto: str
    moneda: str
    referencia: str = ""


def movimientos_desde_libro(libro) -> list[MovimientoLibro]:
    """Deriva movimientos cotejables del LibroFinanzas (solo lectura)."""
    movs: list[MovimientoLibro] = []
    for v in libro._ventas.values():
        movs.append(MovimientoLibro(
            mov_id=f"venta:{v.venta_id}", fecha=(v.fecha or "")[:10],
            descripcion=f"venta {v.venta_id}", monto=str(v.total()),
            moneda=v.moneda, referencia=v.venta_id))
    fuente = (libro.registro_gastos.todos()
              if getattr(libro, "registro_gastos", None) is not None
              else libro._gastos_fuente())
    for g in fuente:
        movs.append(MovimientoLibro(
            mov_id=f"gasto:{g.gasto.gasto_id}",
            fecha=(g.gasto.fecha or "")[:10],
            descripcion=g.gasto.descripcion or f"gasto {g.gasto.gasto_id}",
            monto=str(-_dec(g.gasto.monto)), moneda=g.gasto.moneda,
            referencia=g.gasto.comprobante or g.gasto.gasto_id))
    return movs


def _cotejar_linea(linea: LineaExtracto,
                   movs: list[MovimientoLibro]) -> tuple[str, str, dict]:
    """Devuelve (estado, contraparte_id, evidencia)."""
    monto = _dec(linea.monto)
    candidatos = [m for m in movs
                  if (m.moneda or "") == (linea.moneda or "")
                  and _dec(m.monto) == monto]
    # 1) referencia exacta + monto => conciliado (fecha a ≤1 día exige
    #    la evidencia; si difiere mucho, es discrepancia).
    if linea.referencia:
        for m in candidatos:
            if m.referencia == linea.referencia:
                dias = abs((_parse_fecha(linea.fecha)
                            - _parse_fecha(m.fecha)).days)
                if dias <= TOLERANCIA_DIAS:
                    return ("conciliado", m.mov_id,
                            {"regla": "referencia+monto+fecha≤1d",
                             "dias_diferencia": dias})
                return ("discrepancia", m.mov_id,
                        {"regla": "misma referencia, fecha difiere",
                         "dias_diferencia": dias,
                         "nota": "revisar: ¿mismo movimiento en otra "
                                  "fecha o cargo duplicado?"})
        # Misma referencia pero OTRO monto en el libro => discrepancia.
        for m in movs:
            if ((m.moneda or "") == (linea.moneda or "")
                    and m.referencia == linea.referencia
                    and _dec(m.monto) != monto):
                return ("discrepancia", m.mov_id,
                        {"regla": "misma referencia, distinto monto",
                         "monto_extracto": str(monto),
                         "monto_libro": str(_dec(m.monto)),
                         "nota": "revisar: posible error de monto o "
                                  "cargo parcial"})
    # 2) monto+moneda+fecha≤1d sin referencia.
    for m in candidatos:
        dias = abs((_parse_fecha(linea.fecha) - _parse_fecha(m.fecha)).days)
        if dias <= TOLERANCIA_DIAS:
            return ("conciliado", m.mov_id,
                    {"regla": "monto+moneda+fecha≤1d (sin referencia)",
                     "dias_diferencia": dias})
    # 3) monto igual pero fecha lejana => posible error.
    for m in candidatos:
        dias = abs((_parse_fecha(linea.fecha) - _parse_fecha(m.fecha)).days)
        if dias > DISCREPANCIA_DIAS:
            return ("discrepancia", m.mov_id,
                    {"regla": "mismo monto, fecha difiere >7d",
                     "dias_diferencia": dias,
                     "nota": "revisar: ¿movimiento viejo o cargo nuevo?"})
    return ("pendiente", "",
            {"regla": "sin contraparte en el libro",
             "nota": "puede ser un movimiento no registrado o del banco "
                      "real no cargado en el OS"})


def _parsear_csv_extracto(texto_csv: str) -> list[dict]:
    """Valida y normaliza el CSV de extracto.

    Devuelve una fila dict por línea: fecha, descripcion, monto (str),
    moneda, referencia. Lanza ErrorConciliacion con los mismos mensajes
    que cargar_csv. (Extraído para reutilizar en el registro PG sin
    duplicar las reglas de validación.)
    """
    if not (texto_csv or "").strip():
        raise ErrorConciliacion("el CSV está vacío")
    try:
        filas = list(csv.DictReader(io.StringIO(texto_csv.strip())))
    except Exception as e:
        raise ErrorConciliacion(f"CSV ilegible: {e}")
    esperadas = {"fecha", "descripcion", "monto", "moneda"}
    if not filas or not esperadas.issubset(
            {k.strip() for k in (filas[0].keys() or [])}):
        raise ErrorConciliacion(
            "el CSV debe tener columnas: fecha, descripcion, monto, "
            "moneda (referencia opcional)")
    out = []
    for f in filas:
        try:
            _parse_fecha(f["fecha"].strip())
            _dec(f["monto"].strip())
        except Exception:
            raise ErrorConciliacion(
                f"fila inválida (fecha/monto): {f}")
        out.append({"fecha": f["fecha"].strip()[:10],
                    "descripcion": (f.get("descripcion") or "").strip(),
                    "monto": str(_dec(f["monto"].strip())),
                    "moneda": (f.get("moneda") or "").strip().upper(),
                    "referencia": (f.get("referencia") or "").strip()})
    return out


class RegistroConciliacion:
    """Extractos simulados y su cotejo, por empresa."""

    def __init__(self, company_id: str):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        self.company_id = company_id
        self._lock = threading.RLock()
        self._lineas: dict[str, LineaExtracto] = {}
        self._seq = 0

    # ---- carga ----------------------------------------------------------
    def cargar_csv(self, texto_csv: str) -> dict:
        """Carga líneas de extracto desde CSV.

        Columnas: fecha, descripcion, monto, moneda, referencia(opcional).
        fecha en YYYY-MM-DD; monto con signo (+ entrada, − salida).
        """
        filas = _parsear_csv_extracto(texto_csv)
        cargadas = []
        with self._lock:
            for f in filas:
                self._seq += 1
                lid = f"L{self._seq}"
                self._lineas[lid] = LineaExtracto(linea_id=lid, **f)
                cargadas.append(lid)
        return {"simulado": True, "aviso": AVISO_SIMULADO,
                "lineas_cargadas": len(cargadas), "linea_ids": cargadas}

    def lineas(self) -> list[LineaExtracto]:
        with self._lock:
            return [self._lineas[k] for k in sorted(self._lineas)]

    # ---- cotejo ---------------------------------------------------------
    def conciliar(self, movimientos: list[MovimientoLibro]) -> dict:
        """Coteja cada línea contra los movimientos del libro."""
        movs = list(movimientos)
        usadas: set[str] = set()
        detalle = []
        with self._lock:
            for lin in self.lineas():
                if lin.marca_manual:
                    # La marca manual del operador prevalece.
                    detalle.append({**lin.to_dict(),
                                    "evidencia": lin.marca_manual})
                    if lin.contraparte_id:
                        usadas.add(lin.contraparte_id)
                    continue
                estado, contraparte, evidencia = _cotejar_linea(lin, movs)
                if estado == "conciliado":
                    if contraparte in usadas:
                        # La contraparte ya fue usada por otra línea:
                        # posible duplicado en el extracto.
                        estado = "discrepancia"
                        evidencia = {
                            "regla": "contraparte ya conciliada por otra "
                                     "línea",
                            "contraparte_id": contraparte,
                            "nota": "revisar: ¿cargo duplicado en el "
                                     "extracto?"}
                        contraparte = ""
                    else:
                        usadas.add(contraparte)
                # En "discrepancia", contraparte_id es la candidata
                # posible (informativa); no se marca como usada.
                lin.estado, lin.contraparte_id = estado, contraparte or ""
                detalle.append({**lin.to_dict(), "evidencia": evidencia})
        # Movimientos del libro sin contraparte en el extracto.
        sin_extracto = [m.mov_id for m in movs if m.mov_id not in usadas]
        resumen: dict[str, int] = {}
        for d in detalle:
            resumen[d["estado"]] = resumen.get(d["estado"], 0) + 1
        return {"simulado": True, "aviso": AVISO_SIMULADO,
                "lineas": detalle, "resumen": resumen,
                "movimientos_libro_sin_extracto": sin_extracto,
                "nota": ("los 'pendiente'/'discrepancia' son lista de "
                         "trabajo: revíselos contra su banco real")}

    # ---- marca manual ---------------------------------------------------
    def marcar(self, linea_id: str, estado: str, actor: str = "",
               nota: str = "") -> dict:
        if estado not in ESTADOS:
            raise ErrorConciliacion(
                f"estado inválido: {estado} (válidos: {', '.join(ESTADOS)})")
        with self._lock:
            lin = self._lineas.get(linea_id)
            if lin is None:
                raise NoEncontrado(f"línea inexistente: {linea_id}")
            lin.marca_manual = {"estado_manual": estado,
                                "actor": actor or "(sin actor)",
                                "nota": nota or "",
                                "simulado": True}
            lin.estado = estado
            return {"simulado": True, "aviso": AVISO_SIMULADO,
                    "linea": lin.to_dict()}
