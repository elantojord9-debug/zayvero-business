"""BLOQUE 1 (2026-10-12) — Auditoría empresarial del Business OS.

Diseño de integración (decisión documentada):

- `auditar(company_id, tipo, actor, ref_id, detalle)` lo llama la CAPA DE
  RUTA (webapp/os_routes.py) justo después de que la operación de dominio
  reporta éxito, DENTRO del `fn` que ejecuta `mutar_os`.
- `mutar_os` (os_persist/tx.py) activa una cola thread-local antes de
  ejecutar `fn` y, solo si `status < 400`, inserta los eventos encolados
  con el cursor de LA MISMA TRANSACCIÓN, antes de `guardar_snapshot` y
  del COMMIT. Atomicidad garantizada: evento y estado se confirman juntos.
- Si la operación devuelve status >= 400 o lanza excepción, hay ROLLBACK
  y la cola se descarta: el evento NUNCA queda registrado como exitoso
  para una operación fallida.
- El `tipo` está restringido a TIPOS_EVENTO; el `detalle` (jsonb) lleva
  cantidades/importes clave para trazabilidad (qué cambió).
- La tabla es append-only por convención: este módulo solo INSERTA y LEE.

Modo memoria (sin PG):
- La auditoría se guarda en `bundle.eventos_memoria` (lista acotada a
  las últimas 500, NO duradera: se pierde al reiniciar). Decisión: es lo
  más simple que conserva utilidad en desarrollo sin inventar
  persistencia fuera del diseño de snapshots.

Fuera de `mutar_os` con backend PG:
- `auditar()` es no-op seguro (devuelve False) y NO inserta nada: un
  evento de auditoría sin la transacción de su operación rompería la
  garantía de atomicidad. Toda escritura de rutas pasa por `mutar_os`.

Seguridad/integridad:
- Aislamiento multiempresa: `leer_eventos` SIEMPRE filtra por company_id
  (los índices de os_eventos empiezan por company_id).
- `detalle` se serializa con `default=str`: Decimals/fechas no rompen el
  INSERT y quedan como texto trazable.
- Nunca se interpolan valores en SQL: todo por parámetros.
"""
from __future__ import annotations

import json
import threading
import uuid

TIPOS_EVENTO = ("venta", "compra", "recepcion", "cobro",
                "devolucion", "aprobacion", "ajuste", "gasto",
                "importacion",
                # BLOQUE EMPLEADOS (2026-10-13, migración 011): eventos del
                # módulo os_empleados. 'tarea' = crear/cambiar estado de
                # tareas (ref_id=tarea_id); 'empleado' = altas/cambios en
                # el registro de personal (ref_id=empleado_id).
                "tarea", "empleado",
                # BLOQUE CRM/VENTAS (2026-10-13): eventos del bloque os_crm.
                "oportunidad", "descuento", "recordatorio", "promesa_pago",
                # BLOQUE CONTABILIDAD (2026-10-13, migración 013): eventos
                # del módulo os_contab. 'presupuesto' = crear presupuesto
                # (ref_id=presupuesto_id); 'conciliacion' = cargar extracto
                # simulado / marca manual (ref_id=linea_id o 'extracto');
                # 'ncf' = crear rango / emitir NCF sintético (ref_id=tipo o
                # ncf).
                "presupuesto", "conciliacion", "ncf",
                # BLOQUE SECTOR (2026-10-13): 'sector' = asignación/cambio
                # de plantilla sectorial (ref_id=sector_id; detalle:
                # sector_anterior/sector_nuevo). NOTA: el CHECK de
                # os_eventos en PG aún no incluye 'sector' (no hubo
                # migración SQL en este bloque por diseño); antes de
                # cablear sector/asignar con backend PG, extender el
                # CHECK con el patrón DROP/ADD de la migración 013.
                "sector",
                # BLOQUE NOTIFICACIONES (2026-10-13, migración 015):
                # eventos del módulo os_notif (ref_id=notif_id|prog_id|
                # salida_id|canal_id).
                "notificacion",
                # BLOQUE E GARANTÍAS (2026-10-13, migración 017): eventos
                # del módulo os_garantias. 'garantia' = registrar término
                # / activar tarjetas (ref_id=termino_id o venta_id);
                # 'reclamo_garantia' = intake/diagnóstico/resolución
                # (ref_id=reclamo_id).
                "garantia", "reclamo_garantia",
                # BLOQUE IA (2026-10-13, migración 020): eventos del
                # módulo os_ia/almacen.py. 'ia_propuesta' = crear /
                # solicitar aprobación / decisión / ejecución
                # registrada de una propuesta del Autopilot
                # (ref_id=propuesta_id); 'ia_decision' = crear / cambio
                # de estado de una decisión del Centro de Decisiones
                # (ref_id=decision_id).
                "ia_propuesta", "ia_decision")
