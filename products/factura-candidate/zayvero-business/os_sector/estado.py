"""BLOQUE SECTOR — Asignación de sector por empresa.

Diseño (decisión documentada, sin migración SQL):
- El sector se guarda en el BUNDLE de la empresa (atributo
  `_os_sector_estado`), igual que os_contab guarda su bloque en
  `_os_contab_bloque`. NO hay tabla nueva ni migración: es
  configuración, no datos operativos.
- Cada cambio de sector se audita en os_eventos con tipo "sector"
  (agregado a TIPOS_EVENTO en os_persist/auditoria.py) vía el mismo
  patrón que os_contab/rutas.py::_auditar: en memoria cae en
  bundle.eventos_memoria; en PG dentro de mutar_os.
- company_id SIEMPRE del ctx (anti-IDOR). Solo rol owner/admin
  (o permiso company.config, que la matriz de tenant/roles.py da
  exactamente a owner y admin).

La plantilla NO inventa datos: solo configura qué mirar. El estado
guarda únicamente {sector, fijado_en, fijado_por, historial}.
"""
from __future__ import annotations

import sys
import os
import threading
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .errores import ErrorSector, PermisoDenegado  # noqa: E402
from .plantillas import SECTORES, obtener_plantilla  # noqa: E402

_BLOQUE_ATTR = "_os_sector_estado"
_bloque_lock = threading.RLock()

_ROLES_ADMIN = ("owner", "admin")


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _estado_inicial() -> dict:
    return {"sector": "", "fijado_en": "", "fijado_por": "",
            "historial": []}


def estado(bundle) -> dict:
    """Estado de sector del bundle (copia; nunca el interno)."""
    with _bloque_lock:
        e = getattr(bundle, _BLOQUE_ATTR, None)
        if e is None:
            e = _estado_inicial()
            setattr(bundle, _BLOQUE_ATTR, e)
        return {"sector": e["sector"], "fijado_en": e["fijado_en"],
                "fijado_por": e["fijado_por"],
                "historial": [dict(h) for h in e["historial"]]}


def _ctx_val(ctx, nombre: str) -> str:
    if ctx is None:
        return ""
    v = getattr(ctx, nombre, None)
    if v:
        return str(v)
    if isinstance(ctx, dict):
        return str(ctx.get(nombre, "") or "")
    return ""


def _ctx_company(bundle, ctx) -> str:
    cid = _ctx_val(ctx, "company_id")
    if not cid or cid != bundle.company_id:
        raise ErrorSector("company_id del contexto no coincide con la empresa")
    return cid


def _actor(ctx) -> str:
    return str(_ctx_val(ctx, "user_id") or _ctx_val(ctx, "actor") or "")


def _es_admin(ctx) -> bool:
    rol = _ctx_val(ctx, "role").lower()
    if rol in _ROLES_ADMIN:
        return True
    if hasattr(ctx, "has"):
        return bool(ctx.has("company.config"))
    if isinstance(ctx, dict):
        return "company.config" in (ctx.get("permisos") or [])
    return False


def _auditar(bundle, ctx, tipo: str, ref_id: str, detalle: dict):
    from os_persist.auditoria import auditar
    auditar(_ctx_company(bundle, ctx), tipo, _actor(ctx), ref_id, detalle)


def asignar(bundle, ctx, sector_id: str) -> dict:
    """Asigna (o cambia) el sector de la empresa. Solo owner/admin.

    Devuelve el estado resultante. Si el sector ya era el mismo, no
    audita y marca "sin_cambios": True (idempotente).
    """
    _ctx_company(bundle, ctx)
    if not _es_admin(ctx):
        raise PermisoDenegado(
            "asignar sector requiere rol owner o admin "
            "(permiso company.config)")
    sector_id = str(sector_id or "").strip().lower()
    if sector_id not in SECTORES:
        raise ErrorSector(
            f"sector inválido: {sector_id!r}. Válidos: "
            + ", ".join(SECTORES))
    plantilla = obtener_plantilla(sector_id)
    with _bloque_lock:
        e = getattr(bundle, _BLOQUE_ATTR, None)
        if e is None:
            e = _estado_inicial()
            setattr(bundle, _BLOQUE_ATTR, e)
        anterior = e["sector"]
        if anterior == sector_id:
            out = estado(bundle)
            out["sin_cambios"] = True
            return out
        e["sector"] = sector_id
        e["fijado_en"] = _ahora_iso()
        e["fijado_por"] = _actor(ctx)
        e["historial"].append({"sector": sector_id,
                               "anterior": anterior,
                               "en": e["fijado_en"],
                               "por": e["fijado_por"]})
    _auditar(bundle, ctx, "sector", sector_id,
             {"sector_anterior": anterior, "sector_nuevo": sector_id,
              "plantilla": plantilla["nombre"]})
    out = estado(bundle)
    out["sin_cambios"] = False
    return out
