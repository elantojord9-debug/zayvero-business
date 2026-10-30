"""BLOQUE PLATAFORMA SAAS (2026-10-13) — Persistencia PostgreSQL del SaaS.

Tablas `saas_*` (migración 009). Reglas:
- Aislamiento: TODA lectura/escritura filtra por company_id o email_norm
  (la clave de la cuenta). Nunca se toca la fila de otra empresa.
- Atomicidad: cada operación de negocio corre en UNA transacción que
  incluye su evento de auditoría en os_eventos (tipos 'saas_*', CHECK
  ampliado en la migración 009). Si algo falla: ROLLBACK total.
- os_eventos es append-only: este módulo solo INSERTA (nunca UPDATE/DELETE).
- El tenant (os_empresas/os_usuarios/os_sesiones) se escribe a través de
  `tenant.auth` (no SQL directo): es el dueño de esas tablas.

Requiere ZAYVERO_OS_DATABASE_URL; sin PG lanza ErrorSaas 503 con mensaje
claro (este bloque no tiene modo memoria: el registro SaaS es plataforma,
no demo).
"""
from __future__ import annotations

import hashlib
import json
import os as _os
import secrets as _secrets
import uuid

from .modelo import ErrorSaas


def _dsn() -> str:
    dsn = (_os.environ.get("ZAYVERO_OS_DATABASE_URL") or "").strip()
    if not dsn.startswith("postgresql://"):
        raise ErrorSaas(
            503, "saas_sin_postgres",
            "El módulo SaaS requiere PostgreSQL "
            "(ZAYVERO_OS_DATABASE_URL no configurado).")
    return dsn


def conectar():
    """Conexión nueva a PG (el llamador la cierra)."""
    from os_persist.pg import connect
    return connect(_dsn())


# ---------------------------------------------------------------------------
# Auditoría en os_eventos (tipos 'saas_*')
# ---------------------------------------------------------------------------
# NOTA: os_persist/auditoria.py::auditar() rechaza tipos fuera de
# TIPOS_EVENTO (ValueError). Los eventos SaaS usan INSERT directo con el
# cursor de LA MISMA transacción de la operación (mismo patrón que
# aprobaciones/http.py). La tabla es append-only por convención.

TIPOS_SAAS = ("saas_registro", "saas_plan", "saas_config", "saas_usuario",
              "saas_onboarding")


def auditar_saas(cur, company_id: str, tipo: str, actor: str,
                 ref_id: str = "", detalle: dict | None = None) -> None:
    """INSERT del evento SaaS con el cursor de la transacción en curso."""
    if tipo not in TIPOS_SAAS:
        raise ErrorSaas(500, "tipo_auditoria_invalido",
                        f"Tipo de auditoría SaaS inválido: {tipo!r}")
    cur.execute(
        """INSERT INTO os_eventos
               (evento_id, company_id, tipo, actor, ref_id, detalle,
                resultado)
           VALUES (%s, %s, %s, %s, %s, %s::jsonb, 'exito')""",
        (uuid.uuid4().hex, company_id, tipo, actor or "",
         ref_id or "", json.dumps(detalle or {}, default=str)))


def _asegurar_os_company(cur, company_id: str, nombre: str) -> None:
    """os_eventos.company_id referencia os_companies: la fila debe existir."""
    cur.execute(
        """INSERT INTO os_companies (company_id, nombre)
           VALUES (%s, %s) ON CONFLICT (company_id) DO NOTHING""",
        (company_id, nombre or ""))


# ---------------------------------------------------------------------------
# Cuentas y empresas
# ---------------------------------------------------------------------------

def obtener_cuenta(cur, email_norm: str) -> dict | None:
    cur.execute(
        "SELECT email_norm, plan_id FROM saas_cuentas WHERE email_norm=%s",
        (email_norm,))
    row = cur.fetchone()
    return {"email": row[0], "plan_id": row[1]} if row else None


def guardar_cuenta(cur, email_norm: str, plan_id: str) -> None:
    cur.execute(
        """INSERT INTO saas_cuentas (email_norm, plan_id)
           VALUES (%s, %s)
           ON CONFLICT (email_norm)
           DO UPDATE SET plan_id=EXCLUDED.plan_id,
                         updated_at=now()""",
        (email_norm, plan_id))


def contar_empresas_cuenta(cur, email_norm: str) -> int:
    cur.execute(
        "SELECT COUNT(*) FROM saas_empresas WHERE creado_por_email=%s",
        (email_norm,))
    return int(cur.fetchone()[0])


