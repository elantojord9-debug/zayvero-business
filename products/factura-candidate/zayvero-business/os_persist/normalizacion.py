"""BLOQUE 5 (2026-10-12) — Normalización PostgreSQL del Business OS.

Write-through DENTRO de la transacción de `mutar_os`
(os_persist/tx.py): cada mutación que cambia el bundle inserta además
las filas normalizadas correspondientes (os_movimientos, os_ordenes,
os_recepciones, os_ventas, os_cuentas, os_gastos, os_clientes,
os_documentos, os_saldos). Si la transacción hace rollback no queda ni
snapshot ni fila normalizada.

Estrategia completa en docs/normalizacion.md. Resumen:

- UN solo camino de escritura (mutar_os). Nada escribe las tablas
  normalizadas fuera de él.
- El coordinador NO invade los motores: hace diff de claves
  (claves_antes / sincronizar_write_through).
- Filas inmutables -> INSERT ... ON CONFLICT DO NOTHING.
- Filas con estado mutable (orden, venta, cuenta, saldo) -> upserts
  convergentes (INSERT ... ON CONFLICT DO UPDATE con el valor actual).
- NO se reutilizan los métodos *_tx de OSPgStore dentro de mutar_os:
  gestionan su propia transacción (commit/rollback) y romperían la
  atomicidad. Sí se reutilizan los INSERT planos e idempotentes
  (guardar_movimiento, guardar_gasto, guardar_cuenta, guardar_cliente).

También incluye:
- backfill(): rellena las normalizadas desde snapshots existentes
  (idempotente, re-ejecutable; NO es migración automática).
- revertir(): borra las filas normalizadas de una empresa
  (los snapshots no se tocan).
- verificar_integridad(): compara snapshot vs normalizadas por empresa.
"""
from __future__ import annotations

import json
from decimal import Decimal

# TipoMovimiento (minúsculas en motores) -> CHECK implícito del esquema
_TIPO_MAP = {
    "entrada": "ENTRADA",
    "salida": "SALIDA",
    "ajuste": "AJUSTE",
    "transferencia_in": "TRANSFERENCIA",
    "transferencia_out": "TRANSFERENCIA",
    "devolucion_in": "DEVOLUCION",
    "devolucion_out": "DEVOLUCION",
}

# Tablas normalizadas (para revertir)
TABLAS = ("os_movimientos", "os_ordenes", "os_recepciones", "os_ventas",
          "os_cuentas", "os_gastos", "os_clientes", "os_documentos",
          "os_saldos")


def _ts(v):
    """Fecha vacía ("") o None -> NULL (PG no acepta "" en timestamptz)."""
    return v or None


def _fecha(v):
    """Fecha de negocio; si el motor no trae fecha (""), se usa el
    momento del registro (las tablas normalizadas exigen NOT NULL)."""
    from datetime import datetime, timezone
    return v or datetime.now(timezone.utc).isoformat()


def _dec(v) -> Decimal:
    return Decimal(str(v))


def _estado_venta(reg) -> str:
    """Estado del motor ("confirmada"/"cancelada") -> esquema
    (PAGADA|PENDIENTE_COBRO|CANCELADA). Se usa igual al escribir y al
    verificar, para comparar peras con peras."""
    if reg["estado"] == "cancelada":
        return "CANCELADA"
    return ("PENDIENTE_COBRO" if reg["solicitud"].a_credito else "PAGADA")


def claves_antes(bundle) -> dict:
    """Captura los sets de IDs existentes ANTES de ejecutar fn().

    Se llama con el bundle recién hidratado dentro de la transacción
    (lock por empresa ya tomado): lo que aparezca después es nuevo.
    """
    return {
        "mov": set(bundle.inventario._movs),
        "rec": set(bundle.compras._recepciones),
        "vta": set(bundle.ventas._ventas),
        "gasto": set(bundle.gastos._gastos),
        "cli": set(bundle.crm._clientes),
        "doc": set(bundle.documentos._docs),
    }


def claves_vacias() -> dict:
    """`antes` vacío: todo se considera nuevo (para el backfill)."""
    return {k: set() for k in ("mov", "rec", "vta", "gasto", "cli", "doc")}


