"""BLOQUE EMPLEADOS (2026-10-13) — Task Monitor: lógica pura.

Opera sobre listas de dicts de tareas (las que devuelve el almacén).
Sin I/O: el CRUD vive en `almacen.py`; las rutas en `rutas.py`.

Definiciones operativas (documentadas también en
`docs/bloque-empleados.md`):
- VENCIDA: estado != 'completada' y vencimiento < hoy.
- POR VENCER: estado != 'completada' y hoy <= vencimiento <= hoy+2d.
- A TIEMPO (para analytics): completada con completada_en <= vencimiento.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .modelo import hoy_ymd

_UMBRAL_POR_VENCER_DIAS = 2


def _f(venc: str):
    return datetime.strptime(venc[:10], "%Y-%m-%d").date()


def es_vencida(tarea: dict, hoy: str) -> bool:
    """Vencida = no completada y vencimiento anterior a hoy."""
    return (tarea.get("estado") != "completada"
            and _f(tarea["vencimiento"]) < _f(hoy))


def es_por_vencer(tarea: dict, hoy: str,
                  dias: int = _UMBRAL_POR_VENCER_DIAS) -> bool:
    h = _f(hoy)
    v = _f(tarea["vencimiento"])
    return (tarea.get("estado") != "completada"
            and h <= v <= h + timedelta(days=dias))


def fue_a_tiempo(tarea: dict) -> bool | None:
    """True si se completó en fecha; False si tarde; None si no aplica
    (no completada o sin fecha de completitud registrada)."""
    if tarea.get("estado") != "completada":
        return None
    comp = tarea.get("completada_en")
    if not comp:
        return None
    return comp[:10] <= tarea["vencimiento"][:10]


def alertas(tareas: list[dict], hoy: str | None = None) -> dict:
    """Alertas operativas: vencidas y por vencer. Solo lectura."""
    hoy = hoy_ymd(hoy)
    vencidas = [t for t in tareas if es_vencida(t, hoy)]
    por_vencer = [t for t in tareas if es_por_vencer(t, hoy)]
    return {
        "hoy": hoy,
        "vencidas": sorted(vencidas, key=lambda t: t["vencimiento"]),
        "n_vencidas": len(vencidas),
        "por_vencer_48h": sorted(por_vencer,
                                 key=lambda t: t["vencimiento"]),
        "n_por_vencer": len(por_vencer),
        "requiere_atencion": bool(vencidas),
    }


def resumen(tareas: list[dict], hoy: str | None = None) -> dict:
    """Conteo por estado y prioridad + vencidas. Solo lectura."""
    hoy = hoy_ymd(hoy)
    por_estado: dict[str, int] = {}
    por_prioridad: dict[str, int] = {}
    n_vencidas = 0
    for t in tareas:
        por_estado[t["estado"]] = por_estado.get(t["estado"], 0) + 1
        por_prioridad[t["prioridad"]] = por_prioridad.get(t["prioridad"],
                                                          0) + 1
        if es_vencida(t, hoy):
            n_vencidas += 1
    return {
        "hoy": hoy,
        "total": len(tareas),
        "por_estado": por_estado,
        "por_prioridad": por_prioridad,
        "n_vencidas": n_vencidas,
    }
