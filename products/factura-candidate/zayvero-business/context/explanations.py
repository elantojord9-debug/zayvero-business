"""
ZAYVERO BUSINESS — FASE 2C: posibles explicaciones.

Genera HIPÓTESIS basadas exclusivamente en patrones observables del
dataset. Cada explicación se etiqueta como POSSIBLE_EXPLANATION y
documenta el patrón que la sustenta (basis).

NUNCA se presenta una hipótesis como hecho. NUNCA se afirma causalidad.
"""

from __future__ import annotations


def _h(text: str, basis: str) -> dict:
    return {"kind": "POSSIBLE_EXPLANATION", "text": text, "basis": basis}


def build_explanations(ctx: dict) -> list[dict]:
    """Construye hipótesis a partir del contexto calculado.

    ctx trae: recurrence, concentration, trend, history_depth,
    finding_type, entity_label, etc.
    """
    out: list[dict] = []
    rec = ctx.get("recurrence", {}).get("recurrence", "unknown")
    conc = ctx.get("concentration", {})
    trend = ctx.get("trend", {}).get("trend", "unknown")
    ftype = ctx.get("finding_type", "")
    depth = ctx.get("history_depth", {})

    # 1. Concentración alta → pedido extraordinario / compra concentrada
    if conc.get("concentrated"):
        out.append(_h(
            "El comportamiento podría estar relacionado con un pedido "
            "extraordinario o una compra concentrada en pocas transacciones.",
            f"patrón observado: el {conc.get('top3_share', 0):.0%} del total "
            f"se concentra en {conc.get('top3_n', 0)} transacción(es)"))

    # 2. Recurrencia
    if rec == "recurrent":
        out.append(_h(
            "El patrón se repite en el historial; podría estar relacionado "
            "con un ciclo comercial, estacionalidad o un comportamiento "
            "habitual en ciertos períodos.",
            f"patrón observado: {ctx['recurrence'].get('n_similar', 0)} "
            "períodos adicionales con magnitud similar"))
    elif rec == "isolated":
        out.append(_h(
            "Se trata de un evento aislado en el historial disponible; "
            "podría corresponder a una operación puntual.",
            "patrón observado: ningún otro período con magnitud similar "
            "en el historial"))
    elif rec == "rarely_recurrent":
        out.append(_h(
            "El evento es poco frecuente pero no único; podría responder a "
            "circunstancias operativas específicas que conviene identificar.",
            f"patrón observado: {ctx['recurrence'].get('n_similar', 0)} "
            "período(s) adicional(es) similares"))

    # 3. Precio: cambio brusco
    if ftype == "PRICE_ANOMALY":
        out.append(_h(
            "El cambio de precio podría estar relacionado con un ajuste "
            "comercial, una condición especial de la operación o un valor "
            "registrado que requiere verificación.",
            "patrón observado: precio fuera del rango habitual del producto"))
    if ftype == "QUANTITY_ANOMALY":
        out.append(_h(
            "La cantidad podría corresponder a un pedido de volumen "
            "extraordinario, una compra de mayorista o un valor que "
            "requiere verificación contra el documento de la operación.",
            "patrón observado: cantidad fuera del rango habitual del producto"))

    # 4. Tendencia posterior
    if trend == "returned_to_normal":
        out.append(_h(
            "El retorno a niveles habituales sugiere un episodio puntual "
            "más que un cambio estructural.",
            "patrón observado: comportamiento posterior similar al previo"))
    elif trend == "sustained_high":
        out.append(_h(
            "El nivel elevado sostenido podría reflejar un cambio en la "
            "demanda, en las condiciones comerciales o en la operación; "
            "conviene monitorear su evolución.",
            "patrón observado: nivel posterior elevado vs. previo"))

    # 5. Cliente concentrado
    rel = ctx.get("related_entities", {})
    tc = rel.get("top_customers", [])
    if tc and len(tc) == 1:
        out.append(_h(
            "El evento se concentra en un único cliente; podría tratarse "
            "de un pedido especial o de una condición particular de ese cliente.",
            f"patrón observado: 1 cliente concentra el evento ({tc[0]['id']})"))

    # 6. Sin evidencia suficiente → honestidad analítica
    if depth.get("evidence_quality") == "LOW":
        out.append({
            "kind": "POSSIBLE_EXPLANATION",
            "text": ("No existe evidencia suficiente para determinar la causa "
                     "del comportamiento observado."),
            "basis": depth.get("note", "historial insuficiente"),
        })

    # Deduplicar por texto
    seen, uniq = set(), []
    for h in out:
        if h["text"] not in seen:
            seen.add(h["text"])
            uniq.append(h)
    return uniq
