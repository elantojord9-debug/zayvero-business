"""BLOQUE 6 — Aprobaciones: entidades y reglas puras (sin PG).

`requiere_aprobacion()` decide si una operación sensible necesita el
paso humano. Es pura: se prueba sin base de datos.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

TIPOS = ("cambio_precio", "devolucion_venta", "devolucion_compra",
         "aprobacion_financiera",
         # BLOQUE A SEGURIDAD (2026-10-13): borrado de cuenta (soft-delete
         # de la empresa). Siempre requiere aprobación humana.
         "eliminacion_cuenta",
         # BLOQUE IA (2026-10-13, P1c): propuesta del Autopilot que
         # requiere aprobación humana explícita antes de registrarse
         # como ejecutada. Nunca ejecuta nada por sí misma.
         "ia_propuesta")
ESTADOS = ("pendiente_aprobacion", "aprobada", "rechazada", "ejecutada",
           "cancelada")


class ErrorAprobacion(Exception):
    """Base de errores de aprobaciones."""


def validar_tipo(tipo: str) -> str:
    t = (tipo or "").strip()
    if t not in TIPOS:
        raise ErrorAprobacion(
            f"tipo inválido: {tipo!r} (use {', '.join(TIPOS)})")
    return t


def _dec(valor, campo: str) -> Decimal:
    try:
        return Decimal(str(valor))
    except (InvalidOperation, ValueError, TypeError):
        raise ErrorAprobacion(f"{campo} debe ser numérico: {valor!r}")


@dataclass
class ConfigAprobaciones:
    """Umbrales que disparan la aprobación humana.

    DEFAULTS DE DEMO (sintéticos, no calibrados con datos reales):
    - cambio de precio > 25 % vs vigente -> requiere aprobación.
    - devolución / operación financiera >= 5000 (moneda de la
      operación, sin conversión: simplificación de demo) -> requiere
      aprobación.
    La empresa los personaliza vía POST /api/v1/os/catalogo/config.
    """
    umbral_cambio_precio: Decimal = Decimal("0.25")
    umbral_devolucion_monto: Decimal = Decimal("5000")
    moneda_defecto: str = "DOP"
    origen: str = "demo_por_defecto"  # demo_por_defecto | personalizado

    @classmethod
    def desde_fila(cls, fila: dict | None) -> "ConfigAprobaciones":
        if not fila:
            return cls()
        return cls(
            umbral_cambio_precio=_dec(
                fila.get("umbral_cambio_precio", "0.25"),
                "umbral_cambio_precio"),
            umbral_devolucion_monto=_dec(
                fila.get("umbral_devolucion_monto", "5000"),
                "umbral_devolucion_monto"),
            moneda_defecto=str(fila.get("moneda_defecto") or "DOP"),
            origen=("personalizado" if fila.get("personalizada")
                    else "demo_por_defecto"))


@dataclass
class SolicitudAprobacion:
    company_id: str
    aprobacion_id: str
    tipo: str
    estado: str = "pendiente_aprobacion"
    solicitante: str = ""
    payload: dict = field(default_factory=dict)
    monto: Decimal | None = None
    moneda: str | None = None
    motivo_solicitud: str = ""

    def validar(self):
        if not (self.company_id or "").strip():
            raise ErrorAprobacion("company_id no puede estar vacío")
        if not (self.aprobacion_id or "").strip():
            raise ErrorAprobacion("aprobacion_id no puede estar vacío")
        validar_tipo(self.tipo)
        if self.estado not in ESTADOS:
            raise ErrorAprobacion(f"estado inválido: {self.estado!r}")


def requiere_aprobacion(tipo: str, cfg: ConfigAprobaciones, *,
                        precio_vigente=None, precio_nuevo=None,
                        monto=None) -> tuple[bool, str]:
    """(requiere, motivo). Pura y comprobable.

    - cambio_precio: |nuevo-vigente|/vigente > umbral_cambio_precio.
      Sin precio vigente previo (primer precio) -> no requiere
      (no hay contra qué comparar; documentado).
    - devolucion_venta / devolucion_compra / aprobacion_financiera:
      monto >= umbral_devolucion_monto. Sin monto -> se requiere
      (fail-closed: ante la duda, que lo vea un humano).
    - eliminacion_cuenta (BLOQUE A SEGURIDAD): siempre requiere
      aprobación humana, sin umbral.
    """
    validar_tipo(tipo)
    if tipo == "eliminacion_cuenta":
        # BLOQUE A SEGURIDAD: el borrado de cuenta SIEMPRE pasa por un
        # humano (no hay umbral que lo exima).
        return True, "eliminación de cuenta: siempre requiere aprobación humana"
    if tipo == "cambio_precio":
        if precio_vigente is None or precio_nuevo is None:
            return False, "sin precio vigente previo: no hay base de comparación"
        pv = _dec(precio_vigente, "precio_vigente")
        pn = _dec(precio_nuevo, "precio_nuevo")
        if pv <= 0:
            return False, "precio vigente no positivo: no hay base de comparación"
        delta = abs(pn - pv) / pv
        if delta > cfg.umbral_cambio_precio:
            return True, (
                f"cambio de {delta * 100:.1f} % supera el umbral de "
                f"{cfg.umbral_cambio_precio * 100:.0f} % "
                f"({cfg.origen})")
        return False, (
            f"cambio de {delta * 100:.1f} % dentro del umbral de "
            f"{cfg.umbral_cambio_precio * 100:.0f} %")
    if monto is None:
        return True, "monto desconocido: se requiere aprobación (fail-closed)"
    m = _dec(monto, "monto")
    if m >= cfg.umbral_devolucion_monto:
        return True, (
            f"monto {m} >= umbral {cfg.umbral_devolucion_monto} "
            f"({cfg.origen})")
    return False, (
        f"monto {m} bajo el umbral {cfg.umbral_devolucion_monto}")
