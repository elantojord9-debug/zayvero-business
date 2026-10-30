"""BLOQUE IA · 6 — Persistencia PostgreSQL de propuestas y Centro de
Decisiones (P1c, 2026-10-13, migración 020).

Replica en PG lo que `autopilot.py` y `decisiones.py` guardaban en
memoria por company_id (se perdía al reiniciar). Patrón copiado de
`os_empleados/almacen.py`:

- `tx_ia(company_id)`: transacción de escritura con lock por empresa
  (`os_company_lock`); RE-ENTRANTE: si el hilo ya corre dentro de un
  `mutar_os` para la misma empresa (p. ej. una ruta POST cableada en
  webapp), PARTICIPA en esa transacción en vez de abrir una conexión
  anidada (que se interbloquea con el lock de empresa).
- `lectura_ia_pg(company_id)`: conexión de solo lectura (sin lock).
- `AlmacenPGIA(cur, company_id, auditar)`: operaciones; toda lectura
  filtra por company_id; los dicts son JSON-serializables.

CICLO DE VIDA (propuesta):
  propuesta -> (aprobación humana en aprobaciones/) -> ejecutada
  1. `guardar_propuesta`      estado 'propuesta' (idempotente).
  2. `solicitar_aprobacion`   crea la solicitud en os_aprobaciones
     (tipo 'ia_propuesta') y la vincula; NO cambia el estado.
  3. `refrescar_desde_aprobacion` lee la decisión humana: aprobada ->
     'aprobada'; rechazada/cancelada -> 'rechazada'.
  4. `registrar_ejecucion`    solo desde 'aprobada' y con la aprobación
     en 'aprobada': marca AMBAS (propuesta + aprobación) como
     'ejecutada' en la MISMA transacción, una sola vez.

REGLA DURA — este módulo NUNCA ejecuta operaciones financieras ni
externas: no llama a ventas, compras, inventario, tesorería ni gastos;
no toca la red; no programa trabajos. `registrar_ejecucion` solo
REGISTRA que un humano ejecutó la acción fuera del sistema (igual que
el Centro de Decisiones en memoria). Hay una prueba que verifica por
inspección del código fuente que no existe ruta de ejecución
(tests/test_p1c_ia_pg.py).
"""
from __future__ import annotations

import json
import os as _os
import sys
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from .base import verificar_aislamiento, q1  # noqa: E402

TIPOS_PROPUESTA = ("reordenar", "ajustar_precio", "revisar_proveedor",
                   "revisar_producto")
ESTADOS_PROPUESTA = ("propuesta", "aprobada", "rechazada", "ejecutada")
FUENTES_DECISION = ("inspector", "shield", "advisor", "autopilot")
ESTADOS_DECISION = ("pendiente", "aprobada", "rechazada", "ejecutada")
_TRANSICIONES_DECISION = {"pendiente": ("aprobada", "rechazada"),
                          "aprobada": ("ejecutada",),
                          "rechazada": (), "ejecutada": ()}


class ErrorIA(Exception):
    """Error de dominio del bloque IA persistente (propuesta/decision
    inexistente, doble ejecución, transición inválida, sin aprobación)."""


# --- serialización ------------------------------------------------------
def _js(v):
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, datetime):
        return v.isoformat()
    if hasattr(v, "isoformat"):  # date
        return v.isoformat()
    return v


def _fila_dict(cur) -> list[dict]:
    cols = [d[0] for d in cur.description]
    return [{c: _js(v) for c, v in zip(cols, fila)}
            for fila in cur.fetchall()]


def _una(cur) -> dict | None:
    filas = _fila_dict(cur)
    return filas[0] if filas else None


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- eventos de auditoría -------------------------------------------------
def _evento(company_id: str, tipo: str, actor: str, ref_id: str,
            detalle: dict) -> dict:
    return {
        "evento_id": uuid.uuid4().hex,
        "company_id": company_id,
        "tipo": tipo,
        "actor": actor or "",
        "ref_id": ref_id or "",
        "detalle": detalle or {},
        "resultado": "exito",
    }


class _TxPG:
    """Transacción propia del bloque IA (conexión + lock por empresa)."""

    def __init__(self, conn, company_id: str):
        self.conn = conn
        self.cur = conn.cursor()
        self.company_id = company_id
        self._eventos: list[dict] = []

    def auditar(self, tipo: str, actor: str, ref_id: str,
                detalle: dict | None = None) -> None:
        from os_persist.auditoria import TIPOS_EVENTO
        if tipo not in TIPOS_EVENTO:
            raise ErrorIA(f"tipo de evento inválido: {tipo!r}")
        self._eventos.append(
            _evento(self.company_id, tipo, actor, ref_id, detalle or {}))

    def commit(self) -> None:
        from os_persist.auditoria import insertar_evento
        for ev in self._eventos:
            insertar_evento(self.cur, ev)
        self._eventos = []
        self.conn.commit()

    def rollback(self) -> None:
        self._eventos = []
        self.conn.rollback()


