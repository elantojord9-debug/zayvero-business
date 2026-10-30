"""Auditoría duradera de TALENTO (FASE 5).

Registro inmutable de acciones críticas. Solo INSERT desde la app;
no hay UPDATE ni DELETE. Vive en la misma BD transaccional.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from factura_persist import db


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def registrar(empresa_id: int | None, usuario_id: int | None, accion: str,
              entidad: str = "", entidad_id: int | None = None,
              detalle: dict | None = None, ip: str = "") -> None:
    """Registra un evento de auditoría. Nunca debe romper el flujo
    principal: si falla, se ignora silenciosamente (best-effort)."""
    try:
        conn = db.connect()
        try:
            cur = conn.cursor()
            with db.tx(conn):
                db.execute(
                    cur, "INSERT INTO factura_auditoria (empresa_id,"
                         " usuario_id, accion, entidad, entidad_id, detalle,"
                         " ip, creada_en)"
                         " VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (empresa_id, usuario_id, accion, entidad, entidad_id,
                     json.dumps(detalle or {}, ensure_ascii=False),
                     ip or "", _ahora()))
        finally:
            conn.close()
    except Exception:
        from factura_persist.uow import in_transaction
        if in_transaction():
            raise
        import logging
        logging.getLogger(__name__).exception("No se pudo registrar evento de auditoría")


def listar(empresa_id: int, limite: int = 200,
           accion: str | None = None) -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        sql = ("SELECT id, usuario_id, accion, entidad, entidad_id,"
               " detalle, ip, creada_en FROM factura_auditoria"
               " WHERE empresa_id=%s")
        params: list = [empresa_id]
        if accion:
            sql += " AND accion=%s"
            params.append(accion)
        sql += " ORDER BY id DESC LIMIT %s"
        params.append(limite)
        return [dict(f) for f in db.fetchall(cur, sql, params)]
    finally:
        conn.close()
