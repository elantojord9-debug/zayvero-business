"""BLOQUE 6 (sprint 3, 2026-10-12) — Umbrales configurables del catálogo.

Una fila por empresa en `os_catalogo_config` (migración 008). Los
DEFAULTS son de DEMO — sintéticos, NO calibrados con datos reales — y
las respuestas lo indican explícitamente (`origen`):
- `demo_por_defecto`: la empresa nunca los tocó.
- `personalizado`: la empresa los fijó vía POST /api/v1/os/catalogo/config.

Los usa: Price Advisor (márgenes), aprobación de cambios de precio
(umbral_cambio_precio) y aprobación de devoluciones
(umbral_devolucion_monto).
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from catalogo.modelo import ErrorCatalogo, validar_id, validar_moneda  # noqa: E402

DSN_ENV = "ZAYVERO_OS_DATABASE_URL"

# DEFAULTS DE DEMO (sintéticos; ver docstring del módulo).
DEMO_DEFAULTS = {
    "margen_objetivo": Decimal("0.40"),
    "margen_minimo": Decimal("0.00"),
    "umbral_cambio_precio": Decimal("0.25"),
    "umbral_devolucion_monto": Decimal("5000"),
    "moneda_defecto": "DOP",
}

_CAMPOS_NUM = ("margen_objetivo", "margen_minimo", "umbral_cambio_precio",
               "umbral_devolucion_monto")


def _dsn() -> str:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        raise ErrorCatalogo(
            f"la configuración requiere {DSN_ENV} (PostgreSQL local)")
    return dsn


def leer_config(company_id: str) -> dict:
    """Config de la empresa + `origen`. Sin fila: defaults de demo."""
    company_id = validar_id(company_id, "company_id")
    from os_persist.pg import connect
    conn = connect(_dsn())
    try:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT margen_objetivo, margen_minimo,
                          umbral_cambio_precio, umbral_devolucion_monto,
                          moneda_defecto, personalizada
                   FROM os_catalogo_config WHERE company_id=%s""",
                (company_id,))
            r = cur.fetchone()
    finally:
        conn.close()
    cfg = dict(DEMO_DEFAULTS)
    origen = "demo_por_defecto"
    if r:
        cfg = {"margen_objetivo": Decimal(str(r[0])),
               "margen_minimo": Decimal(str(r[1])),
               "umbral_cambio_precio": Decimal(str(r[2])),
               "umbral_devolucion_monto": Decimal(str(r[3])),
               "moneda_defecto": r[4]}
        origen = "personalizado" if r[5] else "demo_por_defecto"
    return {**{k: str(v) for k, v in cfg.items()},
            "origen": origen,
            "nota_demo": ("valores de demostración: no calibrados con "
                          "datos reales; personalícelos vía POST "
                          "catalogo/config") if origen == "demo_por_defecto"
            else "valores fijados por la empresa"}


def guardar_config(company_id: str, **campos) -> dict:
    """Actualiza los campos dados; marca la fila como personalizada."""
    company_id = validar_id(company_id, "company_id")
    cambios = {}
    for k in _CAMPOS_NUM:
        if k in campos and campos[k] is not None:
            try:
                v = Decimal(str(campos[k]))
            except Exception:
                raise ErrorCatalogo(f"{k} debe ser numérico: {campos[k]!r}")
            if k == "margen_objetivo" and not (Decimal("0") < v < Decimal("1")):
                raise ErrorCatalogo(
                    "margen_objetivo debe estar entre 0 y 1 (exclusivo)")
            if v < 0:
                raise ErrorCatalogo(f"{k} no puede ser negativo")
            cambios[k] = v
    if "moneda_defecto" in campos and campos["moneda_defecto"] is not None:
        cambios["moneda_defecto"] = validar_moneda(
            campos["moneda_defecto"])
    if not cambios:
        raise ErrorCatalogo("nada que actualizar")
    from os_persist.pg import connect
    conn = connect(_dsn())
    try:
        conn.autocommit = False
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO os_companies (company_id) VALUES (%s)"
                " ON CONFLICT (company_id) DO NOTHING", (company_id,))
            sets = ", ".join(f"{k}=%s" for k in cambios)
            args = [str(cambios[k]) if isinstance(cambios[k], Decimal)
                    else cambios[k] for k in cambios]
            cur.execute(
                f"""INSERT INTO os_catalogo_config
                    (company_id, {", ".join(cambios)}, personalizada)
                    VALUES (%s, {", ".join(["%s"] * len(cambios))}, TRUE)
                    ON CONFLICT (company_id) DO UPDATE
                    SET {sets}, personalizada=TRUE,
                        updated_at=now()""",
                (company_id, *args, *args))
        conn.commit()
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        conn.close()
    return leer_config(company_id)
