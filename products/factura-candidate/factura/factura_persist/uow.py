"""Company-scoped unit of work. Nested services share one transaction.

All decorated writers acquire the same company row lock on PostgreSQL.
SQLite uses BEGIN IMMEDIATE. This deliberately favors correctness over
parallel writes within a small merchant; different PG companies do not block.
"""
from contextvars import ContextVar
from contextlib import contextmanager
from functools import wraps
from decimal import Decimal
import hashlib
import inspect
import json

_active = ContextVar("factura_uow", default=None)


class BorrowedConnection:
    def __init__(self, conn):
        self._conn = conn

    def __getattr__(self, name):
        return getattr(self._conn, name)

    def close(self):
        pass  # Only the outer unit owns connection lifetime.

    def commit(self):
        raise RuntimeError("solo la unidad de trabajo puede confirmar")

    def rollback(self):
        raise RuntimeError("solo la unidad de trabajo puede revertir")


def is_borrowed(conn):
    return isinstance(conn, BorrowedConnection)


def in_transaction():
    return _active.get() is not None


def borrowed_connection():
    current = _active.get()
    return BorrowedConnection(current[0]) if current else None


def require_company(company):
    current = _active.get()
    if not current or int(company) != current[1]:
        raise ValueError("operación fuera de la transacción de la empresa")


@contextmanager
def company_transaction(company):
    from . import db
    company = int(company)
    current = _active.get()
    if current:
        require_company(company)
        yield BorrowedConnection(current[0])
        return
    conn = db.connect()
    try:
        with db.locked(conn):
            cur = conn.cursor()
            sql = "SELECT id FROM factura_empresas WHERE id=%s"
            if db.backend() == "pg":
                sql += " FOR UPDATE"
            if not db.fetchone(cur, sql, (company,)):
                raise ValueError("empresa no encontrada")
            token = _active.set((conn, company))
            try:
                yield BorrowedConnection(conn)
            finally:
                _active.reset(token)
    finally:
        conn.close()


def _validate_numbers(value, field=""):
    fields = {"cantidad", "precio", "costo", "stock", "stock_nuevo",
              "stock_minimo", "monto", "monto_final", "monto_inicial",
              "descuento", "descuento_global"}
    if isinstance(value, dict):
        for key, item in value.items():
            _validate_numbers(item, key)
    elif isinstance(value, (list, tuple)):
        for item in value:
            _validate_numbers(item, field)
    elif field in fields and value not in (None, ""):
        try:
            valid = Decimal(str(value)).is_finite()
        except Exception:
            valid = False
        if not valid:
            raise ValueError("importe o cantidad no finito/inválido")


def atomic_company(func):
    """Atomic writer, transactional audit, and durable payload-aware replay."""
    signature = inspect.signature(func)
    operation = func.__module__ + "." + func.__name__

    @wraps(func)
    def wrapped(*args, **kwargs):
        from . import db
        bound = signature.bind(*args, **kwargs)
        bound.apply_defaults()
        params = dict(bound.arguments)
        company = int(params["empresa_id"])
        _validate_numbers(params)
        key = str(params.get("idempotencia") or "").strip()
        if len(key) > 200:
            raise ValueError("clave de idempotencia demasiado larga")
        canonical = json.dumps(params, sort_keys=True, ensure_ascii=False,
                               separators=(",", ":"), default=str, allow_nan=False)
        digest = hashlib.sha256(canonical.encode()).hexdigest()
        with company_transaction(company) as conn:
            cur = conn.cursor()
            if key:
                prior = db.fetchone(cur, "SELECT payload_hash, respuesta FROM factura_operaciones WHERE empresa_id=%s AND operacion=%s AND clave=%s", (company, operation, key))
                if prior:
                    if prior[0] != digest:
                        raise ValueError("clave de idempotencia utilizada con otros datos")
                    result = json.loads(prior[1])
                    if operation.endswith(".registrar_pago"):
                        result["duplicado_evitado"] = True
                    return result
            result = func(*args, **kwargs)
            from factura_auditoria import registrar
            actor = params.get("creada_por", params.get("creado_por", params.get("usuario_id")))
            entity_id = result.get("id") if isinstance(result, dict) else None
            registrar(company, actor, operation, "operacion", entity_id,
                      {"transaccional": True})
            if key:
                response = json.dumps(result, ensure_ascii=False, default=str, allow_nan=False)
                db.execute(cur, "INSERT INTO factura_operaciones (empresa_id, operacion, clave, payload_hash, respuesta) VALUES (%s,%s,%s,%s,%s)", (company, operation, key, digest, response))
            return result
    return wrapped


@contextmanager
def join_new_company(conn, company):
    """Join the company/user bootstrap transaction before its first commit."""
    if in_transaction():
        raise RuntimeError("bootstrap ya tiene transacción")
    token = _active.set((conn, int(company)))
    try:
        yield
    finally:
        _active.reset(token)
