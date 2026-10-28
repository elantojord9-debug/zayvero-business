"""BLOQUE 1 (sprint 2) + SPRINT 3 PRIORIDAD 1 — Backend PostgreSQL del tenant store.

Problema que resuelve: TenantStore (store.py) guarda empresas/usuarios/
sesiones en JSON con threading.RLock, que solo protege hilos del MISMO
proceso. Con 2+ procesos hay read-modify-write sin lock: sesiones
perdidas, corrupción posible.

Diseño (misma API pública que TenantStore):
- Tablas os_empresas / os_usuarios / os_sesiones_v2 (migraciones 006+008).
  La columna `datos` (JSONB) guarda el dict completo del modelo: paridad
  de campos con el JSON (sin secretos en claro desde el sprint 3).
- Escrituras por empresa: transacción propia + SELECT ... FOR UPDATE
  sobre su fila de os_company_lock (se crea si falta) + bump de
  `version` como barrera — mismo patrón que os_persist/tx.py.
  Serializa escritores entre procesos.
- Sesiones (sprint 3): la BD guarda SOLO verificadores del Bearer token
  (ver tenant/session_tokens.py), nunca el token reutilizable. Upsert
  atómico por id interno; búsqueda por `token_sha` (SHA-256 hex, índice
  único) + verificación en tiempo constante del verificador salado.
  Expiración y revocación se verifican en CADA lookup (falla cerrado).
- Auditoría: se conserva el JSONL append-only (open(..., "a") usa
  O_APPEND: atómico entre procesos). Sin cambios de comportamiento.
- Hash de contraseñas: NO se reinventa — lo hace tenant/crypto.py
  (PBKDF2-HMAC-SHA256) vía tenant/auth.py; aquí solo se persiste el
  `password_hash` en la columna `hash` + `datos`.

NOTA sobre users.json / Secret File: en modo postgres los usuarios se
leen de os_usuarios, NO de ningún archivo. El flujo de producción
(ZAYVERO_REQUIRE_SECRET_FILE=1 + Secret File) NO se toca: la carga
inicial a PG se hace UNA vez con tenant/migrate_json_to_pg.py, que
resuelve el archivo con la misma prioridad
(ZAYVERO_USERS_FILE > /etc/secrets/users.json > local) y respeta
REQUIRE_SECRET_FILE (aborta antes que migrar la copia local).

Activación: ZAYVERO_TENANT_BACKEND=postgres (default: json).
Ver tenant/store.py::create_tenant_store.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import contextmanager
from datetime import datetime, timezone

from .models import Company, Session, User
from .store import DEFAULT_DATA_DIR, DEMO_COMPANY_ID, TenantStore, utcnow_iso
from . import session_tokens as st_mod

DSN_ENV_VAR = "ZAYVERO_OS_DATABASE_URL"

_log = logging.getLogger(__name__)

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _parse_ts(value: str | None):
    """'2026-01-01T00:00:00Z' -> datetime aware UTC. Inválido -> None."""
    try:
        return datetime.strptime(value or "", _TS_FMT).replace(
            tzinfo=timezone.utc)
    except Exception:
        return None


class PostgresTenantStore(TenantStore):
    """TenantStore multiproceso sobre PostgreSQL.

    Misma API pública que TenantStore. NO llama al __init__ del padre
    (evita la resolución de users.json: en PG los usuarios viven en
    os_usuarios); hereda append_audit/list_audit/list_roles/
    list_permissions, que no dependen del backend.
    """

    backend = "postgres"

    def __init__(self, data_dir: str | None = None, dsn: str | None = None,
                 auto_migrate: bool = True):
        self.data_dir = os.path.abspath(data_dir or DEFAULT_DATA_DIR)
        os.makedirs(self.data_dir, exist_ok=True)
        # Auditoría: mismo JSONL append-only que el backend JSON
        # (O_APPEND es atómico entre procesos; sin cambios).
        self._audit_file = os.path.join(self.data_dir, "audit.jsonl")
        self._dsn = dsn or os.environ.get(DSN_ENV_VAR, "")
        if not self._dsn:
            raise RuntimeError(
                f"PostgresTenantStore requiere DSN: defina {DSN_ENV_VAR} "
                "(ver os_persist/pg.py)")
        if auto_migrate:
            # P0 PRE-PR (issue #8): en staging/producción el runtime NO hace
            # DDL. Las migraciones las aplica un rol acotado en predeploy y el
            # preflight las verifica antes de abrir el puerto. Solo local
            # conserva auto-migrate (desarrollo y tests).
            from webapp.preflight import auto_migrate_permitido
            if not auto_migrate_permitido():
                _log.info(
                    "tenant PG: auto-migrate omitido (entorno no local;"
                    " migraciones verificadas por preflight)")
            else:
                aplicadas = self.migrate()
                if aplicadas:
                    _log.info("tenant PG: migraciones aplicadas: %s", aplicadas)
                # Sprint 3: convierte filas legacy de os_sesiones (token en
                # claro) a verificadores en os_sesiones_v2. Idempotente.
                migradas = self._migrar_sesiones_legacy()
                if migradas:
                    _log.warning(
                        "tenant PG: %d sesiones legacy migradas a verificadores;"
                        " la tabla os_sesiones (tokens en claro) fue eliminada",
                        migradas)

    # ---- conexión y transacciones ------------------------------------
    def _connect(self):
        import psycopg
        return psycopg.connect(self._dsn)

    def migrate(self) -> list[str]:
        """Aplica os_persist/schema/*.sql de forma idempotente."""
        from os_persist.pg import migrate
        conn = self._connect()
        try:
            return migrate(conn)
        finally:
            conn.close()

    def _fetch(self, sql: str, params=()) -> list:
        conn = self._connect()
        try:
            return conn.execute(sql, params).fetchall()
        finally:
            conn.close()

    @contextmanager
    def _tx(self):
        """Transacción simple en conexión propia (sin lock por empresa)."""
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def _company_tx(self, company_id: str):
        """Transacción + lock pesimista por empresa (patrón tx.py).

        Crea la fila de os_company_lock si falta, toma FOR UPDATE y al
        confirmar hace bump de `version` como barrera anti-lost-update.
        Todo rollback en fallo: ningún estado parcial sobrevive.
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            # os_company_lock.company_id referencia os_companies: la
            # fila ancla se crea primero (igual que os_persist/tx.py).
            cur.execute(
                "INSERT INTO os_companies (company_id) VALUES (%s)"
                " ON CONFLICT (company_id) DO NOTHING",
                (company_id,))
            cur.execute(
                "INSERT INTO os_company_lock (company_id) VALUES (%s)"
                " ON CONFLICT (company_id) DO NOTHING",
                (company_id,))
            cur.execute(
                "SELECT version FROM os_company_lock WHERE company_id=%s"
                " FOR UPDATE",
                (company_id,))
            version = cur.fetchone()[0]
            yield cur
            cur.execute(
                "UPDATE os_company_lock SET version=%s, updated_at=now()"
                " WHERE company_id=%s AND version=%s",
                (version + 1, company_id, version))
            if cur.rowcount != 1:
                raise RuntimeError(
                    "conflicto de versión en os_company_lock")
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _migrar_sesiones_legacy(self) -> int:
        """Migra os_sesiones (token en claro) -> os_sesiones_v2 (verificadores).

        Idempotente: si la tabla legacy no existe o está vacía, no hace
        nada (y la elimina si está vacía). Si una corrida anterior se
        interrumpió, el upsert por token_sha evita duplicados. Al final
        la tabla legacy se ELIMINA: un dump futuro no debe contener
        tokens en claro.

        Como la BD ya contuvo tokens en claro, en producción se exigiría
        re-login forzoso tras este cambio; en el entorno local demo se
        migra por hashing y queda constancia (ver migración 008).
        """
        conn = self._connect()
        try:
            cur = conn.cursor()
            existe = cur.execute(
                "SELECT to_regclass('public.os_sesiones')").fetchone()[0]
            if not existe:
                return 0
            filas = cur.execute(
                "SELECT datos FROM os_sesiones").fetchall()
            if not filas:
                cur.execute("DROP TABLE IF EXISTS os_sesiones")
                conn.commit()
                return 0
            nuevas = st_mod.migrate_legacy_sessions(
                [r[0] for r in filas if isinstance(r[0], dict)])
            for ses in nuevas:
                # Reintento seguro: si una corrida anterior ya migró este
                # token (mismo token_sha), se omite en vez de fallar.
                ya = cur.execute(
                    "SELECT 1 FROM os_sesiones_v2 WHERE token_sha=%s",
                    (ses.token_sha,)).fetchone()
                if not ya:
                    self._guardar_sesion_cur(cur, ses)
            cur.execute("DROP TABLE os_sesiones")
            conn.commit()
            return len(nuevas)
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    @staticmethod
    def _ensure_empresa_row(cur, company_id: str):
        """Fila ancla en os_empresas (para las FK). No pisa datos.

        El `datos` queda '{}' (stub): las lecturas lo ignoran hasta que
        un save_company real lo complete (ver _row_to_company).
        """
        cur.execute(
            "INSERT INTO os_empresas (id) VALUES (%s)"
            " ON CONFLICT (id) DO NOTHING",
            (company_id,))

    @staticmethod
    def _row_to_company(d: dict) -> Company | None:
        # Los stubs (datos='{}', creados solo para satisfacer FKs) no
        # son empresas: se tratan como inexistentes (paridad con JSON).
        if not d.get("company_id"):
            return None
        return Company(**d)

    @staticmethod
    def _row_to_user(d: dict) -> User | None:
        if not d.get("user_id"):
            return None
        return User(**d)

    # ---- companies ----------------------------------------------------
    def list_companies(self) -> list[Company]:
        rows = self._fetch(
            "SELECT datos FROM os_empresas ORDER BY created_at, id")
        return [c for c in (self._row_to_company(r[0]) for r in rows)
                if c is not None]

    def get_company(self, company_id: str) -> Company | None:
        rows = self._fetch(
            "SELECT datos FROM os_empresas WHERE id=%s", (company_id,))
        return self._row_to_company(rows[0][0]) if rows else None

    def save_company(self, company: Company):
        with self._company_tx(company.company_id) as cur:
            self._ensure_empresa_row(cur, company.company_id)
            cur.execute(
                """INSERT INTO os_empresas (id, nombre, datos, version,
                                            updated_at)
                   VALUES (%s, %s, %s, 1, now())
                   ON CONFLICT (id) DO UPDATE SET
                       nombre=EXCLUDED.nombre,
                       datos=EXCLUDED.datos,
                       version=os_empresas.version + 1,
                       updated_at=now()""",
                (company.company_id, company.name,
                 json.dumps(company.to_dict(), ensure_ascii=False)))

    def ensure_demo_company(self) -> Company:
        from intl.models import DEMO_CONFIG

        existing = self.get_company(DEMO_COMPANY_ID)
        if existing:
            changed = False
            for k, v in DEMO_CONFIG.items():
                if not getattr(existing, k, ""):
                    setattr(existing, k, v)
                    changed = True
            if changed:
                existing.updated_at = utcnow_iso()
                self.save_company(existing)
            return self.get_company(DEMO_COMPANY_ID)
        company = Company(
            company_id=DEMO_COMPANY_ID,
            name="Demo Retail (UCI Online Retail II) — DATOS DEMO",
            status="active",
            is_demo=True,
            created_at=utcnow_iso(),
            updated_at=utcnow_iso(),
            **DEMO_CONFIG,
        )
        self.save_company(company)
        return company

    # ---- users ----------------------------------------------------------
    def list_users(self) -> list[User]:
        rows = self._fetch(
            "SELECT datos FROM os_usuarios ORDER BY created_at, id")
        return [u for u in (self._row_to_user(r[0]) for r in rows)
                if u is not None]

    def get_user(self, user_id: str) -> User | None:
        rows = self._fetch(
            "SELECT datos FROM os_usuarios WHERE id=%s", (user_id,))
        return self._row_to_user(rows[0][0]) if rows else None

    def get_user_by_email(self, email: str) -> User | None:
        email = (email or "").strip().lower()
        if not email:
            return None
        rows = self._fetch(
            "SELECT datos FROM os_usuarios"
            " WHERE email <> '' AND lower(email)=%s LIMIT 1",
            (email,))
        return self._row_to_user(rows[0][0]) if rows else None

    def list_users_by_email(self, email: str) -> list[User]:
        """Todos los usuarios con ese email, en TODAS las empresas.

        Sprint 3: el email es único por empresa, no global; el login usa
        esto para detectar ambigüedad (nunca auto-elige empresa).
        """
        email = (email or "").strip().lower()
        if not email:
            return []
        rows = self._fetch(
            "SELECT datos FROM os_usuarios"
            " WHERE email <> '' AND lower(email)=%s"
            " ORDER BY created_at, id",
            (email,))
        return [u for u in (self._row_to_user(r[0]) for r in rows)
                if u is not None]

    def save_user(self, user: User):
        # Check-then-insert bajo el lock por empresa: dos procesos
        # creando el mismo email a la vez no duplican (uno recibe
        # AuthError("email ya registrado"), igual que en serie).
        from .auth import AuthError
        email_norm = (user.email or "").strip().lower()
        with self._company_tx(user.company_id) as cur:
            self._ensure_empresa_row(cur, user.company_id)
            if email_norm:
                dup = cur.execute(
                    "SELECT id FROM os_usuarios"
                    " WHERE empresa_id=%s AND email <> ''"
                    " AND lower(email)=%s AND id<>%s LIMIT 1",
                    (user.company_id, email_norm, user.user_id)).fetchone()
                if dup:
                    raise AuthError("email ya registrado")
            cur.execute(
                """INSERT INTO os_usuarios
                       (id, empresa_id, email, hash, rol, datos, version,
                        updated_at)
                   VALUES (%s, %s, %s, %s, %s, %s, 1, now())
                   ON CONFLICT (id) DO UPDATE SET
                       empresa_id=EXCLUDED.empresa_id,
                       email=EXCLUDED.email,
                       hash=EXCLUDED.hash,
                       rol=EXCLUDED.rol,
                       datos=EXCLUDED.datos,
                       version=os_usuarios.version + 1,
                       updated_at=now()""",
                (user.user_id, user.company_id, email_norm,
                 user.password_hash or "", user.role_id or "viewer",
                 json.dumps(user.to_dict(include_secret=True),
                            ensure_ascii=False)))

    def list_users_by_company(self, company_id: str) -> list[User]:
        rows = self._fetch(
            "SELECT datos FROM os_usuarios WHERE empresa_id=%s"
            " ORDER BY created_at, id",
            (company_id,))
        return [u for u in (self._row_to_user(r[0]) for r in rows)
                if u is not None]

    # ---- sessions -------------------------------------------------------
    # Sprint 3 (PRIORIDAD 1): os_sesiones_v2 guarda SOLO verificadores del
    # Bearer token (ver tenant/session_tokens.py), nunca el token en
    # claro. get_session localiza por token_sha (índice único), verifica
    # el verificador salado en tiempo constante, y falla cerrado ante
    # sesión revocada o expirada — en CADA lookup, no solo en la limpieza
    # periódica.
    def list_sessions(self) -> list[Session]:
        rows = self._fetch(
            "SELECT datos FROM os_sesiones_v2 ORDER BY created_at, id")
        return [Session(**r[0]) for r in rows
                if isinstance(r[0], dict) and r[0].get("session_id")]

    @staticmethod
    def _row_to_session(row) -> Session | None:
        (sid, uid, cid, vfy, salt, csrf, expira, revocada, oref,
         datos) = row
        if not isinstance(datos, dict) or not datos.get("session_id"):
            return None
        s = Session(**datos)
        # Las columnas estructuradas mandan (datos es espejo/paridad).
        s.session_id = sid
        s.user_id = uid
        s.company_id = cid
        s.token_verifier = vfy
        s.token_salt = salt
        s.csrf_token = csrf
        s.revoked = bool(revocada)
        s.origin_session_ref = oref or ""
        return s

    _SES_COLS = ("id, usuario_id, empresa_id, token_verifier, token_salt,"
                 " csrf_token, expira_at, revocada, origin_session_ref,"
                 " datos")

    def _get_session_row(self, where: str, params) -> Session | None:
        """Lookup interno: aplica revocación y expiración (falla cerrado)."""
        rows = self._fetch(
            f"SELECT {self._SES_COLS} FROM os_sesiones_v2 WHERE {where}",
            params)
        if not rows:
            return None
        s = self._row_to_session(rows[0])
        if s is None or s.revoked:
            return None
        expira = rows[0][6]
        if expira is not None and expira < datetime.now(timezone.utc):
            return None
        if expira is None and not s.expires_at:
            # Sin expiración parseable no se puede validar: falla cerrado.
            return None
        return s

    def get_session(self, token: str) -> Session | None:
        if not token:
            return None
        s = self._get_session_row("token_sha=%s",
                                  (st_mod.lookup_key(token),))
        if s is None:
            return None
        # Prueba de posesión en tiempo constante (el token_sha solo
        # localiza; el verificador autentica).
        if not st_mod.verify_token(token, s.token_verifier, s.token_salt):
            return None
        return s

    def get_session_by_id(self, session_id: str) -> Session | None:
        """Busca por id interno (sin token). Falla cerrado si expiró/revocó.

        Se usa para el enlace demo -> sesión origen (exit_demo rota el
        token de la origen sin conocerlo).
        """
        if not session_id:
            return None
        return self._get_session_row("id=%s", (session_id,))

    @staticmethod
    def _guardar_sesion_cur(cur, session: Session):
        cur.execute(
            """INSERT INTO os_sesiones_v2
                   (id, usuario_id, empresa_id, token_sha, token_verifier,
                    token_salt, csrf_token, expira_at, revocada,
                    origin_session_ref, datos)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT (id) DO UPDATE SET
                   usuario_id=EXCLUDED.usuario_id,
                   empresa_id=EXCLUDED.empresa_id,
                   token_sha=EXCLUDED.token_sha,
                   token_verifier=EXCLUDED.token_verifier,
                   token_salt=EXCLUDED.token_salt,
                   csrf_token=EXCLUDED.csrf_token,
                   expira_at=EXCLUDED.expira_at,
                   revocada=EXCLUDED.revocada,
                   origin_session_ref=EXCLUDED.origin_session_ref,
                   datos=EXCLUDED.datos""",
            (session.session_id, session.user_id, session.company_id,
             session.token_sha, session.token_verifier, session.token_salt,
             session.csrf_token or "",
             _parse_ts(session.expires_at), bool(session.revoked),
             session.origin_session_ref or "",
             json.dumps(st_mod.sanitize_datos(session.to_dict()),
                        ensure_ascii=False)))

    def save_session(self, session: Session):
        # Upsert atómico por id interno: sin read-modify-write, imposible
        # perder sesiones entre procesos (el bug del backend JSON).
        # La FK exige filas de empresa/usuario: se crean como stub
        # (ON CONFLICT DO NOTHING) si aún no existen; save_user/
        # save_company posteriores las completan con los datos reales.
        with self._tx() as conn:
            cur = conn.cursor()
            cur.execute(
                "INSERT INTO os_companies (company_id) VALUES (%s)"
                " ON CONFLICT (company_id) DO NOTHING",
                (session.company_id,))
            cur.execute(
                "INSERT INTO os_company_lock (company_id) VALUES (%s)"
                " ON CONFLICT (company_id) DO NOTHING",
                (session.company_id,))
            self._ensure_empresa_row(cur, session.company_id)
            cur.execute(
                "INSERT INTO os_usuarios (id, empresa_id) VALUES (%s, %s)"
                " ON CONFLICT (id) DO NOTHING",
                (session.user_id, session.company_id))
            self._guardar_sesion_cur(cur, session)

    def purge_expired_sessions(self) -> int:
        """Borra sesiones expiradas. Devuelve cuántas eliminó."""
        with self._tx() as conn:
            cur = conn.execute(
                "DELETE FROM os_sesiones_v2"
                " WHERE expira_at IS NOT NULL AND expira_at < now()")
            return cur.rowcount
