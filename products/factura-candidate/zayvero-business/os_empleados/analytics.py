"""BLOQUE EMPLEADOS (2026-10-13) — Employee Analytics.

Indicadores operativos por empleado calculados EXCLUSIVAMENTE desde
registros verificables del sistema:

- ventas registradas ......... eventos `os_eventos` tipo='venta'
                                (actor = usuario vinculado al empleado)
- recepciones procesadas ...... eventos `os_eventos` tipo='recepcion'
- tareas completadas .......... tabla `os_tareas` (propia del bloque)
- puntualidad en promesas ..... tabla `os_promesas_seguimiento`
                                (propia del bloque)

REGLA ÉTICA DURA — estas métricas son instrumentos de GESTIÓN
OPERATIVA. NUNCA constituyen prueba de mala conducta ni autorizan
acusaciones automáticas contra trabajadores: toda lectura requiere
revisión humana con contexto (carga de trabajo, rol, temporada,
calidad de los datos fuente). Sin datos suficientes, el reporte dice
"datos_insuficientes" en vez de inventar un juicio. Esta nota viaja
en CADA reporte (`nota_etica`).

FÓRMULAS (explícitas; también documentadas en
`docs/bloque-empleados.md`):

- universo_tareas   = tareas del responsable con vencimiento <= hoy
                      (las que ya debieron cumplirse).
- tasa_tareas       = tareas completadas con completada_en <= vencimiento
                      / universo_tareas. None si universo = 0.
- universo_promesas = promesas con fecha_prometida <= hoy.
- tasa_promesas     = promesas cumplidas con cumplida_en <=
                      fecha_prometida / universo_promesas.
                      None si universo = 0.
- productividad     = promedio ponderado (pesos iguales) de las tasas
                      con muestra suficiente (universo >= muestra_minima,
                      default 3). Si ninguna tasa tiene muestra suficiente
                      → "datos_insuficientes". La fórmula usada queda
                      registrada en el campo `formula`.
- ventas/recepciones se reportan como actividad cruda (conteos y
  montos); NO entran al compuesto porque no tienen "esperado" contra
  el cual normalizar sin inventar un estándar.

Sin I/O directo: recibe el `almacen` (lecturas) y un `lector` de
eventos (ver `LectorEventosPG` / `LectorEventosMem`).
"""
from __future__ import annotations

from decimal import Decimal

from .modelo import hoy_ymd
from .tareas import fue_a_tiempo

NOTA_ETICA = (
    "Métricas de GESTIÓN OPERATIVA calculadas desde registros "
    "verificables del sistema. NO constituyen prueba de mala conducta "
    "ni autorizan acusaciones automáticas contra trabajadores: toda "
    "lectura requiere revisión humana con contexto (rol, carga, "
    "temporada, calidad de los datos fuente). "
    "Sin datos suficientes el reporte indica 'datos_insuficientes'."
)

MUESTRA_MINIMA_DEFAULT = 3
ORIGEN_DATOS = "sintetico_demo"


# --- Lectores de eventos ----------------------------------------------------------
class LectorEventosPG:
    """Lee os_eventos por actor y rango de fechas (solo lectura)."""

    def __init__(self, conn, company_id: str):
        self.conn = conn
        self.company_id = company_id

    def eventos(self, tipo: str, actor: str, desde: str | None,
                hasta: str | None) -> list[dict]:
        sql = ("SELECT fecha, ref_id, detalle FROM os_eventos"
               " WHERE company_id=%s AND tipo=%s AND actor=%s")
        params: list = [self.company_id, tipo, actor]
        if desde:
            sql += " AND fecha >= %s::date"
            params.append(desde)
        if hasta:
            sql += " AND fecha < (%s::date + INTERVAL '1 day')"
            params.append(hasta)
        sql += " ORDER BY fecha ASC"
        cur = self.conn.cursor()
        cur.execute(sql, params)
        out = []
        for fecha, ref_id, detalle in cur.fetchall():
            out.append({
                "fecha": fecha.isoformat() if hasattr(fecha, "isoformat")
                else str(fecha),
                "ref_id": ref_id,
                "detalle": detalle if isinstance(detalle, dict) else {},
            })
        return out


