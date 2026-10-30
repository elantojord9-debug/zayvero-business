"""Capa SaaS (FASE 4): límites por plan, trial, estados de empresa,
onboarding y operaciones del superadmin local.

Todo sintético, sin cobros reales. Los límites se hacen cumplir en el
servidor antes de crear usuarios, sucursales y documentos.
"""
from __future__ import annotations

from factura_core import clock

from datetime import date, datetime, timedelta, timezone

import config
from factura_persist import db


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def obtener_empresa(empresa_id: int) -> dict | None:
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(cur, "SELECT * FROM factura_empresas WHERE id=%s",
                           (empresa_id,))
        return dict(fila) if fila else None
    finally:
        conn.close()


def plan_de(empresa: dict) -> dict:
    slug = (empresa.get("plan") or "basico")
    return config.PLANES.get(slug, config.PLANES["basico"])


def trial_dias_restantes(empresa: dict) -> int | None:
    """Días de prueba restantes. None = sin trial (cuenta heredada)."""
    hasta = (empresa.get("trial_hasta") or "").strip()
    if not hasta:
        return None
    try:
        fin = date.fromisoformat(hasta[:10])
    except Exception:
        return None
    return (fin - clock.today_business()).days


def bloqueada_por_trial(empresa: dict) -> bool:
    """Al vencer el trial la empresa queda en solo lectura."""
    if (empresa.get("estado") or "activa") != "activa":
        return True
    dias = trial_dias_restantes(empresa)
    return dias is not None and dias < 0


def suspendida(empresa: dict) -> bool:
    return (empresa.get("estado") or "activa") == "suspendida"


def contar_usuarios_activos(empresa_id: int) -> int:
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(
            cur, "SELECT COUNT(*) FROM factura_usuarios"
                 " WHERE empresa_id=%s AND activo=1", (empresa_id,))
        return int(fila[0])
    finally:
        conn.close()


def contar_sucursales_activas(empresa_id: int) -> int:
    from factura_core import sucursales
    return sucursales.contar_activas(empresa_id)


def contar_docs_mes(empresa_id: int) -> int:
    """Documentos de venta (no anulados) creados en el mes calendario."""
    today = clock.today_business()
    first = today.replace(day=1)
    next_month = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(
            cur, "SELECT COUNT(*) FROM factura_ventas"
                 " WHERE empresa_id=%s AND estado<>'anulada'"
                 " AND creada>=%s AND creada<%s", (empresa_id, clock.utc_start(first), clock.utc_start(next_month)))
        return int(fila[0])
    finally:
        conn.close()


def uso_actual(empresa_id: int) -> dict:
    empresa = obtener_empresa(empresa_id)
    plan = plan_de(empresa or {})
    return {
        "plan": (empresa or {}).get("plan") or "basico",
        "plan_nombre": plan["nombre"],
        "estado": (empresa or {}).get("estado") or "activa",
        "trial_dias": trial_dias_restantes(empresa or {}),
        "usuarios": {"usados": contar_usuarios_activos(empresa_id),
                     "limite": plan["max_usuarios"]},
        "sucursales": {"usados": contar_sucursales_activas(empresa_id),
                       "limite": plan["max_sucursales"]},
        "documentos_mes": {"usados": contar_docs_mes(empresa_id),
                           "limite": plan["max_docs_mes"]},
        "empresas": {"usados": 1, "limite": plan["max_empresas"]},
    }


def verificar_limite_usuarios(empresa_id: int) -> None:
    u = uso_actual(empresa_id)
    if u["usuarios"]["usados"] >= u["usuarios"]["limite"]:
        raise ValueError(
            "límite del plan alcanzado: su plan "
            f"{u['plan_nombre']} permite {u['usuarios']['limite']} "
            "usuario(s)")


def verificar_limite_documentos(empresa_id: int) -> None:
    u = uso_actual(empresa_id)
    if u["documentos_mes"]["usados"] >= u["documentos_mes"]["limite"]:
        raise ValueError(
            "límite del plan alcanzado: su plan "
            f"{u['plan_nombre']} permite {u['documentos_mes']['limite']} "
            "documentos por mes")


def cambiar_plan(empresa_id: int, plan: str) -> dict:
    """Cambia el plan de la empresa (configurable, sin cobro real)."""
    if plan not in config.PLANES:
        raise ValueError("plan inválido")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "UPDATE factura_empresas SET plan=%s WHERE id=%s",
                       (plan, empresa_id))
        return obtener_empresa(empresa_id)
    finally:
        conn.close()


# --- superadmin local ---------------------------------------------------------

def listar_empresas() -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        filas = db.fetchall(
            cur, "SELECT id, nombre, rnc, plan, estado, trial_hasta, creada"
                 " FROM factura_empresas ORDER BY id")
        out = []
        for f in filas:
            e = dict(f)
            e["trial_dias"] = trial_dias_restantes(e)
            e["usuarios"] = contar_usuarios_activos(e["id"])
            out.append(e)
        return out
    finally:
        conn.close()


def cambiar_estado(empresa_id: int, estado: str) -> dict:
    if estado not in ("activa", "suspendida"):
        raise ValueError("estado inválido")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "UPDATE factura_empresas SET estado=%s"
                            " WHERE id=%s", (estado, empresa_id))
        return obtener_empresa(empresa_id)
    finally:
        conn.close()


