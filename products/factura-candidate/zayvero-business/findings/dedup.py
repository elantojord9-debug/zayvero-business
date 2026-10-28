"""
ZAYVERO BUSINESS — FASE 2B.

`dedup`: deduplicación/agrupación de anomalías que representan el mismo
evento empresarial.

REGLA: una misma anomalía detectada por varios métodos (o varias
transacciones atípicas del mismo producto) se agrupa en UN solo hallazgo.
No se muestran alertas independientes si representan esencialmente el
mismo evento. Los métodos/tipos contribuyentes quedan guardados.

Estrategia de agrupación (documentada):
    clave = (finding_type, entity_kind, entity_id)
    - price_outlier / quantity_outlier: varias transacciones atípicas del
      mismo producto → 1 hallazgo por producto (n_merged = nº trx).
    - product_sales_change / customer_*: normalmente 1:1, pero si hay
      solape de período para la misma entidad también se agrupan.
    - temporal_*: cada período es único; no se agrupan entre sí.

El hallazgo agrupado conserva como "primaria" la anomalía de mayor
|difference|; la diferencia monetaria total es la SUMA de |diferencias|
(son transacciones distintas, no doble conteo).
"""

from __future__ import annotations

from typing import Any

# Mapeo tipo 2A -> tipo de hallazgo 2B
TYPE_MAP = {
    "product_sales_change": "PRODUCT_ANOMALY",
    "price_outlier": "PRICE_ANOMALY",
    "quantity_outlier": "QUANTITY_ANOMALY",
    "customer_unusual_purchase": "CUSTOMER_ANOMALY",
    "customer_frequency_burst": "CUSTOMER_ANOMALY",
    "temporal_spike": "TEMPORAL_ANOMALY",
    "temporal_drop": "TEMPORAL_ANOMALY",
}
# Reservado en la taxonomía para futuros hallazgos de ventas agregadas.
RESERVED_TYPES = ["SALES_ANOMALY"]

# Tipos donde varias anomalías 2A de la misma entidad = mismo evento.
MERGEABLE_TYPES = {
    "price_outlier",
    "quantity_outlier",
    "product_sales_change",
    "customer_unusual_purchase",
    "customer_frequency_burst",
}


def finding_type_of(anomaly_type: str) -> str:
    return TYPE_MAP.get(anomaly_type, "PRODUCT_ANOMALY")


def _absdiff(a: dict[str, Any]) -> float:
    try:
        return abs(float(a.get("difference") or 0.0))
    except (TypeError, ValueError):
        return 0.0


def dedup_anomalies(
    anomalies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """
    Agrupa anomalías del mismo evento. Devuelve lista de grupos:
    {
      "primary": <anomalía de mayor |difference|>,
      "members": [<anomalías>],
      "n_merged": int,
      "finding_type": str,
      "contributing_detectors": [tipos 2A únicos],
      "contributing_anomaly_ids": [ids],
      "total_difference": float (suma de |diferencias|),
    }
    """
    groups: dict[tuple, list[dict[str, Any]]] = {}
    order: list[tuple] = []
    for a in anomalies:
        t2a = a.get("type", "")
        ent = a.get("entity") or {}
        key = (
            finding_type_of(t2a),
            str(ent.get("kind", "")),
            str(ent.get("id", "")),
        )
        # Solo se agrupan los tipos mergeables; el resto va 1:1.
        if t2a not in MERGEABLE_TYPES:
            key = (key[0], key[1], key[2], a.get("anomaly_id", id(a)))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(a)

    result: list[dict[str, Any]] = []
    for key in order:
        members = groups[key]
        members.sort(key=_absdiff, reverse=True)
        primary = members[0]
        detectors: list[str] = []
        for m in members:
            t = m.get("type", "")
            if t not in detectors:
                detectors.append(t)
        total_diff = sum(_absdiff(m) for m in members)
        result.append(
            {
                "primary": primary,
                "members": members,
                "n_merged": len(members),
                "finding_type": key[0],
                "contributing_detectors": detectors,
                "contributing_anomaly_ids": [
                    m.get("anomaly_id") for m in members
                ],
                "total_difference": total_diff,
            }
        )
    return result
