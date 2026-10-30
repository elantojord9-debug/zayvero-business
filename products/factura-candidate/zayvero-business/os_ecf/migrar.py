"""Migraciones del módulo e-CF (Fase 1, local, sintético).

Esquema PROPIO e independiente:
- Tracking en `os_ecf_migrations` (tabla de control con los archivos
  aplicados). JAMÁS toca `os_persist/schema/`, `os_migrations` ni el
  manifiesto de 23 migraciones.
- Orden de aplicación: `os_ecf/schema/_orden.txt`.
- Idempotente: solo aplica los archivos no registrados; la segunda
  corrida devuelve lista vacía.

Sin DDL en runtime fuera de esta función: ningún otro módulo de os_ecf
crea ni altera tablas.

AVISO: SIMULACIÓN — NO VÁLIDO ANTE DGII.
"""
from __future__ import annotations

import os
from pathlib import Path

import psycopg

from .modelos import ErrorECF

_SCHEMA_DIR = Path(__file__).resolve().parent / "schema"
_ORDEN = _SCHEMA_DIR / "_orden.txt"
_TABLA_CONTROL = "os_ecf_migrations"


def _resolver_dsn(dsn: str | None) -> str:
    valor = dsn or os.environ.get("ZAYVERO_OS_DATABASE_URL")
    if not valor:
        raise ErrorECF(
            "sin DSN: defina ZAYVERO_OS_DATABASE_URL o pase el parámetro dsn")
    return valor


def migrar(dsn: str | None = None) -> list[str]:
    """Aplica las migraciones pendientes del esquema os_ecf.

    Crea `os_ecf_migrations` si no existe, aplica en orden los SQL de
    `_orden.txt` que no estén registrados y devuelve la lista de
    archivos aplicados (vacía si no había nada pendiente).
    """
    dsn = _resolver_dsn(dsn)
    # El único DDL permitido en este módulo vive dentro de esta función.
    _sql_control = (
        f"CREATE TABLE IF NOT EXISTS {_TABLA_CONTROL} ("
        "    archivo TEXT PRIMARY KEY,"
        "    aplicado_en TIMESTAMPTZ NOT NULL DEFAULT now()"
        ")"
    )
    with open(_ORDEN, encoding="utf-8") as f:
        archivos = [ln.strip() for ln in f
                    if ln.strip() and not ln.strip().startswith("#")]
    aplicados: list[str] = []
    with psycopg.connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(_sql_control)
            cur.execute(f"SELECT archivo FROM {_TABLA_CONTROL}")
            hechos = {fila[0] for fila in cur.fetchall()}
        for archivo in archivos:
            if archivo in hechos:
                continue
            ruta = _SCHEMA_DIR / archivo
            if not ruta.is_file():
                raise ErrorECF(
                    f"migración e-CF no encontrada: {archivo!r}")
            sql = ruta.read_text(encoding="utf-8")
            if archivo == "004_os_ecf_emisor_canonico.sql":
                # Pre-verificación INLINE (el guard test_migrar_sin_ddl exige
                # que todo .execute() viva dentro de migrar()): aborta si dos
                # comprobantes compartirían (emisor_rnc_norm, e_ncf). Sin
                # corrupción silenciosa: el operador resuelve manualmente.
                with psycopg.connect(dsn) as _chk:
                    with _chk.cursor() as _cur:
                        _cur.execute(
                            "SELECT regexp_replace(emisor_rnc, '\\D', '', 'g'),"
                            "       e_ncf, COUNT(*),"
                            "       string_agg(DISTINCT company_id, ', ')"
                            "  FROM os_ecf_comprobantes"
                            " WHERE emisor_rnc IS NOT NULL"
                            " GROUP BY 1, 2 HAVING COUNT(*) > 1")
                        _choques = _cur.fetchall()
                if _choques:
                    _det = "; ".join(
                        f"rnc={r[0]} e_ncf={r[1]} empresas={r[3]}"
                        for r in _choques[:5])
                    raise ErrorECF(
                        "migración 004 abortada: e-NCF duplicados por emisor "
                        f"({len(_choques)} casos: {_det}). Resuelva "
                        "manualmente antes de reintentar.")
            with conn.cursor() as cur:
                cur.execute(sql)
                cur.execute(
                    f"INSERT INTO {_TABLA_CONTROL} (archivo) VALUES (%s)",
                    (archivo,))
            conn.commit()
            aplicados.append(archivo)
    return aplicados


if __name__ == "__main__":  # pragma: no cover
    for a in migrar():
        print(f"aplicada: {a}")