class _TxAmbiente:
    """Adaptador re-entrante: participa en la transacción ambiente de
    mutar_os. No abre conexión, no toma lock, no hace commit/rollback.
    La auditoría usa la cola global de mutar_os vía
    os_persist.auditoria.auditar (se inserta pre-commit)."""

    def __init__(self, cur, company_id: str):
        self.cur = cur
        self.company_id = company_id

    def auditar(self, tipo: str, actor: str, ref_id: str,
                detalle: dict | None = None) -> None:
        from os_persist.auditoria import auditar, TIPOS_EVENTO
        if tipo not in TIPOS_EVENTO:
            raise ErrorIA(f"tipo de evento inválido: {tipo!r}")
        auditar(self.company_id, tipo, actor, ref_id, detalle or {})


@contextmanager
def tx_ia(company_id: str):
    """Transacción de escritura del bloque IA (patrón tx_empleados).

    Re-entrante: dentro de un mutar_os para la misma empresa participa
    en esa transacción (sin commit/rollback propios).
    """
    if not company_id:
        raise ErrorIA("company_id es obligatorio")
    from os_persist.tx import tx_ambiente
    amb_cid, amb_cur = tx_ambiente()
    if amb_cid == company_id and amb_cur is not None:
        yield _TxAmbiente(amb_cur, company_id)
        return
    from os_persist.pg import connect
    conn = connect()
    tx = None
    try:
        conn.autocommit = False
        tx = _TxPG(conn, company_id)
        tx.cur.execute(
            "INSERT INTO os_companies (company_id) VALUES (%s)"
            " ON CONFLICT (company_id) DO NOTHING", (company_id,))
        tx.cur.execute(
            "INSERT INTO os_company_lock (company_id) VALUES (%s)"
            " ON CONFLICT (company_id) DO NOTHING", (company_id,))
        tx.cur.execute(
            "SELECT version FROM os_company_lock WHERE company_id=%s"
            " FOR UPDATE", (company_id,))
        yield tx
        tx.commit()
    except Exception:
        if tx is not None:
            tx.rollback()
        raise
    finally:
        conn.close()


@contextmanager
def lectura_ia_pg(company_id: str):
    """Conexión de solo lectura (sin lock). Yields (conn, AlmacenPGIA)."""
    if not company_id:
        raise ErrorIA("company_id es obligatorio")
    from os_persist.pg import connect
    conn = connect()
    try:
        conn.autocommit = True
        yield conn, AlmacenPGIA(conn.cursor(), company_id)
    finally:
        conn.close()


_COLS_PROP = ("company_id, propuesta_id, idempotencia, tipo, titulo_es,"
              " titulo_en, descripcion_es, descripcion_en, payload, fuente,"
              " evidencia, impacto_estimado, estado, aprobacion_id,"
              " creada_por, decidida_por, decidida_at, motivo_decision,"
              " creada_at, actualizada_at")
_COLS_DEC = ("company_id, decision_id, fuente, problema, causa_probable,"
             " evidencia, impacto_estimado, accion_recomendada,"
             " responsable_sugerido, estado, aprobacion_id, creada_at,"
             " actualizada_at")


