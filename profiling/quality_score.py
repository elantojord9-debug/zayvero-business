"""
ZAYVERO BUSINESS — FASE 1B.

`quality_score`: Data Quality Score (0–100) con explicación.

El score parte de 100 y resta penalizaciones (con tope por componente).
Cada penalización se calcula sobre el % de filas afectadas y queda
registrada en `deductions` con su explicación en lenguaje claro.

COMPONENTES (pesos y topes documentados):
    - missing_critical:  filas con algún campo crítico nulo
                         (Transaction, Date, Quantity, UnitPrice):
                         pct × 3, tope 30
    - duplicates:        filas duplicadas (del reporte de calidad de FASE 1A
                         si está disponible; si no, se recalcula):
                         pct × 2, tope 20
    - invalid_dates:     Date = NaT con fecha original no nula:
                         pct × 2, tope 15
    - invalid_quantities: Quantity = NaN (no numérico en origen):
                         pct × 2, tope 15
    - invalid_prices:    UnitPrice = NaN (no numérico en origen):
                         pct × 2, tope 15
    - negative_quantities: Quantity < 0: pct × 1, tope 10
    - non_positive_prices: UnitPrice ≤ 0: pct × 1, tope 10
    - unmapped_columns:  columnas sin mapear (del audit): 2 pts c/u, tope 10

El score NUNCA esconde problemas: `explanation` enumera cada deducción
con conteos reales. Un dataset vacío recibe score 0 con explicación.
"""

from __future__ import annotations

import pandas as pd

from profiling.trace import make_trace, to_jsonable

CRITICAL_FIELDS = ["Transaction", "Date", "Quantity", "UnitPrice"]


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

    # 1) Campos críticos nulos
    missing_mask = pd.Series(False, index=df.index)
    for col in CRITICAL_FIELDS:
        if col in df.columns:
            missing_mask = missing_mask | df[col].isna()
    n_missing = int(missing_mask.sum())
    deduct(
        "missing_critical",
        min(_pct(n_missing, n) * 3, 30),
        f"{n_missing} filas ({_pct(n_missing, n)}%) con algún campo crítico "
        f"nulo {CRITICAL_FIELDS}.",
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

    # 3) Fechas inválidas (NaT con original no nulo)
    if "Date" in df.columns:
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

    # 4-5) Cantidades / precios no numéricos (NaN con original no nulo)
    for col, orig, comp in (
        ("Quantity", "orig_Quantity", "invalid_quantities"),
        ("UnitPrice", "orig_UnitPrice", "invalid_prices"),
    ):
        if col in df.columns:
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

    # 7) Precios ≤ 0
    if "UnitPrice" in df.columns:
        n_zp = int((df["UnitPrice"] <= 0).sum())
        deduct(
            "non_positive_prices",
            min(_pct(n_zp, n) * 1, 10),
            f"{n_zp} precios ≤ 0 ({_pct(n_zp, n)}%).",
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
                "duplicados 20, fechas/cantidades/precios inválidos 15 c/u, "
                "negativos 10, precios≤0 10, unmapped 10)",
                filters="todas las filas del Parquet normalizado + reporte "
                "de calidad del audit de FASE 1A",
                rows_considered=n,
                period="n/a (calidad, no temporal)",
                notes="El score describe calidad de datos, no del negocio.",
            ),
        }
    )
