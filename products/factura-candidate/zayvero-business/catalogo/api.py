"""BLOQUE 5 — Catálogo maestro: CRUD con locks por empresa (PostgreSQL).

Patrón de concurrencia: reutiliza `os_company_lock` + SELECT ... FOR
UPDATE de `os_persist/tx.py` (una fila por empresa: sin deadlocks). Cada
mutación abre SU propia conexión y transacción; el lock serializa a los
escritores de la misma empresa entre procesos.

DIFERENCIA con `mutar_os`: el catálogo NO pasa por snapshots — sus
tablas normalizadas SON la fuente primaria (no hay bundle que
hidratar). Por eso tiene su propio `_mutar` mínimo en vez de reusar
`mutar_os` (que acopla snapshots/eventos/idempotencia del OS). Sí se
incrementa `os_company_lock.version` para mantener el invariante
"version cambió ⇒ algo cambió en la empresa".

Lecturas: SELECTs directos sin lock (el catálogo es solo-lectura para
los motores vía `catalogo/integracion.py`).

Requiere ZAYVERO_OS_DATABASE_URL (PostgreSQL local). Sin DSN: las
escrituras lanzan ErrorCatalogo; las lecturas de integración devuelven
None (fallback de los motores).
"""
from __future__ import annotations

import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from .modelo import (  # noqa: E402
    Almacen, Categoria, CategoriaConProductos, ConflictoVersion,
    CostoVigente, ErrorCatalogo, PrecioVigente, Producto, ProductoNoExiste,
    CategoriaNoExiste, AlmacenNoExiste, SKUduplicado, ValidacionCatalogo,
    advertencias_precio_costo, validar_id, validar_moneda,
)

DSN_ENV = "ZAYVERO_OS_DATABASE_URL"


def _dsn() -> str:
    dsn = os.environ.get(DSN_ENV)
    if not dsn:
        raise ErrorCatalogo(
            f"el catálogo requiere {DSN_ENV} (PostgreSQL local)")
    return dsn


def _mutar(company_id: str, fn):
    """Ejecuta fn(cur) -> resultado en transacción con lock por empresa."""
    from os_persist.pg import connect
    conn = connect(_dsn())
    try:
        conn.autocommit = False
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO os_companies (company_id) VALUES (%s)"
            " ON CONFLICT (company_id) DO NOTHING", (company_id,))
        cur.execute(
            "INSERT INTO os_company_lock (company_id) VALUES (%s)"
            " ON CONFLICT (company_id) DO NOTHING", (company_id,))
        cur.execute(
            "SELECT version FROM os_company_lock WHERE company_id=%s"
            " FOR UPDATE", (company_id,))
        fila = cur.fetchone()
        version = fila[0] if fila else 0
        resultado = fn(cur)
        cur.execute(
            "UPDATE os_company_lock SET version=%s, updated_at=now()"
            " WHERE company_id=%s AND version=%s",
            (version + 1, company_id, version))
        if cur.rowcount != 1:
            raise ConflictoVersion(
                f"conflicto de versión en os_company_lock ({company_id})")
        conn.commit()
        return resultado
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _leer(fn):
    """Lectura simple (sin lock)."""
    from os_persist.pg import connect
    conn = connect(_dsn())
    try:
        return fn(conn.cursor())
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _fila_producto(r) -> dict:
    (cid, pid, sku, nombre, desc, cat, unidad, activo, meta,
     creada, actualiz, ver) = r
    return {"company_id": cid, "producto_id": pid, "sku": sku,
            "nombre": nombre, "descripcion": desc, "categoria_id": cat,
            "unidad": unidad, "activo": activo, "metadata": meta,
            "created_at": creada.isoformat() if creada else None,
            "updated_at": actualiz.isoformat() if actualiz else None,
            "version": ver}


_COLS_PROD = ("company_id, producto_id, sku, nombre, descripcion,"
              " categoria_id, unidad, activo, metadata, created_at,"
              " updated_at, version")


