"""FASE 6A — Persistencia JSON del tenant store.

Guarda en <data_dir>/:
  companies.json  — lista de Company
  users.json      — lista de User (incluye password_hash; archivo con permiso 600)
  sessions.json   — lista de Session (tokens)
  audit.jsonl     — un AuditEvent por línea (append-only)

Capa de adaptación tenant ↔ datos existentes (sin modificar fases anteriores):
  dataset_owner(dataset_id) → company_id dueño del dataset.
  El dataset "demo-retail" pertenece a la empresa demo "demo-retail".
"""

from __future__ import annotations

import json
import os
import threading

from . import roles as roles_mod
from . import session_tokens as _st
from .models import AuditEvent, Company, Session, User

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "tenant")

# Backend del tenant store: "json" (default) o "postgres".
TENANT_BACKEND_ENV_VAR = "ZAYVERO_TENANT_BACKEND"

# Render Secret Files se montan aquí. Ver docs/security-remediation-plan.md.
RENDER_SECRETS_USERS_FILE = "/etc/secrets/users.json"

# Variable de entorno para apuntar explícitamente al archivo de usuarios.
# Prioridad de resolución: ZAYVERO_USERS_FILE > /etc/secrets/users.json > <data_dir>/users.json
USERS_FILE_ENV_VAR = "ZAYVERO_USERS_FILE"

# Si ZAYVERO_REQUIRE_SECRET_FILE=1 (o true/yes/on), la resolución NUNCA puede
# caer silenciosamente a la copia local del repo: si el origen resuelto es
# "local", se aborta con RuntimeError. Pensado para producción (Render):
# evita que un users.json re-commiteado por error reactive credenciales viejas.
REQUIRE_SECRET_FILE_ENV_VAR = "ZAYVERO_REQUIRE_SECRET_FILE"


def _strtobool(value: str) -> bool:
    return (value or "").strip().lower() in ("1", "true", "yes", "on")


def resolve_users_file(data_dir: str) -> tuple[str, str]:
    """Resuelve la ruta del archivo de usuarios.

    Devuelve (ruta, origen) donde origen es "env" | "render-secret" | "local".
    Si el archivo no existe en la ruta resuelta, el store arranca vacío:
    la app funciona pero nadie puede iniciar sesión (comportamiento seguro
    y explícito; NUNCA se crean usuarios por defecto).
    """
    env_path = os.environ.get(USERS_FILE_ENV_VAR, "").strip()
    if env_path:
        path, origin = env_path, "env"
    elif os.path.exists(RENDER_SECRETS_USERS_FILE):
        path, origin = RENDER_SECRETS_USERS_FILE, "render-secret"
    else:
        path, origin = os.path.join(data_dir, "users.json"), "local"
    if origin == "local" and _strtobool(os.environ.get(REQUIRE_SECRET_FILE_ENV_VAR, "")):
        raise RuntimeError(
            f"{REQUIRE_SECRET_FILE_ENV_VAR}=1 exige un archivo de usuarios fuera del "
            f"repo (variable {USERS_FILE_ENV_VAR} o {RENDER_SECRETS_USERS_FILE}); "
            f"no se permite la ruta local {path}."
        )
    return path, origin


def create_tenant_store(data_dir: str | None = None,
                        backend: str | None = None):
    """Crea el store del tenant según el backend pedido.

    backend: "postgres" | "json" | None (None = lee ZAYVERO_TENANT_BACKEND,
    default "json"). "postgres" exige ZAYVERO_OS_DATABASE_URL y aplica
    las migraciones si faltan (PostgresTenantStore, auto_migrate=True).
    """
    mode = (backend if backend is not None
            else os.environ.get(TENANT_BACKEND_ENV_VAR, "json"))
    mode = (mode or "json").strip().lower()
    if mode == "postgres":
        from .store_pg import PostgresTenantStore
        return PostgresTenantStore(data_dir=data_dir)
    if mode == "json":
        return TenantStore(data_dir)
    raise ValueError(
        f"{TENANT_BACKEND_ENV_VAR}={mode!r} no válido: use 'json' o 'postgres'")


# company_id de la empresa demo. El dataset UCI Online Retail II NUNCA se
# presenta como datos de un cliente real.
DEMO_COMPANY_ID = "demo-retail"
DEMO_DATASET_ID = "demo-retail"


def utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session_expired(expires_at: str) -> bool:
    """True si la sesión expiró o su fecha no es parseable (falla cerrado)."""
    from datetime import datetime, timezone

    try:
        exp = datetime.strptime(
            expires_at or "", "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=timezone.utc)
    except Exception:
        return True
    return exp < datetime.now(timezone.utc)


def dataset_owner(dataset_id: str) -> str | None:
    """Adaptador: ¿a qué empresa pertenece un dataset? Sin recalcular nada."""
    if dataset_id == DEMO_DATASET_ID:
        return DEMO_COMPANY_ID
    return None


class TenantStore:
    def __init__(self, data_dir: str | None = None):
        self.data_dir = os.path.abspath(data_dir or DEFAULT_DATA_DIR)
        os.makedirs(self.data_dir, exist_ok=True)
        self._companies_file = os.path.join(self.data_dir, "companies.json")
        # La ruta de usuarios se resuelve por prioridad:
        # ZAYVERO_USERS_FILE > /etc/secrets/users.json > <data_dir>/users.json
        # Si el archivo no existe, el store arranca vacío (nadie puede entrar).
        self._users_file, self._users_file_origin = resolve_users_file(self.data_dir)
        import logging
        _log = logging.getLogger(__name__)
        if not os.path.exists(self._users_file):
            _log.warning(
                "users.json no encontrado en %s (origen: %s). "
                "Sin usuarios: nadie puede iniciar sesión. "
                "Provea %s o un Secret File en Render.",
                self._users_file, self._users_file_origin, USERS_FILE_ENV_VAR,
            )
        else:
            # Línea informativa para verificar en logs de Render qué fuente
            # de usuarios está activa (sin exponer contenido).
            _log.info(
                "users.json activo: %s (origen: %s)",
                self._users_file, self._users_file_origin,
            )
        self._sessions_file = os.path.join(self.data_dir, "sessions.json")
        self._audit_file = os.path.join(self.data_dir, "audit.jsonl")
        # REFORZAMIENTO (2026-10-13): candado para los ciclos
        # leer-modificar-escribir del store JSON. Sin él, logins
        # concurrentes pierden sesiones (una escritura pisa a la otra y
        # la siguiente petición del worker recibe 401).
        self._lock = threading.RLock()
        # Sprint 3: migra sesiones legacy (token en claro en session_id) a
        # verificadores. Idempotente.
        self._migrar_sesiones_legacy()

    def _migrar_sesiones_legacy(self):
        """Convierte sesiones legacy (token en claro en session_id) al
        formato con verificador. Idempotente; falla cerrado ante filas
        corruptas (se conservan sin migrar, inutilizables para login).

        Como el archivo ya contuvo tokens en claro, en producción se
        exigiría re-login forzoso; en local se migra por hashing.
        """
        dicts = self._load_list(self._sessions_file)

        def _es_legacy(s):
            return (isinstance(s, dict) and not s.get("token_sha")
                    and s.get("session_id"))

        if not any(_es_legacy(s) for s in dicts):
            return
        migradas = _st.migrate_legacy_sessions(
            [s for s in dicts if _es_legacy(s)])
        conservadas = [s for s in dicts if not _es_legacy(s)]
        self._save_list(
            self._sessions_file,
            conservadas + [s.to_dict() for s in migradas])

    # ---- helpers de archivo -------------------------------------------
    def _load_list(self, path: str) -> list:
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return json.load(f)

    def _save_list(self, path: str, items: list):
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        if path == self._users_file or path.endswith("users.json"):
            os.chmod(path, 0o600)

    # ---- companies -----------------------------------------------------
    def list_companies(self) -> list[Company]:
        return [Company(**c) for c in self._load_list(self._companies_file)]

    def get_company(self, company_id: str) -> Company | None:
        for c in self.list_companies():
            if c.company_id == company_id:
                return c
        return None

    def save_company(self, company: Company):
        with self._lock:
            items = [c for c in self._load_list(self._companies_file)
                     if c["company_id"] != company.company_id]
            items.append(company.to_dict())
            self._save_list(self._companies_file, items)

    def ensure_demo_company(self) -> Company:
        """Crea la empresa demo si no existe. NO toca datos de fases anteriores.

        FASE 7C: la demo recibe su configuración internacional (la moneda
        GBP corresponde al contexto del dataset UCI, no a un cliente real).
        Si la empresa demo ya existe sin configuración, se completa de
        forma compatible (backfill, sin romper nada).
        """
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
        return [User(**u) for u in self._load_list(self._users_file)]

    def get_user(self, user_id: str) -> User | None:
        for u in self.list_users():
            if u.user_id == user_id:
                return u
        return None

    def get_user_by_email(self, email: str) -> User | None:
        email = (email or "").strip().lower()
        for u in self.list_users():
            if u.email.strip().lower() == email:
                return u
        return None

    def list_users_by_email(self, email: str) -> list[User]:
        """Todas las cuentas con ese email (multi-empresa, sprint 4).

        El login anti-enumeración valida la contraseña contra CADA una
        antes de revelar nada.
        """
        email = (email or "").strip().lower()
        return [u for u in self.list_users()
                if u.email.strip().lower() == email]

    def save_user(self, user: User):
        with self._lock:
            items = [u for u in self._load_list(self._users_file)
                     if u["user_id"] != user.user_id]
            d = user.to_dict(include_secret=True)
            items.append(d)
            self._save_list(self._users_file, items)

    def list_users_by_company(self, company_id: str) -> list[User]:
        return [u for u in self.list_users() if u.company_id == company_id]

    # ---- sessions -------------------------------------------------------
    def list_sessions(self) -> list[Session]:
        # Tolerante a esquemas: ignora claves desconocidas (p. ej.
        # token_sha/token_verifier/token_salt del verificador de sesión)
        # para no romper la lectura de filas escritas por versiones nuevas.
        campos = {f.name for f in Session.__dataclass_fields__.values()}
        return [Session(**{k: v for k, v in s.items() if k in campos})
                for s in self._load_list(self._sessions_file)]

    def get_session(self, token: str) -> Session | None:
        """Localiza la sesión por el Bearer token (verificadores, sprint 3).

        Recomputa token_sha para el lookup y verifica la posesión en
        tiempo constante. La revocación se verifica en cada lookup
        (falla cerrado); la expiración la verifica get_tenant_context()
        (que distingue "sesión expirada" de "sesión inválida").
        """
        if not token:
            return None
        import hmac as _hmac
        lookup = _st.lookup_key(token)
        for s in self.list_sessions():
            if not s.token_sha:
                continue
            if not _hmac.compare_digest(s.token_sha, lookup):
                continue
            if not _st.verify_token(token, s.token_verifier, s.token_salt):
                return None
            if s.revoked:
                return None
            return s
        return None

    def get_session_by_id(self, session_id: str) -> Session | None:
        """Busca por id interno (sin token). Falla cerrado si expiró/revocó.

        Se usa para el enlace demo -> sesión origen (exit_demo rota el
        token de la origen sin conocerlo).
        """
        if not session_id:
            return None
        for s in self.list_sessions():
            if s.session_id == session_id:
                if s.revoked or _session_expired(s.expires_at):
                    return None
                return s
        return None

    def save_session(self, session: Session):
        with self._lock:
            items = [s for s in self._load_list(self._sessions_file)
                     if s["session_id"] != session.session_id]
            items.append(session.to_dict())
            self._save_list(self._sessions_file, items)

    # ---- audit (append-only) --------------------------------------------
    def append_audit(self, event: AuditEvent):
        line = json.dumps(event.to_dict(), ensure_ascii=False)
        with open(self._audit_file, "a", encoding="utf-8") as f:
            f.write(line + "\n")

    def list_audit(self, company_id: str | None = None) -> list[AuditEvent]:
        if not os.path.exists(self._audit_file):
            return []
        events = []
        with open(self._audit_file, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                events.append(AuditEvent(**json.loads(line)))
        if company_id is not None:
            events = [e for e in events if e.company_id == company_id]
        return events

    # ---- roles / permissions --------------------------------------------
    def list_roles(self) -> list:
        from .models import Role, Permission
        return [
            {"role": Role(role_id=k, name=v).to_dict(),
             "permissions": roles_mod.permissions_for(k)}
            for k, v in roles_mod.ROLES.items()
        ]

    def list_permissions(self) -> list:
        from .models import Permission
        return [Permission(permission_id=k, name=v).to_dict()
                for k, v in roles_mod.PERMISSIONS.items()]