RESULTADOS = ("exito", "fallo")

_MAX_EVENTOS_MEMORIA = 500

_local = threading.local()


def _cola():
    """Cola de eventos de la transacción en curso (None si no hay)."""
    return getattr(_local, "pendientes", None)


def _activar_cola() -> None:
    _local.pendientes = []


def _tomar_cola() -> list:
    cola = getattr(_local, "pendientes", None)
    _local.pendientes = None
    return cola or []


def auditar(company_id: str, tipo: str, actor: str, ref_id: str,
            detalle: dict | None = None, resultado: str = "exito") -> bool:
    """Encola (PG dentro de mutar_os) o guarda (memoria) un evento.

    Devuelve True si el evento quedó registrado/encolado, False si fue
    no-op (PG fuera de transacción). Lanza ValueError con tipo o
    resultado inválidos.
    """
    if tipo not in TIPOS_EVENTO:
        raise ValueError(f"tipo de evento inválido: {tipo!r}")
    if resultado not in RESULTADOS:
        raise ValueError(f"resultado de evento inválido: {resultado!r}")
    evento = {
        "evento_id": uuid.uuid4().hex,
        "company_id": company_id,
        "tipo": tipo,
        "actor": actor or "",
        "ref_id": ref_id or "",
        "detalle": detalle or {},
        "resultado": resultado,
    }
    cola = _cola()
    if cola is not None:
        # Dentro de mutar_os: mutar_os lo inserta pre-commit.
        cola.append(evento)
        return True
    # Fuera de mutar_os: importa webapp.os_api de forma perezosa para
    # evitar ciclos (os_api no importa este módulo).
    from webapp.os_api import backend_activo, get_bundle
    if backend_activo() == "postgres":
        # Sin la transacción de la operación no hay garantía de
        # atomicidad: no-op documentado en vez de insert suelto.
        return False
    b = get_bundle(company_id)
    mem = getattr(b, "eventos_memoria", None)
    if mem is None:
        b.eventos_memoria = mem = []
    mem.append(evento)
    del mem[:-_MAX_EVENTOS_MEMORIA]
    return True


def insertar_evento(cur, evento: dict) -> None:
    """INSERT del evento con el cursor de la transacción en curso."""
    cur.execute(
        """INSERT INTO os_eventos
               (evento_id, company_id, tipo, actor, ref_id, detalle,
                resultado)
           VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)""",
        (evento["evento_id"], evento["company_id"], evento["tipo"],
         evento["actor"], evento["ref_id"],
         json.dumps(evento["detalle"], default=str),
         evento["resultado"]))


def leer_eventos(conn, company_id: str, tipo: str | None = None,
                 ref_id: str | None = None, limite: int = 200) -> list[dict]:
    """Lee eventos de UNA empresa (aislamiento por company_id).

    Orden cronológico. `detalle` llega como dict (jsonb). Nunca expone
    eventos de otra empresa: company_id es filtro obligatorio.
    """
    sql = ("SELECT evento_id, company_id, tipo, actor, fecha, ref_id,"
           " detalle, resultado FROM os_eventos WHERE company_id = %s")
    params: list = [company_id]
    if tipo is not None:
        if tipo not in TIPOS_EVENTO:
            raise ValueError(f"tipo de evento inválido: {tipo!r}")
        sql += " AND tipo = %s"
        params.append(tipo)
    if ref_id is not None:
        sql += " AND ref_id = %s"
        params.append(ref_id)
    sql += " ORDER BY fecha ASC, evento_id ASC LIMIT %s"
    params.append(int(limite))
    cur = conn.cursor()
    cur.execute(sql, params)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, fila)) for fila in cur.fetchall()]
