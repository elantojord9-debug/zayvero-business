"""
ZAYVERO BUSINESS — FASE 1B.

`quality_score`: Data Quality Score (0–100) con explicación.

El score parte de 100 y resta penalizaciones (con tope por componente).
Cada penalización se calcula sobre el % de filas afectadas y queda
registrada en `deductions` con su explicación en lenguaje claro.

COMPONENTES (pesos y topes documentados):
    - missing_critical:  filas con algún campo crítico nulo, SOLO en campos
                         cuya columna existe en el archivo
                         (Transaction, Date, Quantity, UnitPrice):
                         pct × 3, tope 30
    - structural_gaps:   campos críticos cuya columna nunca existió en el
                         archivo (ausencia estructural, p. ej. reportes sin
                         facturas). NO es corrupción de datos: penalización
                         fija 8 pts por campo, tope 20, con advertencia visible.
    - duplicates:        filas duplicadas (del reporte de calidad de FASE 1A
                         si está disponible; si no, se recalcula):
                         pct × 2, tope 20
    - invalid_dates:     Date = NaT con fecha original no nula (se omite si
                         Date es estructuralmente ausente): pct × 2, tope 15
    - invalid_quantities: Quantity = NaN (no numérico en origen; se omite si
                         Quantity es estructuralmente ausente): pct × 2, tope 15
    - invalid_prices:    UnitPrice = NaN (no numérico en origen; se omite si
                         UnitPrice es estructuralmente ausente): pct × 2, tope 15
    - negative_quantities: Quantity < 0: pct × 1, tope 10
    - non_positive_prices: UnitPrice ≤ 0 en VENTAS (los gastos identificados
                         por Tipo=gasto no se penalizan; sin tipo identificable
                         se es conservador y sí cuentan): pct × 1, tope 10
    - unmapped_columns:  columnas sin mapear (del audit): 2 pts c/u, tope 10

La ausencia estructural se detecta por las columnas orig_<canónico>:
normalize() solo las crea cuando la columna existía en el archivo.
Sin ninguna columna orig_ (formato antiguo), se conserva el
comportamiento anterior a este cambio.

El score NUNCA esconde problemas: `explanation` enumera cada deducción
con conteos reales. Un dataset vacío recibe score 0 con explicación.
"""

from __future__ import annotations

import pandas as pd

from profiling.trace import make_trace, to_jsonable

CRITICAL_FIELDS = ["Transaction", "Date", "Quantity", "UnitPrice"]

# Campo crítico -> columna orig_ que demuestra existencia estructural.
# normalize() solo crea orig_<canónico> si la columna existía en el archivo.
CRITICAL_ORIGIN = {
    "Transaction": "orig_Invoice",
    "Date": "orig_InvoiceDate",
    "Quantity": "orig_Quantity",
    "UnitPrice": "orig_UnitPrice",
}

# Penalización fija por campo crítico estructuralmente ausente (no escalada
# por filas: la ausencia de la columna no es corrupción de datos).
STRUCTURAL_GAP_POINTS = 8
STRUCTURAL_GAP_CAP = 20


def _identify_expenses(df: pd.DataFrame) -> "pd.Series":
    """Filas identificadas positivamente como gastos.

    Requiere orig_Tipo (conservada por normalize) con valor 'gasto'
    (insensible a mayúsculas y espacios). Sin orig_Tipo, o con valor
    nulo/distinto, la fila NO se considera gasto (tratamiento conservador:
    un registro sin tipo identificable sigue evaluándose como venta).
    """
    import pandas as pd

    if "orig_Tipo" not in df.columns:
        return pd.Series(False, index=df.index)
    tipo = df["orig_Tipo"].astype("string").str.strip().str.lower()
    return (tipo == "gasto").fillna(False)


def _pct(part: int, total: int) -> float:
    return round(part / total * 100, 2) if total else 0.0


