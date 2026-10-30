"""Registro público de empresas (FASE 1 + FASE 4).

Alta de empresa + usuario administrador inicial. La empresa nace en
estado 'activa' con trial_hasta = hoy + config.TRIAL_DIAS y plan
'basico'. Se crea también su sucursal "Principal".
"""
from __future__ import annotations

from factura_core import clock

from datetime import date, datetime, timedelta, timezone

import config
from factura_persist import db
from factura_auth import auth as auth_mod


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def registrar_empresa(nombre: str, rnc: str, telefono: str, direccion: str,
                      email_admin: str, password: str) -> dict:
    """Crea empresa + admin. Valida email único y política de clave."""
    from factura_core import clientes as cli_mod  # validación RNC reutilizada
    nombre = (nombre or "").strip()
    if not nombre:
        raise ValueError("el nombre de la empresa es obligatorio")
    rnc_n = cli_mod.normalizar_rnc(rnc) if (rnc or "").strip() else ""
    if not rnc_n:
        raise ValueError("el RNC de la empresa es obligatorio (9 u 11 dígitos)")
    email_n = (email_admin or "").strip().lower()
    if "@" not in email_n:
        raise ValueError("email del administrador inválido")
    if auth_mod._fila_usuario_por_email(email_n):
        raise ValueError("ese email ya está registrado")

    trial_hasta = (clock.today_business() +
                   timedelta(days=config.TRIAL_DIAS)).isoformat()
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            eid = db.insert_id(
                cur, "INSERT INTO factura_empresas (nombre, rnc, telefono,"
                     " direccion, plan, itbis_tasa, e_cf_mode, creada,"
                     " estado, trial_hasta)"
                     " VALUES (%s,%s,%s,%s,'basico','18','disabled',%s,"
                     " 'activa',%s)",
                (nombre, rnc_n, (telefono or "").strip(),
                 (direccion or "").strip(), _ahora(), trial_hasta))
            db.execute(
                cur, "INSERT INTO factura_sucursales (empresa_id, nombre,"
                     " direccion, telefono, activa, creada)"
                     " VALUES (%s,'Principal',%s,%s,1,%s)",
                (eid, (direccion or "").strip(), (telefono or "").strip(),
                 _ahora()))
            from factura_persist.uow import join_new_company
            with join_new_company(conn, eid):
                usuario = auth_mod.crear_usuario(eid, email_n, password, "admin")
                from factura_auditoria import registrar
                registrar(eid, usuario["id"], "empresa.registrada", "empresa", eid,
                          {"transaccional": True})
        return {"empresa_id": eid, "usuario": usuario,
                "trial_hasta": trial_hasta,
                "trial_dias": config.TRIAL_DIAS}
    finally:
        conn.close()
