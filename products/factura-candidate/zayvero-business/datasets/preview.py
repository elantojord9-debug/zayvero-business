"""FASE 7A — Vista previa de datos (primeras 10 filas).

Nunca muestra el archivo completo: solo una muestra para que el cliente
confirme que ZAYVERO leyó correctamente su reporte.
"""

from __future__ import annotations

from typing import Any, Dict


def build_preview(file_path: str, n_rows: int = 10) -> Dict[str, Any]:
    """Devuelve las primeras `n_rows` filas + el total de registros."""
    from ingestion import ingest_sheets

    ingested = ingest_sheets(file_path)
    if not ingested:
        return {"ok": False, "error": "No se pudo leer el archivo."}

    sheet_name, df_raw, _summary = ingested[0]
    total = len(df_raw)
    sample = df_raw.head(n_rows)

    rows = []
    for _, row in sample.iterrows():
        record = {}
        for col in sample.columns:
            v = row[col]
            try:
                import pandas as pd
                if pd.isna(v):
                    record[str(col)] = None
                else:
                    record[str(col)] = v.item() if hasattr(v, "item") else v
            except Exception:
                record[str(col)] = str(v)
        rows.append(record)

    return {
        "ok": True,
        "sheet": sheet_name or "única",
        "columns": [str(c) for c in sample.columns],
        "rows": rows,
        "shown": len(rows),
        "total_rows": total,
        "note": ("Mostrando las primeras "
                 + str(len(rows))
                 + f" de {total:,} registros.".replace(",", ".")),
    }