class LectorEventosMem:
    """Lee la lista de eventos del AlmacenMem (mismo shape)."""

    def __init__(self, almacen):
        self.almacen = almacen

    def eventos(self, tipo: str, actor: str, desde: str | None,
                hasta: str | None) -> list[dict]:
        out = []
        for ev in self.almacen.eventos:
            if ev.get("tipo") != tipo or ev.get("actor") != actor:
                continue
            f = (ev.get("fecha") or "")[:10]
            if desde and f < desde:
                continue
            if hasta and f > hasta:
                continue
            out.append({"fecha": ev.get("fecha"), "ref_id": ev.get("ref_id"),
                        "detalle": ev.get("detalle") or {}})
        return sorted(out, key=lambda e: e["fecha"] or "")


# --- Agregados ----------------------------------------------------------------------
def _agregar_ventas(eventos: list[dict]) -> dict:
    por_moneda: dict[str, Decimal] = {}
    for ev in eventos:
        det = ev.get("detalle") or {}
        mon = str(det.get("moneda") or "DOP")
        try:
            tot = Decimal(str(det.get("total") or 0))
        except Exception:
            tot = Decimal("0")
        por_moneda[mon] = por_moneda.get(mon, Decimal("0")) + tot
    return {"n_ventas": len(eventos),
            "monto_por_moneda": {m: str(v)
                                 for m, v in sorted(por_moneda.items())}}


def _agregar_recepciones(eventos: list[dict]) -> dict:
    lineas = 0
    unidades = Decimal("0")
    for ev in eventos:
        for lin in (ev.get("detalle") or {}).get("lineas") or []:
            lineas += 1
            try:
                unidades += abs(Decimal(str(lin.get("cantidad") or 0)))
            except Exception:
                pass
    return {"n_recepciones": len(eventos),
            "n_lineas": lineas,
            "unidades_totales": str(unidades)}


def _tasa_tareas(tareas: list[dict], hoy: str,
                 desde: str | None) -> dict:
    universo = [t for t in tareas
                if t["vencimiento"][:10] <= hoy
                and (not desde or t["vencimiento"][:10] >= desde)]
    a_tiempo = sum(1 for t in universo if fue_a_tiempo(t) is True)
    n = len(universo)
    return {
        "universo": n,
        "completadas_a_tiempo": a_tiempo,
        "tasa": round(a_tiempo / n, 4) if n else None,
        "definicion": ("tareas del responsable con vencimiento <= hoy; "
                       "a_tiempo = completada_en <= vencimiento"),
    }


def _tasa_promesas(promesas: list[dict], hoy: str,
                   desde: str | None) -> dict:
    universo = [p for p in promesas
                if p["fecha_prometida"][:10] <= hoy
                and (not desde or p["fecha_prometida"][:10] >= desde)]
    a_tiempo = sum(
        1 for p in universo
        if p.get("cumplida") is True and (p.get("cumplida_en") or "")[:10]
        <= p["fecha_prometida"][:10])
    n = len(universo)
    return {
        "universo": n,
        "cumplidas_a_tiempo": a_tiempo,
        "tasa": round(a_tiempo / n, 4) if n else None,
        "definicion": ("promesas con fecha_prometida <= hoy; "
                       "a_tiempo = cumplida_en <= fecha_prometida"),
    }


