"""
ZAYVERO BUSINESS — FASE 2C: recommendation engine.

Genera RECOMENDACIONES DE REVISIÓN basadas exclusivamente en la
evidencia. Nunca ejecuta acciones, nunca envía mensajes, nunca
modifica datos.
"""

from __future__ import annotations


def _r(text: str, basis: str) -> dict:
    return {"kind": "RECOMMENDATION", "text": text, "basis": basis}


def build_recommendations(ctx: dict) -> list[dict]:
    """Recomendaciones de revisión según tipo de hallazgo y contexto."""
    out: list[dict] = []
    ftype = ctx.get("finding_type", "")
    label = ctx.get("entity_label", "la entidad")
    period_txt = ctx.get("period_txt", "el período analizado")
    trx_id = ctx.get("transaction_id")
    rec = ctx.get("recurrence", {}).get("recurrence", "unknown")

    if ftype == "PRODUCT_ANOMALY":
        out.append(_r(
            f"Revisar las facturas o pedidos asociados al producto {label} "
            f"en el período {period_txt} para confirmar si la desviación corresponde a "
            "un evento de ventas extraordinario.",
            "evidencia: desviación de ventas vs. historial del producto"))
        out.append(_r(
            "Confirmar si el volumen corresponde a un pedido extraordinario, "
            "a un cambio operativo o a un evento comercial del período.",
            "evidencia: magnitud y concentración del evento"))
    elif ftype == "PRICE_ANOMALY":
        ref = f" en la transacción {trx_id}" if trx_id else ""
        out.append(_r(
            f"Verificar si el precio registrado{ref} corresponde a la operación "
            "real y compararlo con la lista de precios vigente en esa fecha.",
            "evidencia: precio fuera del rango habitual del producto"))
        out.append(_r(
            "Revisar el documento de la operación para descartar un error de "
            "captura en el precio.",
            "evidencia: desviación extrema de precio"))
    elif ftype == "QUANTITY_ANOMALY":
        ref = f" en la transacción {trx_id}" if trx_id else ""
        out.append(_r(
            f"Confirmar si la cantidad registrada{ref} corresponde a una compra "
            "extraordinaria o a un pedido de volumen especial.",
            "evidencia: cantidad fuera del rango habitual del producto"))
        out.append(_r(
            "Verificar la unidad de medida utilizada en el registro contra el "
            "documento fuente.",
            "evidencia: magnitud atípica de cantidad"))
    elif ftype == "CUSTOMER_ANOMALY":
        clabel = label[8:] if label.lower().startswith("cliente ") else label
        out.append(_r(
            f"Revisar el historial completo del cliente {clabel} para confirmar "
            "si la compra corresponde a su patrón de mayorista o a una "
            "operación puntual.",
            "evidencia: compra muy superior a su historial"))
        out.append(_r(
            "Comparar este comportamiento con pedidos anteriores del cliente "
            "y verificar el documento de la operación.",
            "evidencia: desviación vs. comportamiento histórico del cliente"))
    elif ftype == "TEMPORAL_ANOMALY":
        out.append(_r(
            f"Revisar si existe un evento comercial, operativo o de calendario "
            f"asociado al período {period_txt} que explique el comportamiento.",
            "evidencia: desviación temporal agregada"))
        out.append(_r(
            "Identificar los productos y clientes que más contribuyeron al "
            "período para una revisión focalizada.",
            "evidencia: concentración del período"))

    if rec == "recurrent":
        out.append(_r(
            "Dado que el patrón se repite, revisar si existe una causa "
            "estructural o estacional que deba incorporarse al seguimiento habitual.",
            "evidencia: recurrencia detectada en el historial"))
    if rec == "isolated":
        out.append(_r(
            "Al tratarse de un evento aislado, priorizar la verificación del "
            "documento fuente antes de considerarlo un nuevo patrón.",
            "evidencia: evento sin precedentes similares"))

    # Deduplicar por texto
    seen, uniq = set(), []
    for r in out:
        if r["text"] not in seen:
            seen.add(r["text"])
            uniq.append(r)
    return uniq
