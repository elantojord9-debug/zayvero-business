"""BLOQUE EMPLEADOS (2026-10-13) — Validaciones puras del dominio.

Sin I/O: todo lo que toca PostgreSQL vive en `almacen.py`. Las
funciones aquí solo validan y normalizan; lanzan `ErrorEmpleados`
ante datos inválidos (la capa de ruta lo traduce a 400).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation


class ErrorEmpleados(Exception):
    """Dato de dominio inválido en el bloque empleados/operaciones."""


class PermissionDeniedEmpleados(PermissionError):
    """403 del bloque (el cableado la traduce a HTTP 403)."""


# --- Vocabularios cerrados -------------------------------------------------
ESTADOS_TAREA = ("pendiente", "en_curso", "bloqueada", "completada")
PRIORIDADES = ("baja", "media", "alta", "critica")

# Tipos de operación SLA predefinidos (el objetivo es configurable por
# empresa en os_sla_config; estos son solo valores iniciales sugeridos,
# en horas).
TIPOS_SLA_PREDEFINIDOS = {
    "preparacion_pedido": "12.00",   # del pedido confirmado a listo para entrega
    "respuesta_reclamo": "24.00",    # primera respuesta a un reclamo de cliente
    "reposicion_stock": "72.00",     # reponer un producto bajo mínimo
    "cierre_caja": "2.00",           # arqueo y cierre de caja del día
}

# Transiciones de estado permitidas (completada es terminal: la
# reapertura se hace creando una tarea nueva, para no reescribir
# historia).
_TRANSICIONES = {
    "pendiente": ("en_curso", "bloqueada"),
    "en_curso": ("pendiente", "bloqueada", "completada"),
    "bloqueada": ("pendiente", "en_curso", "completada"),
    "completada": (),
}


# --- Dataclasses ------------------------------------------------------------
@dataclass
class Empleado:
    empleado_id: str
    nombre: str
    cargo: str = ""
    sucursal_id: str = ""
    usuario_id: str = ""   # vínculo opcional con el login (ctx.user_id)
    email: str = ""
    activo: bool = True


@dataclass
class Tarea:
    tarea_id: str
    titulo: str
    responsable: str        # empleado_id
    vencimiento: str        # YYYY-MM-DD
    estado: str = "pendiente"
    prioridad: str = "media"
    descripcion: str = ""
    sucursal_id: str = ""
    creada_por: str = ""


@dataclass
class SLAConfig:
    tipo_operacion: str
    objetivo_horas: str     # Decimal como texto, > 0
    activo: bool = True


@dataclass
class PromesaSeguimiento:
    cuenta_id: str
    empleado_id: str
    fecha_prometida: str    # YYYY-MM-DD
    monto: str
    moneda: str = "DOP"


@dataclass
class Sucursal:
    sucursal_id: str
    nombre: str
    ubicacion: str = ""
    almacenes: list = field(default_factory=list)  # almacenes de inventario asociados
    activa: bool = True


# --- Helpers ----------------------------------------------------------------
def _txt(v, campo: str, max_len: int = 200) -> str:
    s = str(v or "").strip()
    if not s:
        raise ErrorEmpleados(f"{campo} es obligatorio")
    if len(s) > max_len:
        raise ErrorEmpleados(f"{campo} supera {max_len} caracteres")
    return s


def _opt_txt(v, campo: str, max_len: int = 200) -> str:
    s = str(v or "").strip()
    if len(s) > max_len:
        raise ErrorEmpleados(f"{campo} supera {max_len} caracteres")
    return s


def _id_empresa(v) -> str:
    return _txt(v, "company_id", 120)


def _fecha_ymd(v, campo: str) -> str:
    s = _txt(v, campo, 10)
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError:
        raise ErrorEmpleados(f"{campo} debe ser YYYY-MM-DD (recibido {s!r})")
    return s


def _decimal_positivo(v, campo: str) -> str:
    try:
        d = Decimal(str(v))
    except (InvalidOperation, ValueError, TypeError):
        raise ErrorEmpleados(f"{campo} debe ser numérico")
    if d <= 0:
        raise ErrorEmpleados(f"{campo} debe ser > 0")
    return str(d)


# --- Validadores ---------------------------------------------------------------
def validar_empleado(d: dict, company_id: str) -> Empleado:
    """Valida el registro de un empleado. `d` = campos del body."""
    _id_empresa(company_id)
    email = _opt_txt(d.get("email", ""), "email")
    if email and "@" not in email:
        raise ErrorEmpleados("email inválido")
    return Empleado(
        empleado_id=_txt(d.get("empleado_id"), "empleado_id", 80),
        nombre=_txt(d.get("nombre"), "nombre"),
        cargo=_opt_txt(d.get("cargo", ""), "cargo"),
        sucursal_id=_opt_txt(d.get("sucursal_id", ""), "sucursal_id", 80),
        usuario_id=_opt_txt(d.get("usuario_id", ""), "usuario_id", 120),
        email=email,
        activo=bool(d.get("activo", True)),
    )


def validar_tarea(d: dict, company_id: str) -> Tarea:
    _id_empresa(company_id)
    estado = str(d.get("estado", "pendiente")).strip() or "pendiente"
    if estado not in ESTADOS_TAREA:
        raise ErrorEmpleados(
            f"estado inválido: use {', '.join(ESTADOS_TAREA)}")
    prioridad = str(d.get("prioridad", "media")).strip() or "media"
    if prioridad not in PRIORIDADES:
        raise ErrorEmpleados(
            f"prioridad inválida: use {', '.join(PRIORIDADES)}")
    return Tarea(
        tarea_id=_txt(d.get("tarea_id"), "tarea_id", 80),
        titulo=_txt(d.get("titulo"), "titulo"),
        responsable=_txt(d.get("responsable"), "responsable", 80),
        vencimiento=_fecha_ymd(d.get("vencimiento"), "vencimiento"),
        estado=estado,
        prioridad=prioridad,
        descripcion=_opt_txt(d.get("descripcion", ""), "descripcion", 1000),
        sucursal_id=_opt_txt(d.get("sucursal_id", ""), "sucursal_id", 80),
        creada_por=_opt_txt(d.get("creada_por", ""), "creada_por", 120),
    )


def validar_transicion_estado(anterior: str, nuevo: str) -> None:
    """Lanza ErrorEmpleados si la transición no está permitida."""
    if anterior not in ESTADOS_TAREA or nuevo not in ESTADOS_TAREA:
        raise ErrorEmpleados("estado desconocido en la transición")
    if nuevo not in _TRANSICIONES[anterior]:
        raise ErrorEmpleados(
            f"transición no permitida: {anterior} -> {nuevo} "
            f"(permitidas: {', '.join(_TRANSICIONES[anterior]) or 'ninguna'})")


def validar_sla_config(d: dict, company_id: str) -> SLAConfig:
    _id_empresa(company_id)
    return SLAConfig(
        tipo_operacion=_txt(d.get("tipo_operacion"), "tipo_operacion", 80),
        objetivo_horas=_decimal_positivo(d.get("objetivo_horas"),
                                        "objetivo_horas"),
        activo=bool(d.get("activo", True)),
    )


def validar_promesa(d: dict, company_id: str) -> PromesaSeguimiento:
    _id_empresa(company_id)
    moneda = _opt_txt(d.get("moneda", "DOP"), "moneda", 3).upper() or "DOP"
    if len(moneda) != 3:
        raise ErrorEmpleados("moneda debe ser ISO 4217 (3 letras)")
    return PromesaSeguimiento(
        cuenta_id=_txt(d.get("cuenta_id"), "cuenta_id", 80),
        empleado_id=_txt(d.get("empleado_id"), "empleado_id", 80),
        fecha_prometida=_fecha_ymd(d.get("fecha_prometida"),
                                  "fecha_prometida"),
        monto=_decimal_positivo(d.get("monto"), "monto"),
        moneda=moneda,
    )


def validar_sucursal(d: dict, company_id: str) -> Sucursal:
    _id_empresa(company_id)
    almacenes = d.get("almacenes", [])
    if not isinstance(almacenes, list) or any(
            not isinstance(a, str) for a in almacenes):
        raise ErrorEmpleados("almacenes debe ser una lista de textos")
    return Sucursal(
        sucursal_id=_txt(d.get("sucursal_id"), "sucursal_id", 80),
        nombre=_txt(d.get("nombre"), "nombre"),
        ubicacion=_opt_txt(d.get("ubicacion", ""), "ubicacion"),
        almacenes=[a.strip() for a in almacenes if a.strip()],
        activa=bool(d.get("activa", True)),
    )


def hoy_ymd(hoy: str | None = None) -> str:
    """Fecha de referencia YYYY-MM-DD (hoy real si no se indica)."""
    if hoy:
        return _fecha_ymd(hoy, "hoy")
    return date.today().isoformat()