class Catalogo:
    """Catálogo maestro de UNA empresa."""

    def __init__(self, company_id: str):
        self.company_id = validar_id(company_id, "company_id")
        _dsn()  # falla temprano y claro sin DSN

    # ---- categorías ----------------------------------------------------
    def crear_categoria(self, categoria_id: str, nombre: str,
                        padre_id: str | None = None) -> dict:
        cat = Categoria(self.company_id, categoria_id, nombre, padre_id)
        cat.validar()

        def _op(cur):
            if cat.padre_id is not None:
                cur.execute(
                    "SELECT 1 FROM os_catalogo_categorias"
                    " WHERE company_id=%s AND categoria_id=%s",
                    (self.company_id, cat.padre_id))
                if not cur.fetchone():
                    raise CategoriaNoExiste(
                        f"categoría padre inexistente: {cat.padre_id!r}")
            try:
                cur.execute(
                    """INSERT INTO os_catalogo_categorias
                       (company_id, categoria_id, nombre, padre_id)
                       VALUES (%s,%s,%s,%s)""",
                    (self.company_id, cat.categoria_id, cat.nombre,
                     cat.padre_id))
            except Exception as e:
                if "os_catalogo_categorias_pkey" in str(e):
                    raise ValidacionCatalogo(
                        f"categoría duplicada: {cat.categoria_id!r}")
                raise
            return {"ok": True, "categoria_id": cat.categoria_id,
                    "nombre": cat.nombre, "padre_id": cat.padre_id}

        return _mutar(self.company_id, _op)

    def listar_categorias(self, solo_activas: bool = True) -> list[dict]:
        def _op(cur):
            q = ("SELECT company_id, categoria_id, nombre, padre_id, activa,"
                 " version FROM os_catalogo_categorias WHERE company_id=%s")
            if solo_activas:
                q += " AND activa"
            cur.execute(q + " ORDER BY nombre", (self.company_id,))
            return [{"categoria_id": r[1], "nombre": r[2], "padre_id": r[3],
                     "activa": r[4], "version": r[5]}
                    for r in cur.fetchall()]
        return _leer(_op)

    def eliminar_categoria(self, categoria_id: str) -> dict:
        categoria_id = validar_id(categoria_id, "categoria_id")

        def _op(cur):
            cur.execute(
                "SELECT COUNT(*) FROM os_catalogo_productos"
                " WHERE company_id=%s AND categoria_id=%s",
                (self.company_id, categoria_id))
            if cur.fetchone()[0]:
                raise CategoriaConProductos(
                    f"categoría {categoria_id!r} tiene productos asociados")
            cur.execute(
                "DELETE FROM os_catalogo_categorias"
                " WHERE company_id=%s AND categoria_id=%s",
                (self.company_id, categoria_id))
            if cur.rowcount == 0:
                raise CategoriaNoExiste(f"categoría inexistente: {categoria_id!r}")
            return {"ok": True, "categoria_id": categoria_id}
        return _mutar(self.company_id, _op)

    # ---- almacenes ------------------------------------------------------
    def crear_almacen(self, almacen_id: str, nombre: str,
                      direccion: str = "") -> dict:
        alm = Almacen(self.company_id, almacen_id, nombre, direccion)
        alm.validar()

        def _op(cur):
            try:
                cur.execute(
                    """INSERT INTO os_almacenes
                       (company_id, almacen_id, nombre, direccion)
                       VALUES (%s,%s,%s,%s)""",
                    (self.company_id, alm.almacen_id, alm.nombre,
                     alm.direccion))
            except Exception as e:
                if "os_almacenes_pkey" in str(e):
                    raise ValidacionCatalogo(
                        f"almacén duplicado: {alm.almacen_id!r}")
                raise
            return {"ok": True, "almacen_id": alm.almacen_id,
                    "nombre": alm.nombre}
        return _mutar(self.company_id, _op)

    def listar_almacenes(self, solo_activos: bool = True) -> list[dict]:
        def _op(cur):
            q = ("SELECT almacen_id, nombre, direccion, activo, version"
                 " FROM os_almacenes WHERE company_id=%s")
            if solo_activos:
                q += " AND activo"
            cur.execute(q + " ORDER BY almacen_id", (self.company_id,))
            return [{"almacen_id": r[0], "nombre": r[1], "direccion": r[2],
                     "activo": r[3], "version": r[4]}
                    for r in cur.fetchall()]
        return _leer(_op)

    def eliminar_almacen(self, almacen_id: str) -> dict:
        almacen_id = validar_id(almacen_id, "almacen_id")

        def _op(cur):
            cur.execute(
                "DELETE FROM os_almacenes"
                " WHERE company_id=%s AND almacen_id=%s",
                (self.company_id, almacen_id))
            if cur.rowcount == 0:
                raise AlmacenNoExiste(f"almacén inexistente: {almacen_id!r}")
            return {"ok": True, "almacen_id": almacen_id}
        return _mutar(self.company_id, _op)

    # ---- productos -------------------------------------------------------
    def crear_producto(self, producto_id: str, sku: str, nombre: str,
                       descripcion: str = "",
                       categoria_id: str | None = None,
                       unidad: str = "und",
                       metadata: dict | None = None,
                       precio: str | Decimal | None = None,
                       costo: str | Decimal | None = None,
                       moneda: str = "DOP") -> dict:
        """Crea el producto y, si se dan, su precio/costo inicial."""
        prod = Producto(self.company_id, producto_id, sku, nombre,
                        descripcion, categoria_id, unidad,
                        metadata=metadata or {})
        prod.validar()
        moneda = validar_moneda(moneda)
        from .modelo import validar_precio, validar_costo
        pv = validar_precio(precio) if precio is not None else None
        cv = validar_costo(costo) if costo is not None else None
        advertencias = advertencias_precio_costo(pv, cv)

        def _op(cur):
            if prod.categoria_id is not None:
                cur.execute(
                    "SELECT 1 FROM os_catalogo_categorias"
                    " WHERE company_id=%s AND categoria_id=%s",
                    (self.company_id, prod.categoria_id))
                if not cur.fetchone():
                    raise CategoriaNoExiste(
                        f"categoría inexistente: {prod.categoria_id!r}")
            import json
            try:
                cur.execute(
                    f"""INSERT INTO os_catalogo_productos ({_COLS_PROD})
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,now(),now(),1)""",
                    (self.company_id, prod.producto_id, prod.sku,
                     prod.nombre, prod.descripcion, prod.categoria_id,
                     prod.unidad, True, json.dumps(prod.metadata)))
            except Exception as e:
                msg = str(e)
                if "ux_cat_sku_empresa" in msg:
                    raise SKUduplicado(
                        f"SKU duplicado en {self.company_id}: {prod.sku!r}")
                if "os_catalogo_productos_pkey" in msg:
                    raise ValidacionCatalogo(
                        f"producto_id duplicado: {prod.producto_id!r}")
                raise
            if pv is not None:
                _insertar_precio(cur, self.company_id, prod.producto_id,
                                 moneda, pv, "precio inicial")
            if cv is not None:
                _insertar_costo(cur, self.company_id, prod.producto_id,
                                moneda, cv, "manual", "costo inicial")
            return {"ok": True, "producto_id": prod.producto_id,
                    "sku": prod.sku, "advertencias": advertencias}
        return _mutar(self.company_id, _op)

    def obtener_producto(self, producto_id: str) -> dict | None:
        producto_id = validar_id(producto_id, "producto_id")

        def _op(cur):
            cur.execute(
                f"SELECT {_COLS_PROD} FROM os_catalogo_productos"
                " WHERE company_id=%s AND producto_id=%s",
                (self.company_id, producto_id))
            r = cur.fetchone()
            return _fila_producto(r) if r else None
        return _leer(_op)

    def buscar_productos(self, texto: str | None = None,
                         categoria_id: str | None = None,
                         solo_activos: bool = True,
                         limite: int = 100) -> list[dict]:
        limite = max(1, min(int(limite), 1000))

        def _op(cur):
            q = (f"SELECT {_COLS_PROD} FROM os_catalogo_productos"
                 " WHERE company_id=%s")
            args: list = [self.company_id]
            if solo_activos:
                q += " AND activo"
            if categoria_id:
                q += " AND categoria_id=%s"
                args.append(validar_id(categoria_id, "categoria_id"))
            if texto:
                q += " AND (nombre ILIKE %s OR sku ILIKE %s)"
                args += [f"%{texto}%", f"%{texto}%"]
            cur.execute(q + " ORDER BY nombre LIMIT %s", (*args, limite))
            return [_fila_producto(r) for r in cur.fetchall()]
        return _leer(_op)

    def actualizar_producto(self, producto_id: str, version_esperada: int,
                            **campos) -> dict:
        """Bloqueo optimista: version_esperada debe coincidir o lanza
        ConflictoVersion (el lector re-lee y reintenta)."""
        producto_id = validar_id(producto_id, "producto_id")
        permitidos = {"nombre", "descripcion", "categoria_id", "unidad",
                      "metadata"}
        cambios = {k: v for k, v in campos.items() if k in permitidos}
        if not cambios:
            raise ValidacionCatalogo("nada que actualizar")
        if "nombre" in cambios:
            cambios["nombre"] = validar_id(cambios["nombre"], "nombre")
        if "categoria_id" in cambios and cambios["categoria_id"] is not None:
            cambios["categoria_id"] = validar_id(
                cambios["categoria_id"], "categoria_id")

        def _op(cur):
            if cambios.get("categoria_id") is not None:
                cur.execute(
                    "SELECT 1 FROM os_catalogo_categorias"
                    " WHERE company_id=%s AND categoria_id=%s",
                    (self.company_id, cambios["categoria_id"]))
                if not cur.fetchone():
                    raise CategoriaNoExiste(
                        f"categoría inexistente: {cambios['categoria_id']!r}")
            import json
            sets, args = [], []
            for k, v in cambios.items():
                sets.append(f"{k}=%s")
                args.append(json.dumps(v) if k == "metadata" else v)
            args += [self.company_id, producto_id, int(version_esperada)]
            cur.execute(
                "UPDATE os_catalogo_productos SET " + ",".join(sets) +
                ", updated_at=now(), version=version+1"
                " WHERE company_id=%s AND producto_id=%s AND version=%s",
                args)
            if cur.rowcount == 0:
                cur.execute(
                    "SELECT version FROM os_catalogo_productos"
                    " WHERE company_id=%s AND producto_id=%s",
                    (self.company_id, producto_id))
                r = cur.fetchone()
                if r is None:
                    raise ProductoNoExiste(
                        f"producto inexistente: {producto_id!r}")
                raise ConflictoVersion(
                    f"{producto_id!r}: versión esperada {version_esperada},"
                    f" actual {r[0]}; re-lea y reintente")
            return {"ok": True, "producto_id": producto_id}
        return _mutar(self.company_id, _op)

    def fijar_activo(self, producto_id: str, activo: bool) -> dict:
        producto_id = validar_id(producto_id, "producto_id")

        def _op(cur):
            cur.execute(
                "UPDATE os_catalogo_productos SET activo=%s, updated_at=now(),"
                " version=version+1 WHERE company_id=%s AND producto_id=%s",
                (bool(activo), self.company_id, producto_id))
            if cur.rowcount == 0:
                raise ProductoNoExiste(
                    f"producto inexistente: {producto_id!r}")
            return {"ok": True, "producto_id": producto_id,
                    "activo": bool(activo)}
        return _mutar(self.company_id, _op)

    # ---- precios ----------------------------------------------------------
    def fijar_precio(self, producto_id: str, precio,
                     moneda: str = "DOP", motivo: str = "") -> dict:
        pv = PrecioVigente(self.company_id, producto_id, moneda, precio,
                           motivo=motivo)
        pv.validar()

        def _op(cur):
            _existe_producto(cur, self.company_id, pv.producto_id)
            vigente = _precio_vigente(cur, self.company_id, pv.producto_id,
                                      pv.moneda)
            costo = _costo_vigente(cur, self.company_id, pv.producto_id,
                                   pv.moneda)
            advertencias = advertencias_precio_costo(
                pv.precio, Decimal(str(costo["costo"])) if costo else None)
            _insertar_precio(cur, self.company_id, pv.producto_id, pv.moneda,
                             pv.precio, pv.motivo)
            return {"ok": True, "producto_id": pv.producto_id,
                    "moneda": pv.moneda, "precio": str(pv.precio),
                    "advertencias": advertencias}
        return _mutar(self.company_id, _op)

    def precio_vigente(self, producto_id: str,
                       moneda: str = "DOP") -> dict | None:
        producto_id = validar_id(producto_id, "producto_id")
        moneda = validar_moneda(moneda)

        def _op(cur):
            return _precio_vigente(cur, self.company_id, producto_id,
                                   moneda)
        return _leer(_op)

    def historial_precios(self, producto_id: str, moneda: str = "DOP",
                          limite: int = 50) -> list[dict]:
        producto_id = validar_id(producto_id, "producto_id")
        moneda = validar_moneda(moneda)

        def _op(cur):
            cur.execute(
                """SELECT precio, vigente_desde, vigente_hasta, motivo
                   FROM os_catalogo_precios
                   WHERE company_id=%s AND producto_id=%s AND moneda=%s
                   ORDER BY vigente_desde DESC LIMIT %s""",
                (self.company_id, producto_id, moneda,
                 max(1, min(int(limite), 500))))
            return [{"precio": str(r[0]),
                     "vigente_desde": r[1].isoformat() if r[1] else None,
                     "vigente_hasta": r[2].isoformat() if r[2] else None,
                     "motivo": r[3]} for r in cur.fetchall()]
        return _leer(_op)

    # ---- costos ------------------------------------------------------------
    def fijar_costo(self, producto_id: str, costo, moneda: str = "DOP",
                    fuente: str = "manual", motivo: str = "") -> dict:
        cv = CostoVigente(self.company_id, producto_id, moneda, costo,
                          fuente, motivo=motivo)
        cv.validar()

        def _op(cur):
            _existe_producto(cur, self.company_id, cv.producto_id)
            precio = _precio_vigente(cur, self.company_id, cv.producto_id,
                                     cv.moneda)
            advertencias = advertencias_precio_costo(
                Decimal(str(precio["precio"])) if precio else None, cv.costo)
            _insertar_costo(cur, self.company_id, cv.producto_id, cv.moneda,
                            cv.costo, cv.fuente, cv.motivo)
            return {"ok": True, "producto_id": cv.producto_id,
                    "moneda": cv.moneda, "costo": str(cv.costo),
                    "advertencias": advertencias}
        return _mutar(self.company_id, _op)

    def costo_vigente(self, producto_id: str,
                      moneda: str = "DOP") -> dict | None:
        producto_id = validar_id(producto_id, "producto_id")
        moneda = validar_moneda(moneda)

        def _op(cur):
            return _costo_vigente(cur, self.company_id, producto_id,
                                  moneda)
        return _leer(_op)

    def historial_costos(self, producto_id: str, moneda: str = "DOP",
                         limite: int = 50) -> list[dict]:
        producto_id = validar_id(producto_id, "producto_id")
        moneda = validar_moneda(moneda)

        def _op(cur):
            cur.execute(
                """SELECT costo, fuente, vigente_desde, vigente_hasta,
                          motivo
                   FROM os_catalogo_costos
                   WHERE company_id=%s AND producto_id=%s AND moneda=%s
                   ORDER BY vigente_desde DESC LIMIT %s""",
                (self.company_id, producto_id, moneda,
                 max(1, min(int(limite), 500))))
            return [{"costo": str(r[0]), "fuente": r[1],
                     "vigente_desde": r[2].isoformat() if r[2] else None,
                     "vigente_hasta": r[3].isoformat() if r[3] else None,
                     "motivo": r[4]} for r in cur.fetchall()]
        return _leer(_op)

    # ---- validación de costos ---------------------------------------------
    def validar_costos(self, moneda: str = "DOP",
                       almacenes: list[str] | None = None) -> dict:
        """Validación de costos por producto (y por almacén si hay
        stock registrado).

        Reporta por producto+moneda:
        - sin_costo_vigente: no hay costo vigente (no se puede fijar
          precio con piso confiable).
        - costo_negativo: defensa en profundidad (el CHECK de PG y
          validar_costo ya lo impiden al escribir).
        - precio_bajo_costo: precio vigente < costo vigente.
        Si `almacenes` se da (o hay stock en os_movimientos), cruza
        con stock por almacén y reporta sin_costo_con_stock.
        """
        moneda = validar_moneda(moneda)
        almacenes = ([validar_id(a, "almacen_id") for a in almacenes]
                     if almacenes else None)

        def _op(cur):
            cur.execute(
                f"""SELECT {_COLS_PROD} FROM os_catalogo_productos
                    WHERE company_id=%s AND activo""",
                (self.company_id,))
            productos = [_fila_producto(r) for r in cur.fetchall()]
            hallazgos: list[dict] = []
            for p in productos:
                pid = p["producto_id"]
                costo = _costo_vigente(cur, self.company_id, pid, moneda)
                precio = _precio_vigente(cur, self.company_id, pid, moneda)
                base = {"producto_id": pid, "sku": p["sku"],
                        "moneda": moneda}
                if costo is None:
                    hallazgos.append({**base,
                                      "problema": "sin_costo_vigente",
                                      "detalle": "no hay costo vigente; "
                                      "el piso de precio no es confiable"})
                else:
                    c = Decimal(str(costo["costo"]))
                    if c < 0:
                        hallazgos.append({**base,
                                          "problema": "costo_negativo",
                                          "detalle": f"costo={c}"})
                    if precio is not None:
                        pr = Decimal(str(precio["precio"]))
                        if pr < c:
                            hallazgos.append({**base,
                                              "problema": "precio_bajo_costo",
                                              "detalle": f"precio={pr} < "
                                              f"costo={c}"})
            # Dimensión almacén: productos con stock pero sin costo.
            stock = self._stock_por_almacen(cur, almacenes)
            sin_costo = {h["producto_id"] for h in hallazgos
                         if h["problema"] == "sin_costo_vigente"}
            for (pid, alm), s in sorted(stock.items()):
                if s > 0 and pid in sin_costo:
                    hallazgos.append(
                        {"producto_id": pid, "moneda": moneda,
                         "almacen_id": alm,
                         "problema": "sin_costo_con_stock",
                         "detalle": f"stock={s} sin costo vigente"})
            return {"moneda": moneda,
                    "productos_evaluados": len(productos),
                    "hallazgos": hallazgos}
        return _leer(_op)

    def _stock_por_almacen(self, cur, almacenes):
        """{(producto_id, almacen_id): stock} desde os_movimientos.

        Tolerante: si la tabla no existe (o no hay filas), devuelve {}.
        """
        try:
            q = ("""SELECT producto_id, almacen_id,
                           COALESCE(SUM(cantidad),0)
                    FROM os_movimientos WHERE company_id=%s""")
            args: list = [self.company_id]
            if almacenes:
                q += " AND almacen_id = ANY(%s)"
                args.append(almacenes)
            q += " GROUP BY producto_id, almacen_id"
            cur.execute(q, args)
            return {(r[0], r[1]): Decimal(str(r[2]))
                    for r in cur.fetchall()}
        except Exception:
            return {}
    def hay_datos(self) -> bool:
        """True si la empresa tiene al menos un producto en el catálogo
        (lo usan los motores para decidir catálogo vs fallback)."""
        def _op(cur):
            cur.execute(
                "SELECT 1 FROM os_catalogo_productos"
                " WHERE company_id=%s LIMIT 1", (self.company_id,))
            return cur.fetchone() is not None
        try:
            return _leer(_op)
        except Exception:
            return False

    def resumen(self) -> dict:
        def _op(cur):
            cur.execute(
                "SELECT COUNT(*), COUNT(*) FILTER (WHERE activo)"
                " FROM os_catalogo_productos WHERE company_id=%s",
                (self.company_id,))
            np, npa = cur.fetchone()
            cur.execute(
                "SELECT COUNT(*) FROM os_catalogo_categorias"
                " WHERE company_id=%s", (self.company_id,))
            nc = cur.fetchone()[0]
            cur.execute(
                "SELECT COUNT(*) FROM os_almacenes WHERE company_id=%s",
                (self.company_id,))
            na = cur.fetchone()[0]
            return {"productos": np, "productos_activos": npa,
                    "categorias": nc, "almacenes": na}
        return _leer(_op)