class AlmacenPGIA:
    """Operaciones del bloque IA sobre PostgreSQL (migración 020)."""

    def __init__(self, cur, company_id: str, auditar=None):
        if not company_id:
            raise ErrorIA("company_id es obligatorio")
        self.cur = cur
        self.c = company_id
        self._auditar = auditar

    def _audit(self, tipo: str, actor: str, ref_id: str,
               detalle: dict | None = None) -> None:
        if self._auditar is not None:
            self._auditar(tipo, actor, ref_id, detalle or {})

    def _historial_prop(self, propuesta_id: str, actor: str,
                        anterior: str, nuevo: str, nota: str = "") -> None:
        self.cur.execute(
            """INSERT INTO os_ia_propuestas_historial
                   (hist_id, company_id, propuesta_id, actor,
                    estado_anterior, estado_nuevo, nota)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (uuid.uuid4().hex, self.c, propuesta_id, actor or "",
             anterior, nuevo, (nota or "")[:500]))

    def _historial_dec(self, decision_id: str, actor: str,
                       anterior: str, nuevo: str, nota: str = "") -> None:
        self.cur.execute(
            """INSERT INTO os_centro_decisiones_historial
                   (hist_id, company_id, decision_id, actor,
                    estado_anterior, estado_nuevo, nota)
               VALUES (%s,%s,%s,%s,%s,%s,%s)""",
            (uuid.uuid4().hex, self.c, decision_id, actor or "",
             anterior, nuevo, (nota or "")[:500]))

    # ---- propuestas ---------------------------------------------------
    @staticmethod
    def _params_propuesta(p) -> tuple:
        d = p.to_dict() if hasattr(p, "to_dict") else dict(p)
        tipo = d.get("tipo", "")
        if tipo not in TIPOS_PROPUESTA:
            raise ErrorIA(f"tipo de propuesta inválido: {tipo!r}")
        return (
            d.get("id") or d.get("propuesta_id") or "",
            d.get("idempotencia") or d.get("id") or d.get("propuesta_id")
            or "",
            tipo,
            d.get("titulo_es", "") or "", d.get("titulo_en", "") or "",
            d.get("descripcion_es", "") or "",
            d.get("descripcion_en", "") or "",
            json.dumps(d.get("payload") or {}),
            json.dumps(d.get("fuente") or {}),
            json.dumps(d.get("evidencia") or {}),
            (json.dumps(d.get("impacto_estimado"))
             if d.get("impacto_estimado") is not None else None),
            d.get("creada_por", "autopilot") or "autopilot",
        )

    def guardar_propuesta(self, p) -> dict:
        """Persiste la propuesta (idempotente por idempotencia).

        Si ya existe, la devuelve intacta: conserva el estado ya
        decidido (igual que el _guardar en memoria del Autopilot).
        """
        (pid, idem, tipo, tes, ten, des, den, pay, fue, evi, imp,
         creada_por) = self._params_propuesta(p)
        if not pid:
            raise ErrorIA("la propuesta necesita un id")
        cur = self.cur
        cur.execute(
            """INSERT INTO os_ia_propuestas
                   (company_id, propuesta_id, idempotencia, tipo,
                    titulo_es, titulo_en, descripcion_es, descripcion_en,
                    payload, fuente, evidencia, impacto_estimado,
                    creada_por)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,
                       %s::jsonb,%s::jsonb,%s::jsonb,%s::jsonb,%s)
               ON CONFLICT (company_id, idempotencia) DO NOTHING""",
            (self.c, pid, idem, tipo, tes, ten, des, den, pay, fue, evi,
             imp, creada_por))
        if cur.rowcount == 1:
            self._historial_prop(pid, creada_por, "-", "propuesta",
                                 "propuesta creada por el Autopilot")
            self._audit("ia_propuesta", creada_por, pid,
                        {"accion": "crear", "tipo": tipo,
                         "idempotencia": idem})
        fila = self.obtener_propuesta(pid)
        # Toda propuesta persistida tiene su decisión espejo en el
        # Centro (fuente 'autopilot'); idempotente.
        self.registrar_decision_propuesta(fila)
        return fila

    def obtener_propuesta(self, propuesta_id: str) -> dict | None:
        self.cur.execute(
            f"SELECT {_COLS_PROP} FROM os_ia_propuestas"
            " WHERE company_id=%s AND propuesta_id=%s",
            (self.c, propuesta_id))
        return _una(self.cur)

    def listar_propuestas(self, estado: str | None = None,
                          tipo: str | None = None) -> list[dict]:
        if estado is not None and estado not in ESTADOS_PROPUESTA:
            raise ErrorIA(f"estado inválido: {estado!r}")
        if tipo is not None and tipo not in TIPOS_PROPUESTA:
            raise ErrorIA(f"tipo inválido: {tipo!r}")
        sql = (f"SELECT {_COLS_PROP} FROM os_ia_propuestas"
               " WHERE company_id=%s")
        params: list = [self.c]
        if estado:
            sql += " AND estado=%s"
            params.append(estado)
        if tipo:
            sql += " AND tipo=%s"
            params.append(tipo)
        sql += " ORDER BY creada_at, propuesta_id"
        self.cur.execute(sql, params)
        return _fila_dict(self.cur)

    def solicitar_aprobacion(self, propuesta_id: str, solicitante: str,
                             motivo: str = "", monto=None,
                             moneda: str | None = None) -> dict:
        """Vincula la propuesta a una solicitud en aprobaciones/.

        Crea la solicitud (tipo 'ia_propuesta') y la vincula en la
        MISMA transacción: o ambas quedan o ninguna (atomicidad).
        No cambia el estado de la propuesta: la decisión la toma el
        humano en aprobaciones/. Idempotente: si ya hay solicitud
        vinculada, la devuelve sin crear otra.
        """
        from aprobaciones.modelo import validar_tipo
        validar_tipo("ia_propuesta")
        if not solicitante:
            raise ErrorIA("solicitante es obligatorio")
        cur = self.cur
        prop = self.obtener_propuesta(propuesta_id)
        if prop is None:
            raise ErrorIA(f"propuesta inexistente: {propuesta_id}")
        if prop["estado"] != "propuesta":
            raise ErrorIA(
                f"la propuesta ya está {prop['estado']}; no se puede "
                "solicitar aprobación")
        if prop["aprobacion_id"]:
            return self._solicitud_vinculada(prop)
        aid = f"apr-{uuid.uuid4().hex[:12]}"
        cur.execute(
            "INSERT INTO os_companies (company_id) VALUES (%s)"
            " ON CONFLICT (company_id) DO NOTHING", (self.c,))
        cur.execute(
            """INSERT INTO os_aprobaciones
                   (company_id, aprobacion_id, tipo, estado, solicitante,
                    payload, monto, moneda, motivo_solicitud)
               VALUES (%s,%s,'ia_propuesta','pendiente_aprobacion',
                       %s,%s,%s,%s,%s)""",
            (self.c, aid, solicitante or "",
             json.dumps({"propuesta_id": propuesta_id,
                         "tipo_propuesta": prop["tipo"],
                         "titulo": prop["titulo_es"],
                         "origen": "os_ia"}),
             str(monto) if monto is not None else None,
             moneda or None, motivo or ""))
        cur.execute(
            """UPDATE os_ia_propuestas SET aprobacion_id=%s,
                      actualizada_at=now()
               WHERE company_id=%s AND propuesta_id=%s
                 AND estado='propuesta' AND aprobacion_id IS NULL""",
            (aid, self.c, propuesta_id))
        if cur.rowcount != 1:
            raise ErrorIA(
                f"la propuesta {propuesta_id} cambió de estado durante "
                "la solicitud")
        self._historial_prop(
            propuesta_id, solicitante, "propuesta", "propuesta",
            f"solicitud de aprobación {aid} creada")
        self._audit("ia_propuesta", solicitante, propuesta_id,
                    {"accion": "solicitar_aprobacion",
                     "aprobacion_id": aid})
        return self._solicitud_vinculada(
            self.obtener_propuesta(propuesta_id))

    def _solicitud_vinculada(self, prop: dict) -> dict:
        self.cur.execute(
            "SELECT aprobacion_id, tipo, estado, solicitante,"
            " motivo_solicitud, created_at, decidida_por, decidida_at,"
            " motivo_decision FROM os_aprobaciones"
            " WHERE company_id=%s AND aprobacion_id=%s",
            (self.c, prop["aprobacion_id"]))
        apr = _una(self.cur)
        return {"ok": True, "propuesta": prop, "aprobacion": apr,
                "nota": ("solicitud vinculada; la decisión la toma un "
                         "humano en aprobaciones/")}

    def refrescar_desde_aprobacion(self, propuesta_id: str,
                                   actor: str = "sistema") -> dict:
        """Sincroniza el estado de la propuesta con la decisión humana.

        aprobada -> 'aprobada'; rechazada/cancelada -> 'rechazada'.
        pendiente_aprobacion -> sin cambio. Idempotente.
        """
        prop = self.obtener_propuesta(propuesta_id)
        if prop is None:
            raise ErrorIA(f"propuesta inexistente: {propuesta_id}")
        if prop["estado"] != "propuesta":
            return prop  # ya decidida: nada que refrescar
        if not prop["aprobacion_id"]:
            raise ErrorIA(
                f"{propuesta_id} no tiene solicitud de aprobación "
                "vinculada")
        self.cur.execute(
            "SELECT estado, decidida_por, decidida_at, motivo_decision"
            " FROM os_aprobaciones WHERE company_id=%s"
            " AND aprobacion_id=%s",
            (self.c, prop["aprobacion_id"]))
        apr = _una(self.cur)
        if apr is None:
            raise ErrorIA(
                f"solicitud {prop['aprobacion_id']} inexistente")
        estado_apr = apr["estado"]
        if estado_apr == "pendiente_aprobacion":
            return prop
        nuevo = ("aprobada" if estado_apr == "aprobada"
                 else "rechazada")
        self.cur.execute(
            """UPDATE os_ia_propuestas
               SET estado=%s, decidida_por=%s, decidida_at=%s,
                   motivo_decision=%s, actualizada_at=now()
               WHERE company_id=%s AND propuesta_id=%s
                 AND estado='propuesta'""",
            (nuevo, apr["decidida_por"] or "", apr["decidida_at"],
             apr["motivo_decision"] or "", self.c, propuesta_id))
        if cur_rowcount(self.cur) != 1:
            return self.obtener_propuesta(propuesta_id)
        self._historial_prop(
            propuesta_id, actor or "sistema", "propuesta", nuevo,
            f"decisión humana en aprobaciones/ ({prop['aprobacion_id']})")
        self._audit("ia_propuesta", actor or "sistema", propuesta_id,
                    {"accion": "decision_humana", "estado": nuevo,
                     "aprobacion_id": prop["aprobacion_id"]})
        self._espejo_decision(propuesta_id, nuevo, actor or "sistema",
                              prop["aprobacion_id"])
        return self.obtener_propuesta(propuesta_id)

    def registrar_ejecucion(self, propuesta_id: str, usuario: str,
                            nota: str = "") -> dict:
        """Registra que un HUMANO ejecutó la acción fuera del sistema.

        FAIL-CLOSED: exige propuesta 'aprobada' Y solicitud en
        'aprobada'. Sin aprobación humana previa -> ErrorIA y nada
        cambia. Solo se ejecuta UNA vez: el UPDATE es condicional
        (WHERE estado='aprobada'); el segundo intento falla.

        Este método NO ejecuta ninguna operación financiera ni externa:
        solo marca estados (propuesta + aprobación + decisión del
        centro) en la misma transacción.
        """
        if not usuario:
            raise ErrorIA("usuario es obligatorio")
        cur = self.cur
        prop = self.obtener_propuesta(propuesta_id)
        if prop is None:
            raise ErrorIA(f"propuesta inexistente: {propuesta_id}")
        if prop["estado"] != "aprobada":
            raise ErrorIA(
                f"{propuesta_id} no está aprobada (estado: "
                f"{prop['estado']}); sin aprobación humana NUNCA se "
                "ejecuta")
        if not prop["aprobacion_id"]:
            raise ErrorIA(f"{propuesta_id} no tiene aprobación vinculada")
        # 1) la aprobación pasa a 'ejecutada' solo desde 'aprobada'
        cur.execute(
            """UPDATE os_aprobaciones
               SET estado='ejecutada', ejecutada_at=now()
               WHERE company_id=%s AND aprobacion_id=%s
                 AND estado='aprobada'""",
            (self.c, prop["aprobacion_id"]))
        if cur.rowcount != 1:
            raise ErrorIA(
                f"la aprobación {prop['aprobacion_id']} no está en "
                "'aprobada'; ejecución bloqueada")
        # 2) la propuesta pasa a 'ejecutada' solo desde 'aprobada'
        #    (doble ejecución imposible: el segundo intento ve 0 filas)
        cur.execute(
            """UPDATE os_ia_propuestas SET estado='ejecutada',
                      actualizada_at=now()
               WHERE company_id=%s AND propuesta_id=%s
                 AND estado='aprobada'""",
            (self.c, propuesta_id))
        if cur.rowcount != 1:
            raise ErrorIA(
                f"{propuesta_id} ya no está aprobada; la ejecución no "
                "se registró dos veces")
        self._historial_prop(
            propuesta_id, usuario, "aprobada", "ejecutada",
            (nota or "humano ejecutó la acción fuera del sistema")[:500])
        self._audit("ia_propuesta", usuario, propuesta_id,
                    {"accion": "ejecutar",
                     "aprobacion_id": prop["aprobacion_id"],
                     "nota": (nota or "")[:200]})
        self._espejo_decision(propuesta_id, "ejecutada", usuario,
                              prop["aprobacion_id"])
        return self.obtener_propuesta(propuesta_id)

    # ---- centro de decisiones -----------------------------------------
    def registrar_decision_propuesta(self, prop: dict) -> dict:
        """Registra la decisión espejo de una propuesta del Autopilot
        (id `dec:autopilot:<propuesta_id>`, fuente 'autopilot').
        Idempotente: si ya existe (p. ej. ya decidida), la conserva."""
        from . import decisiones as _decisiones
        return self.registrar_decision({
            "decision_id": f"dec:autopilot:{prop['propuesta_id']}",
            "fuente": "autopilot",
            "problema": prop.get("titulo_es", ""),
            "causa_probable": prop.get("descripcion_es", ""),
            "evidencia": {"propuesta_id": prop["propuesta_id"],
                          "payload": prop.get("payload") or {}},
            "impacto_estimado": prop.get("impacto_estimado"),
            "accion_recomendada": ("Aprobar o rechazar la propuesta en "
                                   "ia/autopilot/solicitar_aprobacion."),
            "responsable_sugerido": _decisiones._responsable(
                prop.get("tipo", "")),
        })

    def registrar_decision(self, d: dict) -> dict:
        """Persiste una decisión (idempotente por decision_id).

        Si ya existe, la devuelve intacta (conserva el estado).
        """
        did = (d.get("id") or d.get("decision_id") or "").strip()
        if not did:
            raise ErrorIA("la decisión necesita un id")
        fuente = d.get("fuente", "autopilot")
        if fuente not in FUENTES_DECISION:
            raise ErrorIA(f"fuente inválida: {fuente!r}")
        cur = self.cur
        cur.execute(
            """INSERT INTO os_centro_decisiones
                   (company_id, decision_id, fuente, problema,
                    causa_probable, evidencia, impacto_estimado,
                    accion_recomendada, responsable_sugerido)
               VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)
               ON CONFLICT (company_id, decision_id) DO NOTHING""",
            (self.c, did, fuente,
             d.get("problema", "") or "",
             d.get("causa_probable", "") or "",
             json.dumps(d.get("evidencia") or {}),
             (json.dumps(d.get("impacto_estimado"))
              if d.get("impacto_estimado") is not None else None),
             d.get("accion_recomendada", "") or "",
             d.get("responsable_sugerido", "gerencia") or "gerencia"))
        if cur.rowcount == 1:
            self._historial_dec(did, "sistema", "-", "pendiente",
                                "decisión registrada")
            self._audit("ia_decision", "sistema", did,
                        {"accion": "crear", "fuente": fuente})
        return self.obtener_decision(did)

    def obtener_decision(self, decision_id: str) -> dict | None:
        self.cur.execute(
            f"SELECT {_COLS_DEC} FROM os_centro_decisiones"
            " WHERE company_id=%s AND decision_id=%s",
            (self.c, decision_id))
        return _una(self.cur)

    def listar_decisiones(self, estado: str | None = None,
                          fuente: str | None = None) -> list[dict]:
        if estado is not None and estado not in ESTADOS_DECISION:
            raise ErrorIA(f"estado inválido: {estado!r}")
        if fuente is not None and fuente not in FUENTES_DECISION:
            raise ErrorIA(f"fuente inválida: {fuente!r}")
        sql = (f"SELECT {_COLS_DEC} FROM os_centro_decisiones"
               " WHERE company_id=%s")
        params: list = [self.c]
        if estado:
            sql += " AND estado=%s"
            params.append(estado)
        if fuente:
            sql += " AND fuente=%s"
            params.append(fuente)
        sql += (" ORDER BY CASE estado WHEN 'pendiente' THEN 0"
                " WHEN 'aprobada' THEN 1 WHEN 'rechazada' THEN 2"
                " ELSE 3 END, decision_id")
        self.cur.execute(sql, params)
        return _fila_dict(self.cur)

    def cambiar_estado_decision(self, decision_id: str, estado: str,
                                usuario: str, nota: str = "") -> dict:
        """Cambia el estado registrando quién, cuándo y la transición."""
        if estado not in ESTADOS_DECISION:
            raise ErrorIA(f"estado inválido: {estado!r}")
        if not usuario:
            raise ErrorIA("usuario es obligatorio")
        dec = self.obtener_decision(decision_id)
        if dec is None:
            raise ErrorIA(f"decisión inexistente: {decision_id}")
        anterior = dec["estado"]
        if estado not in _TRANSICIONES_DECISION[anterior]:
            raise ErrorIA(
                f"transición inválida: {anterior} -> {estado}")
        self.cur.execute(
            """UPDATE os_centro_decisiones SET estado=%s,
                      actualizada_at=now()
               WHERE company_id=%s AND decision_id=%s AND estado=%s""",
            (estado, self.c, decision_id, anterior))
        if cur_rowcount(self.cur) != 1:
            raise ErrorIA(
                f"{decision_id} cambió de estado concurrentemente")
        self._historial_dec(decision_id, usuario, anterior, estado, nota)
        self._audit("ia_decision", usuario, decision_id,
                    {"accion": "cambio_estado",
                     "estado_anterior": anterior,
                     "estado_nuevo": estado,
                     "nota": (nota or "")[:200]})
        return self.obtener_decision(decision_id)

    def _espejo_decision(self, propuesta_id: str, estado_prop: str,
                         actor: str, aprobacion_id: str | None) -> None:
        """Espeja el ciclo de la propuesta en la decisión vinculada
        (dec:autopilot:<propuesta_id>), si existe. Mapeo: aprobada ->
        aprobada, rechazada -> rechazada, ejecutada -> ejecutada."""
        did = f"dec:autopilot:{propuesta_id}"
        dec = self.obtener_decision(did)
        if dec is None:
            return
        if estado_prop not in ("aprobada", "rechazada", "ejecutada"):
            return
        if dec["estado"] == estado_prop:
            return
        permitidas = _TRANSICIONES_DECISION.get(dec["estado"], ())
        if estado_prop not in permitidas:
            return  # la decisión tomó otro camino; no forzar
        anterior = dec["estado"]
        self.cur.execute(
            """UPDATE os_centro_decisiones
               SET estado=%s, aprobacion_id=%s, actualizada_at=now()
               WHERE company_id=%s AND decision_id=%s AND estado=%s""",
            (estado_prop, aprobacion_id, self.c, did, anterior))
        if cur_rowcount(self.cur) == 1:
            self._historial_dec(
                did, actor, anterior, estado_prop,
                f"espejo de la propuesta {propuesta_id}")
            self._audit("ia_decision", actor, did,
                        {"accion": "espejo_propuesta",
                         "propuesta_id": propuesta_id,
                         "estado": estado_prop})

    def historial_propuesta(self, propuesta_id: str) -> list[dict]:
        self.cur.execute(
            "SELECT hist_id, propuesta_id, fecha, actor, estado_anterior,"
            " estado_nuevo, nota FROM os_ia_propuestas_historial"
            " WHERE company_id=%s AND propuesta_id=%s"
            " ORDER BY fecha ASC, hist_id ASC",
            (self.c, propuesta_id))
        return _fila_dict(self.cur)

    def historial_decision(self, decision_id: str) -> list[dict]:
        self.cur.execute(
            "SELECT hist_id, decision_id, fecha, actor, estado_anterior,"
            " estado_nuevo, nota FROM os_centro_decisiones_historial"
            " WHERE company_id=%s AND decision_id=%s"
            " ORDER BY fecha ASC, hist_id ASC",
            (self.c, decision_id))
        return _fila_dict(self.cur)


def cur_rowcount(cur) -> int:
    """rowcount tolerante (psycopg lo expone siempre tras DML)."""
    try:
        return cur.rowcount if cur.rowcount is not None else 0
    except Exception:
        return 0


# ---------------------------------------------------------------------
# Contrato de integración: rutas ia/autopilot/*_pg e ia/decisiones/*_pg
# (mismo patrón que autopilot.rutas()/decisiones.rutas(); los handlers
# reciben (bundle, ctx, query|body) y levantan ValueError en errores de
# validación).
# ---------------------------------------------------------------------
def _propuestas_pg(bundle, ctx, query):
    company_id = verificar_aislamiento(bundle, ctx)
    estado = q1(query, "estado") or None
    tipo = q1(query, "tipo") or None
    try:
        with lectura_ia_pg(company_id) as (_conn, alm):
            props = alm.listar_propuestas(estado, tipo)
    except ErrorIA as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"company_id": company_id,
                                 "n_propuestas": len(props),
                                 "persistencia": "postgresql",
                                 "propuestas": props}}


def _proponer_pg(bundle, ctx, body):
    from . import autopilot as _autopilot
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    nuevas = _autopilot.generar_propuestas(bundle, fecha=body.get("hoy"))
    try:
        with tx_ia(company_id) as tx:
            alm = AlmacenPGIA(tx.cur, company_id, tx.auditar)
            guardadas = [alm.guardar_propuesta(p) for p in nuevas]
    except ErrorIA as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {
        "company_id": company_id, "n_propuestas": len(guardadas),
        "persistencia": "postgresql",
        "nota": ("modo propuesta: ninguna operación fue ejecutada; "
                  "cada propuesta requiere aprobación humana explícita"),
        "propuestas": guardadas}}


def _solicitar_aprobacion(bundle, ctx, body):
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    for campo in ("propuesta_id", "solicitante"):
        if not body.get(campo):
            raise ValueError(f"{campo} es obligatorio")
    try:
        with tx_ia(company_id) as tx:
            alm = AlmacenPGIA(tx.cur, company_id, tx.auditar)
            res = alm.solicitar_aprobacion(
                body["propuesta_id"], body["solicitante"],
                body.get("motivo", ""), body.get("monto"),
                body.get("moneda"))
    except ErrorIA as e:
        raise ValueError(str(e))
    return {"ok": True, "data": res}


def _refrescar_aprobacion(bundle, ctx, body):
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    if not body.get("propuesta_id"):
        raise ValueError("propuesta_id es obligatorio")
    try:
        with tx_ia(company_id) as tx:
            alm = AlmacenPGIA(tx.cur, company_id, tx.auditar)
            prop = alm.refrescar_desde_aprobacion(
                body["propuesta_id"], body.get("actor", "sistema"))
    except ErrorIA as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"propuesta": prop}}


def _registrar_ejecucion(bundle, ctx, body):
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    for campo in ("propuesta_id", "usuario"):
        if not body.get(campo):
            raise ValueError(f"{campo} es obligatorio")
    try:
        with tx_ia(company_id) as tx:
            alm = AlmacenPGIA(tx.cur, company_id, tx.auditar)
            prop = alm.registrar_ejecucion(
                body["propuesta_id"], body["usuario"],
                body.get("nota", ""))
    except ErrorIA as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {
        "propuesta": prop,
        "nota": ("ejecución registrada: el humano ejecutó la acción "
                 "fuera del sistema; el Autopilot nunca ejecuta")}}


def _decisiones_pg(bundle, ctx, query):
    company_id = verificar_aislamiento(bundle, ctx)
    estado = q1(query, "estado") or None
    fuente = q1(query, "fuente") or None
    try:
        with lectura_ia_pg(company_id) as (_conn, alm):
            ds = alm.listar_decisiones(estado, fuente)
    except ErrorIA as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"company_id": company_id,
                                 "n_decisiones": len(ds),
                                 "persistencia": "postgresql",
                                 "decisiones": ds}}


def _consolidar_pg(bundle, ctx, body):
    from . import decisiones as _decisiones
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    mem = _decisiones.consolidar(bundle, fecha=body.get("hoy"))
    try:
        with tx_ia(company_id) as tx:
            alm = AlmacenPGIA(tx.cur, company_id, tx.auditar)
            guardadas = [alm.registrar_decision(d.to_dict())
                         for d in mem]
    except ErrorIA as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"company_id": company_id,
                                 "n_decisiones": len(guardadas),
                                 "persistencia": "postgresql",
                                 "decisiones": guardadas}}


def _estado_pg(bundle, ctx, body):
    company_id = verificar_aislamiento(bundle, ctx)
    if not isinstance(body, dict):
        raise ValueError("el cuerpo debe ser un objeto JSON")
    for campo in ("decision_id", "estado", "usuario"):
        if not body.get(campo):
            raise ValueError(f"{campo} es obligatorio")
    try:
        with tx_ia(company_id) as tx:
            alm = AlmacenPGIA(tx.cur, company_id, tx.auditar)
            dec = alm.cambiar_estado_decision(
                body["decision_id"], body["estado"], body["usuario"],
                body.get("nota", ""))
    except ErrorIA as e:
        raise ValueError(str(e))
    return {"ok": True, "data": {"decision": dec}}


def rutas():
    """Contrato ia/autopilot/*_pg e ia/decisiones/*_pg (persistentes).

    GET  ia/autopilot/propuestas_pg      (bundle, ctx, query)
    POST ia/autopilot/proponer_pg       (bundle, ctx, body)
    POST ia/autopilot/solicitar_aprobacion (bundle, ctx, body)
    POST ia/autopilot/refrescar_aprobacion  (bundle, ctx, body)
    POST ia/autopilot/registrar_ejecucion   (bundle, ctx, body)
    GET  ia/decisiones/lista_pg         (bundle, ctx, query)
    POST ia/decisiones/consolidar_pg    (bundle, ctx, body)
    POST ia/decisiones/estado_pg        (bundle, ctx, body)
    """
    return {"GET ia/autopilot/propuestas_pg": _propuestas_pg,
            "POST ia/autopilot/proponer_pg": _proponer_pg,
            "POST ia/autopilot/solicitar_aprobacion":
                _solicitar_aprobacion,
            "POST ia/autopilot/refrescar_aprobacion":
                _refrescar_aprobacion,
            "POST ia/autopilot/registrar_ejecucion":
                _registrar_ejecucion,
            "GET ia/decisiones/lista_pg": _decisiones_pg,
            "POST ia/decisiones/consolidar_pg": _consolidar_pg,
            "POST ia/decisiones/estado_pg": _estado_pg}