def empresas_de_cuenta(cur, email_norm: str) -> list[dict]:
    cur.execute(
        """SELECT company_id, nombre, identificador_fiscal, moneda_base,
                  plan_id, estado, created_at
           FROM saas_empresas WHERE creado_por_email=%s
           ORDER BY created_at""",
        (email_norm,))
    return [{
        "company_id": r[0], "nombre": r[1], "identificador_fiscal": r[2],
        "moneda_base": r[3], "plan_id": r[4], "estado": r[5],
        "created_at": r[6].isoformat() if r[6] else None,
    } for r in cur.fetchall()]


def existe_fiscal_o_nombre(cur, fiscal_norm: str,
                           nombre_norm: str) -> bool:
    """True si el fiscal O el nombre normalizado ya existen (global).
    Una sola consulta y un solo booleano: no revela CUÁL colisionó
    (anti-enumeración en el registro)."""
    cur.execute(
        """SELECT 1 FROM saas_empresas
           WHERE identificador_fiscal=%s OR nombre_normalizado=%s
           LIMIT 1""",
        (fiscal_norm, nombre_norm))
    return cur.fetchone() is not None


def crear_empresa(cur, *, company_id: str, nombre: str, nombre_norm: str,
                  fiscal_norm: str, moneda: str, plan_id: str,
                  sucursales: list, creado_por_email: str,
                  actor: str) -> dict:
    """INSERT de la empresa + fila os_companies + evento de auditoría.
    Todo en la transacción del llamador."""
    cur.execute(
        """INSERT INTO saas_empresas
               (company_id, nombre, nombre_normalizado,
                identificador_fiscal, moneda_base, plan_id, sucursales,
                creado_por_email)
           VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s)""",
        (company_id, nombre, nombre_norm, fiscal_norm, moneda, plan_id,
         json.dumps(sucursales), creado_por_email))
    _asegurar_os_company(cur, company_id, nombre)
    cur.execute(
        """INSERT INTO saas_onboarding (company_id, pasos)
           VALUES (%s, %s::jsonb)""",
        (company_id, json.dumps({
            "crear_empresa": {"estado": "completado",
                              "origen": "registro"},
        })))
    auditar_saas(cur, company_id, "saas_registro", actor,
                 ref_id=company_id,
                 detalle={"nombre": nombre,
                          "identificador_fiscal": fiscal_norm,
                          "moneda_base": moneda, "plan_id": plan_id,
                          "sucursales": len(sucursales)})
    return {"company_id": company_id, "nombre": nombre,
            "identificador_fiscal": fiscal_norm, "moneda_base": moneda,
            "plan_id": plan_id, "sucursales": sucursales,
            "estado": "activa"}


def obtener_empresa(cur, company_id: str) -> dict | None:
    cur.execute(
        """SELECT company_id, nombre, identificador_fiscal, moneda_base,
                  plan_id, sucursales, estado, creado_por_email, created_at
           FROM saas_empresas WHERE company_id=%s""",
        (company_id,))
    row = cur.fetchone()
    if not row:
        return None
    return {
        "company_id": row[0], "nombre": row[1],
        "identificador_fiscal": row[2], "moneda_base": row[3],
        "plan_id": row[4], "sucursales": row[5] or [],
        "estado": row[6], "creado_por_email": row[7],
        "created_at": row[8].isoformat() if row[8] else None,
    }


def actualizar_empresa(cur, company_id: str, *, moneda: str | None = None,
                       sucursales: list | None = None,
                       estado: str | None = None) -> dict:
    sets, vals = [], []
    if moneda is not None:
        sets.append("moneda_base=%s")
        vals.append(moneda)
    if sucursales is not None:
        sets.append("sucursales=%s::jsonb")
        vals.append(json.dumps(sucursales))
    if estado is not None:
        sets.append("estado=%s")
        vals.append(estado)
    if not sets:
        raise ErrorSaas(400, "sin_cambios", "No se indicó ningún cambio.")
    vals.append(company_id)
    cur.execute(
        f"UPDATE saas_empresas SET {', '.join(sets)}, updated_at=now() "
        "WHERE company_id=%s",
        tuple(vals))
    if cur.rowcount == 0:
        raise ErrorSaas(404, "empresa_no_encontrada",
                        "La empresa no existe.")
    return obtener_empresa(cur, company_id)


