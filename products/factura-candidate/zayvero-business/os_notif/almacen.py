"""BLOQUE B (2026-10-13) — Persistencia PostgreSQL del centro de
notificaciones.

Una clase por empresa (`CentroNotificaciones(company_id)`):
aislamiento por construcción — TODAS las consultas filtran por
company_id.

Hermeticidad: el DSN se lee de forma perezosa en cada conexión (nunca
`os.environ` a nivel de import). Sin DSN: ErrorNotif claro.

La aprobación del envío externo NO se reimplementa: se delega en
`aprobaciones.api.Aprobaciones` (tipo "aprobacion_financiera", el tipo
genérico fail-closed de revisión humana). os_notif solo LEE la
decisión y la refleja en la bandeja; el despacho real no existe
(REGLA DURA: `enviar_real()` siempre levanta ErrorNotif).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .errores import ErrorNotif, NoEncontrado  # noqa: E402
from .modelo import (  # noqa: E402
    CANALES_EXTERNOS, PRIORIDADES, validar_tipo_alerta, validar_canal,
    validar_frecuencia, validar_formato_prog, validar_modulo_reporte,
    validar_hora, validar_rol,
)
from .sanitizar import mascara_destino, sanitizar  # noqa: E402

DSN_ENV = "ZAYVERO_OS_DATABASE_URL"


def _dsn() -> str:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        raise ErrorNotif(
            f"os_notif requiere {DSN_ENV} (PostgreSQL local)")
    return dsn


def _nueva_id(prefijo: str) -> str:
    return f"{prefijo}-{uuid.uuid4().hex[:12]}"


def _fila_notif(r) -> dict:
    (cid, nid, tipo, pri, tit, res, fue, reg, hue, pay, lei, cre,
     lei_at) = r
    import datetime
    def _iso(v):
        if v is None:
            return None
        return v.isoformat() if isinstance(v, datetime.datetime) else str(v)
    return {"company_id": cid, "notif_id": nid, "tipo": tipo,
            "prioridad": pri, "titulo": tit, "resumen": res,
            "fuente": fue, "regla": reg, "huella": hue,
            "payload": pay or {}, "leida": bool(lei),
            "creada_at": _iso(cre), "leida_at": _iso(lei_at)}


class CentroNotificaciones:
    """Centro de notificaciones de UNA empresa."""

    def __init__(self, company_id: str):
        cid = (company_id or "").strip()
        if not cid:
            raise ErrorNotif("company_id no puede estar vacío")
        self.company_id = cid
        _dsn()  # falla temprano y claro sin DSN

    # -- conexión ------------------------------------------------------
    def _conn(self):
        from os_persist.pg import connect
        return connect(_dsn())

    def _asegurar_empresa(self, cur):
        cur.execute(
            "INSERT INTO os_companies (company_id) VALUES (%s)"
            " ON CONFLICT (company_id) DO NOTHING",
            (self.company_id,))

    def _auditar(self, tipo: str, ref_id: str, detalle: dict):
        from os_persist.auditoria import auditar
        auditar(self.company_id, tipo, "", ref_id, detalle)

    # =================================================================
    # Notificaciones (inbox)
    # =================================================================
    def publicar(self, notifs: list[dict]) -> dict:
        """Inserta notificaciones con dedup por huella.

        Devuelve {"nuevas": n, "duplicadas": m}. El payload se
        re-sanitiza por seguridad antes de guardar.
        """
        nuevas = 0
        duplicadas = 0
        conn = self._conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                self._asegurar_empresa(cur)
                for n in notifs or []:
                    tipo = str(n.get("tipo") or "")
                    validar_tipo_alerta(tipo)
                    pri = str(n.get("prioridad") or "info")
                    if pri not in PRIORIDADES:
                        pri = "info"
                    huella = str(n.get("huella") or "")
                    if not huella:
                        huella = hashlib.sha256(
                            json.dumps(n, sort_keys=True,
                                       default=str).encode()
                        ).hexdigest()
                    payload = sanitizar(n.get("payload") or {})
                    cur.execute(
                        """INSERT INTO os_notificaciones
                           (company_id, notif_id, tipo, prioridad, titulo,
                            resumen, fuente, regla, huella, payload)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (company_id, huella) DO NOTHING""",
                        (self.company_id, _nueva_id("notif"), tipo, pri,
                         str(n.get("titulo") or "")[:500],
                         str(n.get("resumen") or "")[:2000],
                         str(n.get("fuente") or "")[:50],
                         str(n.get("regla") or "")[:80], huella,
                         json.dumps(payload, ensure_ascii=False)))
                    if cur.rowcount == 1:
                        nuevas += 1
                    else:
                        duplicadas += 1
            if nuevas:
                self._auditar("notificacion", "publicar",
                              {"nuevas": nuevas, "duplicadas": duplicadas})
            return {"nuevas": nuevas, "duplicadas": duplicadas}
        finally:
            conn.close()

    def inbox(self, leida: bool | None = None,
              prioridad: str | None = None, tipo: str | None = None,
              limite: int = 100) -> list[dict]:
        q = ("SELECT company_id, notif_id, tipo, prioridad, titulo,"
             " resumen, fuente, regla, huella, payload, leida, creada_at,"
             " leida_at FROM os_notificaciones"
             " WHERE company_id=%s")
        args: list = [self.company_id]
        if leida is not None:
            q += " AND leida=%s"
            args.append(bool(leida))
        if prioridad:
            if prioridad not in PRIORIDADES:
                raise ErrorNotif(f"prioridad inválida: {prioridad!r}")
            q += " AND prioridad=%s"
            args.append(prioridad)
        if tipo:
            validar_tipo_alerta(tipo)
            q += " AND tipo=%s"
            args.append(tipo)
        q += " ORDER BY creada_at DESC LIMIT %s"
        args.append(max(1, min(int(limite), 500)))
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(q, args)
                return [_fila_notif(r) for r in cur.fetchall()]
        finally:
            conn.close()

    def contar_no_leidas(self) -> int:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM os_notificaciones"
                    " WHERE company_id=%s AND leida=FALSE",
                    (self.company_id,))
                return int(cur.fetchone()[0])
        finally:
            conn.close()

    def marcar_leida(self, notif_id: str) -> dict:
        conn = self._conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE os_notificaciones
                       SET leida=TRUE, leida_at=now()
                       WHERE company_id=%s AND notif_id=%s
                         AND leida=FALSE""",
                    (self.company_id, (notif_id or "").strip()))
                if cur.rowcount != 1:
                    raise NoEncontrado(
                        f"notificación inexistente o ya leída: "
                        f"{notif_id!r}")
            self._auditar("notificacion", notif_id, {"accion": "marcar_leida"})
            return {"ok": True, "notif_id": notif_id, "leida": True}
        finally:
            conn.close()

    # =================================================================
    # Preferencias: suscripción a tipos de alerta por canal
    # =================================================================
    def suscribir(self, user_id: str, tipo_alerta: str, canal: str,
                  suscrito: bool) -> dict:
        validar_tipo_alerta(tipo_alerta)
        validar_canal(canal)
        conn = self._conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                self._asegurar_empresa(cur)
                cur.execute(
                    """INSERT INTO os_notif_preferencias
                       (company_id, user_id, tipo_alerta, canal, suscrito,
                        updated_at)
                       VALUES (%s,%s,%s,%s,%s,now())
                       ON CONFLICT (company_id, user_id, tipo_alerta,
                                    canal)
                       DO UPDATE SET suscrito=EXCLUDED.suscrito,
                                     updated_at=now()""",
                    (self.company_id, user_id or "", tipo_alerta, canal,
                     bool(suscrito)))
            self._auditar("notificacion", f"pref:{user_id}",
                          {"tipo_alerta": tipo_alerta, "canal": canal,
                           "suscrito": bool(suscrito)})
            return {"ok": True, "user_id": user_id or "",
                    "tipo_alerta": tipo_alerta, "canal": canal,
                    "suscrito": bool(suscrito)}
        finally:
            conn.close()

    def preferencias(self, user_id: str) -> list[dict]:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id, tipo_alerta, canal, suscrito,"
                    " updated_at FROM os_notif_preferencias"
                    " WHERE company_id=%s AND user_id=%s"
                    " ORDER BY tipo_alerta, canal",
                    (self.company_id, user_id or ""))
                return [{"user_id": r[0], "tipo_alerta": r[1],
                         "canal": r[2], "suscrito": bool(r[3]),
                         "updated_at": str(r[4])}
                        for r in cur.fetchall()]
        finally:
            conn.close()

    def puede_recibir(self, user_id: str, tipo_alerta: str,
                      canal: str) -> tuple[bool, str]:
        """(puede, motivo). Sin fila: inapp=suscrito, externos=no.

        Los canales externos exigen además canal registrado con
        consentimiento explícito y habilitado (ver canales()).
        """
        validar_canal(canal)
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT suscrito FROM os_notif_preferencias"
                    " WHERE company_id=%s AND user_id=%s"
                    " AND tipo_alerta IN (%s,'*') AND canal=%s"
                    " ORDER BY tipo_alerta DESC",
                    (self.company_id, user_id or "", tipo_alerta, canal))
                filas = cur.fetchall()
                suscrito = True  # default sin fila
                if filas:
                    # fila específica gana sobre '*'
                    suscrito = bool(filas[0][0])
                if not suscrito:
                    return False, "usuario no suscrito a este tipo/canal"
                if canal not in CANALES_EXTERNOS:
                    return True, "inapp: suscrito"
                cur.execute(
                    "SELECT consentimiento, habilitado"
                    " FROM os_notif_canales"
                    " WHERE company_id=%s AND user_id=%s AND canal=%s"
                    " ORDER BY creada_at DESC LIMIT 1",
                    (self.company_id, user_id or "", canal))
                r = cur.fetchone()
                if not r:
                    return False, (f"canal {canal} no registrado para "
                                   "este usuario")
                cons, hab = bool(r[0]), bool(r[1])
                if not cons:
                    return False, (f"canal {canal} sin consentimiento "
                                   "explícito")
                if not hab:
                    return False, f"canal {canal} deshabilitado"
                return True, f"{canal}: suscrito con consentimiento"
        finally:
            conn.close()

    # =================================================================
    # Canales externos con consentimiento explícito
    # =================================================================
    def registrar_canal(self, user_id: str, canal: str,
                        destino: str) -> dict:
        """Registra un canal externo. SIEMPRE nace DESHABILITADO y sin
        consentimiento: el consentimiento se da aparte y explícito."""
        canal = validar_canal(canal)
        if canal not in CANALES_EXTERNOS:
            raise ErrorNotif(
                f"registrar_canal es solo para canales externos: "
                f"{canal!r}")
        if not (destino or "").strip():
            raise ErrorNotif("destino no puede estar vacío")
        mask, h = mascara_destino(canal, destino)
        cid = _nueva_id("canal")
        conn = self._conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                self._asegurar_empresa(cur)
                cur.execute(
                    """INSERT INTO os_notif_canales
                       (company_id, canal_id, user_id, canal,
                        destino_mask, destino_hash,
                        consentimiento, habilitado)
                       VALUES (%s,%s,%s,%s,%s,%s,FALSE,FALSE)""",
                    (self.company_id, cid, user_id or "", canal,
                     mask, h))
            self._auditar("notificacion", cid,
                          {"accion": "registrar_canal", "canal": canal,
                           "destino_mask": mask})
            return {"ok": True, "canal_id": cid, "canal": canal,
                    "destino_mask": mask, "consentimiento": False,
                    "habilitado": False}
        finally:
            conn.close()

    def consentir_canal(self, canal_id: str, actor: str) -> dict:
        """Consentimiento EXPLÍCITO del usuario: habilita el canal."""
        conn = self._conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE os_notif_canales
                       SET consentimiento=TRUE, consentimiento_at=now(),
                           consentimiento_por=%s, habilitado=TRUE
                       WHERE company_id=%s AND canal_id=%s""",
                    (actor or "", self.company_id,
                     (canal_id or "").strip()))
                if cur.rowcount != 1:
                    raise NoEncontrado(
                        f"canal inexistente: {canal_id!r}")
            self._auditar("notificacion", canal_id,
                          {"accion": "consentimiento_explicito",
                           "por": actor or ""})
            return {"ok": True, "canal_id": canal_id,
                    "consentimiento": True, "habilitado": True}
        finally:
            conn.close()

    def revocar_canal(self, canal_id: str, actor: str) -> dict:
        """Revoca el consentimiento: el canal vuelve a deshabilitado."""
        conn = self._conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE os_notif_canales
                       SET consentimiento=FALSE, habilitado=FALSE
                       WHERE company_id=%s AND canal_id=%s""",
                    (self.company_id, (canal_id or "").strip()))
                if cur.rowcount != 1:
                    raise NoEncontrado(
                        f"canal inexistente: {canal_id!r}")
            self._auditar("notificacion", canal_id,
                          {"accion": "revocar_consentimiento",
                           "por": actor or ""})
            return {"ok": True, "canal_id": canal_id,
                    "consentimiento": False, "habilitado": False}
        finally:
            conn.close()

    def canales(self, user_id: str | None = None) -> list[dict]:
        q = ("SELECT canal_id, user_id, canal, destino_mask,"
             " consentimiento, consentimiento_at, habilitado, creada_at"
             " FROM os_notif_canales WHERE company_id=%s")
        args: list = [self.company_id]
        if user_id is not None:
            q += " AND user_id=%s"
            args.append(user_id)
        q += " ORDER BY creada_at DESC"
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(q, args)
                return [{"canal_id": r[0], "user_id": r[1], "canal": r[2],
                         "destino_mask": r[3],
                         "consentimiento": bool(r[4]),
                         "consentimiento_at": (str(r[5])
                                               if r[5] else None),
                         "habilitado": bool(r[6]),
                         "creada_at": str(r[7])}
                        for r in cur.fetchall()]
        finally:
            conn.close()

    # =================================================================
    # Programaciones de reportes
    # =================================================================
    def crear_programacion(self, modulo: str, formato: str,
                           frecuencia: str, hora: str,
                           roles_destino: list) -> dict:
        validar_modulo_reporte(modulo)
        validar_formato_prog(formato)
        validar_frecuencia(frecuencia)
        validar_hora(hora)
        roles = [validar_rol(r) for r in (roles_destino or [])]
        pid = _nueva_id("prog")
        conn = self._conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                self._asegurar_empresa(cur)
                cur.execute(
                    """INSERT INTO os_notif_programaciones
                       (company_id, prog_id, modulo, formato, frecuencia,
                        hora, roles_destino, activa)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,TRUE)""",
                    (self.company_id, pid, modulo, formato, frecuencia,
                     hora, roles))
            self._auditar("notificacion", pid,
                          {"accion": "crear_programacion",
                           "modulo": modulo, "formato": formato,
                           "frecuencia": frecuencia, "hora": hora,
                           "roles_destino": roles})
            return {"ok": True, "prog_id": pid, "modulo": modulo,
                    "formato": formato, "frecuencia": frecuencia,
                    "hora": hora, "roles_destino": roles,
                    "activa": True}
        finally:
            conn.close()

    def programaciones(self, solo_activas: bool = False) -> list[dict]:
        q = ("SELECT prog_id, modulo, formato, frecuencia, hora,"
             " roles_destino, activa, ultima_ejecucion,"
             " proxima_ejecucion, creada_at"
             " FROM os_notif_programaciones WHERE company_id=%s")
        args: list = [self.company_id]
        if solo_activas:
            q += " AND activa=TRUE"
        q += " ORDER BY creada_at DESC"
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(q, args)
                return [{"prog_id": r[0], "modulo": r[1], "formato": r[2],
                         "frecuencia": r[3], "hora": r[4],
                         "roles_destino": list(r[5] or []),
                         "activa": bool(r[6]),
                         "ultima_ejecucion": (str(r[7]) if r[7]
                                              else None),
                         "proxima_ejecucion": (str(r[8]) if r[8]
                                               else None),
                         "creada_at": str(r[9])}
                        for r in cur.fetchall()]
        finally:
            conn.close()

    def cambiar_estado_programacion(self, prog_id: str,
                                     activa: bool) -> dict:
        conn = self._conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE os_notif_programaciones SET activa=%s
                       WHERE company_id=%s AND prog_id=%s""",
                    (bool(activa), self.company_id,
                     (prog_id or "").strip()))
                if cur.rowcount != 1:
                    raise NoEncontrado(
                        f"programación inexistente: {prog_id!r}")
            self._auditar("notificacion", prog_id,
                          {"accion": ("activar" if activa
                                      else "desactivar") + "_programacion"})
            return {"ok": True, "prog_id": prog_id,
                    "activa": bool(activa)}
        finally:
            conn.close()

    def marcar_ejecucion_programacion(self, prog_id: str,
                                      proxima_iso: str | None) -> None:
        conn = self._conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE os_notif_programaciones
                       SET ultima_ejecucion=now(), proxima_ejecucion=%s
                       WHERE company_id=%s AND prog_id=%s""",
                    (proxima_iso, self.company_id,
                     (prog_id or "").strip()))
        finally:
            conn.close()

    # =================================================================
    # Bandeja de salida (SIMULADA)
    # =================================================================
    def crear_salida(self, *, tipo: str = "reporte_programado",
                     ref_id: str = "", formato: str = "pdf",
                     nombre_archivo: str = "", contenido: bytes | None = None,
                     destinatario_rol: str = "", canal: str = "inapp",
                     nota: str = "") -> dict:
        if tipo not in ("reporte_programado", "alerta_externa"):
            raise ErrorNotif(f"tipo de salida inválido: {tipo!r}")
        validar_formato_prog(formato)
        validar_canal(canal)
        sha = hashlib.sha256(contenido or b"").hexdigest()
        sid = _nueva_id("salida")
        conn = self._conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                self._asegurar_empresa(cur)
                cur.execute(
                    """INSERT INTO os_notif_bandeja
                       (company_id, salida_id, tipo, ref_id, formato,
                        nombre_archivo, contenido, contenido_sha256,
                        destinatario_rol, canal, estado, simulado, nota)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                               'simulado',TRUE,%s)""",
                    (self.company_id, sid, tipo, ref_id or "", formato,
                     nombre_archivo or "", contenido,
                     sha, destinatario_rol or "", canal,
                     nota or ""))
            self._auditar("notificacion", sid,
                          {"accion": "crear_salida_simulada",
                           "tipo": tipo, "formato": formato,
                           "sha256": sha})
            return {"ok": True, "salida_id": sid, "estado": "simulado",
                    "simulado": True, "contenido_sha256": sha,
                    "nota": ("SIMULADO: ningún envío real se realizó ni "
                             "se realizará sin aprobación humana en "
                             "aprobaciones/")}
        finally:
            conn.close()

    def obtener_salida(self, salida_id: str) -> dict:
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """SELECT salida_id, tipo, ref_id, formato,
                              nombre_archivo, contenido_sha256,
                              destinatario_rol, canal, estado, simulado,
                              aprobacion_id, creada_at, aprobada_at,
                              enviada_at, nota
                       FROM os_notif_bandeja
                       WHERE company_id=%s AND salida_id=%s""",
                    (self.company_id, (salida_id or "").strip()))
                r = cur.fetchone()
                if not r:
                    raise NoEncontrado(
                        f"salida inexistente: {salida_id!r}")
                return {"salida_id": r[0], "tipo": r[1], "ref_id": r[2],
                        "formato": r[3], "nombre_archivo": r[4],
                        "contenido_sha256": r[5],
                        "destinatario_rol": r[6], "canal": r[7],
                        "estado": r[8], "simulado": bool(r[9]),
                        "aprobacion_id": r[10],
                        "creada_at": str(r[11]),
                        "aprobada_at": (str(r[12]) if r[12] else None),
                        "enviada_at": (str(r[13]) if r[13] else None),
                        "nota": r[14]}
        finally:
            conn.close()

    def bandeja(self, estado: str | None = None,
                limite: int = 100) -> list[dict]:
        q = ("SELECT salida_id, tipo, ref_id, formato, nombre_archivo,"
             " contenido_sha256, destinatario_rol, canal, estado,"
             " simulado, aprobacion_id, creada_at, nota"
             " FROM os_notif_bandeja WHERE company_id=%s")
        args: list = [self.company_id]
        if estado:
            from .modelo import ESTADOS_SALIDA
            if estado not in ESTADOS_SALIDA:
                raise ErrorNotif(f"estado inválido: {estado!r}")
            q += " AND estado=%s"
            args.append(estado)
        q += " ORDER BY creada_at DESC LIMIT %s"
        args.append(max(1, min(int(limite), 500)))
        conn = self._conn()
        try:
            with conn.cursor() as cur:
                cur.execute(q, args)
                return [{"salida_id": r[0], "tipo": r[1], "ref_id": r[2],
                         "formato": r[3], "nombre_archivo": r[4],
                         "contenido_sha256": r[5],
                         "destinatario_rol": r[6], "canal": r[7],
                         "estado": r[8], "simulado": bool(r[9]),
                         "aprobacion_id": r[10],
                         "creada_at": str(r[11]), "nota": r[12]}
                        for r in cur.fetchall()]
        finally:
            conn.close()

    # -- integración con aprobaciones/ (delegada, no reimplementada) --
    def solicitar_aprobacion_envio(self, salida_id: str,
                                   solicitante: str) -> dict:
        """Crea la solicitud de aprobación humana en aprobaciones/
        (tipo 'aprobacion_financiera': revisión humana genérica
        fail-closed). La bandeja pasa a 'pendiente_aprobacion'.

        NO envía nada: solo registra la intención de envío para que un
        humano la revise.
        """
        from aprobaciones.api import Aprobaciones
        salida = self.obtener_salida(salida_id)
        if salida["estado"] != "simulado":
            raise ErrorNotif(
                f"solo se puede solicitar aprobación desde 'simulado' "
                f"(estado actual: {salida['estado']})")
        apr = Aprobaciones(self.company_id)
        res = apr.solicitar(
            tipo="aprobacion_financiera",
            solicitante=solicitante or "",
            payload={"origen": "os_notif", "salida_id": salida_id,
                     "tipo_salida": salida["tipo"],
                     "destinatario_rol": salida["destinatario_rol"],
                     "canal": salida["canal"],
                     "contenido_sha256": salida["contenido_sha256"],
                     "advertencia": ("SIMULADO: autorizar esto solo "
                                     "permite el envío futuro; el "
                                     "despacho real no está implementado")},
            motivo=("Envío externo SIMULADO desde os_notif "
                    f"(salida {salida_id}). Requiere revisión humana "
                    "antes de cualquier envío real."))
        conn = self._conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE os_notif_bandeja
                       SET estado='pendiente_aprobacion',
                           aprobacion_id=%s
                       WHERE company_id=%s AND salida_id=%s""",
                    (res["aprobacion_id"], self.company_id, salida_id))
            self._auditar("notificacion", salida_id,
                          {"accion": "solicitar_aprobacion_envio",
                           "aprobacion_id": res["aprobacion_id"]})
            return {"ok": True, "salida_id": salida_id,
                    "estado": "pendiente_aprobacion",
                    "aprobacion_id": res["aprobacion_id"]}
        finally:
            conn.close()

    def sincronizar_aprobacion(self, salida_id: str) -> dict:
        """Lee la decisión en aprobaciones/ y la refleja en la bandeja.

        aprobada -> 'aprobado' (listo, RETENIDO: el despacho real es
        una implementación futura). rechazada/cancelada -> su estado.
        """
        from aprobaciones.api import Aprobaciones
        salida = self.obtener_salida(salida_id)
        aid = salida.get("aprobacion_id")
        if not aid:
            raise ErrorNotif(
                f"la salida {salida_id} no tiene solicitud de "
                "aprobación (use solicitar_aprobacion_envio primero)")
        reg = Aprobaciones(self.company_id).obtener(aid)
        if not reg:
            raise NoEncontrado(f"aprobación inexistente: {aid!r}")
        mapa = {"aprobada": "aprobado", "rechazada": "rechazado",
                "cancelada": "cancelado",
                "pendiente_aprobacion": "pendiente_aprobacion",
                "ejecutada": "aprobado"}
        nuevo = mapa.get(reg["estado"])
        if nuevo is None:
            raise ErrorNotif(f"estado de aprobación desconocido: "
                             f"{reg['estado']!r}")
        conn = self._conn()
        try:
            conn.autocommit = True
            with conn.cursor() as cur:
                cur.execute(
                    """UPDATE os_notif_bandeja
                       SET estado=%s,
                           aprobada_at=CASE WHEN %s='aprobado'
                                            THEN now() ELSE aprobada_at END
                       WHERE company_id=%s AND salida_id=%s""",
                    (nuevo, nuevo, self.company_id, salida_id))
            self._auditar("notificacion", salida_id,
                          {"accion": "sincronizar_aprobacion",
                           "aprobacion_id": aid,
                           "estado_aprobacion": reg["estado"],
                           "estado_bandeja": nuevo})
            return {"ok": True, "salida_id": salida_id,
                    "estado": nuevo,
                    "nota": ("APROBADO pero RETENIDO: el envío real "
                             "requiere la implementación futura del "
                             "despacho (hoy no envía nada real).")
                    if nuevo == "aprobado" else ""}
        finally:
            conn.close()

    def enviar_real(self, salida_id: str) -> dict:
        """REGLA DURA: el envío real NO existe todavía.

        Siempre levanta ErrorNotif, incluso con la aprobación en mano:
        el despacho (SMTP/WhatsApp/Telegram) es una implementación
        futura que requiere decisión explícita del propietario.
        """
        raise ErrorNotif(
            "envío real DESHABILITADO por REGLA DURA: os_notif nunca "
            "envía mensajes reales (WhatsApp/SMS/correos). La salida "
            f"{salida_id} queda en la bandeja como SIMULADA; el "
            "despacho real es una implementación futura aprobada por "
            "el propietario (ver docs/bloque-notificaciones.md).")