# --- API principal --------------------------------------------------------------------
def indicadores_empleado(almacen, lector, empleado_id: str,
                         desde: str | None = None,
                         hasta: str | None = None,
                         hoy: str | None = None,
                         muestra_minima: int = MUESTRA_MINIMA_DEFAULT
                         ) -> dict:
    """Indicadores operativos de UN empleado. Solo lectura."""
    hoy = hoy_ymd(hoy)
    emp = almacen.obtener_empleado(empleado_id)
    if not emp:
        raise ValueError(f"empleado inexistente: {empleado_id}")

    advertencias: list[str] = []
    usuario_id = emp.get("usuario_id") or ""
    if usuario_id:
        ev_ventas = lector.eventos("venta", usuario_id, desde, hasta)
        ev_recep = lector.eventos("recepcion", usuario_id, desde, hasta)
    else:
        ev_ventas, ev_recep = [], []
        advertencias.append(
            "empleado sin usuario vinculado: ventas y recepciones no "
            "son atribuibles (no se inventa atribución)")

    ventas = _agregar_ventas(ev_ventas)
    recepciones = _agregar_recepciones(ev_recep)
    tareas = almacen.listar_tareas(responsable=empleado_id)
    promesas = almacen.listar_promesas(empleado_id=empleado_id)

    comp_tareas = _tasa_tareas(tareas, hoy, desde)
    comp_prom = _tasa_promesas(promesas, hoy, desde)
    comp_tareas["muestra_suficiente"] = (
        comp_tareas["universo"] >= muestra_minima)
    comp_prom["muestra_suficiente"] = (
        comp_prom["universo"] >= muestra_minima)

    # Compuesto: promedio ponderado (pesos iguales) de tasas con muestra
    # suficiente. Explícito en `formula`.
    partes = []
    if comp_tareas["muestra_suficiente"] and comp_tareas["tasa"] is not None:
        partes.append(("tasa_tareas_a_tiempo", comp_tareas["tasa"]))
    if comp_prom["muestra_suficiente"] and comp_prom["tasa"] is not None:
        partes.append(("puntualidad_promesas", comp_prom["tasa"]))
    if partes:
        productividad = round(
            100 * sum(v for _, v in partes) / len(partes), 1)
        formula = ("promedio ponderado (pesos iguales) de: "
                   + ", ".join(f"{n}={v}" for n, v in partes))
    else:
        productividad = "datos_insuficientes"
        formula = (f"ningún componente alcanza la muestra mínima "
                   f"(n>={muestra_minima}); no se calcula el índice")

    return {
        "empleado": {
            "empleado_id": emp["empleado_id"],
            "nombre": emp["nombre"],
            "cargo": emp.get("cargo", ""),
            "sucursal_id": emp.get("sucursal_id", ""),
            "activo": emp.get("activo", True),
        },
        "periodo": {"desde": desde, "hasta": hasta, "hoy": hoy},
        "ventas_registradas": {
            **ventas,
            "fuente": "os_eventos tipo='venta' (actor vinculado)",
        },
        "recepciones_procesadas": {
            **recepciones,
            "fuente": "os_eventos tipo='recepcion' (actor vinculado)",
        },
        "tareas": {**comp_tareas, "fuente": "os_tareas"},
        "promesas_puntualidad": {**comp_prom,
                                 "fuente": "os_promesas_seguimiento"},
        "productividad_operativa": {
            "valor": productividad,
            "formula": formula,
            "escala": "0-100 (promedio de tasas 0-1)",
        },
        "advertencias": advertencias,
        "nota_etica": NOTA_ETICA,
        "origen_datos": ORIGEN_DATOS,
    }


def resumen_equipo(almacen, lector, desde: str | None = None,
                   hasta: str | None = None, hoy: str | None = None,
                   muestra_minima: int = MUESTRA_MINIMA_DEFAULT,
                   solo_activos: bool = True) -> dict:
    """Indicadores de todo el equipo + comparativa. Solo lectura."""
    hoy = hoy_ymd(hoy)
    empleados = almacen.listar_empleados(activos_solo=solo_activos)
    items = [indicadores_empleado(almacen, lector, e["empleado_id"],
                                 desde, hasta, hoy, muestra_minima)
             for e in empleados]
    # Comparativa simple y honesta: ordena por productividad cuando es
    # numérica; los "datos_insuficientes" quedan al final sin rankear.
    def _clave(it):
        v = it["productividad_operativa"]["valor"]
        return (0, -v) if isinstance(v, (int, float)) else (1, 0)
    ranking = sorted(items, key=_clave)
    return {
        "periodo": {"desde": desde, "hasta": hasta, "hoy": hoy},
        "n_empleados": len(items),
        "empleados": items,
        "ranking_productividad": [
            {"empleado_id": it["empleado"]["empleado_id"],
             "nombre": it["empleado"]["nombre"],
             "productividad": it["productividad_operativa"]["valor"]}
            for it in ranking],
        "nota_etica": NOTA_ETICA,
        "origen_datos": ORIGEN_DATOS,
    }
