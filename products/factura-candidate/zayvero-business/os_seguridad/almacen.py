"""BLOQUE A SEGURIDAD — Persistencia del bloque (PG o JSON local).

`RegistroSeguridad` guarda secretos MFA, códigos de recuperación,
solicitudes de recuperación asistida, intentos fallidos y políticas de
retención.

- Con DSN (PostgreSQL 16 local): tablas de la migración 014
  (os_mfa_secretos, os_codigos_recuperacion, os_solicitudes_recuperacion,
  os_mfa_intentos, os_politicas_retencion). `asegurar_esquema()` las crea
  de forma idempotente si faltan.
- Sin DSN: archivo JSON `<data_dir>/os_seguridad.json` con RLock (mismo
  patrón que tenant/store.py). Útil para tests herméticos y modo local.

`crear_registro(store=None, dsn=None)`: si `dsn` es None se lee
ZAYVERO_OS_DATABASE_URL del entorno; sin DSN cae al JSON del data_dir
del store (o ./data). El constructor NUNCA toca os.environ.

Ningún método persiste secretos en claro: el registro solo ve hashes,
cifrados y metadatos (ver envoltorio.py).
"""

from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timedelta, timezone

DSN_ENV = "ZAYVERO_OS_DATABASE_URL"


def _ahora() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ") if dt else ""


