"""
ZAYVERO BUSINESS — FASE 2B.

`explain`: títulos, doble explicación (estadística + empresarial) y
recomendación de revisión.

- statistical_explanation: reutiliza la explicación factual de FASE 2A
  (observado vs esperado con números).
- business_explanation: traduce a lenguaje empresarial NEUTRO. Nunca
  "tiene un problema", "fraude", "pérdida", "robo". Siempre enmarca como
  evento inusual que merece revisión para determinar su causa.
- recommended_review: acción de revisión concreta basada solo en evidencia.
  NO ejecuta acciones, no contacta clientes, no modifica precios/inventario.
"""

from __future__ import annotations

from typing import Any

TITLES = {
    "PRODUCT_ANOMALY": "Ventas inusuales del producto {label}",
    "PRICE_ANOMALY": "Precio atípico en {label}",
    "QUANTITY_ANOMALY": "Cantidad extraordinaria en {label}",
    "CUSTOMER_ANOMALY": "Comportamiento de compra inusual: {label}",
    "TEMPORAL_ANOMALY": "Período con ventas fuera de patrón: {label}",
    "SALES_ANOMALY": "Anomalía de ventas agregadas: {label}",
}

BUSINESS_EXPLANATIONS = {
    "PRODUCT_ANOMALY": (
        "Las ventas de este producto en el período analizado se desvían "
        "notablemente de su comportamiento habitual. Esto representa un "
        "evento de ventas inusual que debería revisarse para determinar si "
        "refleja un pico legítimo de demanda, un pedido especial, un cambio "
        "operativo u otro factor."
    ),
    "PRICE_ANOMALY": (
        "Se registraron transacciones de este producto a precios muy "
        "distantes de su rango habitual. Esto representa una desviación de "
        "precio inusual que debería revisarse para confirmar si los precios "
        "son correctos o si requieren corrección en el sistema de origen."
    ),
    "QUANTITY_ANOMALY": (
        "Se registraron transacciones de este producto con cantidades muy "
        "distantes de su rango habitual. Esto representa un volumen inusual "
        "que debería revisarse para determinar si corresponde a un pedido "
        "extraordinario legítimo o a un dato que requiere verificación."
    ),
    "CUSTOMER_ANOMALY": (
        "Este cliente mostró un comportamiento de compra muy distante de su "
        "propio historial. Esto representa una desviación inusual que "
        "debería revisarse para determinar si refleja un pedido legítimo de "
        "gran volumen, un cambio en su patrón de compra u otro factor."
    ),
    "TEMPORAL_ANOMALY": (
        "Las ventas de este período se desvían notablemente del patrón "
        "esperado según la tendencia móvil. Esto representa un período "
        "inusual que debería revisarse para determinar si refleja un evento "
        "real de demanda, un efecto calendario/estacional o un factor "
        "operativo."
    ),
    "SALES_ANOMALY": (
        "Las ventas agregadas muestran una desviación notable respecto al "
        "comportamiento esperado. Debería revisarse para determinar su causa."
    ),
}

REVIEW_TEMPLATES = {
    "PRODUCT_ANOMALY": (
        "Revisar las transacciones del producto {label} entre {start} y "
        "{end} para confirmar si la desviación corresponde a un evento de "
        "ventas extraordinario legítimo."
    ),
    "PRICE_ANOMALY": (
        "Revisar las {n} transacciones con precio atípico del producto "
        "{label} y contrastarlas con el sistema de origen para confirmar "
        "si los precios registrados son correctos."
    ),
    "QUANTITY_ANOMALY": (
        "Revisar las {n} transacciones con cantidad extraordinaria del "
        "producto {label} para confirmar si corresponden a pedidos "
        "legítimos."
    ),
    "CUSTOMER_ANOMALY": (
        "Revisar el historial y las transacciones recientes del {label} "
        "para determinar si el comportamiento observado refleja un pedido "
        "legítimo o un cambio en su patrón de compra."
    ),
    "TEMPORAL_ANOMALY": (
        "Revisar las ventas del {label} frente a la mediana móvil del "
        "período para determinar si el desvío refleja un evento real de "
        "demanda o un factor calendario/operativo."
    ),
    "SALES_ANOMALY": (
        "Revisar las ventas agregadas del período {label} frente al "
        "comportamiento esperado."
    ),
}


def _period_str(period: Any) -> tuple[str, str]:
    if isinstance(period, dict):
        s = str(period.get("start", "?"))[:10]
        e = str(period.get("end", "?"))[:10]
        return s, e
    return "?", "?"


def build_title(finding_type: str, entity_label: str) -> str:
    tmpl = TITLES.get(finding_type, "Hallazgo: {label}")
    return tmpl.format(label=entity_label)


def business_explanation(finding_type: str) -> str:
    return BUSINESS_EXPLANATIONS.get(
        finding_type,
        "Desviación inusual detectada que debería revisarse para "
        "determinar su causa.",
    )


def recommended_review(
    finding_type: str,
    entity_label: str,
    period: Any,
    n_merged: int,
) -> str:
    start, end = _period_str(period)
    tmpl = REVIEW_TEMPLATES.get(
        finding_type, "Revisar la evidencia del hallazgo {label}."
    )
    return tmpl.format(
        label=entity_label, start=start, end=end, n=n_merged
    )
