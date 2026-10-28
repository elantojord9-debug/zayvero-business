"""LOTE 9B — Snapshots del Business OS a PostgreSQL.

Estrategia write-through:
- PostgreSQL es la FUENTE DE VERDAD (tabla os_snapshots).
- La memoria es caché: al crear el bundle se hidrata desde PG;
  cada operación que muta hace snapshot en UNA transacción PG.
- Si el proceso muere antes del commit PG, el cliente no recibió éxito
  y puede reintentar (idempotencia por claves naturales).

Formato: JSON con marcadores __dc__ (dataclass), __dec__ (Decimal),
__tuple__ (claves tupla), __set__ (conjuntos).
"""
from __future__ import annotations

import dataclasses
import json
from decimal import Decimal

# --- serialización genérica --------------------------------------------

_DC_REGISTRY: dict[str, type] = {}


def _registrar(*clases):
    for c in clases:
        _DC_REGISTRY[c.__name__] = c


def ser(obj):
    if dataclasses.is_dataclass(obj) and not isinstance(obj, type):
        return {"__dc__": type(obj).__name__,
                **{f.name: ser(getattr(obj, f.name))
                   for f in dataclasses.fields(obj)}}
    if isinstance(obj, Decimal):
        return {"__dec__": str(obj)}
    if isinstance(obj, (list, tuple)):
        return {"__list__": [ser(x) for x in obj],
                "is_tuple": isinstance(obj, tuple)}
    if isinstance(obj, (set, frozenset)):
        return {"__set__": [ser(x) for x in sorted(obj, key=repr)]}
    if isinstance(obj, dict):
        return {"__dict__": [[ser(k), ser(v)] for k, v in obj.items()]}
    return obj


def de_ser(obj):
    if isinstance(obj, dict):
        if "__dc__" in obj:
            cls = _DC_REGISTRY[obj["__dc__"]]
            kwargs = {k: de_ser(v) for k, v in obj.items()
                      if k != "__dc__"}
            return cls(**kwargs)
        if "__dec__" in obj:
            return Decimal(obj["__dec__"])
        if "__list__" in obj:
            items = [de_ser(x) for x in obj["__list__"]]
            return tuple(items) if obj.get("is_tuple") else items
        if "__set__" in obj:
            return set(de_ser(x) for x in obj["__set__"])
        if "__dict__" in obj:
            out = {}
            for k, v in obj["__dict__"]:
                k2 = de_ser(k)
                out[k2] = de_ser(v)
            return out
    return obj


def _registro_dataclasses():
    """Registra todos los dataclasses de los módulos de motores."""
    import dataclasses
    import importlib
    mods = ["os_inventory.engine",
            "os_compras.ordenes", "os_compras.recepciones",
            "os_compras.proveedores",
            "os_finanzas.gastos", "os_finanzas.profit",
            "os_finanzas.tesoreria", "os_finanzas.costo",
            "os_crm.clientes", "os_crm.ventas", "os_crm.cobranzas",
            "os_docs.documentos", "os_docs.contador",
            "oschema.models"]
    for nombre in mods:
        try:
            m = importlib.import_module(nombre)
        except Exception:
            continue
        for attr in vars(m).values():
            if (isinstance(attr, type)
                    and dataclasses.is_dataclass(attr)
                    and attr.__module__ == m.__name__):
                _DC_REGISTRY[attr.__name__] = attr


# --- snapshot por bundle ----------------------------------------------

_MODULOS = ("inventario", "compras", "costo", "libro", "tesoreria",
            "gastos", "crm", "ventas", "cobranzas", "documentos", "mesa")


def exportar_bundle(bundle) -> dict:
    """Serializa el estado mutable de los 11 motores."""
    _registro_dataclasses()
    b = bundle
    return {
        "inventario": {
            "movs": ser(b.inventario._movs),
            "niveles": ser(b.inventario._niveles),
        },
        "compras": {
            "ordenes": ser(b.compras._ordenes),
            "recepciones": ser(b.compras._recepciones),
            "recibido": ser(b.compras._recibido),
        },
        "costo": {"estado": ser(b.costo._estado)},
        "libro": {
            "ventas": ser(b.libro._ventas),
            "gastos": ser(b.libro._gastos),
            "notas_credito": ser(b.libro._notas_credito),
            # 2026-10-13: impuestos explícitos del contador (P&L).
            # .get(): snapshots viejos no traen la clave.
            "impuestos": ser(b.libro._impuestos),
        },
        "tesoreria": {
            "caja": ser(b.tesoreria._caja),
            "cxc": ser(b.tesoreria._cxc),
            "cxp": ser(b.tesoreria._cxp),
            "futuros": ser(b.tesoreria._futuros),
        },
        "gastos": {"gastos": ser(b.gastos._gastos)},
        "crm": {
            "clientes": ser(b.crm._clientes),
            "notas": ser(b.crm._notas),
            "historial": ser(b.crm._historial),
            # BLOQUE CRM/VENTAS (2026-10-13): detalle de líneas por compra
            # (productos preferidos de la ficha 360).
            "lineas_compras": ser(b.crm._lineas_compras),
        },
        "ventas": {"ventas": ser(b.ventas._ventas)},
        "cobranzas": {"promesas": ser(b.cobranzas._promesas)},
        "documentos": {
            "docs": ser(b.documentos._docs),
            "vistos": ser(b.documentos._vistos),
        },
        "mesa": {"props": ser(b.mesa._props)},
        "demo_cargada": b.demo_cargada,
    }


