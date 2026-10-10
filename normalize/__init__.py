"""
ZAYVERO BUSINESS — FASE 1A.

Paquete `normalize`: estructura interna normalizada.

Esquema normalizado (columnas de salida):
    company_id          str   (multiempresa; obligatorio)
    Transaction         str   <- Invoice (canónico)
    Product             str   <- StockCode
    Customer            str   <- CustomerID
    Date                datetime64 <- InvoiceDate
    Quantity            float <- Quantity
    UnitPrice           float <- UnitPrice
    Country             str   <- Country
    Revenue             float = Quantity * UnitPrice (null si alguno falta)
    transaction_status  str   <- completed | cancelled | unknown (configurable)
    source_sheet        str   <- nombre de la hoja Excel de origen (FASE 1C; None en CSV)
    source_file         str   <- nombre del archivo fuente (FASE 1C)
    orig_<col>          ...   valores originales conservados

La detección de cancelaciones es CONFIGURABLE vía `cancellation_rules`:
    {"invoice_prefix": "C"}  -> Invoice que empieza con "C" = cancelled
    {"invoice_values": [...]} -> lista explícita de facturas canceladas
    {"column": "Status", "cancelled_values": [...]} -> por columna de estado
Sin regla coincidente y con Invoice presente -> completed.
Sin Invoice -> unknown.
"""

from __future__ import annotations

from typing import Any

import pandas as pd

NORMALIZED_COLUMNS = [
    "company_id",
    "Transaction",
    "Product",
    "Customer",
    "Date",
    "Quantity",
    "UnitPrice",
    "Country",
    "Revenue",
    "transaction_status",
    "source_sheet",
    "source_file",
]

# Mapeo canónico (post-aliases) -> campo normalizado
CANONICAL_TO_NORMALIZED = {
    "Invoice": "Transaction",
    "StockCode": "Product",
    "CustomerID": "Customer",
    "InvoiceDate": "Date",
    "Quantity": "Quantity",
    "UnitPrice": "UnitPrice",
    "Country": "Country",
}

STATUS_COMPLETED = "completed"
STATUS_CANCELLED = "cancelled"
STATUS_UNKNOWN = "unknown"


def detect_cancellation(
    invoice: Any, rules: dict[str, Any] | None = None
) -> str:
    """Clasifica una factura según reglas configurables.

    Valores ausentes (None, NaN, pd.NA, pd.NaT) -> "unknown", como indica
    la documentación del módulo ("Sin Invoice -> unknown").
    """
    rules = rules or {"invoice_prefix": "C"}
    if invoice is None:
        return STATUS_UNKNOWN
    try:
        if bool(pd.isna(invoice)):
            return STATUS_UNKNOWN
    except (TypeError, ValueError):
        # Objeto no escalar: se evalúa como texto más abajo.
        pass
    inv = str(invoice).strip()

    prefix = rules.get("invoice_prefix")
    if prefix and inv.startswith(str(prefix)):
        return STATUS_CANCELLED

    values = rules.get("invoice_values")
    if values and inv in {str(v) for v in values}:
        return STATUS_CANCELLED

    return STATUS_COMPLETED


