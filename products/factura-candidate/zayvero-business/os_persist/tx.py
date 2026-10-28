"""REFORZAMIENTO — Mutaciones transaccionales del Business OS.

Problema que resuelve: con snapshots, dos procesos podían leer el mismo
estado, mutar en memoria y pisarse al guardar (lost update).

Diseño:
- Cada mutación abre SU propia conexión PG y transacción.
- `SELECT ... FOR UPDATE` sobre `os_company_lock` serializa a los
  escritores de la misma empresa (una sola fila por empresa: sin deadlocks).
- Se hidrata un bundle FRESCO desde PG dentro de la transacción
  (el estado autoritativo, no la caché).
- Solo si la operación reporta éxito (2xx) se escriben los snapshots,
  se incrementa la versión y se hace COMMIT.
- En éxito: la caché compartida se reemplaza por el bundle fresco.
- En fallo: ROLLBACK + se invalida la caché (próximo GET re-hidrata).
  Ningún GET muestra operaciones no guardadas (requisito 3).

AUDITORÍA (BLOQUE 1, 2026-10-12): `fn` puede llamar a
`os_persist.auditoria.auditar(...)`. Los eventos se encolan en una cola
thread-local y se insertan en os_eventos con el cursor de ESTA MISMA
transacción, ANTES del commit, solo si la operación reportó éxito
(status < 400). Si falla o lanza excepción: rollback y la cola se
descarta — ningún evento queda registrado como exitoso.

IDEMPOTENCIA (BLOQUE 2, 2026-10-12): `mutar_os(company_id, fn, idem)` con
`idem = (clave, endpoint, request_hash)` reclama la Idempotency-Key con
el cursor de ESTA MISMA transacción (ver os_persist/idempotencia.py),
antes de ejecutar `fn`:
- clave nueva o liberada -> 'en_curso', se ejecuta fn y al final se
  guarda la respuesta ('completado'/'fallido') pre-commit;
- clave 'completado' + mismo hash -> se devuelve la respuesta
  almacenada SIN ejecutar fn (ni snapshots, ni eventos, ni versión);
- clave 'completado' + distinto hash -> 422 sin ejecutar fn;
- clave 'en_curso' fresca de otro intento -> espera breve con
  relectura; si no se resuelve -> 409.
Si la operación falla (status >= 400 o excepción): rollback y la clave
se marca 'fallido' en una transacción propia (best-effort) para que el
cliente pueda reintentar con la misma clave.

NORMALIZACIÓN (BLOQUE 5, 2026-10-12): en la misma transacción y solo
si la operación reportó éxito, se escriben además las filas
normalizadas correspondientes (os_movimientos, os_ordenes,
os_recepciones, os_ventas, os_cuentas, os_gastos, os_clientes,
os_documentos, os_saldos) vía
os_persist/normalizacion.py::sincronizar_write_through, con el MISMO
cursor: rollback borra también esas filas. Ver docs/normalizacion.md.
"""
from __future__ import annotations

import os
import threading

_tx_local = threading.local()


def tx_ambiente():
    """(company_id, cursor) de la transacción mutar_os activa en este hilo.

    Devuelve (None, None) si no hay. Permite que los helpers de bloque
    (p. ej. os_empleados.tx_empleados) PARTICIPEN en la transacción
    ambiente en vez de abrir una conexión anidada — que se interbloquea
    con el lock de empresa (deadlock por transactionid indetectable).
    """
    return getattr(_tx_local, "actual", (None, None))


def _backend_activo() -> bool:
    return os.environ.get("ZAYVERO_OS_BACKEND", "memoria") == "postgres"


def _idem_error(kind: str):
    """(status, body) para decisiones que no ejecutan la operación."""
    if kind == "conflicto":
        return (422, {"ok": False,
                      "error": "Idempotency-Key ya usada con otro cuerpo; "
                               "use una clave distinta para una operación "
                               "distinta"})
    # 'ocupado'
    return (409, {"ok": False,
                  "error": "operación con esta Idempotency-Key aún en "
                           "curso; reintente en unos segundos"})


