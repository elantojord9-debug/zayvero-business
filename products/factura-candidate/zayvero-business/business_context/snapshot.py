"""FASE 5A — business_snapshot: resumen objetivo del estado del negocio.

Solo métricas reales del BusinessDatasetProfile (FASE 1B/1C). Cada métrica
lleva metric_name, value, period, source y trace. No se inventan métricas.
"""

from __future__ import annotations


def _metric(evidence, name, value, period, source, trace):
    ev_id = evidence.add(
        source_module="FASE_1B_1C",
        source_file="data/profiles/demo-retail/online_retail_II_full_profile.json",
        source_record=None,
        field=name,
        value=value,
        period=period,
        trace=trace,
    )
    return {
        "metric_name": name,
        "value": value,
        "period": period,
        "source": source,
        "trace": trace,
        "evidence_id": ev_id,
    }


def build_snapshot(profile: dict, evidence) -> dict:
    period = profile.get("date_range", {})
    period_str = f"{period.get('min_date', '')} → {period.get('max_date', '')}"
    tx = profile.get("transactions", {})
    sales = profile.get("sales", {})
    ent = profile.get("entities", {})
    cust = profile.get("customers", {})
    dq = profile.get("data_quality", {})

    metrics = [
        _metric(evidence, "gross_revenue", sales.get("gross_revenue"), period_str,
                "FASE_1B_1C.sales", sales.get("trace")),
        _metric(evidence, "net_revenue", sales.get("net_revenue"), period_str,
                "FASE_1B_1C.sales", sales.get("trace")),
        _metric(evidence, "cancelled_revenue", sales.get("cancelled_revenue"), period_str,
                "FASE_1B_1C.sales", sales.get("trace")),
        _metric(evidence, "total_rows", tx.get("total_rows"), period_str,
                "FASE_1B_1C.transactions", tx.get("trace")),
        _metric(evidence, "unique_transactions", tx.get("unique_transactions"), period_str,
                "FASE_1B_1C.transactions", tx.get("trace")),
        _metric(evidence, "completed_rows", tx.get("completed_rows"), period_str,
                "FASE_1B_1C.transactions", tx.get("trace")),
        _metric(evidence, "cancelled_rows", tx.get("cancelled_rows"), period_str,
                "FASE_1B_1C.transactions", tx.get("trace")),
        _metric(evidence, "total_customers", cust.get("total_customers"), period_str,
                "FASE_1B_1C.customers", cust.get("trace")),
        _metric(evidence, "active_customers", cust.get("active_customers"), period_str,
                "FASE_1B_1C.customers", cust.get("trace")),
        _metric(evidence, "total_products", profile.get("products", {}).get("total_products"), period_str,
                "FASE_1B_1C.products", profile.get("products", {}).get("trace")),
        _metric(evidence, "total_countries", ent.get("countries"), period_str,
                "FASE_1B_1C.entities", ent.get("trace")),
        _metric(evidence, "units_sold_net", sales.get("units_sold_net"), period_str,
                "FASE_1B_1C.sales", sales.get("trace")),
        _metric(evidence, "data_quality_score", dq.get("score"), period_str,
                "FASE_1B_1C.data_quality", {"formula": "Data Quality Score 0-100 (FASE 1B)"}),
        _metric(evidence, "cancellations_count", profile.get("cancellations", {}).get("count"), period_str,
                "FASE_1B_1C.cancellations", profile.get("cancellations", {}).get("trace")),
    ]

    return {
        "business_period": {
            "start": period.get("min_date"),
            "end": period.get("max_date"),
            "days_with_activity": period.get("days_with_activity"),
        },
        "dataset_label": profile.get("dataset_metadata", {}).get("dataset_label"),
        "metrics": metrics,
    }