def sincronizar_write_through(cur, company_id: str, bundle,
                              antes: dict) -> dict:
    """Inserta en las tablas normalizadas lo que cambió el bundle.

    `cur` es el cursor de la transacción de mutar_os: todo participa
    del mismo commit/rollback. `antes` viene de claves_antes()
    (o claves_vacias() en el backfill).

    Devuelve conteo de filas tocadas por tabla. Idempotente y
    convergente: re-ejecutar con el mismo estado no duplica ni cambia
    el resultado final.
    """
    from os_persist.pg import OSPgStore
    store = OSPgStore(cur)  # métodos planos: solo INSERT ... ON CONFLICT
    res = {}

    # -- movimientos (inmutables): solo los nuevos ---------------------
    n = 0
    for mid, m in bundle.inventario._movs.items():
        if mid in antes["mov"]:
            continue
        n += store.guardar_movimiento(company_id, {
            "movimiento_id": m.movimiento_id,
            "producto_id": m.producto_id,
            "almacen_id": m.almacen_id,
            "tipo": _TIPO_MAP.get(m.tipo, str(m.tipo).upper()),
            "cantidad": str(_dec(m.cantidad)),
            "fecha": _fecha(m.fecha),
            "referencia": m.referencia_id or "",
        })
    res["os_movimientos"] = n

    # -- órdenes: upsert convergente (el estado cambia: BORRADOR ->
    #    ENVIADA -> PARCIAL -> RECIBIDA -> CERRADA) ---------------------
    n = 0
    for oc in bundle.compras._ordenes.values():
        cur.execute(
            """INSERT INTO os_ordenes
               (compra_id, company_id, proveedor_id, moneda, estado, lineas,
                fecha_emision, fecha_entrega_esperada)
               VALUES (%(compra_id)s,%(company_id)s,%(proveedor_id)s,
                       %(moneda)s,%(estado)s,%(lineas)s,
                       %(fecha_emision)s,%(fecha_entrega_esperada)s)
               ON CONFLICT (company_id, compra_id) DO UPDATE SET
                 proveedor_id=EXCLUDED.proveedor_id,
                 moneda=EXCLUDED.moneda,
                 estado=EXCLUDED.estado,
                 lineas=EXCLUDED.lineas,
                 fecha_emision=EXCLUDED.fecha_emision,
                 fecha_entrega_esperada=EXCLUDED.fecha_entrega_esperada""",
            {"company_id": company_id,
             "compra_id": oc.compra_id,
             "proveedor_id": oc.proveedor_id,
             "moneda": oc.moneda,
             "estado": oc.estado,
             "lineas": json.dumps([l.to_dict() for l in oc.lineas]),
             "fecha_emision": _ts(oc.fecha_emision),
             "fecha_entrega_esperada": _ts(oc.fecha_entrega_esperada)})
        n += cur.rowcount
    res["os_ordenes"] = n

    # -- recepciones (inmutables): solo las nuevas ---------------------
    n = 0
    for rid, r in bundle.compras._recepciones.items():
        if rid in antes["rec"]:
            continue
        cur.execute(
            """INSERT INTO os_recepciones
               (recepcion_id, company_id, compra_id, almacen_id, lineas,
                fecha, factura_ref)
               VALUES (%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (company_id, recepcion_id) DO NOTHING""",
            (r.recepcion_id, company_id, r.compra_id, r.almacen_id,
             json.dumps([l.to_dict() for l in r.lineas]),
             _fecha(r.fecha), getattr(r, "factura_ref", "") or ""))
        n += cur.rowcount
    res["os_recepciones"] = n

    # -- ventas: upsert convergente (estado puede pasar a CANCELADA) ----
    n = 0
    for vid, reg in bundle.ventas._ventas.items():
        sol = reg["solicitud"]
        lineas = []
        for lin in sol.lineas:
            lineas.append({
                "producto_id": lin["producto_id"],
                "cantidad": str(_dec(lin["cantidad"])),
                "precio_unitario": str(_dec(lin["precio_unitario"])),
                "almacen_id": lin.get("almacen_id", "principal"),
                # LIMITACIÓN: el motor no guarda COGS por línea en el
                # registro; queda "0" hasta la mejora pendiente.
                "costo_unitario": "0",
            })
        cur.execute(
            """INSERT INTO os_ventas
               (venta_id, company_id, cliente_id, moneda, fecha, lineas,
                total, estado, a_credito)
               VALUES (%(venta_id)s,%(company_id)s,%(cliente_id)s,
                       %(moneda)s,%(fecha)s,%(lineas)s,%(total)s,
                       %(estado)s,%(a_credito)s)
               ON CONFLICT (company_id, venta_id) DO UPDATE SET
                 estado=EXCLUDED.estado, total=EXCLUDED.total""",
            {"company_id": company_id, "venta_id": vid,
             "cliente_id": sol.cliente_id, "moneda": sol.moneda,
             "fecha": _fecha(sol.fecha), "lineas": json.dumps(lineas),
             "total": str(_dec(reg["total"])),
             "estado": _estado_venta(reg),
             "a_credito": bool(sol.a_credito)})
        n += cur.rowcount
    res["os_ventas"] = n

    # -- cuentas CxC/CxP: upsert convergente (pagado/cobrado cambia) -----
    n = 0
    for c in bundle.tesoreria._cxc.values():
        cur.execute(
            """INSERT INTO os_cuentas
               (cuenta_id, company_id, tipo, tercero_id, monto, moneda,
                pagado, vencimiento, ref_id)
               VALUES (%(cuenta_id)s,%(company_id)s,'cxc',
                       %(tercero_id)s,%(monto)s,%(moneda)s,%(pagado)s,
                       %(vencimiento)s,%(ref_id)s)
               ON CONFLICT (company_id, cuenta_id) DO UPDATE SET
                 monto=EXCLUDED.monto, pagado=EXCLUDED.pagado,
                 vencimiento=EXCLUDED.vencimiento""",
            {"company_id": company_id, "cuenta_id": c.cuenta_id,
             "tercero_id": c.cliente_id, "monto": str(_dec(c.monto)),
             "moneda": c.moneda, "pagado": str(_dec(c.cobrado)),
             "vencimiento": _ts(c.vencimiento), "ref_id": c.referencia or ""})
        n += cur.rowcount
    for c in bundle.tesoreria._cxp.values():
        cur.execute(
            """INSERT INTO os_cuentas
               (cuenta_id, company_id, tipo, tercero_id, monto, moneda,
                pagado, vencimiento, ref_id)
               VALUES (%(cuenta_id)s,%(company_id)s,'cxp',
                       %(tercero_id)s,%(monto)s,%(moneda)s,%(pagado)s,
                       %(vencimiento)s,%(ref_id)s)
               ON CONFLICT (company_id, cuenta_id) DO UPDATE SET
                 monto=EXCLUDED.monto, pagado=EXCLUDED.pagado,
                 vencimiento=EXCLUDED.vencimiento""",
            {"company_id": company_id, "cuenta_id": c.cuenta_id,
             "tercero_id": c.proveedor_id, "monto": str(_dec(c.monto)),
             "moneda": c.moneda, "pagado": str(_dec(c.pagado)),
             "vencimiento": _ts(c.vencimiento), "ref_id": c.referencia or ""})
        n += cur.rowcount
    res["os_cuentas"] = n

    # -- gastos (inmutables): solo los nuevos --------------------------
    n = 0
    for gid, gd in bundle.gastos._gastos.items():
        if gid in antes["gasto"]:
            continue
        g = gd.gasto
        n += store.guardar_gasto(company_id, {
            "gasto_id": g.gasto_id, "categoria": g.categoria,
            "monto": str(_dec(g.monto)), "moneda": g.moneda,
            "fecha": _fecha(g.fecha), "proveedor_id": g.proveedor_id or "",
        })
    res["os_gastos"] = n

    # -- clientes (inmutables en el motor): solo los nuevos -------------
    n = 0
    for cid2, c in bundle.crm._clientes.items():
        if cid2 in antes["cli"]:
            continue
        n += store.guardar_cliente(company_id, {
            "cliente_id": c.cliente_id, "nombre": c.nombre,
            "email": c.email or "", "telefono": c.telefono or "",
        })
    res["os_clientes"] = n

    # -- documentos (inmutables): solo los nuevos -----------------------
    n = 0
    for did, f in bundle.documentos._docs.items():
        if did in antes["doc"]:
            continue
        cur.execute(
            """INSERT INTO os_documentos
               (documento_id, company_id, numero, proveedor, fecha, moneda,
                total, formato, extraccion)
               VALUES (%(documento_id)s,%(company_id)s,%(numero)s,
                       %(proveedor)s,%(fecha)s,%(moneda)s,%(total)s,
                       %(formato)s,%(extraccion)s)
               ON CONFLICT (company_id, documento_id) DO NOTHING""",
            {"company_id": company_id, "documento_id": f.documento_id,
             "numero": f.numero or "",
             "proveedor": f.proveedor_nombre or f.proveedor_id or "",
             "fecha": (f.fecha[:10] if f.fecha else None),
             "moneda": f.moneda or "DOP",
             "total": str(_dec(f.total)),
             "formato": f.formato or "",
             "extraccion": json.dumps(_json_seguro(f.to_dict())
                                      if hasattr(f, "to_dict") else {})})
        n += cur.rowcount
    res["os_documentos"] = n

    # -- saldos de tesorería: upsert convergente (valor absoluto) -------
    n = 0
    for moneda, saldo in bundle.tesoreria._caja.items():
        cur.execute(
            """INSERT INTO os_saldos (company_id, moneda, saldo)
               VALUES (%s,%s,%s)
               ON CONFLICT (company_id, moneda) DO UPDATE SET
                 saldo=EXCLUDED.saldo, updated_at=now()""",
            (company_id, moneda, str(_dec(saldo))))
        n += cur.rowcount
    res["os_saldos"] = n

    return res


