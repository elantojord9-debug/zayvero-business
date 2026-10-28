"""BLOQUE B (2026-10-13) — Motor de reportes programados.

Genera los reportes de las programaciones vencidas usando el backend
`reportes/` existente (nada se reinventa) y deja cada archivo en la
bandeja de salida con estado SIMULADO. NO envía nada a nadie.

El `generador` es inyectable (firma: (modulo, formato) -> (bytes,
nombre_archivo)) para probar sin un bundle completo; el default usa
reportes.datos.generar + reportes.formatos.generar y necesita el
`bundle` de la empresa.
"""
from __future__ import annotations

import calendar
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .errores import ErrorNotif  # noqa: E402
from .modelo import validar_frecuencia, validar_hora  # noqa: E402

_HORA_RD_OFFSET = timedelta(hours=-4)  # AST (hora de Santo Domingo)


def _parse_hora(hora: str) -> tuple[int, int]:
    validar_hora(hora)
    hh, mm = hora.split(":")
    return int(hh), int(mm)


def proxima_ejecucion(frecuencia: str, hora: str,
                      desde: datetime | None = None) -> str:
    """Siguiente ejecución en ISO (UTC) tras `desde`.

    - diaria: mañana a la hora indicada.
    - semanal: dentro de 7 días a la hora indicada.
    - mensual: el mismo día del mes siguiente (o el último día del mes
      si no existe) a la hora indicada.
    La hora se interpreta en hora RD (AST); el resultado se devuelve
    en UTC ISO.
    """
    validar_frecuencia(frecuencia)
    hh, mm = _parse_hora(hora)
    base = desde or datetime.now(timezone.utc)
    # Fecha RD "de hoy" para anclar el horario local.
    hoy_rd = (base + _HORA_RD_OFFSET).date()

    def _a_utc(fecha_rd) -> datetime:
        # HH:MM RD -> UTC (AST = UTC-4).
        return (datetime(fecha_rd.year, fecha_rd.month, fecha_rd.day,
                         hh, mm, tzinfo=timezone.utc)
                - _HORA_RD_OFFSET)

    hoy_hora = _a_utc(hoy_rd)  # hoy a la hora indicada, en UTC
    if frecuencia == "diaria":
        objetivo = hoy_hora if hoy_hora > base else hoy_hora + timedelta(
            days=1)
    elif frecuencia == "semanal":
        objetivo = hoy_hora if hoy_hora > base else hoy_hora + timedelta(
            days=7)
    else:  # mensual: mismo día del mes a la hora indicada
        if hoy_hora > base:
            objetivo = hoy_hora
        else:
            anio, mes = hoy_rd.year, hoy_rd.month + 1
            if mes > 12:
                anio, mes = anio + 1, 1
            ultimo = calendar.monthrange(anio, mes)[1]
            objetivo = _a_utc(
                datetime(anio, mes, min(hoy_rd.day, ultimo)).date())
    return objetivo.isoformat()


def _generador_default(bundle):
    """Generador con el backend real de reportes/."""
    from reportes.datos import generar as generar_reporte
    from reportes.formatos import generar as generar_formato
    from reportes.datos import nombre_archivo

    def _gen(modulo: str, formato: str) -> tuple[bytes, str]:
        rep = generar_reporte(modulo, bundle, "",
                              lang="es", hoy=None)
        cuerpo = generar_formato(rep, formato)
        nombre = nombre_archivo(modulo, formato, "es",
                                rep.generado)
        return cuerpo, nombre
    return _gen


def ejecutar_vencidas(centro, ahora: datetime | None = None,
                      generador=None, bundle=None) -> list[dict]:
    """Ejecuta las programaciones activas vencidas.

    Devuelve la lista de salidas creadas (dicts de
    `crear_salida`). Una salida por rol destino. Las programaciones
    sin `proxima_ejecucion` (recién creadas) se ejecutan una vez y se
    les agenda la próxima.
    """
    from .almacen import CentroNotificaciones
    if not isinstance(centro, CentroNotificaciones):
        raise ErrorNotif("centro debe ser CentroNotificaciones")
    if generador is None:
        if bundle is None:
            raise ErrorNotif(
                "ejecutar_vencidas necesita `bundle` o un `generador` "
                "inyectado")
        generador = _generador_default(bundle)
    ahora = ahora or datetime.now(timezone.utc)
    creadas = []
    for prog in centro.programaciones(solo_activas=True):
        prox = prog.get("proxima_ejecucion")
        vencida = True
        if prox:
            try:
                vencida = datetime.fromisoformat(prox) <= ahora
            except (ValueError, TypeError):
                vencida = True
        if not vencida:
            continue
        cuerpo, nombre = generador(prog["modulo"], prog["formato"])
        roles = prog.get("roles_destino") or []
        if not roles:
            roles = [""]
        for rol in roles:
            salida = centro.crear_salida(
                tipo="reporte_programado",
                ref_id=prog["prog_id"],
                formato=prog["formato"],
                nombre_archivo=nombre,
                contenido=cuerpo,
                destinatario_rol=rol or "",
                canal="inapp",
                nota=(f"SIMULADO — generado por programación "
                      f"{prog['prog_id']} ({prog['frecuencia']} "
                      f"{prog['hora']} RD). Sin envío real."))
            creadas.append({**salida, "prog_id": prog["prog_id"],
                            "rol": rol or ""})
        centro.marcar_ejecucion_programacion(
            prog["prog_id"],
            proxima_ejecucion(prog["frecuencia"], prog["hora"], ahora))
    return creadas
