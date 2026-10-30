"""Arquitectura de conexión DGII directa y proveedor certificado (FASE B).

SOLO DISEÑO: este módulo NO conecta a ningún servicio real. Toda
transmisión real está bloqueada hasta que se cumplan los requisitos
legales y técnicos documentados en `docs/ecf-requisitos-conexion-real.md`.

Dos rutas de fase B:
  1. `DGIIProveedorDirecto` — conexión directa a los ambientes DGII
     (pre-certificación / certificación / producción).
  2. `ProveedorCertificado` — adaptador para un proveedor de servicios
     de facturación electrónica certificado (ruta recomendada por el
     issue #9 para reducir complejidad operativa).

Ambas clases lanzan `ErrorECF` en cualquier intento de uso en fase 1:
son el CONTRATO que la fase B implementará, no una conexión funcional.

Requisitos previos (ninguno cumplido en fase 1):
  - Autorización de la DGII como emisor electrónico del contribuyente.
  - Certificado digital de procesos tributarios válido (la llave privada
    NUNCA se almacena en código, logs ni repositorio).
  - XSD oficial vigente validado contra el XML generado.
  - Rangos de secuencias autorizados cargados en `origen='fiscal'`.
  - Credenciales del ambiente correspondiente (nunca de terceros).

AVISO: SIMULACIÓN — NO VÁLIDO ANTE DGII.
"""
from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass

from .modelos import AVISO_SIMULACION, Comprobante, ErrorECF
from .proveedor import FiscalProvider, RespuestaDGII


@dataclass(frozen=True)
class ConfigDGII:
    """Configuración de conexión a un ambiente DGII (fase B).

    Las URLs y credenciales se proveen por variables de entorno /
    Secret Manager en fase B; aquí solo se documenta la forma.
    """
    ambiente: str  # "pre-certificacion" | "certificacion" | "produccion"
    url_transmision: str = ""
    url_consulta: str = ""
    rnc_emisor: str = ""
    # La ruta al certificado y su clave se resuelven en runtime desde el
    # almacén seguro; jamás se hardcodean.
    certificado_ref: str = ""


class DGIIProveedorDirecto(FiscalProvider):
    """Conexión directa a la DGII (fase B). NO operativo en fase 1."""

    def __init__(self, config: ConfigDGII) -> None:
        self.config = config

    def _bloqueado(self) -> ErrorECF:
        return ErrorECF(
            "conexión directa DGII no habilitada en fase 1: "
            "se requiere autorización como emisor electrónico, certificado "
            "digital válido y XSD oficial vigente. " + AVISO_SIMULACION)

    def transmitir(self, xml: str, comprobante: Comprobante) -> RespuestaDGII:
        raise self._bloqueado()

    @abstractmethod
    def _firmar_xml(self, xml: str) -> str:  # pragma: no cover
        """Firma XML con certificado válido (fase B). Nunca simular."""
        raise self._bloqueado()


class ProveedorCertificado(FiscalProvider):
    """Adaptador para proveedor de facturación electrónica certificado.

    Ruta recomendada (issue #9): delega firma, transmisión y acuses al
    proveedor. El contrato que debe cumplir el adaptador concreto:
      - transmitir(xml) -> RespuestaDGII con acuse verificable;
      - consultar_estado(e_ncf) -> estado oficial;
      - nunca presentar respuestas del sandbox como aceptación fiscal.
    NO operativo en fase 1: sin proveedor contratado ni credenciales.
    """

    def __init__(self, nombre_proveedor: str = "") -> None:
        self.nombre_proveedor = nombre_proveedor

    def transmitir(self, xml: str, comprobante: Comprobante) -> RespuestaDGII:
        raise ErrorECF(
            "proveedor certificado no configurado en fase 1: "
            "requiere contratación y credenciales del proveedor. "
            + AVISO_SIMULACION)

    def consultar_estado(self, e_ncf: str) -> RespuestaDGII:
        raise ErrorECF(
            "consulta de estado no disponible en fase 1. "
            + AVISO_SIMULACION)
