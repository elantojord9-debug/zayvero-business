"""Proveedores fiscales para la transmisión de e-CF — FASE 1 LOCAL.

=====================================================================
AVISO LEGAL — SIMULACIÓN, NO VÁLIDO ANTE DGII
=====================================================================
- Este módulo NO conecta con la DGII real ni con ningún proveedor
  certificado. El único proveedor disponible es `MockProvider`, un
  simulador LOCAL y OFFLINE con respuestas SINTÉTICAS y deterministas.
- Modo por defecto: "disabled". La transmisión en modo "disabled" está
  BLOQUEADA explícitamente y lanza `ErrorECF`.
- Los modos "sandbox" y "certified_provider" NO están implementados en
  fase 1 (cero conexión real) y lanzan `ErrorECF`.
- NUNCA se simula una firma digital como verdadera: este módulo no
  firma nada ni representa ninguna firma como válida.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone

from . import AVISO_SIMULACION
from .modelos import Comprobante, ErrorECF

ESTADOS_RESPUESTA = ("aceptado", "observado", "rechazado")

_RNC_OBSERVADO = "999999999"  # RNC sintético de prueba: fuerza "observado"


@dataclass
class RespuestaDGII:
    """Respuesta (sintética) de la DGII a una transmisión."""
    estado: str  # "aceptado" | "observado" | "rechazado"
    codigo: str
    mensaje: str
    fecha: str = field(default_factory=lambda: datetime.now(
        timezone.utc).isoformat())

    def __post_init__(self):
        if self.estado not in ESTADOS_RESPUESTA:
            raise ErrorECF(f"estado de respuesta inválido: {self.estado!r}")
        if "SIMULACIÓN" not in (self.mensaje or ""):
            raise ErrorECF(
                "toda respuesta sintética debe incluir 'SIMULACIÓN' "
                "en el mensaje")


class FiscalProvider(ABC):
    """Contrato abstracto de un proveedor fiscal de transmisión e-CF."""
    nombre: str = "abstracto"

    @abstractmethod
    def transmitir(self, xml: str, comprobante: Comprobante) -> RespuestaDGII:
        """Transmite el XML del comprobante y devuelve la respuesta DGII."""


class MockProvider(FiscalProvider):
    """Simulador local offline de la DGII.

    Respuestas SINTÉTICAS y deterministas según el contenido del
    comprobante (NO hay red, NO hay firma, NO hay validez fiscal):
      - total == 0        -> "rechazado"  (código "R-001")
      - RNC receptor "999999999" -> "observado" (código "O-001")
      - cualquier otro caso      -> "aceptado"  (código "A-001")

    Cada respuesta incluye "SIMULACIÓN" en el mensaje.
    """

    nombre = "mock-local"

    def transmitir(self, xml: str, comprobante: Comprobante) -> RespuestaDGII:
        if comprobante.modo == "disabled":
            raise ErrorECF("modo disabled: transmisión bloqueada")
        total = comprobante.totales.total
        if total == 0:
            return RespuestaDGII(
                estado="rechazado",
                codigo="R-001",
                mensaje=(f"{AVISO_SIMULACION}: comprobante rechazado — "
                         "el total no puede ser 0."))
        if comprobante.receptor.rnc == _RNC_OBSERVADO:
            return RespuestaDGII(
                estado="observado",
                codigo="O-001",
                mensaje=(f"{AVISO_SIMULACION}: comprobante observado — "
                         "RNC del receptor en lista de revisión sintética."))
        return RespuestaDGII(
            estado="aceptado",
            codigo="A-001",
            mensaje=(f"{AVISO_SIMULACION}: comprobante aceptado — "
                     "respuesta sintética del simulador local."))


def obtener_proveedor(modo: str) -> FiscalProvider:
    """Devuelve el proveedor fiscal para el modo indicado.

    - "mock"               -> MockProvider (simulador local offline)
    - "disabled"           -> ErrorECF (transmisión bloqueada)
    - "sandbox" / "certified_provider" -> ErrorECF (no implementado en
      fase 1: cero conexión real a la DGII)
    """
    if modo == "mock":
        return MockProvider()
    if modo == "disabled":
        raise ErrorECF("modo disabled: transmisión bloqueada")
    if modo in ("sandbox", "certified_provider"):
        raise ErrorECF(
            f"modo {modo!r}: no implementado en fase 1 — sin conexión real")
    raise ErrorECF(f"modo fiscal desconocido: {modo!r}")
