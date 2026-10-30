"""BLOQUE 2 (2026-10-12) — Idempotencia de operaciones (`Idempotency-Key`).

Semántica (endpoints financieros/operativos; lista en
webapp/os_routes.py::_SUBS_IDEMPOTENTES):

- Misma clave + mismo body  -> se devuelve la respuesta almacenada SIN
  re-ejecutar (sin doble cobro, sin doble descuento de inventario,
  sin compra duplicada).
- Misma clave + body DISTINTO -> 422 (conflicto de clave).
- Clave de una operación que FALLÓ -> queda en estado 'fallido' y el
  cliente puede reintentar con la misma clave (se reclama de nuevo;
  el hash se actualiza al del reintento). Decisión documentada: un
  intento fallido no consume la clave, porque el fallo más común en
  producción es red/cliente (respuesta perdida) y bloquear el reintento
  dejaría la operación a medias sin salida.
- La clave se verifica y se escribe DENTRO de la misma transacción de
  la operación (os_persist/tx.py::mutar_os). Como mutar_os serializa
  los escritores por empresa (SELECT ... FOR UPDATE sobre
  os_company_lock), dos peticiones simultáneas con la misma clave NO
  pueden ejecutar dos veces: la segunda queda bloqueada en el lock
  hasta que la primera hace commit, y al reclamar ve 'completado' (o
  'fallido') y repite/libera sin ejecutar.

Concurrencia fuera de ese camino (modo memoria / uso directo):
- reclamar() hace INSERT ... ON CONFLICT DO NOTHING: la unicidad la
  garantiza la PK (company_id, clave); el perdedor de la carrera lee
  la fila del ganador.
- Si el ganador sigue 'en_curso' (fresco), esperar_resolucion() hace
  espera breve con relectura (sleep corto, sin busy-loop agresivo);
  si expira el tiempo -> 'ocupado' (el llamador responde 409).
- 'en_curso' abandonado (worker caído): si supera
  ZAYVERO_OS_IDEMPOTENCY_STALE_SECS (defecto 300s), reclamar() lo
  considera muerto y reclama la clave de nuevo. Sin esto, una caída
  bloquearía la clave hasta su TTL.

TTL:
- ZAYVERO_OS_IDEMPOTENCY_TTL_HOURS (defecto 24). expires_at se fija al
  reclamar; limpiar_expiradas() borra las vencidas (función + test;
  no hay cron en este bloque).

Aislamiento multiempresa:
- Toda operación filtra por (company_id, clave). Una empresa no puede
  reusar ni leer claves de otra (testeado).

Modo memoria (sin PG):
- Equivalente en un dict thread-safe con TTL. No duradero (se pierde
  al reiniciar), igual que el resto del modo memoria.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timedelta, timezone

ESTADOS = ("en_curso", "completado", "fallido")

_MAX_CLAVE = 255


def ttl_horas() -> float:
    try:
        v = float(os.environ.get("ZAYVERO_OS_IDEMPOTENCY_TTL_HOURS", "24"))
        return v if v > 0 else 24.0
    except (TypeError, ValueError):
        return 24.0


def stale_segs() -> float:
    try:
        v = float(os.environ.get("ZAYVERO_OS_IDEMPOTENCY_STALE_SECS", "300"))
        return v if v > 0 else 300.0
    except (TypeError, ValueError):
        return 300.0


def hash_peticion(body: dict) -> str:
    """sha256 del body canónico (orden de claves estable).

    Dos cuerpos lógicamente iguales (distinto orden de claves o
    espacios) dan el mismo hash. Decimals/fechas se normalizan a str.
    """
    canonico = json.dumps(body, sort_keys=True, separators=(",", ":"),
                          ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(canonico.encode("utf-8")).hexdigest()


def _normalizar_respuesta(body) -> dict:
    """Copia JSON-segura de la respuesta, igual que la que ve el cliente.

    webapp/server.py::_send_json serializa con default=str, así que la
    respuesta repetida debe coincidir con eso (Decimal -> "2000", etc.).
    """
    norm = json.loads(json.dumps(body, ensure_ascii=False, default=str))
    return norm if isinstance(norm, dict) else {"valor": norm}


class Decision:
    """Resultado de reclamar()/esperar_resolucion().

    kind:
      'ejecutar'  -> esta llamada es dueña de la clave; debe ejecutar
                     la operación y luego finalizar().
      'repetir'   -> devolver (status, body) almacenados sin ejecutar.
      'conflicto' -> misma clave, distinto body -> 422.
      'en_curso'  -> otro intento sigue en vuelo (fresco); el llamador
                     puede esperar con esperar_resolucion().
      'ocupado'   -> esperar_resolucion() agotó el tiempo -> 409.
    """
    __slots__ = ("kind", "status", "body")

    def __init__(self, kind: str, status: int = 0, body=None):
        self.kind = kind
        self.status = status
        self.body = body if body is not None else {}

    def __repr__(self):  # pragma: no cover - diagnóstico
        return f"Decision({self.kind}, {self.status})"


def _fila_a_dict(cols, fila) -> dict:
    return dict(zip(cols, fila))


def _leer(cur, company_id: str, clave: str):
    cur.execute(
        """SELECT clave, endpoint, request_hash, response_status,
                  response_body, estado, created_at, expires_at
           FROM os_idempotency
           WHERE company_id = %s AND clave = %s""",
        (company_id, clave))
    fila = cur.fetchone()
    if fila is None:
        return None
    return _fila_a_dict([d[0] for d in cur.description], fila)


def _ahora():
    return datetime.now(timezone.utc)


def _expirada(fila) -> bool:
    return fila["expires_at"] <= _ahora()


def _abandonada(fila) -> bool:
    return (_ahora() - fila["created_at"]).total_seconds() > stale_segs()


def reclamar(cur, company_id: str, clave: str, endpoint: str,
             request_hash: str) -> Decision:
    """Reclama la clave dentro de la transacción en curso (cur).

    No hace commit: la fila 'en_curso' vive y muere con la transacción
    de la operación (atomicidad clave+operación).
    """
    if not clave or len(clave) > _MAX_CLAVE:
        raise ValueError("Idempotency-Key inválida")
    vence = _ahora() + timedelta(hours=ttl_horas())
    cur.execute(
        """INSERT INTO os_idempotency
               (company_id, clave, endpoint, request_hash, estado,
                created_at, expires_at)
           VALUES (%s, %s, %s, %s, 'en_curso', now(), %s)
           ON CONFLICT (company_id, clave) DO NOTHING""",
        (company_id, clave, endpoint, request_hash, vence))
    if cur.rowcount == 1:
        return Decision("ejecutar")

    fila = _leer(cur, company_id, clave)
    if fila is None:
        # Carrera rarísima (borrado concurrente): reintentar una vez.
        cur.execute(
            """INSERT INTO os_idempotency
                   (company_id, clave, endpoint, request_hash, estado,
                    created_at, expires_at)
               VALUES (%s, %s, %s, %s, 'en_curso', now(), %s)
               ON CONFLICT (company_id, clave) DO NOTHING""",
            (company_id, clave, endpoint, request_hash, vence))
        if cur.rowcount == 1:
            return Decision("ejecutar")
        fila = _leer(cur, company_id, clave)
        if fila is None:  # pragma: no cover - defensivo
            raise RuntimeError("idempotencia: no se pudo leer la clave")

    if _expirada(fila):
        # Vencida: se reclama como nueva (el TTL es mecanismo de GC).
        cur.execute(
            "DELETE FROM os_idempotency WHERE company_id=%s AND clave=%s",
            (company_id, clave))
        return reclamar(cur, company_id, clave, endpoint, request_hash)

    if fila["estado"] == "completado":
        if fila["request_hash"] == request_hash:
            return Decision("repetir", fila["response_status"],
                            fila["response_body"])
        return Decision("conflicto")

    if fila["estado"] == "fallido":
        # El intento anterior falló: la clave se libera para reintento.
        # Se actualizan endpoint/hash al del reintento (el body pudo
        # corregirse; lo que importa es que el intento previo NO tuvo
        # efecto persistido: su transacción hizo rollback).
        cur.execute(
            """UPDATE os_idempotency
               SET estado='en_curso', endpoint=%s, request_hash=%s,
                   response_status=0, response_body='{}'::jsonb,
                   created_at=now(), expires_at=%s
               WHERE company_id=%s AND clave=%s""",
            (endpoint, request_hash, vence, company_id, clave))
        return Decision("ejecutar")

    # 'en_curso'
    if _abandonada(fila):
        # Worker caído a mitad de operación: reclamar de nuevo en vez
        # de bloquear la clave hasta el TTL.
        cur.execute(
            "DELETE FROM os_idempotency WHERE company_id=%s AND clave=%s",
            (company_id, clave))
        return reclamar(cur, company_id, clave, endpoint, request_hash)
    return Decision("en_curso")


def esperar_resolucion(cur, company_id: str, clave: str, request_hash: str,
                       timeout_s: float = 5.0,
                       intervalo_s: float = 0.25) -> Decision:
    """Espera breve con relectura a que una clave 'en_curso' se resuelva.

    No es busy-loop: duerme `intervalo_s` entre lecturas. Si se agota
    el tiempo, devuelve Decision('ocupado') (el llamador responde 409).
    Dentro de mutar_os este camino es casi inalcanzable (el lock por
    empresa serializa antes de reclamar), pero existe para uso directo
    y modo memoria.
    """
    limite = time.monotonic() + timeout_s
    while True:
        fila = _leer(cur, company_id, clave)
        if fila is None or _expirada(fila) or _abandonada(fila):
            if fila is not None:
                cur.execute(
                    "DELETE FROM os_idempotency"
                    " WHERE company_id=%s AND clave=%s",
                    (company_id, clave))
            vence = _ahora() + timedelta(hours=ttl_horas())
            cur.execute(
                """INSERT INTO os_idempotency
                       (company_id, clave, endpoint, request_hash, estado,
                        created_at, expires_at)
                   VALUES (%s, %s, %s, %s, 'en_curso', now(), %s)
                   ON CONFLICT (company_id, clave) DO NOTHING""",
                (company_id, clave, "", request_hash, vence))
            if cur.rowcount == 1:
                return Decision("ejecutar")
            continue  # otro la tomó: releer
        if fila["estado"] == "completado":
            if fila["request_hash"] == request_hash:
                return Decision("repetir", fila["response_status"],
                                fila["response_body"])
            return Decision("conflicto")
        if fila["estado"] == "fallido":
            vence = _ahora() + timedelta(hours=ttl_horas())
            cur.execute(
                """UPDATE os_idempotency
                   SET estado='en_curso', request_hash=%s,
                       response_status=0, response_body='{}'::jsonb,
                       created_at=now(), expires_at=%s
                   WHERE company_id=%s AND clave=%s""",
                (request_hash, vence, company_id, clave))
            return Decision("ejecutar")
        if time.monotonic() >= limite:
            return Decision("ocupado")
        time.sleep(intervalo_s)


def finalizar(cur, company_id: str, clave: str, status: int,
              body) -> None:
    """Guarda la respuesta y marca 'completado'/'fallido'.

    Se llama ANTES del commit de la operación, con el mismo cursor:
    respuesta y estado se confirman juntos (atomicidad). status < 400
    -> 'completado'; si no -> 'fallido' (tx.py hace rollback igual y
    luego persiste el marcador con marcar_fallido()).
    """
    estado = "completado" if status < 400 else "fallido"
    cur.execute(
        """UPDATE os_idempotency
           SET estado=%s, response_status=%s, response_body=%s::jsonb
           WHERE company_id=%s AND clave=%s""",
        (estado, int(status),
         json.dumps(_normalizar_respuesta(body), ensure_ascii=False),
         company_id, clave))
    if cur.rowcount != 1:  # pragma: no cover - defensivo
        raise RuntimeError("idempotencia: la clave reclamada desapareció")


def marcar_fallido(company_id: str, clave: str, endpoint: str,
                   request_hash: str, status: int, body) -> bool:
    """Persiste el marcador 'fallido' FUERA de la transacción revertida.

    tx.py lo llama después del rollback: la fila 'en_curso' de la
    transacción fallida ya no existe, así que se inserta el marcador
    en una transacción propia. Best-effort: si esto falla (p.ej. la BD
    está caída), devuelve False sin enmascarar el error original.
    """
    try:
        from os_persist.pg import connect
        vence = _ahora() + timedelta(hours=ttl_horas())
        conn = connect()
        try:
            cur = conn.cursor()
            cur.execute(
                """INSERT INTO os_idempotency
                       (company_id, clave, endpoint, request_hash,
                        response_status, response_body, estado,
                        created_at, expires_at)
                   VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'fallido',
                           now(), %s)
                   ON CONFLICT (company_id, clave) DO UPDATE SET
                       endpoint=EXCLUDED.endpoint,
                       request_hash=EXCLUDED.request_hash,
                       response_status=EXCLUDED.response_status,
                       response_body=EXCLUDED.response_body,
                       estado='fallido',
                       created_at=now(),
                       expires_at=EXCLUDED.expires_at""",
                (company_id, clave, endpoint, request_hash, int(status),
                 json.dumps(_normalizar_respuesta(body
                                                 if body is not None
                                                 else {"ok": False}),
                            ensure_ascii=False),
                 vence))
            conn.commit()
            return True
        finally:
            conn.close()
    except Exception:
        return False


def limpiar_expiradas(conn, company_id: str | None = None) -> int:
    """Borra claves vencidas (todas o de una empresa). Hace commit.

    Devuelve cuántas borró. Función de mantenimiento + test; en este
    bloque no hay cron que la invoque.
    """
    cur = conn.cursor()
    if company_id is None:
        cur.execute("DELETE FROM os_idempotency WHERE expires_at <= now()")
    else:
        cur.execute("DELETE FROM os_idempotency"
                    " WHERE company_id=%s AND expires_at <= now()",
                    (company_id,))
    n = cur.rowcount
    conn.commit()
    return n


# ---------------------------------------------------------------------------
# Modo memoria (sin PG): equivalente thread-safe, no duradero
# ---------------------------------------------------------------------------

_mem_lock = threading.Lock()
_mem: dict[tuple[str, str], dict] = {}


def _mem_vence():
    return _ahora() + timedelta(hours=ttl_horas())


def _mem_reclamar_interno(company_id, clave, endpoint,
                          request_hash) -> Decision:
    ahora = _ahora()
    fila = _mem.get((company_id, clave))
    if fila is None or fila["expires_at"] <= ahora:
        _mem[(company_id, clave)] = {
            "endpoint": endpoint, "request_hash": request_hash,
            "response_status": 0, "response_body": {},
            "estado": "en_curso", "created_at": ahora,
            "expires_at": _mem_vence(),
        }
        return Decision("ejecutar")
    if fila["estado"] == "completado":
        if fila["request_hash"] == request_hash:
            return Decision("repetir", fila["response_status"],
                            fila["response_body"])
        return Decision("conflicto")
    if fila["estado"] == "fallido":
        fila.update({"endpoint": endpoint, "request_hash": request_hash,
                     "response_status": 0, "response_body": {},
                     "estado": "en_curso", "created_at": ahora,
                     "expires_at": _mem_vence()})
        return Decision("ejecutar")
    if (ahora - fila["created_at"]).total_seconds() > stale_segs():
        del _mem[(company_id, clave)]
        return _mem_reclamar_interno(company_id, clave, endpoint,
                                     request_hash)
    return Decision("en_curso")


def reclamar_mem(company_id: str, clave: str, endpoint: str,
                 request_hash: str) -> Decision:
    if not clave or len(clave) > _MAX_CLAVE:
        raise ValueError("Idempotency-Key inválida")
    with _mem_lock:
        return _mem_reclamar_interno(company_id, clave, endpoint,
                                     request_hash)


def esperar_resolucion_mem(company_id: str, clave: str, request_hash: str,
                           timeout_s: float = 5.0,
                           intervalo_s: float = 0.25) -> Decision:
    limite = time.monotonic() + timeout_s
    while True:
        with _mem_lock:
            dec = _mem_reclamar_interno(company_id, clave, "", request_hash)
        if dec.kind != "en_curso":
            return dec
        if time.monotonic() >= limite:
            return Decision("ocupado")
        time.sleep(intervalo_s)


def finalizar_mem(company_id: str, clave: str, status: int, body) -> None:
    with _mem_lock:
        fila = _mem.get((company_id, clave))
        if fila is None:  # pragma: no cover - defensivo
            raise RuntimeError("idempotencia: la clave reclamada desapareció")
        fila["estado"] = "completado" if status < 400 else "fallido"
        fila["response_status"] = int(status)
        fila["response_body"] = _normalizar_respuesta(body)


def marcar_fallido_mem(company_id: str, clave: str, endpoint: str,
                       request_hash: str, status: int, body) -> None:
    with _mem_lock:
        _mem[(company_id, clave)] = {
            "endpoint": endpoint, "request_hash": request_hash,
            "response_status": int(status),
            "response_body": _normalizar_respuesta(
                body if body is not None else {"ok": False}),
            "estado": "fallido", "created_at": _ahora(),
            "expires_at": _mem_vence(),
        }


def limpiar_expiradas_mem(company_id: str | None = None) -> int:
    ahora = _ahora()
    with _mem_lock:
        claves = [k for k, f in _mem.items()
                  if f["expires_at"] <= ahora
                  and (company_id is None or k[0] == company_id)]
        for k in claves:
            del _mem[k]
        return len(claves)