def compute_quality_score(
    df: pd.DataFrame,
    audit_record: dict | None = None,
) -> dict:
    n = len(df)
    deductions: list[dict] = []

    def deduct(component: str, points: float, detail: str) -> None:
        points = round(min(points, 100), 2)
        if points > 0:
            deductions.append(
                {"component": component, "points": points, "detail": detail}
            )

    if n == 0:
        return to_jsonable(
            {
                "score": 0,
                "grade": "sin datos",
                "deductions": [
                    {
                        "component": "empty_dataset",
                        "points": 100,
                        "detail": "El dataset no tiene filas: no hay calidad que medir.",
                    }
                ],
                "explanation": "Score 0/100: dataset vacío, sin filas para evaluar.",
                "trace": make_trace(
                    formula="score = 100 − Σ penalizaciones (topes por componente)",
                    filters="todas las filas del Parquet normalizado",
                    rows_considered=0,
                    period="sin datos",
                ),
            }
        )

    # Distingue ausencia estructural (la columna nunca existió en el archivo)
    # de valores faltantes dentro de columnas existentes. Sin ninguna columna
    # orig_ se conserva el comportamiento anterior (compatibilidad).
    has_any_orig = any(o in df.columns for o in CRITICAL_ORIGIN.values())
    if has_any_orig:
        structural_absent = [
            f for f in CRITICAL_FIELDS
            if CRITICAL_ORIGIN[f] not in df.columns
        ]
        present_critical = [
            f for f in CRITICAL_FIELDS if f not in structural_absent
        ]
    else:
        structural_absent = []
        present_critical = list(CRITICAL_FIELDS)

    # 1) Campos críticos nulos (solo en campos estructuralmente presentes)
    missing_mask = pd.Series(False, index=df.index)
    for col in present_critical:
        if col in df.columns:
            missing_mask = missing_mask | df[col].isna()
    n_missing = int(missing_mask.sum())
    deduct(
        "missing_critical",
        min(_pct(n_missing, n) * 3, 30),
        f"{n_missing} filas ({_pct(n_missing, n)}%) con algún campo crítico "
        f"nulo {present_critical}.",
    )

    # 1b) Brechas estructurales: advertencia visible, penalización fija
    # (no escalada por filas: no es corrupción de datos).
    if structural_absent:
        deduct(
            "structural_gaps",
            min(len(structural_absent) * STRUCTURAL_GAP_POINTS,
                STRUCTURAL_GAP_CAP),
            f"{len(structural_absent)} campo(s) crítico(s) sin columna de "
            f"origen en el archivo: {', '.join(structural_absent)}. "
            "Limitación estructural del reporte, no datos corruptos.",
        )

    # 2) Duplicados: del audit de FASE 1A si existe; si no, recálculo
    n_dup = None
    if audit_record:
        qr = (audit_record.get("quality_report") or {})
        # El reporte de FASE 1A guarda duplicados en distintas claves posibles
        for key in ("duplicates", "n_duplicates", "duplicate_rows"):
            if key in qr:
                try:
                    n_dup = int(qr[key])
                    break
                except (TypeError, ValueError):
                    pass
    if n_dup is None:
        subset = [c for c in ("Transaction", "Product", "Quantity", "UnitPrice", "Date")
                  if c in df.columns]
        n_dup = int(df.duplicated(subset=subset).sum()) if subset else 0
    deduct(
        "duplicates",
        min(_pct(n_dup, n) * 2, 20),
        f"{n_dup} filas duplicadas ({_pct(n_dup, n)}%).",
    )

    # 3) Fechas inválidas (NaT con original no nulo; se omite si Date es
    #    estructuralmente ausente: no hay fechas que evaluar)
    if "Date" in df.columns and "Date" not in structural_absent:
        if "orig_InvoiceDate" in df.columns:
            bad_dates = int(
                (df["Date"].isna() & df["orig_InvoiceDate"].notna()).sum()
            )
        else:
            bad_dates = int(df["Date"].isna().sum())
        deduct(
            "invalid_dates",
            min(_pct(bad_dates, n) * 2, 15),
            f"{bad_dates} fechas no parseables ({_pct(bad_dates, n)}%).",
        )

    # 4-5) Cantidades / precios no numéricos (NaN con original no nulo;
    #    se omiten si el campo es estructuralmente ausente)
    for col, orig, comp, crit in (
        ("Quantity", "orig_Quantity", "invalid_quantities", "Quantity"),
        ("UnitPrice", "orig_UnitPrice", "invalid_prices", "UnitPrice"),
    ):
        if col in df.columns and crit not in structural_absent:
            if orig in df.columns:
                bad = int((df[col].isna() & df[orig].notna()).sum())
            else:
                bad = int(df[col].isna().sum())
            deduct(
                comp,
                min(_pct(bad, n) * 2, 15),
                f"{bad} valores no numéricos en {col} ({_pct(bad, n)}%).",
            )

    # 6) Cantidades negativas
    if "Quantity" in df.columns:
        n_neg = int((df["Quantity"] < 0).sum())
        deduct(
            "negative_quantities",
            min(_pct(n_neg, n) * 1, 10),
            f"{n_neg} cantidades negativas ({_pct(n_neg, n)}%); suelen "
            "corresponder a devoluciones/cancelaciones, pero se señalan.",
        )

    # 7) Precios ≤ 0 en VENTAS. Los gastos legítimos (Tipo=gasto) no se
    #    penalizan como ventas con precio incorrecto; los registros sin tipo
    #    identificable se tratan de forma conservadora (sí cuentan).
    if "UnitPrice" in df.columns:
        is_expense = _identify_expenses(df)
        sales_bad = (df["UnitPrice"] <= 0) & ~is_expense
        n_zp = int(sales_bad.sum())
        n_zp_exp = int(((df["UnitPrice"] <= 0) & is_expense).sum())
        detail = f"{n_zp} precios ≤ 0 en ventas ({_pct(n_zp, n)}%)."
        if n_zp_exp:
            detail += (
                f" {n_zp_exp} gasto(s) con precio 0 no penalizados "
                "(importe en Gasto_DOP)."
            )
        deduct(
            "non_positive_prices",
            min(_pct(n_zp, n) * 1, 10),
            detail,
        )

    # 8) Columnas sin mapear (del audit)
    n_unmapped = 0
    if audit_record:
        cr = audit_record.get("column_resolution") or {}
        unmapped = cr.get("unmapped") or []
        n_unmapped = len(unmapped)
    deduct(
        "unmapped_columns",
        min(n_unmapped * 2, 10),
        f"{n_unmapped} columnas sin mapear a canónicos."
        if n_unmapped
        else "Todas las columnas mapeadas.",
    )

    total_ded = round(sum(d["points"] for d in deductions), 2)
    score = round(max(0.0, 100.0 - total_ded), 2)

    if score >= 90:
        grade = "excelente"
    elif score >= 75:
        grade = "buena"
    elif score >= 60:
        grade = "aceptable"
    elif score >= 40:
        grade = "baja"
    else:
        grade = "crítica"

    if deductions:
        parts = "; ".join(
            f"−{d['points']} por {d['component']}: {d['detail']}" for d in deductions
        )
        explanation = f"Score {score}/100 ({grade}): {parts}."
    else:
        explanation = (
            f"Score {score}/100 ({grade}): sin problemas detectados en "
            "los componentes evaluados."
        )

    return to_jsonable(
        {
            "score": score,
            "grade": grade,
            "deductions": deductions,
            "explanation": explanation,
            "components_evaluated": [
                "missing_critical",
                "structural_gaps",
                "duplicates",
                "invalid_dates",
                "invalid_quantities",
                "invalid_prices",
                "negative_quantities",
                "non_positive_prices",
                "unmapped_columns",
            ],
            "trace": make_trace(
                formula="score = 100 − Σ penalizaciones (topes: missing 30, "
                "estructurales 20, duplicados 20, fechas/cantidades/precios "
                "inválidos 15 c/u, negativos 10, precios≤0 10, unmapped 10)",
                filters="todas las filas del Parquet normalizado + reporte "
                "de calidad del audit de FASE 1A",
                rows_considered=n,
                period="n/a (calidad, no temporal)",
                notes="El score describe calidad de datos, no del negocio. "
                "structural_gaps marca campos críticos sin columna de origen "
                "(limitación del reporte, no corrupción). non_positive_prices "
                "solo cuenta ventas (Tipo=gasto excluido; sin tipo, conservador).",
            ),
        }
    )
