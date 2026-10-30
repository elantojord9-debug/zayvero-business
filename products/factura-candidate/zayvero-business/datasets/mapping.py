"""FASE 7A — Mapeo de columnas con confirmación del usuario.

Si el archivo usa nombres diferentes (fecha_venta, producto, cantidad,
precio, cliente), ZAYVERO sugiere correspondencias con los nombres
canónicos usando el diccionario de aliases de FASE 1A.

REGLA: nunca se acepta automáticamente una correspondencia dudosa.
Cuando existe ambigüedad (dos columnas candidatas, o ninguna), el estado
es "needs_review" y el usuario debe confirmar manualmente.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

# Nombres canónicos que el análisis necesita.
CANONICAL_COLUMNS = [
    "Invoice", "StockCode", "Description", "Quantity",
    "InvoiceDate", "UnitPrice", "CustomerID", "Country",
]

# Columnas imprescindibles para un análisis mínimo.
REQUIRED_CANONICAL = ["Quantity", "InvoiceDate", "UnitPrice"]


def _normalized(name: str) -> str:
    import re
    import unicodedata

    name = unicodedata.normalize("NFKD", str(name))
    name = "".join(c for c in name if not unicodedata.combining(c))
    name = name.lower().strip()
    name = re.sub(r"[_\-\.]+", " ", name)
    name = re.sub(r"\s+", " ", name)
    return name


def suggest_mapping(columns: List[str]) -> Dict[str, Any]:
    """Sugiere el mapeo columna_fuente → canónica.

    Devuelve {"suggestions": [...], "needs_confirmation": bool}.
    Cada sugerencia: {"canonical", "source", "status", "note"} donde status
    es "suggested" (coincidencia clara) o "needs_review" (ambigua/ausente).
    """
    from ingestion.aliases import build_lookup

    lookup = build_lookup()
    norm_to_source: Dict[str, str] = {}
    for col in columns:
        norm_to_source.setdefault(_normalized(col), col)

    suggestions = []
    used_sources = set()
    for canonical in CANONICAL_COLUMNS:
        candidates = [
            src for norm, src in norm_to_source.items()
            if lookup.get(norm) == canonical and src not in used_sources
        ]
        if len(candidates) == 1:
            suggestions.append({
                "canonical": canonical,
                "source": candidates[0],
                "status": "suggested",
                "note": "Coincidencia clara.",
            })
            used_sources.add(candidates[0])
        elif len(candidates) > 1:
            suggestions.append({
                "canonical": canonical,
                "source": None,
                "status": "needs_review",
                "note": ("Varias columnas podrían corresponder: "
                         + ", ".join(candidates)
                         + ". Requiere revisión."),
                "candidates": candidates,
            })
        else:
            note = ("No se encontró una columna equivalente. "
                    "El análisis puede quedar limitado.")
            if canonical in REQUIRED_CANONICAL:
                note = ("No se encontró una columna equivalente. "
                        "Esta columna es necesaria para el análisis. "
                        "Requiere revisión.")
            suggestions.append({
                "canonical": canonical,
                "source": None,
                "status": "needs_review",
                "note": note,
            })

    needs_confirmation = any(
        s["status"] == "needs_review" for s in suggestions
    )
    by_canonical = {s["canonical"]: s for s in suggestions}
    warnings: List[str] = []
    if not (by_canonical.get("Invoice") or {}).get("source"):
        warnings.append(
            "Tu archivo no tiene una columna de factura (Invoice) asignada: "
            "no habrá identificador de transacción y el análisis de "
            "cancelaciones quedará limitado. Puedes continuar si tu reporte "
            "legítimamente no usa facturas."
        )
    return {
        "suggestions": suggestions,
        "needs_confirmation": needs_confirmation,
        "canonical_columns": CANONICAL_COLUMNS,
        "required": REQUIRED_CANONICAL,
        "warnings": warnings,
        # Columnas originales del archivo, en orden: el selector del
        # frontend las muestra TODAS (incluso sin sugerencia) para
        # asignación manual.
        "columns": list(columns),
    }


def confirm_mapping(columns: List[str],
                    user_mapping: Dict[str, Optional[str]]) -> Dict[str, Any]:
    """Valida el mapeo confirmado por el usuario.

    `user_mapping`: {canonical: source_col | None}.
    Devuelve {"ok", "mapping", "errors", "warnings"} con lenguaje empresarial.
    """
    errors: List[str] = []
    warnings: List[str] = []
    mapping: Dict[str, str] = {}
    seen_sources = set()

    for canonical in CANONICAL_COLUMNS:
        src = (user_mapping or {}).get(canonical)
        if not src:
            continue
        if src not in columns:
            errors.append(
                f"La columna '{src}' no existe en el archivo."
            )
            continue
        if src in seen_sources:
            errors.append(
                f"La columna '{src}' está asignada a más de un campo."
            )
            continue
        seen_sources.add(src)
        mapping[canonical] = src

    for req in REQUIRED_CANONICAL:
        if req not in mapping:
            errors.append(
                f"Falta asignar '{req}'. Es necesaria para analizar tus datos."
            )

    return {
        "ok": not errors,
        "mapping": mapping,
        "errors": errors,
        "warnings": warnings,
    }
