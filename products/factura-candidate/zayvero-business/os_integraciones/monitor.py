"""BLOQUE Documentos/Integraciones — Monitor de sincronizaciones.

Estado por fuente (simulada): última sincronización, resultado,
errores, duplicados detectados y mapeo de campos usado.

En modo memoria (este bloque): el estado vive en memoria por empresa
(patrón como RegistroDocumentos). Una sincronización es de SOLO
LECTURA contra el adaptador: lee productos/pedidos/clientes/stock,
detecta duplicados por clave natural y registra el resultado. NO
escribe en el OS (la escritura real futura pasaría por os_import con
sus validaciones).

Estados: ok | con_advertencias | error | nunca_sincronizado.
"""
from __future__ import annotations

import os
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_CLAVES_NATURALES = {
    "productos": ("sku",),
    "pedidos": ("id", "name", "numero", "DocNumber", "VBELN"),
    "clientes": ("email",),
    "stock": ("sku",),
}


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clave_natural(entidad: str, item: dict) -> str:
    for campo in _CLAVES_NATURALES.get(entidad, ()):
        v = item.get(campo)
        if v not in (None, ""):
            return f"{campo}={v}"
    return ""


def detectar_duplicados(entidad: str, items: list[dict]) -> list[dict]:
    """Duplicados por clave natural dentro de lo leído de la fuente."""
    vistos: dict[str, int] = {}
    dups = []
    for i, it in enumerate(items):
        clave = _clave_natural(entidad, it)
        if not clave:
            continue
        if clave in vistos:
            dups.append({"entidad": entidad, "clave": clave,
                         "indices": [vistos[clave], i]})
        else:
            vistos[clave] = i
    return dups


class MonitorSincronizaciones:
    """Estado de sincronizaciones de UNA empresa."""

    def __init__(self, company_id: str):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        self.company_id = company_id
        self._lock = threading.RLock()
        self._estado: dict[str, dict] = {}

    def registrar(self, fuente: str, resultado: dict) -> dict:
        """Guarda el resultado de una sincronización. Devuelve el estado."""
        with self._lock:
            estado = {
                "fuente": fuente,
                "company_id": self.company_id,
                "estado": resultado.get("estado", "error"),
                "ultima_sincronizacion": _ahora_iso(),
                "filas_leidas": resultado.get("filas_leidas", {}),
                "duplicados_detectados":
                    resultado.get("duplicados_detectados", []),
                "errores": resultado.get("errores", []),
                "mapeo_campos": resultado.get("mapeo_campos", {}),
                "modo": "simulado",
            }
            self._estado[fuente] = estado
            return dict(estado)

    def estado(self, fuente: str | None = None) -> dict:
        with self._lock:
            if fuente:
                return dict(self._estado.get(
                    fuente,
                    {"fuente": fuente, "company_id": self.company_id,
                     "estado": "nunca_sincronizado",
                     "ultima_sincronizacion": None, "filas_leidas": {},
                     "duplicados_detectados": [], "errores": [],
                     "mapeo_campos": {}, "modo": "simulado"}))
            return {f: dict(e) for f, e in self._estado.items()}

    def resumen(self) -> dict:
        with self._lock:
            estados = [e["estado"] for e in self._estado.values()]
            return {
                "company_id": self.company_id,
                "fuentes": sorted(self._estado),
                "total": len(self._estado),
                "con_error": sum(1 for s in estados if s == "error"),
                "con_advertencias": sum(
                    1 for s in estados if s == "con_advertencias"),
                "ok": sum(1 for s in estados if s == "ok"),
            }


_MONITORES: dict[str, MonitorSincronizaciones] = {}
_LOCK = threading.RLock()


def obtener_monitor(company_id: str) -> MonitorSincronizaciones:
    with _LOCK:
        m = _MONITORES.get(company_id)
        if m is None:
            m = MonitorSincronizaciones(company_id)
            _MONITORES[company_id] = m
        return m


def sincronizar_fuente(company_id: str, adaptador) -> dict:
    """Sincronización SIMULADA de solo lectura contra un adaptador.

    Lee las 4 entidades, cuenta filas, detecta duplicados por clave
    natural y registra el resultado en el monitor. Devuelve el estado
    registrado. Nunca escribe en el OS.
    """
    monitor = obtener_monitor(company_id)
    filas, dups, errores = {}, [], []
    try:
        for entidad, metodo in (
                ("productos", adaptador.listar_productos),
                ("pedidos", adaptador.listar_pedidos),
                ("clientes", adaptador.listar_clientes),
                ("stock", adaptador.leer_stock)):
            try:
                items = metodo()
                filas[entidad] = len(items)
                dups.extend(detectar_duplicados(entidad, items))
            except Exception as e:  # un adaptador nunca debe tumbar el monitor
                errores.append({"entidad": entidad, "error": str(e)})
                filas[entidad] = 0
        estado = ("error" if errores
                  else "con_advertencias" if dups else "ok")
        return monitor.registrar(adaptador.FUENTE, {
            "estado": estado, "filas_leidas": filas,
            "duplicados_detectados": dups, "errores": errores,
            "mapeo_campos": adaptador.mapeo_campos()})
    except Exception as e:
        return monitor.registrar(adaptador.FUENTE, {
            "estado": "error", "filas_leidas": filas,
            "duplicados_detectados": dups,
            "errores": errores + [{"entidad": "*", "error": str(e)}],
            "mapeo_campos": {}})