def normalize(
    df: pd.DataFrame,
    company_id: str,
    cancellation_rules: dict[str, Any] | None = None,
    source_sheet: str | None = None,
    source_file: str | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """
    Normaliza el DataFrame (con columnas ya en nombre canónico).
    Devuelve (df_normalizado, transformaciones_registradas).
    Toda transformación queda en la lista; nada es silencioso.

    FASE 1C: `source_sheet` / `source_file` etiquetan el origen de cada
    registro (trazabilidad multi-hoja). Pueden ser None (ej. CSV sin hojas).
    """
    if not company_id:
        raise ValueError("company_id es obligatorio (multiempresa).")

    transformations: list[str] = []
    out = pd.DataFrame()
    # NOTA: company_id se asigna AL FINAL para evitar NaN por alineación
    # de índices al asignar columnas de distinta longitud.

    # 1) Mapeo canónico -> normalizado (conservando originales como orig_<col>)
    for canonical, norm in CANONICAL_TO_NORMALIZED.items():
        if canonical in df.columns:
            out[f"orig_{canonical}"] = df[canonical].astype("string")
            out[norm] = df[canonical]
            transformations.append(f"map:{canonical}->{norm}")
        else:
            out[norm] = pd.NA
            transformations.append(f"map:{canonical}->{norm} (columna ausente -> NA)")

    # Description no va al esquema núcleo, pero se conserva como original
    if "Description" in df.columns:
        out["orig_Description"] = df["Description"].astype("string")
        transformations.append("keep:orig_Description")

    # Columnas no canónicas (p. ej. Tipo, Gasto_DOP, Nota): se conservan como
    # orig_<col> en vez de descartarse silenciosamente. Permiten identificar
    # el tipo de registro (venta/gasto) tras la normalización sin alterar el
    # esquema núcleo (quedan en las columnas extra, ordenadas al final).
    # Solo cuando ya hay filas: un archivo sin ninguna columna canónica debe
    # seguir produciendo 0 filas para que el pipeline lo reporte como ERROR
    # (descuadre de filas) en vez de pasarlo a READY.
    mapped_canonicals = set(CANONICAL_TO_NORMALIZED) | {"Description"}
    if len(out):
        for col in df.columns:
            if col not in mapped_canonicals and f"orig_{col}" not in out.columns:
                out[f"orig_{col}"] = df[col].astype("string")
                transformations.append(
                    f"keep:orig_{col} (columna no canónica conservada)"
                )

    # 2) Tipos
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce")
    n_bad_dates = int(out["Date"].isna().sum() - out["orig_InvoiceDate"].isna().sum()) \
        if "orig_InvoiceDate" in out.columns else 0
    transformations.append(
        f"coerce:Date a datetime (no parseables -> NaT: {max(n_bad_dates, 0)})"
    )

    out["Quantity"] = pd.to_numeric(out["Quantity"], errors="coerce")
    out["UnitPrice"] = pd.to_numeric(out["UnitPrice"], errors="coerce")
    transformations.append("coerce:Quantity,UnitPrice a numérico (no numéricos -> NaN)")

    for col in ("Transaction", "Product", "Customer", "Country"):
        out[col] = out[col].astype("string")
    transformations.append("coerce:Transaction,Product,Customer,Country a string")

    # 3) Revenue = Quantity * UnitPrice (null si alguno falta)
    out["Revenue"] = out["Quantity"] * out["UnitPrice"]
    n_rev = int(out["Revenue"].notna().sum())
    transformations.append(
        f"calc:Revenue=Quantity*UnitPrice ({n_rev} filas con Revenue válido)"
    )

    # 4) Estado de la transacción (configurable, NO elimina filas)
    out["transaction_status"] = out["Transaction"].apply(
        lambda v: detect_cancellation(v, cancellation_rules)
    )
    counts = out["transaction_status"].value_counts(dropna=False).to_dict()
    transformations.append(
        f"status:transaction_status con reglas {cancellation_rules or {'invoice_prefix': 'C'}} "
        f"-> {counts}"
    )

    # Orden de columnas estable (company_id primero, asignado al final)
    # FASE 1C: trazabilidad de origen (multi-hoja)
    out["source_sheet"] = source_sheet
    out["source_file"] = source_file
    for col in ("source_sheet", "source_file"):
        out[col] = out[col].astype("string")
    transformations.append(
        f"trace:source_sheet='{source_sheet}', source_file='{source_file}' "
        f"asignados a {len(out)} filas"
    )
    extra = [c for c in out.columns if c not in NORMALIZED_COLUMNS]
    out = out[NORMALIZED_COLUMNS[1:] + sorted(extra)]
    out.insert(0, "company_id", company_id)
    transformations.append(f"multiempresa:company_id='{company_id}' asignado a todas las filas")
    return out, transformations
