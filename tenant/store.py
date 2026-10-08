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

from . import roles as roles_mod
from .models import AuditEvent, Company, Session, User

DEFAULT_DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "data", "tenant")

# company_id de la empresa demo. El dataset UCI Online Retail II NUNCA se
# presenta como datos de un cliente real.
DEMO_COMPANY_ID = "demo-retail"
DEMO_DATASET_ID = "demo-retail"


def utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
        self._users_file = os.path.join(self.data_dir, "users.json")
        self._sessions_file = os.path.join(self.data_dir, "sessions.json")
        self._audit_file = os.path.join(self.data_dir, "audit.jsonl")

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
        if path.endswith("users.json"):
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

    def save_user(self, user: User):
        items = [u for u in self._load_list(self._users_file)
                 if u["user_id"] != user.user_id]
        d = user.to_dict(include_secret=True)
        items.append(d)
        self._save_list(self._users_file, items)

    def list_users_by_company(self, company_id: str) -> list[User]:
        return [u for u in self.list_users() if u.company_id == company_id]

    # ---- sessions -------------------------------------------------------
    def list_sessions(self) -> list[Session]:
        return [Session(**s) for s in self._load_list(self._sessions_file)]

    def get_session(self, session_id: str) -> Session | None:
        for s in self.list_sessions():
            if s.session_id == session_id:
                return s
        return None

    def save_session(self, session: Session):
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