def _parse_iso(s: str | None) -> datetime | None:
    try:
        return datetime.strptime(s or "", "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except Exception:
        return None


class RegistroSeguridad:
    def __init__(self, dsn: str | None = None, ruta_json: str | None = None):
        self._dsn = (dsn or "").strip() or None
        self._ruta = ruta_json
        self._lock = threading.RLock()
        if self._dsn:
            from . import migracion as mig_mod
            mig_mod.asegurar_esquema(self._dsn)
        else:
            if not self._ruta:
                raise ValueError("sin DSN se requiere ruta_json")
            os.makedirs(os.path.dirname(os.path.abspath(self._ruta)),
                        exist_ok=True)

    # ============ backend JSON ==========================================
    def _leer_json(self) -> dict:
        base = {"mfa": {}, "codigos": {}, "recuperacion": {},
                "retencion": {}, "intentos": {}}
        if os.path.exists(self._ruta):
            try:
                with open(self._ruta, encoding="utf-8") as fh:
                    d = json.load(fh)
                for k in base:
                    if isinstance(d.get(k), dict):
                        base[k] = d[k]
            except Exception:
                pass
        return base

    def _guardar_json(self, d: dict) -> None:
        tmp = self._ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(d, fh, ensure_ascii=False)
        os.replace(tmp, self._ruta)

    def _conn(self):
        from os_persist.pg import connect
        return connect(self._dsn)

    # ============ MFA =====================================================
    def mfa_guardar(self, usuario_id: str, sal: str, cifrado: str, sha: str,
                    confirmado: bool = False) -> None:
        if self._dsn:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO os_mfa_secretos
                               (usuario_id, secreto_cif, secreto_sha, sal, confirmado)
                           VALUES (%s,%s,%s,%s,%s)
                           ON CONFLICT (usuario_id) DO UPDATE SET
                               secreto_cif=EXCLUDED.secreto_cif,
                               secreto_sha=EXCLUDED.secreto_sha,
                               sal=EXCLUDED.sal, confirmado=EXCLUDED.confirmado,
                               confirmado_at=CASE WHEN EXCLUDED.confirmado
                                   THEN now() ELSE os_mfa_secretos.confirmado_at END""",
                        (usuario_id, cifrado, sha, sal, confirmado))
                conn.commit()
            finally:
                conn.close()
        else:
            with self._lock:
                d = self._leer_json()
                d["mfa"][usuario_id] = {
                    "sal": sal, "cifrado": cifrado, "sha": sha,
                    "confirmado": bool(confirmado),
                    "creado_at": _iso(_ahora()),
                    "confirmado_at": _iso(_ahora()) if confirmado else "",
                }
                self._guardar_json(d)

    def mfa_leer(self, usuario_id: str) -> dict | None:
        if self._dsn:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT secreto_cif, secreto_sha, sal, confirmado,"
                        " creado_at, confirmado_at FROM os_mfa_secretos"
                        " WHERE usuario_id=%s", (usuario_id,))
                    r = cur.fetchone()
            finally:
                conn.close()
            if not r:
                return None
            cif, sha, sal, conf, creado, conf_at = r
            return {"cifrado": cif, "sha": sha, "sal": sal,
                    "confirmado": bool(conf),
                    "creado_at": _iso(creado),
                    "confirmado_at": _iso(conf_at) if conf_at else ""}
        with self._lock:
            return self._leer_json()["mfa"].get(usuario_id)

    def mfa_confirmar(self, usuario_id: str) -> bool:
        if self._dsn:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE os_mfa_secretos SET confirmado=TRUE,"
                        " confirmado_at=now() WHERE usuario_id=%s"
                        " AND confirmado=FALSE", (usuario_id,))
                    n = cur.rowcount
                conn.commit()
            finally:
                conn.close()
            return n > 0
        with self._lock:
            d = self._leer_json()
            fila = d["mfa"].get(usuario_id)
            if not fila or fila.get("confirmado"):
                return False
            fila["confirmado"] = True
            fila["confirmado_at"] = _iso(_ahora())
            self._guardar_json(d)
            return True

    def mfa_borrar(self, usuario_id: str) -> None:
        """Elimina inscripción + códigos + intentos (recuperación asistida)."""
        if self._dsn:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM os_codigos_recuperacion"
                                " WHERE usuario_id=%s", (usuario_id,))
                    cur.execute("DELETE FROM os_mfa_intentos"
                                " WHERE usuario_id=%s", (usuario_id,))
                    cur.execute("DELETE FROM os_mfa_secretos"
                                " WHERE usuario_id=%s", (usuario_id,))
                conn.commit()
            finally:
                conn.close()
        else:
            with self._lock:
                d = self._leer_json()
                d["mfa"].pop(usuario_id, None)
                d["codigos"].pop(usuario_id, None)
                d["intentos"].pop(usuario_id, None)
                self._guardar_json(d)

    # ============ códigos de recuperación ==================================
    def codigos_guardar(self, usuario_id: str, hashes: list[str]) -> None:
        """Reemplaza los códigos del usuario (regeneración invalida los
        anteriores)."""
        if self._dsn:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM os_codigos_recuperacion"
                                " WHERE usuario_id=%s", (usuario_id,))
                    for h in hashes:
                        cur.execute(
                            "INSERT INTO os_codigos_recuperacion"
                            " (id, usuario_id, codigo_sha) VALUES (%s,%s,%s)"
                            " ON CONFLICT DO NOTHING",
                            ("RC-" + uuid.uuid4().hex[:12], usuario_id, h))
                conn.commit()
            finally:
                conn.close()
        else:
            with self._lock:
                d = self._leer_json()
                ahora = _iso(_ahora())
                d["codigos"][usuario_id] = [
                    {"sha": h, "usado": False, "usado_at": "",
                     "creado_at": ahora} for h in hashes]
                self._guardar_json(d)

    def codigos_restantes(self, usuario_id: str) -> int:
        if self._dsn:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT count(*) FROM os_codigos_recuperacion"
                        " WHERE usuario_id=%s AND usado=FALSE", (usuario_id,))
                    return int(cur.fetchone()[0])
            finally:
                conn.close()
        with self._lock:
            return sum(1 for c in self._leer_json()["codigos"]
                       .get(usuario_id, []) if not c.get("usado"))

    def codigo_usar(self, usuario_id: str, codigo_sha: str) -> bool:
        """Marca un código como usado si estaba sin usar. Atómico:
        devuelve True solo si este llamado lo consumió (un solo uso)."""
        import hmac as _hmac
        if self._dsn:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE os_codigos_recuperacion SET usado=TRUE,"
                        " usado_at=now() WHERE usuario_id=%s AND codigo_sha=%s"
                        " AND usado=FALSE", (usuario_id, codigo_sha))
                    ok = cur.rowcount > 0
                conn.commit()
            finally:
                conn.close()
            return ok
        with self._lock:
            d = self._leer_json()
            for c in d["codigos"].get(usuario_id, []):
                if _hmac.compare_digest(c.get("sha", ""), codigo_sha):
                    if c.get("usado"):
                        return False
                    c["usado"] = True
                    c["usado_at"] = _iso(_ahora())
                    self._guardar_json(d)
                    return True
            return False

    # ============ anti-fuerza bruta ========================================
    VENTANA_INTENTOS_MIN = 15
    MAX_FALLOS = 5
    BLOQUEO_MIN = 15

    def _intentos_leer(self, usuario_id: str) -> dict:
        if self._dsn:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT fallos, bloqueado_hasta"
                                " FROM os_mfa_intentos WHERE usuario_id=%s",
                                (usuario_id,))
                    r = cur.fetchone()
            finally:
                conn.close()
            if not r:
                return {"fallos": [], "bloqueado_hasta": None}
            return {"fallos": list(r[0] or []), "bloqueado_hasta": r[1]}
        with self._lock:
            return self._leer_json()["intentos"].get(
                usuario_id, {"fallos": [], "bloqueado_hasta": ""})

    def _intentos_guardar(self, usuario_id: str, fallos: list,
                          bloqueado_hasta) -> None:
        if self._dsn:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO os_mfa_intentos
                               (usuario_id, fallos, bloqueado_hasta)
                           VALUES (%s,%s,%s)
                           ON CONFLICT (usuario_id) DO UPDATE SET
                               fallos=EXCLUDED.fallos,
                               bloqueado_hasta=EXCLUDED.bloqueado_hasta""",
                        (usuario_id, fallos, bloqueado_hasta))
                conn.commit()
            finally:
                conn.close()
        else:
            with self._lock:
                d = self._leer_json()
                d["intentos"][usuario_id] = {
                    "fallos": [_iso(f) for f in fallos],
                    "bloqueado_hasta": _iso(bloqueado_hasta)
                    if bloqueado_hasta else "",
                }
                self._guardar_json(d)

    def _norm_fallos(self, estado: dict) -> tuple[list, object]:
        ahora = _ahora()
        corte = ahora - timedelta(minutes=self.VENTANA_INTENTOS_MIN)
        fallos = []
        for f in estado.get("fallos") or []:
            dt = f if isinstance(f, datetime) else _parse_iso(f)
            if dt and dt >= corte:
                fallos.append(dt)
        bh = estado.get("bloqueado_hasta")
        bh_dt = bh if isinstance(bh, datetime) else _parse_iso(bh)
        return fallos, bh_dt

    def mfa_bloqueado(self, usuario_id: str) -> tuple[bool, int]:
        """(bloqueado, segundos_restantes)."""
        fallos, bh = self._norm_fallos(self._intentos_leer(usuario_id))
        ahora = _ahora()
        if bh and bh > ahora:
            return True, int((bh - ahora).total_seconds())
        return False, 0

    def mfa_fallo(self, usuario_id: str) -> None:
        fallos, _ = self._norm_fallos(self._intentos_leer(usuario_id))
        fallos.append(_ahora())
        bh = None
        if len(fallos) >= self.MAX_FALLOS:
            bh = _ahora() + timedelta(minutes=self.BLOQUEO_MIN)
        self._intentos_guardar(usuario_id, fallos, bh)

    def mfa_exito(self, usuario_id: str) -> None:
        """Un éxito limpia la cubeta: el usuario legítimo no queda
        bloqueado por fallos viejos."""
        self._intentos_guardar(usuario_id, [], None)

    # ============ recuperación asistida ====================================
    def rec_solicitar(self, empresa_id: str, usuario_objetivo: str,
                      solicitante: str, motivo: str,
                      canal_verificacion: str) -> str:
        sid = "REC-" + uuid.uuid4().hex[:12]
        if self._dsn:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO os_solicitudes_recuperacion
                           (solicitud_id, empresa_id, usuario_objetivo,
                            solicitante, motivo, canal_verificacion)
                           VALUES (%s,%s,%s,%s,%s,%s)""",
                        (sid, empresa_id, usuario_objetivo, solicitante,
                         motivo, canal_verificacion))
                conn.commit()
            finally:
                conn.close()
        else:
            with self._lock:
                d = self._leer_json()
                d["recuperacion"][sid] = {
                    "empresa_id": empresa_id,
                    "usuario_objetivo": usuario_objetivo,
                    "solicitante": solicitante, "estado": "pendiente",
                    "motivo": motivo, "canal_verificacion": canal_verificacion,
                    "creado_at": _iso(_ahora()), "ejecutada_at": "",
                    "ejecutada_por": "",
                }
                self._guardar_json(d)
        return sid

    def rec_leer(self, solicitud_id: str) -> dict | None:
        if self._dsn:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT empresa_id, usuario_objetivo, solicitante,"
                        " estado, motivo, canal_verificacion, creado_at,"
                        " ejecutada_at, ejecutada_por"
                        " FROM os_solicitudes_recuperacion"
                        " WHERE solicitud_id=%s", (solicitud_id,))
                    r = cur.fetchone()
            finally:
                conn.close()
            if not r:
                return None
            (eid, uobj, sol, est, mot, canal, creado, ej_at,
             ej_por) = r
            return {"solicitud_id": solicitud_id, "empresa_id": eid,
                    "usuario_objetivo": uobj, "solicitante": sol,
                    "estado": est, "motivo": mot,
                    "canal_verificacion": canal,
                    "creado_at": _iso(creado),
                    "ejecutada_at": _iso(ej_at) if ej_at else "",
                    "ejecutada_por": ej_por or ""}
        with self._lock:
            s = self._leer_json()["recuperacion"].get(solicitud_id)
            return dict(s, solicitud_id=solicitud_id) if s else None

    def rec_ejecutar(self, solicitud_id: str, ejecutada_por: str) -> dict | None:
        """Marca ejecutada si estaba pendiente. Atómico: una sola
        ejecución (la segunda ve estado != pendiente -> None)."""
        if self._dsn:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "UPDATE os_solicitudes_recuperacion"
                        " SET estado='ejecutada', ejecutada_at=now(),"
                        " ejecutada_por=%s"
                        " WHERE solicitud_id=%s AND estado='pendiente'",
                        (ejecutada_por, solicitud_id))
                    ok = cur.rowcount > 0
                conn.commit()
            finally:
                conn.close()
            return self.rec_leer(solicitud_id) if ok else None
        with self._lock:
            d = self._leer_json()
            s = d["recuperacion"].get(solicitud_id)
            if not s or s.get("estado") != "pendiente":
                return None
            s["estado"] = "ejecutada"
            s["ejecutada_at"] = _iso(_ahora())
            s["ejecutada_por"] = ejecutada_por
            self._guardar_json(d)
            return dict(s, solicitud_id=solicitud_id)

    # ============ políticas de retención ====================================
    def retencion_guardar(self, empresa_id: str, tipo_dato: str,
                          plazo_dias: int, accion: str, base: str,
                          actor: str) -> None:
        if plazo_dias < 0:
            raise ValueError("plazo_dias no puede ser negativo")
        if accion not in ("eliminar", "anonimizar", "archivar"):
            raise ValueError("acción inválida")
        if self._dsn:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO os_politicas_retencion
                           (empresa_id, tipo_dato, plazo_dias, accion, base,
                            actualizado_por)
                           VALUES (%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (empresa_id, tipo_dato) DO UPDATE SET
                               plazo_dias=EXCLUDED.plazo_dias,
                               accion=EXCLUDED.accion, base=EXCLUDED.base,
                               actualizado_at=now(),
                               actualizado_por=EXCLUDED.actualizado_por""",
                        (empresa_id, tipo_dato, plazo_dias, accion, base,
                         actor))
                conn.commit()
            finally:
                conn.close()
        else:
            with self._lock:
                d = self._leer_json()
                d["retencion"].setdefault(empresa_id, {})[tipo_dato] = {
                    "plazo_dias": plazo_dias, "accion": accion, "base": base,
                    "actualizado_at": _iso(_ahora()),
                    "actualizado_por": actor, "origen": "personalizada",
                }
                self._guardar_json(d)

    def retencion_leer(self, empresa_id: str) -> dict:
        """Personalizaciones de la empresa (la matriz por defecto la pone
        la capa de negocio)."""
        if self._dsn:
            conn = self._conn()
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        "SELECT tipo_dato, plazo_dias, accion, base,"
                        " actualizado_at, actualizado_por"
                        " FROM os_politicas_retencion WHERE empresa_id=%s",
                        (empresa_id,))
                    filas = cur.fetchall()
            finally:
                conn.close()
            return {t: {"plazo_dias": p, "accion": a, "base": b,
                        "actualizado_at": _iso(act),
                        "actualizado_por": ap or "",
                        "origen": "personalizada"}
                    for (t, p, a, b, act, ap) in filas}
        with self._lock:
            return dict(self._leer_json()["retencion"].get(empresa_id, {}))


def crear_registro(store=None, dsn: str | None = None,
                   ruta_json: str | None = None) -> RegistroSeguridad:
    """Fábrica del registro.

    - `ruta_json` explícita -> backend JSON (gana al entorno: elección
      explícita para tests herméticos).
    - `dsn` explícito -> backend PG.
    - Si no: se lee ZAYVERO_OS_DATABASE_URL del entorno; sin DSN, JSON
      en el data_dir del store (o ./data)."""
    if ruta_json:
        return RegistroSeguridad(ruta_json=ruta_json)
    dsn = (dsn or "").strip() or os.environ.get(DSN_ENV, "").strip() or None
    if dsn:
        return RegistroSeguridad(dsn=dsn)
    data_dir = getattr(store, "data_dir", None) or os.path.join(
        os.getcwd(), "data")
    return RegistroSeguridad(
        ruta_json=os.path.join(os.path.abspath(data_dir),
                               "os_seguridad.json"))