# ---------------------------------------------------------------------------
# Métricas de uso
# ---------------------------------------------------------------------------

def obtener_metrica(cur, company_id: str, periodo: str) -> dict:
    cur.execute(
        """SELECT ejecuciones_ia, llamadas_api, almacenamiento_bytes
           FROM saas_metricas_uso WHERE company_id=%s AND periodo=%s""",
        (company_id, periodo))
    row = cur.fetchone()
    if row:
        return {"ejecuciones_ia": int(row[0]),
                "llamadas_api": int(row[1]),
                "almacenamiento_bytes": int(row[2])}
    return {"ejecuciones_ia": 0, "llamadas_api": 0,
            "almacenamiento_bytes": 0}


def sumar_uso(cur, company_id: str, periodo: str, *,
              ejecuciones_ia: int = 0, llamadas_api: int = 0,
              almacenamiento_bytes: int = 0) -> dict:
    """Suma consumo al período (upsert). Devuelve la fila resultante."""
    for nombre, valor in (("ejecuciones_ia", ejecuciones_ia),
                          ("llamadas_api", llamadas_api),
                          ("almacenamiento_bytes",
                           almacenamiento_bytes)):
        if not isinstance(valor, int) or valor < 0:
            raise ErrorSaas(400, "consumo_invalido",
                            f"'{nombre}' debe ser un entero >= 0.")
    cur.execute(
        """INSERT INTO saas_metricas_uso
               (company_id, periodo, ejecuciones_ia, llamadas_api,
                almacenamiento_bytes)
           VALUES (%s,%s,%s,%s,%s)
           ON CONFLICT (company_id, periodo)
           DO UPDATE SET ejecuciones_ia=saas_metricas_uso.ejecuciones_ia
                                     +EXCLUDED.ejecuciones_ia,
                         llamadas_api=saas_metricas_uso.llamadas_api
                                     +EXCLUDED.llamadas_api,
                         almacenamiento_bytes=
                             saas_metricas_uso.almacenamiento_bytes
                             +EXCLUDED.almacenamiento_bytes,
                         updated_at=now()""",
        (company_id, periodo, ejecuciones_ia, llamadas_api,
         almacenamiento_bytes))
    return obtener_metrica(cur, company_id, periodo)


def metricas_rango(cur, company_id: str, desde: str,
                   hasta: str) -> list[dict]:
    cur.execute(
        """SELECT periodo, ejecuciones_ia, llamadas_api,
                  almacenamiento_bytes
           FROM saas_metricas_uso
           WHERE company_id=%s AND periodo>=%s AND periodo<=%s
           ORDER BY periodo""",
        (company_id, desde, hasta))
    return [{
        "periodo": r[0], "ejecuciones_ia": int(r[1]),
        "llamadas_api": int(r[2]),
        "almacenamiento_bytes": int(r[3]),
    } for r in cur.fetchall()]


# ---------------------------------------------------------------------------
# Onboarding
# ---------------------------------------------------------------------------

def obtener_onboarding(cur, company_id: str) -> dict:
    cur.execute("SELECT pasos FROM saas_onboarding WHERE company_id=%s",
                (company_id,))
    row = cur.fetchone()
    return dict(row[0] or {}) if row else {}


def marcar_paso_onboarding(cur, company_id: str, paso: str, estado: str,
                           actor: str, origen: str = "manual") -> dict:
    pasos = obtener_onboarding(cur, company_id)
    pasos[paso] = {"estado": estado, "origen": origen}
    cur.execute(
        """INSERT INTO saas_onboarding (company_id, pasos)
           VALUES (%s, %s::jsonb)
           ON CONFLICT (company_id)
           DO UPDATE SET pasos=EXCLUDED.pasos, updated_at=now()""",
        (company_id, json.dumps(pasos)))
    auditar_saas(cur, company_id, "saas_onboarding", actor,
                 ref_id=paso, detalle={"paso": paso, "estado": estado,
                                       "origen": origen})
    return pasos


# ---------------------------------------------------------------------------
# Invitaciones (token hasheado; el crudo se entrega una sola vez)
# ---------------------------------------------------------------------------

