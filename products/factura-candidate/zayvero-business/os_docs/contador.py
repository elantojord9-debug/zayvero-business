"""LOTE 7 — Contador IA asistido: conciliación bancaria y asientos.

- importar_extracto(): CSV ficticio (fecha, descripcion, monto, tipo).
- conciliar(): compara movimientos con CxC/CxP de Tesorería.
  Empareja por monto exacto y fecha cercana (±7 días). Confianza:
  alta (monto exacto + 1 solo candidato + fecha ≤3 días),
  media (monto exacto, fecha ≤7 días o 2 candidatos),
  baja (monto aproximado o >2 candidatos) -> siempre revisión humana.
- NINGUNA operación ambigua se aprueba sola: toda propuesta nace
  "pendiente_aprobacion". aprobar() la ejecuta un humano (registra el
  cobro/pago real en Tesorería).
- generar_asiento(): Debe/Haber de la propuesta aprobada.
  Sin afirmar cumplimiento fiscal: son asientos de gestión interna.
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


def _parse(f: str):
    return datetime.strptime(f[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)


@dataclass
class MovimientoBanco:
    fecha: str
    descripcion: str
    monto: str
    tipo: str  # credito | debito
    moneda: str = "DOP"

    def to_dict(self):
        return {"fecha": self.fecha, "descripcion": self.descripcion,
                "monto": self.monto, "tipo": self.tipo, "moneda": self.moneda}


def importar_extracto(csv_texto: str) -> list[MovimientoBanco]:
    """CSV con columnas: fecha,descripcion,monto,tipo (credito|debito).
    Columna opcional: moneda (ISO 4217, por defecto DOP)."""
    from oschema.contract import require_iso_currency
    movs = []
    for i, row in enumerate(csv.DictReader(io.StringIO(csv_texto))):
        for col in ("fecha", "descripcion", "monto", "tipo"):
            if col not in row or not row[col]:
                raise ValueError(f"fila {i+1}: falta columna {col}")
        if row["tipo"] not in ("credito", "debito"):
            raise ValueError(f"fila {i+1}: tipo inválido {row['tipo']}")
        moneda = (row.get("moneda") or "DOP").strip().upper()
        require_iso_currency(moneda)
        _parse(row["fecha"])
        _dec(row["monto"])
        movs.append(MovimientoBanco(row["fecha"], row["descripcion"],
                                    str(_dec(row["monto"])), row["tipo"],
                                    moneda))
    return movs


@dataclass
class PropuestaConciliacion:
    propuesta_id: str
    movimiento: MovimientoBanco
    cuenta_id: str | None
    tipo_cuenta: str | None  # cxc | cxp
    confianza: str           # alta | media | baja
    estado: str = "pendiente_aprobacion"
    detalle: str = ""

    def to_dict(self):
        d = {"propuesta_id": self.propuesta_id,
             "movimiento": self.movimiento.to_dict(),
             "cuenta_id": self.cuenta_id, "tipo_cuenta": self.tipo_cuenta,
             "confianza": self.confianza, "estado": self.estado,
             "detalle": self.detalle}
        return d


def conciliar(movimientos: list[MovimientoBanco], tesoreria,
              ventana_dias: int = 7) -> list[PropuestaConciliacion]:
    """Genera propuestas. Lo ambiguo queda en confianza baja.
    Solo empareja cuentas en la MISMA moneda (nunca suma DOP con USD)."""
    out = []
    for i, m in enumerate(movimientos):
        candidatos = []
        if m.tipo == "credito":
            universo = [(c.cuenta_id, "cxc", c) for c in tesoreria._cxc.values()
                        if c.pendiente() > 0 and c.moneda == m.moneda]
        else:
            universo = [(c.cuenta_id, "cxp", c) for c in tesoreria._cxp.values()
                        if c.pendiente() > 0 and c.moneda == m.moneda]
        for cid, tcuenta, c in universo:
            if abs(_dec(m.monto) - c.pendiente()) > Decimal("0.01"):
                continue
            delta = abs((_parse(m.fecha) - _parse(c.vencimiento)).days)
            if delta <= ventana_dias:
                candidatos.append((cid, tcuenta, delta))
        pid = f"conc-{i}"
        if not candidatos:
            out.append(PropuestaConciliacion(
                pid, m, None, None, "baja",
                detalle="sin cuenta coincidente: requiere revisión manual"))
        elif len(candidatos) == 1 and candidatos[0][2] <= 3:
            cid, tcuenta, _ = candidatos[0]
            out.append(PropuestaConciliacion(
                pid, m, cid, tcuenta, "alta",
                detalle=f"monto exacto y fecha cercana con {cid}"))
        else:
            cid, tcuenta, _ = candidatos[0]
            out.append(PropuestaConciliacion(
                pid, m, cid, tcuenta, "media" if len(candidatos) <= 2 else "baja",
                detalle=f"{len(candidatos)} candidatos posibles; "
                        f"se sugiere {cid}, verificar"))
    return out


@dataclass
class Asiento:
    """Asiento de gestión interna (NO es declaración fiscal)."""
    descripcion: str
    lineas: list = field(default_factory=list)  # [{cuenta, debe, haber}]

    def validar(self):
        debe = sum((_dec(l["debe"]) for l in self.lineas), Decimal("0"))
        haber = sum((_dec(l["haber"]) for l in self.lineas), Decimal("0"))
        if abs(debe - haber) > Decimal("0.01"):
            raise ValueError(f"asiento descuadrado: debe {debe} != haber {haber}")

    def to_dict(self):
        return {"descripcion": self.descripcion, "lineas": self.lineas}


def generar_asiento(prop: PropuestaConciliacion) -> Asiento:
    if prop.cuenta_id is None:
        raise ValueError("sin cuenta no hay asiento")
    m = _dec(prop.movimiento.monto)
    if prop.tipo_cuenta == "cxc":
        lineas = [{"cuenta": "Caja", "debe": str(m), "haber": "0"},
                  {"cuenta": "CxC clientes", "debe": "0", "haber": str(m)}]
    else:
        lineas = [{"cuenta": "CxP proveedores", "debe": str(m), "haber": "0"},
                  {"cuenta": "Caja", "debe": "0", "haber": str(m)}]
    a = Asiento(descripcion=f"Conciliación {prop.propuesta_id} "
                            f"({prop.cuenta_id})", lineas=lineas)
    a.validar()
    return a


class MesaContador:
    """Bandeja de propuestas: el humano aprueba, el sistema ejecuta."""

    def __init__(self, tesoreria):
        self.tesoreria = tesoreria
        self._lock = threading.RLock()
        self._props: dict[str, PropuestaConciliacion] = {}

    def cargar(self, propuestas: list[PropuestaConciliacion]):
        with self._lock:
            for p in propuestas:
                self._props[p.propuesta_id] = p

    def aprobar(self, propuesta_id: str, aprobado_por: str) -> Asiento:
        """Aprobación humana explícita: ejecuta el cobro/pago real."""
        with self._lock:
            p = self._props.get(propuesta_id)
            if p is None:
                raise ValueError(f"propuesta inexistente: {propuesta_id}")
            if p.estado != "pendiente_aprobacion":
                raise ValueError(f"propuesta en estado {p.estado}")
            if p.cuenta_id is None:
                raise ValueError("no se puede aprobar sin cuenta")
            if p.tipo_cuenta == "cxc":
                self.tesoreria.registrar_cobro(p.cuenta_id,
                                               p.movimiento.monto)
            else:
                self.tesoreria.registrar_pago(p.cuenta_id,
                                              p.movimiento.monto)
            p.estado = f"aprobada_por_{aprobado_por}"
            return generar_asiento(p)

    def pendientes(self) -> list[PropuestaConciliacion]:
        with self._lock:
            return [p for p in self._props.values()
                    if p.estado == "pendiente_aprobacion"]