def mutar_os(company_id: str, fn, idem=None):
    """Ejecuta fn(bundle_fresco) -> (status, body) en transacción.

    idem: None | (clave, endpoint, request_hash) — ver docstring del
    módulo (BLOQUE 2). Sin backend PG: equivalente en memoria
    (no duradero).

    Sin backend PG y sin idem: ejecuta fn sobre el bundle en memoria
    (comportamiento anterior, sin durabilidad).
    """
    if not _backend_activo():
        return _mutar_os_memoria(company_id, fn, idem)

    from os_persist.pg import connect
    from webapp.os_api import OSBundle, _BUNDLES, _LOCK
    from os_persist.snapshots import (cargar_snapshot, guardar_snapshot,
                                      exportar_bundle)
    from os_persist.auditoria import (_activar_cola, _tomar_cola,
                                      insertar_evento)
    from os_persist import idempotencia as _idem

    conn = connect()
    _activar_cola()  # los auditar() de fn() se encolan hasta el commit
    clave = None  # (clave, endpoint, request_hash) una vez reclamada
    try:
        conn.autocommit = False
        cur = conn.cursor()
        # Fila de bloqueo (se crea si no existe) + bloqueo pesimista
        cur.execute(
            """INSERT INTO os_companies (company_id) VALUES (%s)
               ON CONFLICT (company_id) DO NOTHING""", (company_id,))
        cur.execute(
            """INSERT INTO os_company_lock (company_id) VALUES (%s)
               ON CONFLICT (company_id) DO NOTHING""", (company_id,))
        cur.execute(
            "SELECT version FROM os_company_lock WHERE company_id=%s"
            " FOR UPDATE", (company_id,))
        # La transacción ambiente queda visible para helpers re-entrantes
        # (tx_empleados): participan en ella en vez de abrir otra conexión.
        _tx_local.actual = (company_id, cur)
        fila = cur.fetchone()
        version = fila[0] if fila else 0

        # BLOQUE 2: la reclamación va DENTRO de la transacción, con el
        # lock por empresa ya tomado: dos peticiones simultáneas con la
        # misma clave no pueden ejecutar dos veces (la segunda espera
        # en el FOR UPDATE y luego ve 'completado'/'fallido').
        if idem is not None:
            k, endpoint, rhash = idem
            dec = _idem.reclamar(cur, company_id, k, endpoint, rhash)
            if dec.kind == "en_curso":
                dec = _idem.esperar_resolucion(cur, company_id, k, rhash)
            if dec.kind == "repetir":
                conn.rollback()  # nada cambió; liberar limpio
                return dec.status, dec.body
            if dec.kind in ("conflicto", "ocupado"):
                conn.rollback()
                return _idem_error(dec.kind)
            clave = (k, endpoint, rhash)  # 'ejecutar'

        # Bundle fresco hidratado del estado autoritativo
        fresco = OSBundle.crear(company_id)
        cargar_snapshot(conn, company_id, fresco)

        # BLOQUE 5: captura de claves para el write-through normalizado
        # (diff de lo que fn() agregue; se escribe pre-commit, misma tx).
        from os_persist import normalizacion as _norm
        antes = _norm.claves_antes(fresco)

        status, body = fn(fresco)

        # BLOQUE 1: eventos de auditoría encolados por fn(). Solo se
        # persisten si la operación reportó éxito; van en la MISMA
        # transacción que los snapshots (atomicidad evento+estado).
        pendientes = _tomar_cola()
        if status < 400:
            for ev in pendientes:
                insertar_evento(cur, ev)
            if clave is not None:
                # BLOQUE 2: la respuesta se guarda pre-commit, en la
                # misma transacción que el estado (atomicidad).
                _idem.finalizar(cur, company_id, clave[0], status, body)
            # BLOQUE 5: write-through normalizado en la MISMA
            # transacción (antes de los snapshots; el rollback lo
            # borra todo junto).
            _norm.sincronizar_write_through(cur, company_id, fresco, antes)
            guardar_snapshot(conn, company_id, fresco, commit=False)
            cur.execute(
                """UPDATE os_company_lock
                   SET version=%s, updated_at=now()
                   WHERE company_id=%s AND version=%s""",
                (version + 1, company_id, version))
            if cur.rowcount != 1:
                raise RuntimeError("conflicto de versión en os_company_lock")
            conn.commit()
            # Éxito: la caché pasa a reflejar lo guardado
            with _LOCK:
                _BUNDLES[company_id] = fresco
        else:
            conn.rollback()
            if clave is not None:
                # BLOQUE 2: la operación falló -> la clave queda
                # 'fallido' (transacción propia, best-effort) para que
                # el cliente pueda reintentar.
                _idem.marcar_fallido(company_id, clave[0], clave[1],
                                     clave[2], status, body)
        return status, body
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        if clave is not None:
            # BLOQUE 2: excepción -> también libera la clave como
            # 'fallido' (500). Best-effort: no enmascara el error.
            _idem.marcar_fallido(company_id, clave[0], clave[1], clave[2],
                                 500, {"ok": False,
                                       "error": "error interno"})
        # Fallo: invalidar la caché para que ningún GET vea estado sucio
        from webapp.os_api import _BUNDLES as _B, _LOCK as _L
        with _L:
            _B.pop(company_id, None)
        raise
    finally:
        _tx_local.actual = (None, None)  # fin de la transacción ambiente
        _tomar_cola()  # descarta eventos pendientes si hubo fallo/excepción
        try:
            conn.close()
        except Exception:
            pass


def _mutar_os_memoria(company_id: str, fn, idem=None):
    """Equivalente en memoria (sin durabilidad) con soporte de idem."""
    from webapp.os_api import get_bundle
    if idem is None:
        return fn(get_bundle(company_id))
    from os_persist import idempotencia as _idem
    k, endpoint, rhash = idem
    dec = _idem.reclamar_mem(company_id, k, endpoint, rhash)
    if dec.kind == "en_curso":
        dec = _idem.esperar_resolucion_mem(company_id, k, rhash)
    if dec.kind == "repetir":
        return dec.status, dec.body
    if dec.kind in ("conflicto", "ocupado"):
        return _idem_error(dec.kind)
    try:
        status, body = fn(get_bundle(company_id))
    except Exception:
        _idem.marcar_fallido_mem(company_id, k, endpoint, rhash, 500,
                                 {"ok": False, "error": "error interno"})
        raise
    if status < 400:
        _idem.finalizar_mem(company_id, k, status, body)
    else:
        _idem.marcar_fallido_mem(company_id, k, endpoint, rhash,
                                 status, body)
    return status, body
