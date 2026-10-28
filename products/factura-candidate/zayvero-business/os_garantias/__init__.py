"""BLOQUE E — Garantías de productos (2026-10-13).

Términos de garantía por producto/lote, activación automática desde
ventas, ciclo de reclamos (intake → diagnóstico → resolución con costo
real), impacto en margen vía gastos operativos, historial por cliente y
producto, y alertas de vencimiento como fuente para os_notif.

Módulo propio (no dentro de os_crm): reutiliza os_crm.ventas (estado de
la venta), os_inventory.lotes (validación de lotes) y os_finanzas
(gastos/profit) sin modificarlos. Estado en memoria por empresa; la
migración os_persist/schema/017_os_garantias.sql define las tablas
duraderas (el coordinador la registra en _orden.txt).
"""
from .garantias import (
    ErrorGarantia, NoEncontrado, PermisoDenegado,
    TerminoGarantia, TarjetaGarantia, ReclamoGarantia,
    RegistroGarantias,
)

__all__ = [
    "ErrorGarantia", "NoEncontrado", "PermisoDenegado",
    "TerminoGarantia", "TarjetaGarantia", "ReclamoGarantia",
    "RegistroGarantias",
]