def importar_bundle(bundle, datos: dict):
    """Restaura el estado mutable de los 11 motores."""
    _registro_dataclasses()
    b = bundle
    d = datos
    b.inventario._movs = de_ser(d["inventario"]["movs"])
    b.inventario._niveles = de_ser(d["inventario"]["niveles"])
    b.compras._ordenes = de_ser(d["compras"]["ordenes"])
    b.compras._recepciones = de_ser(d["compras"]["recepciones"])
    b.compras._recibido = de_ser(d["compras"]["recibido"])
    b.costo._estado = de_ser(d["costo"]["estado"])
    b.libro._ventas = de_ser(d["libro"]["ventas"])
    b.libro._gastos = de_ser(d["libro"].get("gastos", []))
    b.libro._notas_credito = de_ser(d["libro"]["notas_credito"])
    # 2026-10-13: .get() para snapshots anteriores a la 009/P&L.
    b.libro._impuestos = de_ser(d["libro"].get("impuestos", {}))
    b.tesoreria._caja = de_ser(d["tesoreria"]["caja"])
    b.tesoreria._cxc = de_ser(d["tesoreria"]["cxc"])
    b.tesoreria._cxp = de_ser(d["tesoreria"]["cxp"])
    b.tesoreria._futuros = de_ser(d["tesoreria"]["futuros"])
    b.gastos._gastos = de_ser(d["gastos"]["gastos"])
    b.crm._clientes = de_ser(d["crm"]["clientes"])
    b.crm._notas = de_ser(d["crm"]["notas"])
    b.crm._historial = de_ser(d["crm"]["historial"])
    # .get: los snapshots escritos antes del bloque CRM no traen la clave.
    b.crm._lineas_compras = de_ser(d["crm"].get("lineas_compras", {}))
    b.ventas._ventas = de_ser(d["ventas"]["ventas"])
    b.cobranzas._promesas = de_ser(d["cobranzas"]["promesas"])
    b.documentos._docs = de_ser(d["documentos"]["docs"])
    b.documentos._vistos = de_ser(d["documentos"]["vistos"])
    b.mesa._props = de_ser(d["mesa"]["props"])
    b.demo_cargada = bool(d.get("demo_cargada", False))


def guardar_snapshot(conn, company_id: str, bundle, commit: bool = True):
    """Escribe los 11 módulos en la transacción PG en curso.

    Con commit=True (defecto) confirma; en flujo transaccional
    (os_persist/tx.py) se pasa commit=False y confirma el llamador.
    """
    datos = exportar_bundle(bundle)
    cur = conn.cursor()
    cur.execute(
        """INSERT INTO os_companies (company_id) VALUES (%s)
           ON CONFLICT (company_id) DO NOTHING""", (company_id,))
    for modulo in _MODULOS:
        cur.execute(
            """INSERT INTO os_snapshots (company_id, modulo, datos)
               VALUES (%s, %s, %s)
               ON CONFLICT (company_id, modulo) DO UPDATE
               SET datos = EXCLUDED.datos, updated_at = now()""",
            (company_id, modulo, json.dumps(datos[modulo])))
    if bundle.demo_cargada:
        cur.execute(
            """INSERT INTO os_flags (company_id, flag) VALUES (%s,'demo')
               ON CONFLICT DO NOTHING""", (company_id,))
    if commit:
        conn.commit()


def cargar_snapshot(conn, company_id: str, bundle) -> bool:
    """Hidrata el bundle desde PG. True si había datos."""
    cur = conn.cursor()
    cur.execute("SELECT modulo, datos FROM os_snapshots WHERE company_id=%s",
                (company_id,))
    filas = cur.fetchall()
    if not filas:
        return False
    datos = {mod: (d if isinstance(d, dict) else json.loads(d))
             for mod, d in filas}
    datos["demo_cargada"] = _leer_flag(conn, company_id)
    importar_bundle(bundle, datos)
    return True


def _leer_flag(conn, company_id: str) -> bool:
    cur = conn.cursor()
    cur.execute(
        "SELECT COUNT(*) FROM os_flags WHERE company_id=%s AND flag='demo'",
        (company_id,))
    return cur.fetchone()[0] > 0


def guardar_flag(conn, company_id: str, flag: str):
    conn.cursor().execute(
        """INSERT INTO os_flags (company_id, flag) VALUES (%s,%s)
           ON CONFLICT DO NOTHING""", (company_id, flag))
    conn.commit()
