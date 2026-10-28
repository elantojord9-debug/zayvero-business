"""BLOQUE B (2026-10-13) — Modelo: constantes y validaciones puras.

Sin E/S: todo lo que necesita base de datos vive en almacen.py y
programadas.py. Las validaciones aquí son puras y se prueban sin PG.
"""
from __future__ import annotations

from .errores import ErrorNotif

# Tipos de alerta (fuente.prefijo + regla). "decisiones.nueva" y los de
# finanzas/inventario son reglas locales de os_notif/reglas.py.
TIPOS_ALERTA = (
    "shield.stock_critico",
    "shield.cxc_vencidas",
    "shield.proveedor_concentrado",
    "shield.credito_sin_cobro",
    "shield.movimiento_inusual",
    "shield.devoluciones_frecuentes",
    "shield.cliente_concentrado",
    "shield.reposicion_tardia",
    "finanzas.presupuesto_sobreejecutado",
    "finanzas.cashflow_negativo",
    "inventario.stock_cero",
    "decisiones.nueva",
)

PRIORIDADES = ("info", "alerta", "critico")

CANALES = ("inapp", "email", "whatsapp", "telegram")
CANALES_EXTERNOS = ("email", "whatsapp", "telegram")

FRECUENCIAS = ("diaria", "semanal", "mensual")
FORMATOS_PROG = ("pdf", "xlsx")
ESTADOS_SALIDA = ("simulado", "pendiente_aprobacion", "aprobado",
                  "rechazado", "enviado_real", "cancelado")

# Roles que pueden ser destinatarios de reportes programados
# (alineado con la matriz de permisos del tenant).
ROLES = ("owner", "admin", "analyst", "viewer")

# Módulos de reportes/ que una programación puede generar.
MODULOS_REPORTE = ("inventario", "finanzas", "compras", "cobranzas",
                   "shield")

# Módulos con datos financieros sensibles: programarlos exige
# os.write (misma regla que reportes.rutas.SENSIBLES).
MODULOS_SENSIBLES = frozenset({"finanzas", "cobranzas"})

_TIPO_REGLA_SHIELD = {
    "stock_critico": "shield.stock_critico",
    "cxc_vencidas": "shield.cxc_vencidas",
    "proveedor_concentrado": "shield.proveedor_concentrado",
    "credito_sin_cobro": "shield.credito_sin_cobro",
    "movimiento_inusual": "shield.movimiento_inusual",
    "devoluciones_frecuentes": "shield.devoluciones_frecuentes",
    "cliente_concentrado": "shield.cliente_concentrado",
    "reposicion_tardia": "shield.reposicion_tardia",
}


def validar_tipo_alerta(tipo: str) -> str:
    t = (tipo or "").strip()
    if t != "*" and t not in TIPOS_ALERTA:
        raise ErrorNotif(
            f"tipo de alerta inválido: {t!r} (use '*' o uno de "
            f"{', '.join(TIPOS_ALERTA)})")
    return t


def validar_canal(canal: str) -> str:
    c = (canal or "").strip()
    if c not in CANALES:
        raise ErrorNotif(
            f"canal inválido: {c!r} (use {', '.join(CANALES)})")
    return c


def validar_prioridad(p: str) -> str:
    p = (p or "").strip()
    if p not in PRIORIDADES:
        raise ErrorNotif(
            f"prioridad inválida: {p!r} (use {', '.join(PRIORIDADES)})")
    return p


def validar_frecuencia(f: str) -> str:
    f = (f or "").strip()
    if f not in FRECUENCIAS:
        raise ErrorNotif(
            f"frecuencia inválida: {f!r} (use {', '.join(FRECUENCIAS)})")
    return f


def validar_formato_prog(f: str) -> str:
    f = (f or "").strip()
    if f not in FORMATOS_PROG:
        raise ErrorNotif(
            f"formato inválido: {f!r} (use {', '.join(FORMATOS_PROG)})")
    return f


def validar_modulo_reporte(m: str) -> str:
    m = (m or "").strip()
    if m not in MODULOS_REPORTE:
        raise ErrorNotif(
            f"módulo de reporte inválido: {m!r} (use "
            f"{', '.join(MODULOS_REPORTE)})")
    return m


def validar_hora(h: str) -> str:
    """'HH:MM' en 24h (hora RD)."""
    h = (h or "").strip()
    try:
        hh, mm = h.split(":")
        hi, mi = int(hh), int(mm)
        if 0 <= hi <= 23 and 0 <= mi <= 59 and len(hh) == 2 \
                and len(mm) == 2:
            return h
    except (ValueError, AttributeError):
        pass
    raise ErrorNotif(f"hora inválida: {h!r} (use HH:MM 24h)")


def validar_rol(rol: str) -> str:
    r = (rol or "").strip()
    if r not in ROLES:
        raise ErrorNotif(
            f"rol inválido: {r!r} (use {', '.join(ROLES)})")
    return r


def tipo_shield(regla: str) -> str:
    """shield.reglas.REGLAS -> tipo de alerta os_notif."""
    return _TIPO_REGLA_SHIELD.get((regla or "").strip(),
                                  f"shield.{(regla or '').strip()}")
