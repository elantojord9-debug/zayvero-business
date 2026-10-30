"""Contrato transversal: versión del esquema, enumeraciones y validaciones comunes."""
from __future__ import annotations

from datetime import datetime, timezone

SCHEMA_VERSION = "1"

# ISO 4217 relevantes para RD + LatAm + comercio internacional
ISO_CURRENCIES = {
    "DOP", "USD", "EUR", "GBP", "MXN", "COP", "ARS", "CLP", "PEN", "BRL",
    "CAD", "CHF", "CNY", "JPY",
}


def require_iso_currency(code: str, allow_empty: bool = False):
    if not code and allow_empty:
        return
    if code not in ISO_CURRENCIES:
        raise ValueError(f"moneda inválida (se espera ISO 4217): {code!r}")


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class TipoMovimiento:
    ENTRADA = "entrada"            # compra recibida, ajuste positivo
    SALIDA = "salida"              # venta, merma
    AJUSTE = "ajuste"              # corrección (cantidad con signo)
    TRANSFERENCIA_OUT = "transferencia_out"
    TRANSFERENCIA_IN = "transferencia_in"
    DEVOLUCION_IN = "devolucion_in"    # devolución de cliente -> entra
    DEVOLUCION_OUT = "devolucion_out"  # devolución a proveedor -> sale
    VALORES = (ENTRADA, SALIDA, AJUSTE, TRANSFERENCIA_OUT, TRANSFERENCIA_IN,
               DEVOLUCION_IN, DEVOLUCION_OUT)


class EstadoCompra:
    BORRADOR = "borrador"
    CONFIRMADA = "confirmada"
    RECIBIDA_PARCIAL = "recibida_parcial"
    RECIBIDA = "recibida"
    CANCELADA = "cancelada"
    VALORES = (BORRADOR, CONFIRMADA, RECIBIDA_PARCIAL, RECIBIDA, CANCELADA)


class EstadoVenta:
    BORRADOR = "borrador"
    CONFIRMADA = "confirmada"
    ENTREGADA = "entregada"
    CANCELADA = "cancelada"
    VALORES = (BORRADOR, CONFIRMADA, ENTREGADA, CANCELADA)


class MedioPago:
    EFECTIVO = "efectivo"
    TRANSFERENCIA = "transferencia"
    TARJETA = "tarjeta"
    CHEQUE = "cheque"
    CREDITO = "credito"
    VALORES = (EFECTIVO, TRANSFERENCIA, TARJETA, CHEQUE, CREDITO)


class TipoPago:
    COBRO = "cobro"   # CxC: el cliente nos paga
    PAGO = "pago"     # CxP: pagamos al proveedor
    VALORES = (COBRO, PAGO)


class EstadoPago:
    PENDIENTE = "pendiente"
    PARCIAL = "parcial"
    PAGADO = "pagado"
    VENCIDO = "vencido"
    VALORES = (PENDIENTE, PARCIAL, PAGADO, VENCIDO)


class TipoDevolucion:
    CLIENTE = "cliente"
    PROVEEDOR = "proveedor"
    VALORES = (CLIENTE, PROVEEDOR)


class EstadoDevolucion:
    SOLICITADA = "solicitada"
    APROBADA = "aprobada"
    RECIBIDA = "recibida"
    RECHAZADA = "rechazada"
    VALORES = (SOLICITADA, APROBADA, RECIBIDA, RECHAZADA)
