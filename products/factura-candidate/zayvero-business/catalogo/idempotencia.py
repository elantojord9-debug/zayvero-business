"""BLOQUE 6 (sprint 3, 2026-10-12) — Idempotencia de fijar_precio/fijar_costo.

Misma semántica que `os_persist/idempotencia.py` (BLOQUE 2), adaptada
al catálogo (que NO pasa por mutar_os sino por su propio `_mutar`):

- Misma clave + mismo body -> se repite la respuesta almacenada SIN
  re-ejecutar (el historial de precios/costos no se duplica).
- Misma clave + body DISTINTO -> 422 (conflicto de clave).
- La reclamación ocurre DENTRO de la transacción de `_mutar` (mismo
  cursor): atomicidad clave+operación. Si la operación falla, el
  rollback borra la reclamación y el cliente puede reintentar.
- Serialización: `_mutar` ya toma `os_company_lock` por empresa, así
  que dos peticiones simultáneas con la misma clave se ordenan; la
  segunda ve 'completado' y repite sin ejecutar.

Solo para POST `catalogo/precios` y `catalogo/costos` (opt-in por
header `Idempotency-Key`).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalogo.modelo import ErrorCatalogo  # noqa: E402

ENDPOINTS = ("catalogo/precios", "catalogo/costos")
_ESTADOS = ("en_curso", "completado", "fallido")
_MAX_CLAVE = 255


class ConflictoClave(ErrorCatalogo):
    """Misma clave con distinto body -> 422."""


class _Repetir(Exception):
    """Señal interna: devolver la respuesta almacenada."""

    def __init__(self, status: int, payload: dict):
        self.status = status
        self.payload = payload


def hash_peticion(body: dict) -> str:
    """sha256 del body canónico (orden de claves estable)."""
    canon = json.dumps(body, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":"))
    return hashlib.sha256(canon.encode("utf-8")).hexdigest()


def validar_clave(clave: str) -> str:
    c = (clave or "").strip()
    if not c:
        raise ErrorCatalogo("Idempotency-Key vacía")
    if len(c) > _MAX_CLAVE:
        raise ErrorCatalogo("Idempotency-Key demasiado larga (máx 255)")
    return c


def ejecutar_idempotente(company_id: str, clave: str, endpoint: str,
                         body: dict, operacion):
    """Ejecuta `operacion(cur) -> (status, payload)` con idempotencia.

    Devuelve `(status, payload)`.

    - Sin clave (None/"") -> ejecuta directo, sin idempotencia.
    - Con clave -> reclama dentro de `_mutar`; si la clave ya
      completó con el mismo hash, lanza `_Repetir(status, payload)`;
      si el hash difiere, lanza `ConflictoClave` (-> 422).
    - Si `operacion` falla, el rollback de `_mutar` borra la
      reclamación: el cliente puede reintentar con la misma clave.
    """
    from catalogo.api import _mutar
    if endpoint not in ENDPOINTS:
        raise ErrorCatalogo(f"endpoint no idempotente: {endpoint!r}")
    if not (clave or "").strip():
        return _mutar(company_id, operacion)
    clave = validar_clave(clave)
    rhash = hash_peticion(body)

    def _op(cur):
        cur.execute(
            """INSERT INTO os_catalogo_idempotencia
               (company_id, clave, endpoint, req_hash, estado)
               VALUES (%s,%s,%s,%s,'en_curso')
               ON CONFLICT (company_id, clave) DO NOTHING""",
            (company_id, clave, endpoint, rhash))
        cur.execute(
            """SELECT endpoint, req_hash, estado, respuesta
               FROM os_catalogo_idempotencia
               WHERE company_id=%s AND clave=%s""",
            (company_id, clave))
        ep, h, estado, resp = cur.fetchone()
        if ep != endpoint or h != rhash:
            raise ConflictoClave(
                "Idempotency-Key ya usada con otro cuerpo/endpoint; "
                "use una clave distinta para una operación distinta")
        if estado == "completado" and resp:
            raise _Repetir(int(resp.get("status", 200)),
                           resp.get("payload", {}))
        # 'en_curso' (la acabamos de insertar nosotros: el lock por
        # empresa de _mutar impide que otro la esté ejecutando a la
        # vez): ejecutar.
        status, payload = operacion(cur)
        cur.execute(
            """UPDATE os_catalogo_idempotencia
               SET estado='completado', respuesta=%s, updated_at=now()
               WHERE company_id=%s AND clave=%s""",
            (json.dumps({"status": status, "payload": payload}),
             company_id, clave))
        return status, payload

    try:
        return _mutar(company_id, _op)
    except _Repetir as r:
        payload = dict(r.payload)
        payload["repetida"] = True
        return r.status, payload