# ---- helpers internos (operan con el cursor de la transacción) ------------
def _existe_producto(cur, company_id: str, producto_id: str):
    cur.execute(
        "SELECT 1 FROM os_catalogo_productos"
        " WHERE company_id=%s AND producto_id=%s",
        (company_id, producto_id))
    if not cur.fetchone():
        raise ProductoNoExiste(f"producto inexistente: {producto_id!r}")


def _precio_vigente(cur, company_id: str, producto_id: str,
                    moneda: str) -> dict | None:
    cur.execute(
        """SELECT precio, vigente_desde, motivo FROM os_catalogo_precios
           WHERE company_id=%s AND producto_id=%s AND moneda=%s
             AND vigente_hasta IS NULL
           ORDER BY vigente_desde DESC LIMIT 1""",
        (company_id, producto_id, moneda))
    r = cur.fetchone()
    return ({"precio": str(r[0]),
             "vigente_desde": r[1].isoformat() if r[1] else None,
             "motivo": r[2]} if r else None)


def _costo_vigente(cur, company_id: str, producto_id: str,
                   moneda: str) -> dict | None:
    cur.execute(
        """SELECT costo, fuente, vigente_desde, motivo
           FROM os_catalogo_costos
           WHERE company_id=%s AND producto_id=%s AND moneda=%s
             AND vigente_hasta IS NULL
           ORDER BY vigente_desde DESC LIMIT 1""",
        (company_id, producto_id, moneda))
    r = cur.fetchone()
    return ({"costo": str(r[0]), "fuente": r[1],
             "vigente_desde": r[2].isoformat() if r[2] else None,
             "motivo": r[3]} if r else None)