def _sha(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def crear_invitacion(cur, *, company_id: str, email_norm: str, rol: str,
                     creado_por: str, dias_validez: int = 7) -> dict:
    """Genera la invitación y devuelve el token CRUDO una sola vez."""
    token = _secrets.token_urlsafe(32)
    invitacion_id = "INV-" + uuid.uuid4().hex[:12]
    cur.execute(
        """INSERT INTO saas_invitaciones
               (invitacion_id, token_sha, company_id, email_norm, rol,
                creado_por, expira_at)
           VALUES (%s,%s,%s,%s,%s,%s, now() + (%s || ' days')::interval)
           RETURNING created_at""",
        (invitacion_id, _sha(token), company_id, email_norm, rol,
         creado_por, str(dias_validez)))
    creado = cur.fetchone()[0]
    auditar_saas(cur, company_id, "saas_usuario", creado_por,
                 ref_id=invitacion_id,
                 detalle={"accion": "invitacion_creada",
                          "email": email_norm, "rol": rol})
    return {"invitacion_id": invitacion_id, "token": token,
            "email": email_norm, "rol": rol,
            "expira_en_dias": dias_validez,
            "created_at": creado.isoformat() if creado else None,
            "aviso": ("Entregue este token al invitado por su propio canal: "
                      "el sistema NO envía correos y el token no vuelve a "
                      "mostrarse.")}


def obtener_invitacion_valida(cur, token: str) -> dict | None:
    """Invitación vigente y sin usar para el token. None = inválida,
    vencida o usada (el llamador responde idéntico en los 3 casos)."""
    cur.execute(
        """SELECT invitacion_id, company_id, email_norm, rol, expira_at,
                  usado_at
           FROM saas_invitaciones WHERE token_sha=%s""",
        (_sha(token),))
    row = cur.fetchone()
    if not row:
        return None
    invitacion_id, company_id, email_norm, rol, expira_at, usado_at = row
    from datetime import datetime, timezone
    ahora = datetime.now(timezone.utc)
    if usado_at is not None or expira_at <= ahora:
        return None
    return {"invitacion_id": invitacion_id, "company_id": company_id,
            "email": email_norm, "rol": rol}


def marcar_invitacion_usada(cur, invitacion_id: str) -> None:
    cur.execute(
        "UPDATE saas_invitaciones SET usado_at=now() "
        "WHERE invitacion_id=%s AND usado_at IS NULL",
        (invitacion_id,))
    if cur.rowcount == 0:
        raise ErrorSaas(409, "invitacion_reutilizada",
                        "La invitación ya fue utilizada.")


def invitaciones_pendientes(cur, company_id: str) -> list[dict]:
    cur.execute(
        """SELECT invitacion_id, email_norm, rol, expira_at, created_at
           FROM saas_invitaciones
           WHERE company_id=%s AND usado_at IS NULL AND expira_at > now()
           ORDER BY created_at""",
        (company_id,))
    return [{
        "invitacion_id": r[0], "email": r[1], "rol": r[2],
        "expira_at": r[3].isoformat() if r[3] else None,
        "created_at": r[4].isoformat() if r[4] else None,
    } for r in cur.fetchall()]


def contar_invitaciones_pendientes(cur, company_id: str) -> int:
    cur.execute(
        """SELECT COUNT(*) FROM saas_invitaciones
           WHERE company_id=%s AND usado_at IS NULL AND expira_at > now()""",
        (company_id,))
    return int(cur.fetchone()[0])


# ---------------------------------------------------------------------------
# Lecturas del tenant (solo lectura; la escritura va por tenant.auth)
# ---------------------------------------------------------------------------

def contar_usuarios_activos(cur, company_id: str) -> int:
    # `status` vive en datos (JSONB); `rol` sí es columna (migración 006).
    cur.execute(
        """SELECT COUNT(*) FROM os_usuarios
           WHERE empresa_id=%s AND datos->>'status'='active'
             AND email <> ''""",
        (company_id,))
    return int(cur.fetchone()[0])


def contar_owners_activos(cur, company_id: str) -> int:
    cur.execute(
        """SELECT COUNT(*) FROM os_usuarios
           WHERE empresa_id=%s AND datos->>'status'='active'
             AND rol='owner' AND email <> ''""",
        (company_id,))
    return int(cur.fetchone()[0])


def hay_ventas(cur, company_id: str) -> bool:
    cur.execute(
        "SELECT 1 FROM os_ventas WHERE company_id=%s LIMIT 1",
        (company_id,))
    return cur.fetchone() is not None


def hay_productos_catalogo(cur, company_id: str) -> bool:
    cur.execute(
        "SELECT 1 FROM os_catalogo_productos WHERE company_id=%s LIMIT 1",
        (company_id,))
    return cur.fetchone() is not None
