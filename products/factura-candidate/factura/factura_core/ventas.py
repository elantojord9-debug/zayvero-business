"""Ventas, facturas, POS y cotizaciones.

- Totales con Decimal exacto reutilizando os_ecf.modelos.Linea +
  calcular_totales (tasa None = exento).
- Numeración sin duplicados: factura_secuencias con bloqueo transaccional
  (SELECT ... FOR UPDATE en PG; BEGIN IMMEDIATE en SQLite).
- Las facturas emitidas NO se editan: solo se anulan.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal

import config
from factura_persist import db
from factura_persist.uow import atomic_company
from factura_core import inventario

# Motor reutilizado: líneas y totales con Decimal exacto
from os_ecf.modelos import Linea, calcular_totales, ErrorECF


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def reservar_numero(conn, tipo_doc: str, empresa_id: int) -> str:
    """Reserva el siguiente número de documento sin duplicados.

    Debe llamarse dentro de una transacción con bloqueo: el llamador usa
    db.locked(conn) (SQLite: BEGIN IMMEDIATE) o transacción PG con
    SELECT ... FOR UPDATE aquí dentro.
    """
    tipo_doc = (tipo_doc or "").upper()
    if tipo_doc not in config.SEQ_PREFIJOS:
        raise ValueError(f"tipo de documento inválido: {tipo_doc!r}")
    cur = conn.cursor()
    if db.backend() == "pg":
        db.execute(cur, "INSERT INTO factura_secuencias (tipo_doc, empresa_id,"
                        " ultimo_numero) VALUES (%s,%s,0)"
                        " ON CONFLICT (tipo_doc, empresa_id) DO NOTHING",
                   (tipo_doc, empresa_id))
        fila = db.fetchone(cur, "SELECT ultimo_numero FROM factura_secuencias"
                                " WHERE tipo_doc=%s AND empresa_id=%s"
                                " FOR UPDATE", (tipo_doc, empresa_id))
    else:
        fila = db.fetchone(cur, "SELECT ultimo_numero FROM factura_secuencias"
                                " WHERE tipo_doc=%s AND empresa_id=%s",
                           (tipo_doc, empresa_id))
        if not fila:
            db.execute(cur, "INSERT INTO factura_secuencias (tipo_doc,"
                            " empresa_id, ultimo_numero) VALUES (%s,%s,0)",
                       (tipo_doc, empresa_id))
            fila = (0,)
    nuevo = int(fila[0]) + 1
    db.execute(cur, "UPDATE factura_secuencias SET ultimo_numero=%s"
                    " WHERE tipo_doc=%s AND empresa_id=%s",
               (nuevo, tipo_doc, empresa_id))
    return f"{config.SEQ_PREFIJOS[tipo_doc]}{nuevo:06d}"


def normalizar_items(empresa_id: int, raw_items: list[dict],
                     tasa: Decimal) -> tuple[list[dict], dict]:
    """Valida items contra inventario y calcula totales exactos.

    raw_items: [{producto_id?, nombre, cantidad, precio, itbis_exento?,
                 descuento?}]. Devuelve (items_norm, totales_dict).
    """
    if not raw_items:
        raise ValueError("la venta necesita al menos un ítem")
    items_norm: list[dict] = []
    lineas: list[Linea] = []
    for it in raw_items:
        nombre = (it.get("nombre") or "").strip()
        if not nombre:
            raise ValueError("cada ítem necesita un nombre")
        try:
            cantidad = Decimal(str(it.get("cantidad", 1)))
            precio = Decimal(str(it.get("precio", 0)))
            descuento = Decimal(str(it.get("descuento", 0)))
        except Exception:
            raise ValueError(f"cantidad/precio inválidos en {nombre!r}")
        if cantidad <= 0:
            raise ValueError(f"cantidad inválida en {nombre!r}")
        if precio < 0 or descuento < 0:
            raise ValueError(f"precio/descuento inválidos en {nombre!r}")
        exento = bool(it.get("itbis_exento"))
        tasa_linea = None if exento else tasa
        try:
            linea = Linea(descripcion=nombre, cantidad=cantidad,
                          precio_unitario=precio, tasa_itbis=tasa_linea,
                          descuento=descuento)
        except ErrorECF as exc:
            raise ValueError(str(exc))
        lineas.append(linea)
        items_norm.append({
            "producto_id": it.get("producto_id"),
            "nombre": nombre,
            "cantidad": str(cantidad),
            "precio": str(precio),
            "descuento": str(descuento),
            "itbis_exento": exento,
            "subtotal": str(linea.subtotal),
            "itbis": str(linea.itbis),
        })
    tot = calcular_totales(lineas)
    totales = {"subtotal": str(tot.subtotal), "itbis": str(tot.itbis),
               "total": str(tot.total)}
    return items_norm, totales


def _tasa_empresa(conn, empresa_id: int) -> Decimal:
    cur = conn.cursor()
    fila = db.fetchone(cur, "SELECT itbis_tasa FROM factura_empresas WHERE id=%s",
                       (empresa_id,))
    return Decimal(str(fila[0])) if fila else Decimal(config.ITBIS_TASA_DEFAULT)


@atomic_company
def crear_venta(empresa_id: int, tipo: str, cliente_id: int | None,
                raw_items: list[dict], descuento_global=0,
                creada_por: int | None = None,
                estado: str = "pendiente",
                metodo_pago: str | None = None,
                numero: str | None = None,
                idempotencia: str | None = None,
                vencimiento: str | None = None) -> dict:
    """Crea factura/cotización/POS. Descuenta stock (factura/POS; los
    servicios no mueven inventario). Si metodo_pago se indica, registra
    el pago y marca 'pagada'. `vencimiento` es la fecha límite de pago
    ISO ('' = sin límite).

    FASE 2 — idempotencia: si `idempotencia` (clave del cliente, p. ej.
    UUID generado en el navegador antes del POST) ya existe para la
    empresa, se devuelve la venta existente SIN crear duplicado ni
    descontar stock de nuevo. La verificación ocurre dentro de la misma
    transacción bloqueada; el índice parcial
    UNIQUE(empresa_id, idempotencia) WHERE idempotencia<>'' es la red de
    seguridad ante carreras.
    """
    if tipo not in config.TIPOS_VENTA:
        raise ValueError(f"tipo inválido: {tipo!r}")
    if estado not in config.ESTADOS_VENTA:
        raise ValueError(f"estado inválido: {estado!r}")
    idem = (idempotencia or "").strip()
    ven = _validar_vencimiento(vencimiento)
    try:
        desc_g = Decimal(str(descuento_global or 0))
    except Exception:
        raise ValueError("descuento global inválido")
    if desc_g < 0:
        raise ValueError("el descuento no puede ser negativo")

    conn = db.connect()
    try:
        with db.locked(conn):
            cur = conn.cursor()
            if idem:
                fila = db.fetchone(
                    cur, "SELECT id FROM factura_ventas"
                         " WHERE empresa_id=%s AND idempotencia=%s",
                    (empresa_id, idem))
                if fila:
                    return obtener(empresa_id, int(fila[0]))
            if cliente_id is not None and not db.fetchone(cur, "SELECT id FROM factura_clientes WHERE id=%s AND empresa_id=%s", (cliente_id, empresa_id)):
                raise ValueError("cliente no encontrado")
            tasa = _tasa_empresa(conn, empresa_id)
            items_norm, totales = normalizar_items(empresa_id, raw_items, tasa)
            total = Decimal(totales["total"]) - desc_g
            if total < 0:
                raise ValueError("el descuento supera el total")
            tipo_doc = "POS" if tipo == "pos" else "FACTURA"
            num = numero or reservar_numero(conn, tipo_doc, empresa_id)
            vid = db.insert_id(
                cur, "INSERT INTO factura_ventas (empresa_id, numero, tipo,"
                     " cliente_id, items, subtotal, itbis, descuento, total,"
                     " estado, vencimiento, creada, creada_por, idempotencia)"
                     " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (empresa_id, num, tipo, cliente_id,
                 json.dumps(items_norm, ensure_ascii=False),
                 totales["subtotal"], totales["itbis"], str(desc_g),
                 str(total), estado, ven, _ahora(), creada_por, idem))
            if tipo in ("factura", "pos"):
                for it in items_norm:
                    pid = it.get("producto_id")
                    if pid and not inventario.es_servicio(
                            conn, empresa_id, int(pid)):
                        inventario.ajustar_stock(
                            conn, empresa_id, int(pid),
                            -Decimal(it["cantidad"]))
            if metodo_pago:
                if metodo_pago not in config.METODOS_PAGO:
                    raise ValueError(f"método de pago inválido: {metodo_pago!r}")
                db.execute(
                    cur, "INSERT INTO factura_pagos (empresa_id, venta_id,"
                         " monto, metodo, creada, creado_por)"
                         " VALUES (%s,%s,%s,%s,%s,%s)",
                    (empresa_id, vid, str(total), metodo_pago, _ahora(),
                     creada_por))
                db.execute(cur, "UPDATE factura_ventas SET estado='pagada'"
                                " WHERE id=%s", (vid,))
                estado = "pagada"
        return obtener(empresa_id, vid)
    finally:
        conn.close()


def _validar_vencimiento(vencimiento: str | None) -> str:
    """Fecha límite de pago ISO ('' = sin límite)."""
    from datetime import date
    ven = (vencimiento or "").strip()
    if not ven:
        return ""
    try:
        date.fromisoformat(ven)
    except Exception:
        raise ValueError(f"vencimiento inválido (use AAAA-MM-DD): {ven!r}")
    return ven


@atomic_company
def establecer_vencimiento(empresa_id: int, venta_id: int,
                           vencimiento: str | None) -> dict:
    """Fija (o limpia) la fecha límite de pago de una venta a crédito."""
    ven = _validar_vencimiento(vencimiento)
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            fila = db.fetchone(
                cur, "SELECT estado FROM factura_ventas"
                     " WHERE id=%s AND empresa_id=%s",
                (venta_id, empresa_id))
            if not fila:
                raise ValueError("venta no encontrada")
            if fila[0] != "pendiente":
                raise ValueError(
                    "solo las ventas pendientes llevan vencimiento")
            db.execute(cur, "UPDATE factura_ventas SET vencimiento=%s"
                            " WHERE id=%s AND empresa_id=%s",
                       (ven, venta_id, empresa_id))
        return obtener(empresa_id, venta_id)
    finally:
        conn.close()


def obtener(empresa_id: int, venta_id: int) -> dict | None:
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(cur, "SELECT * FROM factura_ventas"
                                " WHERE id=%s AND empresa_id=%s",
                           (venta_id, empresa_id))
        if not fila:
            return None
        v = dict(fila)
        v["items"] = json.loads(v["items"] or "[]")
        return v
    finally:
        conn.close()


def listar(empresa_id: int, tipo: str | None = None,
           estado: str | None = None, limite: int = 100) -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        sql = "SELECT * FROM factura_ventas WHERE empresa_id=%s"
        params: list = [empresa_id]
        if tipo:
            sql += " AND tipo=%s"
            params.append(tipo)
        if estado:
            sql += " AND estado=%s"
            params.append(estado)
        sql += " ORDER BY id DESC LIMIT %s"
        params.append(limite)
        filas = db.fetchall(cur, sql, params)
        out = []
        for f in filas:
            v = dict(f)
            v["items"] = json.loads(v["items"] or "[]")
            out.append(v)
        return out
    finally:
        conn.close()


@atomic_company
def anular(empresa_id: int, venta_id: int, motivo: str = "") -> dict:
    """Anula una venta (no se editan las emitidas). Devuelve el stock."""
    conn = db.connect()
    try:
        with db.locked(conn):
            v = obtener(empresa_id, venta_id)
            if not v:
                raise ValueError("venta no encontrada")
            if v["estado"] == "anulada":
                raise ValueError("la venta ya está anulada")
            cur = conn.cursor()
            if db.fetchone(cur, "SELECT id FROM factura_devoluciones WHERE empresa_id=%s AND venta_id=%s", (empresa_id, venta_id)):
                raise ValueError("venta con devoluciones: requiere ajuste conciliado")
            if db.fetchone(cur, "SELECT id FROM factura_pagos WHERE empresa_id=%s AND venta_id=%s", (empresa_id, venta_id)):
                raise ValueError("venta cobrada: requiere reverso de cobro antes de anular")
            cur = conn.cursor()
            for it in v["items"]:
                pid = it.get("producto_id")
                if pid and not inventario.es_servicio(conn, empresa_id,
                                                      int(pid)):
                    inventario.ajustar_stock(
                        conn, empresa_id, int(pid),
                        Decimal(it["cantidad"]))
            db.execute(cur, "UPDATE factura_ventas SET estado='anulada'"
                            " WHERE id=%s AND empresa_id=%s",
                       (venta_id, empresa_id))
        return obtener(empresa_id, venta_id)
    finally:
        conn.close()


def ventas_del_dia(empresa_id: int) -> dict:
    from datetime import date
    hoy = date.today().isoformat()
    conn = db.connect()
    try:
        cur = conn.cursor()
        filas = db.fetchall(
            cur, "SELECT total, estado FROM factura_ventas"
                 " WHERE empresa_id=%s AND substr(creada,1,10)=%s"
                 " AND estado<>'anulada'", (empresa_id, hoy))
        total = sum((db.dec(f[0]) for f in filas), Decimal("0"))
        return {"cantidad": len(filas), "total": total, "fecha": hoy}
    finally:
        conn.close()


# --- cotizaciones -----------------------------------------------------------

@atomic_company
def crear_cotizacion(empresa_id: int, cliente_id: int | None,
                     raw_items: list[dict],
                     creada_por: int | None = None) -> dict:
    conn = db.connect()
    try:
        with db.locked(conn):
            cur = conn.cursor()
            if cliente_id is not None and not db.fetchone(cur, "SELECT id FROM factura_clientes WHERE id=%s AND empresa_id=%s", (cliente_id, empresa_id)):
                raise ValueError("cliente no encontrado")
            tasa = _tasa_empresa(conn, empresa_id)
            items_norm, totales = normalizar_items(empresa_id, raw_items, tasa)
            num = reservar_numero(conn, "COTIZACION", empresa_id)
            cur = conn.cursor()
            cid = db.insert_id(
                cur, "INSERT INTO factura_cotizaciones (empresa_id, numero,"
                     " cliente_id, items, subtotal, itbis, total, estado,"
                     " creada, creada_por)"
                     " VALUES (%s,%s,%s,%s,%s,%s,%s,'vigente',%s,%s)",
                (empresa_id, num, cliente_id,
                 json.dumps(items_norm, ensure_ascii=False),
                 totales["subtotal"], totales["itbis"], totales["total"],
                 _ahora(), creada_por))
        return obtener_cotizacion(empresa_id, cid)
    finally:
        conn.close()


def obtener_cotizacion(empresa_id: int, cot_id: int) -> dict | None:
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(cur, "SELECT * FROM factura_cotizaciones"
                                " WHERE id=%s AND empresa_id=%s",
                           (cot_id, empresa_id))
        if not fila:
            return None
        c = dict(fila)
        c["items"] = json.loads(c["items"] or "[]")
        return c
    finally:
        conn.close()


def listar_cotizaciones(empresa_id: int, limite: int = 100) -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        filas = db.fetchall(
            cur, "SELECT * FROM factura_cotizaciones WHERE empresa_id=%s"
                 " ORDER BY id DESC LIMIT %s", (empresa_id, limite))
        out = []
        for f in filas:
            c = dict(f)
            c["items"] = json.loads(c["items"] or "[]")
            out.append(c)
        return out
    finally:
        conn.close()


@atomic_company
def convertir_cotizacion(empresa_id: int, cot_id: int,
                         creada_por: int | None = None) -> dict:
    """Convierte una cotización vigente en factura (descuenta stock)."""
    c = obtener_cotizacion(empresa_id, cot_id)
    if not c:
        raise ValueError("cotización no encontrada")
    if c["estado"] != "vigente":
        raise ValueError("solo se convierten cotizaciones vigentes")
    venta = crear_venta(empresa_id, "factura", c["cliente_id"], c["items"],
                        creada_por=creada_por, estado="pendiente")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "UPDATE factura_cotizaciones SET estado='convertida'"
                            " WHERE id=%s AND empresa_id=%s",
                       (cot_id, empresa_id))
    finally:
        conn.close()
    return venta


@atomic_company
def asignar_sucursal(empresa_id: int, venta_id: int,
                     sucursal_id: int | None) -> None:
    """Asigna la sucursal de una venta (selector al vender). La sucursal
    debe pertenecer a la empresa y estar activa."""
    if not sucursal_id:
        return
    from factura_core import sucursales
    s = sucursales.obtener(empresa_id, int(sucursal_id))
    if not s:
        raise ValueError("sucursal inválida")
    if not s["activa"]:
        raise ValueError("la sucursal está inactiva")
    conn = db.connect()
    try:
        cur = conn.cursor()
        with db.tx(conn):
            db.execute(cur, "UPDATE factura_ventas SET sucursal_id=%s"
                            " WHERE id=%s AND empresa_id=%s",
                       (s["id"], venta_id, empresa_id))
    finally:
        conn.close()


@atomic_company
def emitir_administrativa(empresa_id: int, tipo: str, cliente_id: int | None,
                         raw_items: list[dict], descuento_global=0,
                         creada_por: int | None = None,
                         metodo_pago: str | None = None,
                         sucursal_id=None, idempotencia: str | None = None):
    """HTTP use case: authoritative catalog + sale/payment/cash + audit.

    An uncertain HTTP response must be retried with the same key and payload.
    A successful replay returns before any side effect is re-executed.
    """
    from factura_core import caja, saas
    if not idempotencia or len(idempotencia.strip()) < 8:
        raise ValueError("se requiere Idempotency-Key de al menos 8 caracteres")
    if tipo not in ("pos", "factura"):
        raise ValueError("tipo administrativo inválido")
    saas.verificar_limite_documentos(empresa_id)
    if tipo == "pos" or metodo_pago == "efectivo":
        caja.exigir_abierta(empresa_id)
    conn = db.connect()
    try:
        items = []
        if not raw_items:
            raise ValueError("se requiere al menos un ítem")
        for item in raw_items:
            item = dict(item)
            if item.get("producto_id"):
                cur = conn.cursor()
                product = db.fetchone(cur, "SELECT nombre, precio, itbis_exento FROM factura_productos WHERE id=%s AND empresa_id=%s AND activo=1", (int(item["producto_id"]), empresa_id))
                if not product:
                    raise ValueError("producto no encontrado")
                item.update(nombre=product[0], precio=product[1], itbis_exento=bool(product[2]))
            items.append(item)
        venta = crear_venta(empresa_id, tipo, cliente_id, items,
                            descuento_global=descuento_global,
                            creada_por=creada_por, metodo_pago=metodo_pago)
        if sucursal_id:
            asignar_sucursal(empresa_id, venta["id"], int(sucursal_id))
        if metodo_pago == "efectivo":
            caja.registrar_movimiento(empresa_id, "venta", venta["total"],
                                      "venta " + venta["numero"], creada_por)
        return obtener(empresa_id, venta["id"])
    finally:
        conn.close()