def _insertar_precio(cur, company_id: str, producto_id: str, moneda: str,
                     precio: Decimal, motivo: str):
    # Cierra el vigente anterior en la MISMA transacción (el índice único
    # parcial impide dos vigentes simultáneos aunque algo falle).
    cur.execute(
        """UPDATE os_catalogo_precios SET vigente_hasta=now()
           WHERE company_id=%s AND producto_id=%s AND moneda=%s
             AND vigente_hasta IS NULL""",
        (company_id, producto_id, moneda))
    cur.execute(
        """INSERT INTO os_catalogo_precios
           (company_id, producto_id, moneda, precio, motivo)
           VALUES (%s,%s,%s,%s,%s)""",
        (company_id, producto_id, moneda, str(precio), motivo or ""))


def _insertar_costo(cur, company_id: str, producto_id: str, moneda: str,
                    costo: Decimal, fuente: str, motivo: str):
    cur.execute(
        """UPDATE os_catalogo_costos SET vigente_hasta=now()
           WHERE company_id=%s AND producto_id=%s AND moneda=%s
             AND vigente_hasta IS NULL""",
        (company_id, producto_id, moneda))
    cur.execute(
        """INSERT INTO os_catalogo_costos
           (company_id, producto_id, moneda, costo, fuente, motivo)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (company_id, producto_id, moneda, str(costo), fuente,
         motivo or ""))
