"""BLOQUE EMPLEADOS (2026-10-13) — SLA Monitor: lógica pura.

Mide tiempos de respuesta/resolución por tipo de operación contra un
objetivo configurable (horas). Definiciones:

- DURACIÓN (horas) = (fin - inicio) en horas, 2 decimales.
- CUMPLIÓ = duración <= objetivo_horas del tipo (objetivo vigente al
  cerrar; se guarda el resultado para que no cambie si el objetivo
  se reconfigura después).
- ATRASADA (abierta) = sin fin y horas transcurridas desde inicio >
  objetivo_horas.
- TASA DE CUMPLIMIENTO = cumplidas / cerradas (solo mediciones
  cerradas; las abiertas no cuentan ni a favor ni en contra).

Sin I/O: el CRUD vive en `almacen.py`.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .modelo import ErrorEmpleados, hoy_ymd


def _parse_iso(v: str) -> datetime:
    s = str(v).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        raise ErrorEmpleados(f"fecha/hora inválida: {v!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _horas(inicio: str, fin: str) -> Decimal:
    delta = (_parse_iso(fin) - _parse_iso(inicio)).total_seconds() / 3600
    if delta < 0:
        raise ErrorEmpleados("el fin no puede ser anterior al inicio")
    return Decimal(str(delta)).quantize(Decimal("0.01"),
                                        rounding=ROUND_HALF_UP)


def evaluar_medicion(inicio: str, fin: str,
                     objetivo_horas: str | None) -> tuple[Decimal, bool | None]:
    """Devuelve (duracion_horas, cumplio). cumplio=None si no hay
    objetivo configurado (se registra la duración sin veredicto)."""
    duracion = _horas(inicio, fin)
    if objetivo_horas is None:
        return duracion, None
    try:
        obj = Decimal(str(objetivo_horas))
    except (InvalidOperation, ValueError):
        raise ErrorEmpleados("objetivo_horas inválido")
    return duracion, duracion <= obj


def validar_inicio_medicion(cfg: dict | None, tipo_operacion: str) -> None:
    if not cfg or not cfg.get("activo"):
        raise ErrorEmpleados(
            f"tipo de operación sin SLA activo: {tipo_operacion}")


def horas_transcurridas(inicio: str, hoy: str | None = None) -> Decimal:
    """Horas desde inicio hasta ahora (o hasta `hoy` 23:59 si se indica,
    para pruebas deterministas)."""
    fin = (hoy_ymd(hoy) + "T23:59:59+00:00") if hoy else \
        datetime.now(timezone.utc).isoformat()
    return _horas(inicio, fin)


def es_atrasada(medicion: dict, objetivo_horas: str,
                hoy: str | None = None) -> bool:
    """Abierta y con horas transcurridas > objetivo."""
    if medicion.get("fin"):
        return False
    return horas_transcurridas(medicion["inicio"], hoy) > Decimal(
        str(objetivo_horas))


def resumen_por_tipo(mediciones: list[dict], configs: dict[str, dict],
                     hoy: str | None = None) -> dict:
    """Agregado por tipo de operación. Solo lectura.

    `configs`: {tipo_operacion: {objetivo_horas, activo}}.
    """
    hoy = hoy_ymd(hoy)
    ag: dict[str, dict] = {}
    for m in mediciones:
        tipo = m["tipo_operacion"]
        a = ag.setdefault(tipo, {
            "tipo_operacion": tipo,
            "objetivo_horas": (configs.get(tipo) or {}).get("objetivo_horas"),
            "total": 0, "cerradas": 0, "cumplidas": 0, "incumplidas": 0,
            "abiertas": 0, "abiertas_atrasadas": 0,
            "_sum_horas": Decimal("0"),
        })
        a["total"] += 1
        if m.get("fin"):
            a["cerradas"] += 1
            a["_sum_horas"] += Decimal(str(m.get("duracion_horas") or 0))
            if m.get("cumplio") is True:
                a["cumplidas"] += 1
            elif m.get("cumplio") is False:
                a["incumplidas"] += 1
        else:
            a["abiertas"] += 1
            cfg = configs.get(tipo) or {}
            if cfg.get("objetivo_horas") and es_atrasada(
                    m, cfg["objetivo_horas"], hoy):
                a["abiertas_atrasadas"] += 1
    out = {}
    for tipo, a in sorted(ag.items()):
        suma = a.pop("_sum_horas")
        cerr = a["cerradas"]
        a["tasa_cumplimiento"] = (
            round(a["cumplidas"] / cerr, 4) if cerr else None)
        a["duracion_promedio_horas"] = (
            str((suma / cerr).quantize(Decimal("0.01"))) if cerr else None)
        a["requiere_atencion"] = bool(a["abiertas_atrasadas"])
        out[tipo] = a
    return {"hoy": hoy, "por_tipo": out}


def atrasadas(mediciones: list[dict], configs: dict[str, dict],
              hoy: str | None = None) -> dict:
    """Mediciones abiertas que ya superaron el objetivo. Solo lectura."""
    hoy = hoy_ymd(hoy)
    out = []
    for m in mediciones:
        if m.get("fin"):
            continue
        cfg = configs.get(m["tipo_operacion"]) or {}
        obj = cfg.get("objetivo_horas")
        if not obj:
            continue
        trans = horas_transcurridas(m["inicio"], hoy)
        if trans > Decimal(str(obj)):
            d = dict(m)
            d["horas_transcurridas"] = str(trans)
            d["objetivo_horas"] = str(obj)
            d["exceso_horas"] = str((trans - Decimal(str(obj))).quantize(
                Decimal("0.01")))
            out.append(d)
    out.sort(key=lambda m: Decimal(m["exceso_horas"]), reverse=True)
    return {"hoy": hoy, "atrasadas": out,
            "n_atrasadas": len(out),
            "requiere_atencion": bool(out)}
