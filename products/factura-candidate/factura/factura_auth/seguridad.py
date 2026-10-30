"""Seguridad pre-staging (P0 #1/#2/#3): rate limiting durable, hash de
tokens de recuperación y revocación de sesiones del lado servidor.

- factura_intentos: cada intento fallido/sensible queda registrado con
  tipo+clave+ip+hora; sobrevive reinicios y alimenta los bloqueos
  temporales (fuerza bruta en login y superadmin, abuso de /recuperar).
- factura_sesiones_revocadas: guarda el SHA-256 del valor de la cookie
  de sesión al hacer logout; la sesión queda destruida del lado servidor
  aunque la cookie siga siendo criptográficamente válida.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

from factura_persist import db

# --- límites (documentados en docs/SEGURIDAD.md) -------------------------------
RECUPERAR_MAX_POR_HORA = 5          # por correo y por IP
LOGIN_MAX_FALLOS = 5                # por correo+IP
LOGIN_VENTANA_MIN = 15
SUPERADMIN_MAX_FALLOS = 5           # por IP
SUPERADMIN_VENTANA_MIN = 15

# Tipos de intento registrados en factura_intentos
T_RECUPERAR_EMAIL = "recuperar_email"
T_RECUPERAR_IP = "recuperar_ip"
T_LOGIN = "login"
T_SUPERADMIN = "superadmin"


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def hash_token(token: str) -> str:
    """SHA-256 hex del token de recuperación. En BD nunca se guarda el
    token en claro: solo este hash (el token crudo tiene 256 bits de
    entropía, no es una contraseña)."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def registrar_intento(tipo: str, clave: str, ip: str = "") -> None:
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(
                cur, "INSERT INTO factura_intentos (tipo, clave, ip,"
                     " creado_en) VALUES (%s,%s,%s,%s)",
                (tipo, (clave or "")[:160], (ip or "")[:64], _ahora()))
    finally:
        conn.close()


def intentos_recientes(tipo: str, clave: str, ventana_min: int) -> int:
    desde = (datetime.now(timezone.utc) -
             timedelta(minutes=ventana_min)).isoformat()
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(
            cur, "SELECT COUNT(*) FROM factura_intentos WHERE tipo=%s"
                 " AND clave=%s AND creado_en>=%s",
            (tipo, clave, desde))
        return int(fila[0]) if fila else 0
    finally:
        conn.close()


def bloqueado(tipo: str, clave: str, max_intentos: int,
              ventana_min: int) -> bool:
    """True si se alcanzó el máximo de intentos en la ventana."""
    return intentos_recientes(tipo, clave, ventana_min) >= max_intentos


def limpiar_intentos_antiguos(dias: int = 30) -> int:
    """Poda de higiene (best-effort). Devuelve filas borradas."""
    desde = (datetime.now(timezone.utc) -
             timedelta(days=dias)).isoformat()
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "DELETE FROM factura_intentos WHERE creado_en<%s",
                       (desde,))
            return cur.rowcount or 0
    finally:
        conn.close()


# --- revocación de sesiones ---------------------------------------------------

def _hash_cookie(cookie_value: str) -> str:
    return hashlib.sha256((cookie_value or "").encode("utf-8")).hexdigest()


def revocar_sesion(cookie_value: str, expira_en: str | None = None) -> None:
    """Destruye la sesión del lado servidor (logout). Guarda solo el hash
    de la cookie, nunca su valor."""
    if not cookie_value:
        return
    if expira_en is None:
        expira_en = (datetime.now(timezone.utc) +
                     timedelta(hours=24)).isoformat()
    conn = db.connect()
    try:
        cur = conn.cursor()
        h = _hash_cookie(cookie_value)
        with db.tx(conn):
            if db.backend() == "sqlite":
                db.execute(
                    cur, "INSERT OR REPLACE INTO factura_sesiones_revocadas"
                         " (cookie_hash, expira_en) VALUES (%s,%s)",
                    (h, expira_en))
            else:
                db.execute(
                    cur, "INSERT INTO factura_sesiones_revocadas"
                         " (cookie_hash, expira_en) VALUES (%s,%s)"
                         " ON CONFLICT (cookie_hash) DO UPDATE"
                         " SET expira_en=EXCLUDED.expira_en",
                    (h, expira_en))
    finally:
        conn.close()


def sesion_revocada(cookie_value: str) -> bool:
    """True si la cookie fue revocada (logout) y la revocación sigue vigente."""
    if not cookie_value:
        return False
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(
            cur, "SELECT expira_en FROM factura_sesiones_revocadas"
                 " WHERE cookie_hash=%s", (_hash_cookie(cookie_value),))
        if not fila:
            return False
        if (fila[0] or "") < _ahora():
            return False
        return True
    finally:
        conn.close()


def purgar_revocadas() -> int:
    """Elimina revocaciones vencidas. Devuelve filas borradas."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "DELETE FROM factura_sesiones_revocadas"
                            " WHERE expira_en<%s", (_ahora(),))
            return cur.rowcount or 0
    finally:
        conn.close()
