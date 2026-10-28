"""LOTE 6 — Customer Intelligence y señales de churn.

Todo verificable desde el historial real de compras:
- frecuencia: compras por mes (promedio en ventana).
- ticket_promedio: LTV / nº compras.
- recencia: días desde la última compra.
- ltv: suma histórica (ya la calcula CRM.ficha).
- Señales de churn (NO probabilidades inventadas):
  - en_riesgo: recencia > 2x su intervalo promedio entre compras.
  - inactivo: sin compras en >180 días (coherente con segmentación).
  - Cada señal cita los números que la sostienen.
"""
from __future__ import annotations

import sys
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402


def _parse(f: str):
    return datetime.strptime(f[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)


@dataclass
class PerfilCliente:
    cliente_id: str
    total_compras: int
    frecuencia_mensual: str
    ticket_promedio: str
    recencia_dias: int | None
    ltv: str
    moneda: str

    def to_dict(self):
        return {"cliente_id": self.cliente_id,
                "total_compras": self.total_compras,
                "frecuencia_mensual": self.frecuencia_mensual,
                "ticket_promedio": self.ticket_promedio,
                "recencia_dias": self.recencia_dias, "ltv": self.ltv,
                "moneda": self.moneda}


@dataclass
class SenalChurn:
    cliente_id: str
    nivel: str  # en_riesgo | inactivo
    detalle: str

    def to_dict(self):
        return {"cliente_id": self.cliente_id, "nivel": self.nivel,
                "detalle": self.detalle}


def perfil_cliente(crm, cliente_id: str, hoy: str | None = None,
                   ventana_dias: int = 365) -> PerfilCliente:
    ahora = _parse(hoy) if hoy else datetime.now(timezone.utc)
    hist = sorted((f, m, mon) for f, m, mon in crm._historial.get(cliente_id, [])
                  if f and (ahora - _parse(f)).days <= ventana_dias)
    n = len(hist)
    if n == 0:
        return PerfilCliente(cliente_id, 0, "0", "0", None, "0", "")
    por_moneda: dict[str, Decimal] = {}
    for _, m, mon in hist:
        por_moneda[mon] = por_moneda.get(mon, Decimal("0")) + m
    mon = max(por_moneda, key=lambda k: por_moneda[k])
    total = por_moneda[mon]
    meses = ventana_dias / 30
    recencia = (ahora - _parse(hist[-1][0])).days
    return PerfilCliente(
        cliente_id=cliente_id, total_compras=n,
        frecuencia_mensual=str(round(n / meses, 2)),
        ticket_promedio=str(round(total / n, 2)),
        recencia_dias=recencia, ltv=str(total), moneda=mon)


def senales_churn(crm, hoy: str | None = None) -> list[SenalChurn]:
    """Señales basadas en historial real. Sin probabilidades inventadas."""
    ahora = _parse(hoy) if hoy else datetime.now(timezone.utc)
    out = []
    for cid, hist in crm._historial.items():
        hs = sorted(h for h in hist if h and h[0])
        if not hs:
            continue
        recencia = (ahora - _parse(hs[-1][0])).days
        if recencia > 180:
            out.append(SenalChurn(
                cid, "inactivo",
                f"sin compras en {recencia} días (última: {hs[-1][0][:10]})"))
            continue
        if len(hs) >= 3:
            intervalos = [(_parse(hs[i + 1][0]) - _parse(hs[i][0])).days
                          for i in range(len(hs) - 1)]
            prom = sum(intervalos) / len(intervalos)
            if prom > 0 and recencia > 2 * prom:
                out.append(SenalChurn(
                    cid, "en_riesgo",
                    f"recencia {recencia} días supera 2x su intervalo "
                    f"promedio ({round(prom, 1)} días)"))
    return out


# ---------------------------------------------------------------------------
# BLOQUE CRM/VENTAS (2026-10-13) — Customer Intelligence vía API.
#
# Todo sale del historial real del sistema (ventas registradas). Ningún
# número se inventa: si no hay datos suficientes, el resultado dice
# "datos_insuficientes" en vez de estimar.
# ---------------------------------------------------------------------------

# Umbrales del predictor de churn (documentados; ver docs/bloque-crm.md).
UMBRALES_CHURN = {
    "min_compras": 2,            # menos -> "datos_insuficientes"
    "inactivo_dias": 180,        # recencia mayor -> probabilidad 100
    # puntos por ratio recencia / intervalo_promedio:
    "ratio_hasta_1": 0,          # compra dentro de su ritmo
    "ratio_hasta_2": 30,         # entre 1x y 2x su intervalo
    "ratio_hasta_3": 55,         # entre 2x y 3x su intervalo
    "ratio_mayor_3": 75,         # más de 3x su intervalo
    "ventana_frecuencia_dias": 90,
    "caida_sin_compras_pts": 40,  # 0 compras en 90d habiendo comprado antes
    "caida_mitad_pts": 25,        # compras <= mitad del trimestre anterior
    "nivel_alto": 70,
    "nivel_medio": 40,
}


def _historial_ordenado(crm, cliente_id):
    return sorted((h for h in crm._historial.get(cliente_id, []) if h and h[0]),
                  key=lambda h: h[0])


def probabilidad_churn(crm, cliente_id: str, hoy: str | None = None) -> dict:
    """Probabilidad heurística de abandono (0-100) con factores explicados.

    Fórmula documentada (ver docs/bloque-crm.md):
      prob = min(100, F1 + F2); recencia > 180 días -> 100 directo.
    F1: recencia vs intervalo promedio entre compras (tabla UMBRALES_CHURN).
    F2: caída de frecuencia (compras últimos 90 días vs 90-180 días).
    Niveles: >=70 alto, 40-69 medio, <40 bajo.
    Con menos de `min_compras` compras -> estado "datos_insuficientes".
    """
    ahora = _parse(hoy) if hoy else datetime.now(timezone.utc)
    hs = _historial_ordenado(crm, cliente_id)
    n = len(hs)
    if n == 0:
        return {"cliente_id": cliente_id, "estado": "datos_insuficientes",
                "probabilidad": None, "nivel": None,
                "motivo": "el cliente no tiene compras registradas",
                "factores": [], "umbrales": dict(UMBRALES_CHURN)}
    recencia = (ahora - _parse(hs[-1][0])).days
    # Inactividad extrema: evidencia suficiente por sí sola, aun con una
    # sola compra (coherente con senales_churn nivel "inactivo").
    if recencia > UMBRALES_CHURN["inactivo_dias"]:
        return {"cliente_id": cliente_id, "estado": "ok",
                "probabilidad": 100, "nivel": "alto",
                "factores": [{
                    "codigo": "inactividad",
                    "descripcion": (f"sin compras en {recencia} días "
                                    f"(última: {hs[-1][0][:10]})"),
                    "puntos": 100}],
                "umbrales": dict(UMBRALES_CHURN)}
    if n < UMBRALES_CHURN["min_compras"]:
        return {"cliente_id": cliente_id, "estado": "datos_insuficientes",
                "probabilidad": None, "nivel": None,
                "motivo": (f"se requieren al menos "
                           f"{UMBRALES_CHURN['min_compras']} compras "
                           f"registradas; el cliente tiene {n}"),
                "factores": [], "umbrales": dict(UMBRALES_CHURN)}
    intervalos = [(_parse(hs[i + 1][0]) - _parse(hs[i][0])).days
                  for i in range(n - 1)]
    prom = sum(intervalos) / len(intervalos) if intervalos else 0
    factores = []

    if prom > 0:
        ratio = recencia / prom
        if ratio <= 1:
            p1 = UMBRALES_CHURN["ratio_hasta_1"]
        elif ratio <= 2:
            p1 = UMBRALES_CHURN["ratio_hasta_2"]
        elif ratio <= 3:
            p1 = UMBRALES_CHURN["ratio_hasta_3"]
        else:
            p1 = UMBRALES_CHURN["ratio_mayor_3"]
        factores.append({
            "codigo": "recencia_vs_intervalo",
            "descripcion": (f"recencia {recencia} días vs intervalo promedio "
                            f"{round(prom, 1)} días (ratio {round(ratio, 2)})"),
            "puntos": p1})
    else:  # todas las compras el mismo día: intervalo 0
        p1 = UMBRALES_CHURN["ratio_mayor_3"]
        factores.append({
            "codigo": "recencia_vs_intervalo",
            "descripcion": (f"recencia {recencia} días; compras "
                            "concentradas el mismo día (intervalo 0)"),
            "puntos": p1})

    v = UMBRALES_CHURN["ventana_frecuencia_dias"]
    c_rec = sum(1 for h in hs if (ahora - _parse(h[0])).days <= v)
    c_ant = sum(1 for h in hs
                if v < (ahora - _parse(h[0])).days <= 2 * v)
    if c_ant > 0 and c_rec == 0:
        p2 = UMBRALES_CHURN["caida_sin_compras_pts"]
        desc = (f"0 compras en los últimos {v} días vs {c_ant} en los "
                f"{v} anteriores")
    elif c_ant > 0 and c_rec * 2 <= c_ant:
        p2 = UMBRALES_CHURN["caida_mitad_pts"]
        desc = (f"{c_rec} compras en los últimos {v} días vs {c_ant} en "
                f"los {v} anteriores (cayó a la mitad o más)")
    else:
        p2, desc = 0, (f"frecuencia estable: {c_rec} compras en los últimos "
                       f"{v} días vs {c_ant} en los {v} anteriores")
    factores.append({"codigo": "caida_frecuencia", "descripcion": desc,
                     "puntos": p2})

    prob = min(100, p1 + p2)
    nivel = ("alto" if prob >= UMBRALES_CHURN["nivel_alto"]
             else "medio" if prob >= UMBRALES_CHURN["nivel_medio"]
             else "bajo")
    return {"cliente_id": cliente_id, "estado": "ok",
            "probabilidad": prob, "nivel": nivel,
            "factores": factores, "umbrales": dict(UMBRALES_CHURN)}


# Fórmula del LTV (documentada; ver docs/bloque-crm.md):
#   LTV = histórico + (ticket_promedio × frecuencia_mensual × 12)
# ticket_promedio = histórico / nº compras
# frecuencia_mensual = nº compras / periodo_meses
# periodo_meses = max(días entre primera y última compra / 30, 1)
FORMULA_LTV = ("LTV = histórico + "
               "(ticket_promedio × frecuencia_mensual × 12)")


def ltv_cliente(crm, cliente_id: str, hoy: str | None = None) -> dict:
    """Valor de vida del cliente con fórmula explícita.

    El componente histórico siempre se calcula. La proyección a 12 meses
    requiere al menos 2 compras; si no, estado "datos_insuficientes"
    para la proyección (el histórico se devuelve igual).
    """
    hs = _historial_ordenado(crm, cliente_id)
    n = len(hs)
    por_moneda: dict[str, Decimal] = {}
    for _, m, mon in hs:
        por_moneda[mon] = por_moneda.get(mon, Decimal("0")) + m
    mon = max(por_moneda, key=lambda k: por_moneda[k]) if por_moneda else ""
    historico = por_moneda.get(mon, Decimal("0"))
    base = {"cliente_id": cliente_id, "moneda": mon,
            "historico": str(historico), "total_compras": n,
            "formula": FORMULA_LTV}
    if n < 2:
        base.update({"estado": "datos_insuficientes",
                     "motivo": (f"la proyección requiere al menos 2 compras; "
                                f"el cliente tiene {n}"),
                     "ticket_promedio": None, "periodo_meses": None,
                     "frecuencia_mensual": None, "proyectado_12m": None,
                     "ltv_total": str(historico)})
        return base
    ticket = float(historico) / n
    span_dias = (_parse(hs[-1][0]) - _parse(hs[0][0])).days
    periodo = max(span_dias / 30, 1)
    frecuencia = n / periodo
    proyectado = ticket * frecuencia * 12
    base.update({"estado": "ok",
                 "ticket_promedio": str(round(ticket, 2)),
                 "periodo_meses": str(round(periodo, 2)),
                 "frecuencia_mensual": str(round(frecuencia, 2)),
                 "proyectado_12m": str(round(proyectado, 2)),
                 "ltv_total": str(round(float(historico) + proyectado, 2))})
    return base


# Segmentación RFM: criterios visibles (ver docs/bloque-crm.md).
# R = días desde la última compra (menor es mejor)
# F = nº compras en la ventana (mayor es mejor)
# M = ingresos en la ventana, moneda dominante (mayor es mejor)
# Puntaje 1-5 por quintiles de la población de la empresa en la ventana.
# Con menos de 3 clientes con compras -> "datos_insuficientes".
MIN_POBLACION_RFM = 3
CRITERIOS_RFM = {
    "nuevo": "1 sola compra registrada (no se puntúa RFM)",
    "campeones": "R>=4, F>=4, M>=4",
    "leales": "F>=4",
    "en_riesgo": "R<=2, F>=3",
    "prometedores": "R>=4, F<=2",
    "perdidos": "R<=2, F<=2",
    "potencial": "resto de combinaciones",
}


def _quintiles(valores):
    """valores: [(cliente_id, valor)] ordenados de mejor a peor.

    Devuelve {cliente_id: puntaje 1-5}, 5 = mejor. Determinista
    (empates por cliente_id).
    """
    import math
    n = len(valores)
    return {cid: 6 - math.ceil(5 * (i + 1) / n)
            for i, (cid, _) in enumerate(valores)}


def segmento_rfm(crm, hoy: str | None = None,
                 ventana_dias: int = 365) -> dict:
    """Segmentación RFM de TODOS los clientes de la empresa.

    Devuelve por cliente: R/F/M crudos, puntajes 1-5 y segmento según
    CRITERIOS_RFM. Sin población suficiente -> "datos_insuficientes".
    """
    ahora = _parse(hoy) if hoy else datetime.now(timezone.utc)
    filas = []
    for cid in crm._clientes:
        hs = [h for h in _historial_ordenado(crm, cid)
              if (ahora - _parse(h[0])).days <= ventana_dias]
        if not hs:
            continue
        por_moneda: dict[str, Decimal] = {}
        for _, m, mon in hs:
            por_moneda[mon] = por_moneda.get(mon, Decimal("0")) + m
        mon = max(por_moneda, key=lambda k: por_moneda[k])
        recencia = (ahora - _parse(hs[-1][0])).days
        filas.append({"cliente_id": cid, "r_dias": recencia,
                      "f_compras": len(hs), "m_monto": por_moneda[mon],
                      "moneda": mon,
                      "total_compras": len(crm._historial.get(cid, []))})
    poblacion = len(filas)
    clientes = []
    if poblacion >= MIN_POBLACION_RFM:
        # Orden determinista: mejor primero; empates por cliente_id
        # (sort estable: primero por id, luego por métrica).
        def _ordena(filas_, metrica, mejor_primero):
            base = sorted(filas_, key=lambda f: f["cliente_id"])
            return sorted(base, key=lambda f: f[metrica],
                          reverse=not mejor_primero)
        r_ord = [(f["cliente_id"], f["r_dias"])
                 for f in _ordena(filas, "r_dias", mejor_primero=True)]
        f_ord = [(f["cliente_id"], f["f_compras"])
                 for f in _ordena(filas, "f_compras", mejor_primero=False)]
        m_ord = [(f["cliente_id"], f["m_monto"])
                 for f in _ordena(filas, "m_monto", mejor_primero=False)]
        sr, sf, sm = (_quintiles(r_ord), _quintiles(f_ord),
                      _quintiles(m_ord))
        for f in filas:
            cid = f["cliente_id"]
            r, fs, ms = sr[cid], sf[cid], sm[cid]
            if f["total_compras"] == 1:
                seg = "nuevo"
            elif r >= 4 and fs >= 4 and ms >= 4:
                seg = "campeones"
            elif fs >= 4:
                seg = "leales"
            elif r <= 2 and fs >= 3:
                seg = "en_riesgo"
            elif r >= 4 and fs <= 2:
                seg = "prometedores"
            elif r <= 2 and fs <= 2:
                seg = "perdidos"
            else:
                seg = "potencial"
            clientes.append({"cliente_id": cid, "r_dias": f["r_dias"],
                             "f_compras": f["f_compras"],
                             "m_monto": str(f["m_monto"]),
                             "moneda": f["moneda"],
                             "r_score": r, "f_score": fs, "m_score": ms,
                             "segmento": seg, "estado": "ok"})
    else:
        for f in filas:
            clientes.append({"cliente_id": f["cliente_id"],
                             "r_dias": f["r_dias"],
                             "f_compras": f["f_compras"],
                             "m_monto": str(f["m_monto"]),
                             "moneda": f["moneda"],
                             "r_score": None, "f_score": None,
                             "m_score": None, "segmento": None,
                             "estado": "datos_insuficientes"})
    return {"estado": ("ok" if poblacion >= MIN_POBLACION_RFM
                       else "datos_insuficientes"),
            "poblacion": poblacion,
            "min_poblacion": MIN_POBLACION_RFM,
            "motivo": ("" if poblacion >= MIN_POBLACION_RFM
                       else f"se requieren al menos {MIN_POBLACION_RFM} "
                            f"clientes con compras en la ventana; hay "
                            f"{poblacion}"),
            "criterios": dict(CRITERIOS_RFM),
            "ventana_dias": ventana_dias,
            "clientes": sorted(clientes, key=lambda c: c["cliente_id"])}


def productos_preferidos(crm, cliente_id: str, tope: int = 5) -> list:
    """Top productos por cantidad comprada, desde el detalle de líneas."""
    crm._exigir(cliente_id)
    ag: dict[str, dict] = {}
    for det in getattr(crm, "_lineas_compras", {}).get(cliente_id, []):
        for lin in det:
            pid = lin.get("producto_id", "")
            cant = _dec(str(lin.get("cantidad", "0")))
            pu = _dec(str(lin.get("precio_unitario", "0")))
            a = ag.setdefault(pid, {"cantidad": Decimal("0"),
                                   "monto": Decimal("0")})
            a["cantidad"] += cant
            a["monto"] += cant * pu
    top = sorted(ag.items(), key=lambda kv: (-kv[1]["cantidad"], kv[0]))
    return [{"producto_id": pid, "cantidad": str(v["cantidad"]),
             "monto": str(v["monto"])} for pid, v in top[:tope]]


def ficha360(crm, cliente_id: str, hoy: str | None = None) -> dict:
    """Ficha 360 del cliente: todo sale de los datos del sistema.

    - ficha base (segmentación simple, LTV histórico)
    - perfil (frecuencia, ticket, recencia)
    - productos preferidos (detalle de líneas de sus compras)
    - churn (probabilidad heurística explicada)
    - ltv (fórmula explícita)
    - rfm (puntajes y segmento de la empresa)
    """
    ficha = crm.ficha(cliente_id, hoy).to_dict()
    perfil = perfil_cliente(crm, cliente_id, hoy).to_dict()
    rfm = segmento_rfm(crm, hoy)
    rfm_cli = next((c for c in rfm["clientes"]
                    if c["cliente_id"] == cliente_id), None)
    return {"cliente_id": cliente_id, "ficha": ficha, "perfil": perfil,
            "productos_preferidos": productos_preferidos(crm, cliente_id),
            "churn": probabilidad_churn(crm, cliente_id, hoy),
            "ltv": ltv_cliente(crm, cliente_id, hoy),
            "rfm": rfm_cli}