def extender_trial(empresa_id: int, dias: int) -> dict:
    """Extiende el trial N días desde hoy (o desde el vencimiento si aún
    está vigente)."""
    if dias <= 0 or dias > 365:
        raise ValueError("días inválidos (1-365)")
    empresa = obtener_empresa(empresa_id)
    base = clock.today_business()
    actual = trial_dias_restantes(empresa or {})
    if actual is not None and actual > 0:
        base = clock.today_business() + timedelta(days=actual)
    nuevo = (base + timedelta(days=dias)).isoformat()
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "UPDATE factura_empresas SET trial_hasta=%s"
                            " WHERE id=%s", (nuevo, empresa_id))
        return obtener_empresa(empresa_id)
    finally:
        conn.close()


# --- onboarding ---------------------------------------------------------------

def marcar_onboarding(empresa_id: int, completo: bool = True) -> None:
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "UPDATE factura_empresas SET onboarding_completo=%s"
                            " WHERE id=%s", (1 if completo else 0, empresa_id))
    finally:
        conn.close()


def estado_onboarding(empresa_id: int) -> list[dict]:
    """Pasos del wizard /bienvenida con su estado detectado."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        emp = db.fetchone(cur, "SELECT nombre, rnc, telefono,"
                               " onboarding_completo FROM factura_empresas"
                               " WHERE id=%s", (empresa_id,))
        n_prod = db.fetchone(
            cur, "SELECT COUNT(*) FROM factura_productos WHERE empresa_id=%s",
            (empresa_id,))[0]
        n_cli = db.fetchone(
            cur, "SELECT COUNT(*) FROM factura_clientes WHERE empresa_id=%s",
            (empresa_id,))[0]
        n_ven = db.fetchone(
            cur, "SELECT COUNT(*) FROM factura_ventas WHERE empresa_id=%s"
                 " AND estado<>'anulada'", (empresa_id,))[0]
    finally:
        conn.close()
    datos_ok = bool(emp and emp[0] and emp[1])
    pasos = [
        {"slug": "empresa", "titulo": "Datos de tu empresa",
         "detalle": "Nombre, RNC y teléfono para tus facturas.",
         "hecho": datos_ok, "enlace": "/config"},
        {"slug": "producto", "titulo": "Crea tu primer producto",
         "detalle": "Agrega al menos un producto a tu inventario.",
         "hecho": n_prod > 0, "enlace": "/inventario/nuevo"},
        {"slug": "cliente", "titulo": "Registra tu primer cliente",
         "detalle": "Opcional, pero útil para facturas a crédito.",
         "hecho": n_cli > 0, "enlace": "/clientes/nuevo"},
        {"slug": "venta", "titulo": "Haz tu primera venta",
         "detalle": "Desde el POS o con una factura.",
         "hecho": n_ven > 0, "enlace": "/pos"},
        {"slug": "listo", "titulo": "¡Listo para facturar!",
         "detalle": "Explora reportes, caja y e-CF en simulación.",
         "hecho": bool(emp and emp[3]), "enlace": "/panel"},
    ]
    return pasos


# --- recuperación de acceso -----------------------------------------------------

def crear_token_reset(email: str) -> str | None:
    """Genera un token de un solo uso (30 min) para restablecer la clave.

    CIERRE PRE-STAGING (P0 #1):
    - Anti-enumeración: si el correo no existe o el usuario está inactivo,
      devuelve None SIN lanzar excepción; el servidor muestra la MISMA
      página genérica exista o no el correo.
    - El token NUNCA se guarda en claro: en BD va su HASH SHA-256
      (ver factura_auth.seguridad.hash_token). El token crudo solo se
      devuelve aquí para enviarlo por el despachador de correo; el
      servidor jamás lo pinta en HTML.
    """
    import secrets
    from factura_auth import auth as auth_mod
    from factura_auth import seguridad as seg
    fila = auth_mod._fila_usuario_por_email(email or "")
    if not fila or not fila.get("activo"):
        return None
    token = secrets.token_urlsafe(32)
    expira = (datetime.now(timezone.utc) +
              timedelta(minutes=30)).isoformat()
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(
                cur, "UPDATE factura_reset_tokens SET usado_en=%s"
                     " WHERE usuario_id=%s AND usado_en=''",
                (_ahora(), fila["id"]))
            db.execute(
                cur, "INSERT INTO factura_reset_tokens (usuario_id, token,"
                     " expira_en, creado_en) VALUES (%s,%s,%s,%s)",
                (fila["id"], seg.hash_token(token), expira, _ahora()))
    finally:
        conn.close()
    return token


def consumir_token_reset(token: str) -> dict | None:
    """Valida el token (comparando su hash SHA-256) y lo marca usado.
    Devuelve el usuario o None."""
    from factura_auth import seguridad as seg
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            fila = db.fetchone(
                cur, "SELECT id, usuario_id, expira_en, usado_en"
                     " FROM factura_reset_tokens WHERE token=%s",
                (seg.hash_token(token or ""),))
            if not fila:
                return None
            if fila[3]:  # ya usado
                return None
            if fila[2] < _ahora():
                return None
            db.execute(cur, "UPDATE factura_reset_tokens SET usado_en=%s"
                            " WHERE id=%s", (_ahora(), fila[0]))
            u = db.fetchone(cur, "SELECT * FROM factura_usuarios WHERE id=%s",
                            (fila[1],))
            return dict(u) if u else None
    finally:
        conn.close()