def _json_seguro(obj):
    """Convierte a estructura JSON-serializable (Decimal -> str)."""
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, dict):
        return {k: _json_seguro(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_seguro(v) for v in obj]
    if isinstance(obj, set):
        return sorted((_json_seguro(v) for v in obj), key=repr)
    return obj


# --- verificación de integridad ---------------------------------------

def verificar_integridad(conn, company_id: str) -> list:
    """Compara snapshot vs tablas normalizadas para una empresa.

    Devuelve lista de diferencias:
    {"tabla", "clave", "snapshot", "normalizado"}.
    Vacía = íntegro.
    """
    from webapp.os_api import OSBundle
    from os_persist.snapshots import cargar_snapshot

    diffs = []
    b = OSBundle.crear(company_id)
    cargar_snapshot(conn, company_id, b)
    cur = conn.cursor()

    def _iguales(a, b) -> bool:
        # compara numéricamente (Decimal "50" == "50.0000") y por
        # elementos en tuplas/listas; lo demás como texto.
        if isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
            return (len(a) == len(b)
                    and all(_iguales(x, y) for x, y in zip(a, b)))
        if isinstance(a, Decimal) or isinstance(b, Decimal):
            try:
                return Decimal(str(a)) == Decimal(str(b))
            except Exception:
                pass
        return str(a) == str(b)

    def _dif(tabla, clave, snap, norm):
        if not _iguales(snap, norm):
            diffs.append({"tabla": tabla, "clave": clave,
                          "snapshot": str(snap), "normalizado": str(norm)})

    # 1. stock por (producto, almacén)
    snap_stock = {(p, a): _dec(s)
                  for (p, a), s in b.inventario.stock_empresa().items()}
    cur.execute(
        """SELECT producto_id, almacen_id, COALESCE(SUM(cantidad),0)
           FROM os_movimientos WHERE company_id=%s
           GROUP BY producto_id, almacen_id""", (company_id,))
    norm_stock = {(p, a): _dec(s) for p, a, s in cur.fetchall()}
    for k in set(snap_stock) | set(norm_stock):
        _dif("os_movimientos", f"{k[0]}@{k[1]}",
             snap_stock.get(k, Decimal("0")), norm_stock.get(k, Decimal("0")))
    # 1b. todos los movimiento_id del snapshot existen en la tabla
    cur.execute("SELECT movimiento_id FROM os_movimientos WHERE company_id=%s",
                (company_id,))
    ids_norm = {r[0] for r in cur.fetchall()}
    for mid in sorted(set(b.inventario._movs) ^ ids_norm):
        _dif("os_movimientos", f"id:{mid}",
             "en snapshot" if mid in b.inventario._movs else "ausente",
             "en tabla" if mid in ids_norm else "ausente")

    # 2. órdenes: estado
    snap_ord = {oid: o.estado for oid, o in b.compras._ordenes.items()}
    cur.execute("SELECT compra_id, estado FROM os_ordenes WHERE company_id=%s",
                (company_id,))
    norm_ord = {r[0]: r[1] for r in cur.fetchall()}
    for oid in set(snap_ord) | set(norm_ord):
        _dif("os_ordenes", oid, snap_ord.get(oid, "—"),
             norm_ord.get(oid, "—"))

    # 3. recepciones: IDs
    snap_rec = set(b.compras._recepciones)
    cur.execute("SELECT recepcion_id FROM os_recepciones WHERE company_id=%s",
                (company_id,))
    norm_rec = {r[0] for r in cur.fetchall()}
    for rid in sorted(snap_rec ^ norm_rec):
        _dif("os_recepciones", rid,
             "en snapshot" if rid in snap_rec else "ausente",
             "en tabla" if rid in norm_rec else "ausente")

    # 4. ventas: estado (mapeado igual que al escribir)
    snap_vta = {vid: _estado_venta(r) for vid, r in b.ventas._ventas.items()}
    cur.execute("SELECT venta_id, estado FROM os_ventas WHERE company_id=%s",
                (company_id,))
    norm_vta = {r[0]: r[1] for r in cur.fetchall()}
    for vid in set(snap_vta) | set(norm_vta):
        _dif("os_ventas", vid, snap_vta.get(vid, "—"),
             norm_vta.get(vid, "—"))

    # 5. cuentas: (monto, pagado)
    def _cta(c, tipo, tercero):
        pag = c.cobrado if tipo == "cxc" else c.pagado
        return (_dec(c.monto), _dec(pag))
    snap_cta = {}
    for cid2, c in b.tesoreria._cxc.items():
        snap_cta[cid2] = _cta(c, "cxc", c.cliente_id)
    for cid2, c in b.tesoreria._cxp.items():
        snap_cta[cid2] = _cta(c, "cxp", c.proveedor_id)
    cur.execute(
        """SELECT cuenta_id, monto, pagado FROM os_cuentas
           WHERE company_id=%s""", (company_id,))
    norm_cta = {r[0]: (_dec(r[1]), _dec(r[2])) for r in cur.fetchall()}
    for cid2 in set(snap_cta) | set(norm_cta):
        _dif("os_cuentas", cid2, snap_cta.get(cid2, "—"),
             norm_cta.get(cid2, "—"))

    # 6. gastos / clientes / documentos: IDs
    for nombre, snap_ids, sql in (
            ("os_gastos", set(b.gastos._gastos),
             "SELECT gasto_id FROM os_gastos WHERE company_id=%s"),
            ("os_clientes", set(b.crm._clientes),
             "SELECT cliente_id FROM os_clientes WHERE company_id=%s"),
            ("os_documentos", set(b.documentos._docs),
             "SELECT documento_id FROM os_documentos WHERE company_id=%s")):
        cur.execute(sql, (company_id,))
        norm_ids = {r[0] for r in cur.fetchall()}
        for i in sorted(snap_ids ^ norm_ids):
            _dif(nombre, i,
                 "en snapshot" if i in snap_ids else "ausente",
                 "en tabla" if i in norm_ids else "ausente")

    return diffs


def revertir(conn, company_id: str) -> dict:
    """Borra las filas normalizadas de una empresa.

    Los snapshots NO se tocan: el panel sigue funcionando.
    Útil para rehacer un backfill desde cero o deshacer pruebas.
    """
    cur = conn.cursor()
    res = {}
    for tabla in TABLAS:
        pk = {"os_movimientos": "movimiento_id",
              "os_ordenes": "compra_id",
              "os_recepciones": "recepcion_id",
              "os_ventas": "venta_id",
              "os_cuentas": "cuenta_id",
              "os_gastos": "gasto_id",
              "os_clientes": "cliente_id",
              "os_documentos": "documento_id",
              "os_saldos": "moneda"}[tabla]
        cur.execute(f"DELETE FROM {tabla} WHERE company_id=%s",
                    (company_id,))
        res[tabla] = cur.rowcount
    conn.commit()
    return res
