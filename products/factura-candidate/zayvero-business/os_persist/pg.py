"""LOTE 9A — Persistencia PostgreSQL local del Business OS.

- `migrate(conn)`: aplica schema/*.sql de forma idempotente, en el orden
  del manifiesto schema/_orden.txt; registra (archivo, orden) en
  os_migrations y rellena el orden de filas históricas sin re-aplicar.
- `OSPgStore`: repositorios por entidad, siempre con company_id.
- Idempotencia: PRIMARY KEY (company_id, id) + ON CONFLICT DO NOTHING.
- Transacciones: recibir/venta/cobro escriben todas sus tablas en UNA
  transacción (todo o nada). Sin PG activo, los motores siguen en memoria.
- DSN por variable ZAYVERO_OS_DATABASE_URL (local). Nunca en código.

No comprar, no desplegar: esto es LOCAL hasta la evaluación del jueves.
"""
from __future__ import annotations

import os
import sys
from contextlib import contextmanager
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SCHEMA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "schema")
MANIFEST = "_orden.txt"
DSN_ENV = "ZAYVERO_OS_DATABASE_URL"


def connect(dsn: str | None = None):
    import psycopg
    dsn = dsn or os.environ.get(DSN_ENV)
    if not dsn:
        raise RuntimeError(f"defina {DSN_ENV} (local, sin PG pagado)")
    return psycopg.connect(dsn)


def _orden_migraciones() -> list[str]:
    """Orden determinista de aplicación.

    Fuente: os_persist/schema/_orden.txt (manifiesto explícito). Si el
    manifiesto no existe, se usa el orden alfabético (compatibilidad
    hacia atrás). Con manifiesto, cualquier .sql fuera del manifiesto o
    cualquier línea del manifiesto sin archivo correspondiente es un
    ERROR DURO: fallar a gritos es mejor que aplicar en orden silencioso
    equivocado (lección del duplicado 008_*, ver docs/migraciones.md).
    """
    ruta = os.path.join(SCHEMA_DIR, MANIFEST)
    archivos = [f for f in os.listdir(SCHEMA_DIR) if f.endswith(".sql")]
    if not os.path.exists(ruta):
        return sorted(archivos)
    with open(ruta, encoding="utf-8") as fh:
        orden = [l.strip() for l in fh if l.strip()
                 and not l.strip().startswith("#")]
    if len(set(orden)) != len(orden):
        dup = sorted({x for x in orden if orden.count(x) > 1})
        raise RuntimeError(f"manifiesto {MANIFEST} con duplicados: {dup}")
    faltan = [f for f in archivos if f not in orden]
    if faltan:
        raise RuntimeError(
            f"migraciones sin registrar en {MANIFEST}: {sorted(faltan)}; "
            "agrégalas al final del manifiesto antes de migrar")
    sobran = [f for f in orden if f not in archivos]
    if sobran:
        raise RuntimeError(
            f"{MANIFEST} lista archivos inexistentes: {sobran}")
    return orden


def migrate(conn) -> list[str]:
    """Aplica migraciones idempotentes en el orden del manifiesto.

    Devuelve los archivos aplicados en ESTA corrida ([] = nada pendiente).

    Reconciliación del duplicado 008 (issue #3, ver docs/migraciones.md):
    - NO se renombra ningún archivo: renombrar haría que el migrador
      re-aplicara la migración vieja con el nombre nuevo.
    - os_migrations gana la columna `orden` (posición en el manifiesto).
      El migrador la crea si falta y rellena (backfill) el orden de las
      filas históricas — incluidas las dos 008 ya aplicadas — SIN
      re-ejecutar ningún .sql.
    """
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS os_migrations (
                       archivo TEXT PRIMARY KEY,
                       aplicada_at TIMESTAMPTZ NOT NULL DEFAULT now())""")
    # Reconciliación de metadatos: la columna puede no existir en BDs
    # creadas antes de este cambio; ADD COLUMN IF NOT EXISTS la crea.
    cur.execute("ALTER TABLE os_migrations "
                "ADD COLUMN IF NOT EXISTS orden INTEGER")
    ordenados = _orden_migraciones()
    # Backfill idempotente: solo toca filas sin orden (las históricas).
    for i, nombre in enumerate(ordenados, start=1):
        cur.execute("UPDATE os_migrations SET orden=%s"
                    " WHERE archivo=%s AND orden IS NULL", (i, nombre))
    aplicadas = {r[0] for r in cur.execute("SELECT archivo FROM os_migrations")}
    hechas = []
    for i, nombre in enumerate(ordenados, start=1):
        if nombre in aplicadas:
            continue
        with open(os.path.join(SCHEMA_DIR, nombre), encoding="utf-8") as fh:
            cur.execute(fh.read())
        cur.execute("INSERT INTO os_migrations (archivo, orden)"
                    " VALUES (%s,%s)", (nombre, i))
        hechas.append(nombre)
    conn.commit()
    return hechas


@contextmanager
def tx(conn):
    """Transacción todo-o-nada."""
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


class OSPgStore:
    """Repositorios con company_id obligatorio en cada escritura."""

    def __init__(self, conn):
        self.c = conn

    # -- empresas ------------------------------------------------------
    def asegurar_empresa(self, company_id: str, nombre: str = ""):
        self.c.execute(
            "INSERT INTO os_companies (company_id, nombre) VALUES (%s,%s)"
            " ON CONFLICT (company_id) DO NOTHING", (company_id, nombre))

    # -- inventario ----------------------------------------------------
    def guardar_movimiento(self, company_id: str, mov: dict) -> bool:
        """Idempotente por (company_id, movimiento_id). True si insertó."""
        cur = self.c.execute(
            """INSERT INTO os_movimientos
               (movimiento_id, company_id, producto_id, almacen_id, tipo,
                cantidad, fecha, referencia)
               VALUES (%(movimiento_id)s,%(company_id)s,%(producto_id)s,
                       %(almacen_id)s,%(tipo)s,%(cantidad)s,%(fecha)s,
                       %(referencia)s)
               ON CONFLICT (company_id, movimiento_id) DO NOTHING""",
            {"company_id": company_id, **mov})
        return cur.rowcount == 1

    def stock(self, company_id: str, producto_id: str,
              almacen_id: str = "principal") -> Decimal:
        row = self.c.execute(
            """SELECT COALESCE(SUM(cantidad),0) FROM os_movimientos
               WHERE company_id=%s AND producto_id=%s AND almacen_id=%s""",
            (company_id, producto_id, almacen_id)).fetchone()
        return Decimal(str(row[0]))

    # -- compras -------------------------------------------------------
    def guardar_orden(self, company_id: str, orden: dict) -> bool:
        import json
        cur = self.c.execute(
            """INSERT INTO os_ordenes
               (compra_id, company_id, proveedor_id, moneda, estado, lineas,
                fecha_emision, fecha_entrega_esperada)
               VALUES (%(compra_id)s,%(company_id)s,%(proveedor_id)s,
                       %(moneda)s,%(estado)s,%(lineas)s,%(fecha_emision)s,
                       %(fecha_entrega_esperada)s)
               ON CONFLICT (company_id, compra_id) DO NOTHING""",
            {"company_id": company_id,
             "lineas": json.dumps(orden["lineas"]), **{
                 k: v for k, v in orden.items() if k != "lineas"}})
        return cur.rowcount == 1

    def guardar_recepcion_tx(self, company_id: str, recep: dict,
                             movimientos: list[dict],
                             nuevo_estado_orden: str) -> bool:
        """Recepción + movimientos ENTRADA + estado de orden: una transacción."""
        import json
        with tx(self.c):
            cur = self.c.execute(
                """INSERT INTO os_recepciones
                   (recepcion_id, company_id, compra_id, almacen_id, lineas,
                    fecha, factura_ref)
                   VALUES (%(recepcion_id)s,%(company_id)s,%(compra_id)s,
                           %(almacen_id)s,%(lineas)s,%(fecha)s,%(factura_ref)s)
                   ON CONFLICT (company_id, recepcion_id) DO NOTHING""",
                {"company_id": company_id,
                 "lineas": json.dumps(recep["lineas"]), **{
                     k: v for k, v in recep.items() if k != "lineas"}})
            if cur.rowcount == 0:
                return False  # ya existía: nada que hacer
            for m in movimientos:
                self.guardar_movimiento(company_id, m)
            self.c.execute(
                "UPDATE os_ordenes SET estado=%s"
                " WHERE company_id=%s AND compra_id=%s",
                (nuevo_estado_orden, company_id, recep["compra_id"]))
        return True

    # -- ventas --------------------------------------------------------
    def guardar_venta_tx(self, company_id: str, venta: dict,
                         movimientos: list[dict],
                         cuenta: dict | None) -> bool:
        """Venta + SALIDAs + CxC opcional: una transacción (1 sola vez)."""
        import json
        with tx(self.c):
            cur = self.c.execute(
                """INSERT INTO os_ventas
                   (venta_id, company_id, cliente_id, moneda, fecha, lineas,
                    total, estado, a_credito)
                   VALUES (%(venta_id)s,%(company_id)s,%(cliente_id)s,
                           %(moneda)s,%(fecha)s,%(lineas)s,%(total)s,
                           %(estado)s,%(a_credito)s)
                   ON CONFLICT (company_id, venta_id) DO NOTHING""",
                {"company_id": company_id,
                 "lineas": json.dumps(venta["lineas"]), **{
                     k: v for k, v in venta.items() if k != "lineas"}})
            if cur.rowcount == 0:
                return False
            for m in movimientos:
                self.guardar_movimiento(company_id, m)
            if cuenta:
                self.guardar_cuenta(company_id, cuenta)
        return True

    # -- finanzas ------------------------------------------------------
    def guardar_gasto(self, company_id: str, gasto: dict) -> bool:
        cur = self.c.execute(
            """INSERT INTO os_gastos
               (gasto_id, company_id, categoria, monto, moneda, fecha,
                proveedor_id)
               VALUES (%(gasto_id)s,%(company_id)s,%(categoria)s,%(monto)s,
                       %(moneda)s,%(fecha)s,%(proveedor_id)s)
               ON CONFLICT (company_id, gasto_id) DO NOTHING""",
            {"company_id": company_id, **gasto})
        return cur.rowcount == 1

    def guardar_cuenta(self, company_id: str, cuenta: dict) -> bool:
        cur = self.c.execute(
            """INSERT INTO os_cuentas
               (cuenta_id, company_id, tipo, tercero_id, monto, moneda,
                pagado, vencimiento, ref_id)
               VALUES (%(cuenta_id)s,%(company_id)s,%(tipo)s,%(tercero_id)s,
                       %(monto)s,%(moneda)s,%(pagado)s,%(vencimiento)s,
                       %(ref_id)s)
               ON CONFLICT (company_id, cuenta_id) DO NOTHING""",
            {"company_id": company_id, **cuenta})
        return cur.rowcount == 1

    def registrar_cobro_tx(self, company_id: str, cuenta_id: str,
                           monto: str, moneda: str) -> Decimal:
        """Suma al pagado y al saldo: una transacción. Devuelve el pendiente."""
        with tx(self.c):
            self.c.execute(
                "UPDATE os_cuentas SET pagado = pagado + %s"
                " WHERE company_id=%s AND cuenta_id=%s AND moneda=%s",
                (monto, company_id, cuenta_id, moneda))
            self.c.execute(
                """INSERT INTO os_saldos (company_id, moneda, saldo)
                   VALUES (%s,%s,%s)
                   ON CONFLICT (company_id, moneda) DO UPDATE
                   SET saldo = os_saldos.saldo + EXCLUDED.saldo,
                       updated_at = now()""",
                (company_id, moneda, monto))
            row = self.c.execute(
                "SELECT monto - pagado FROM os_cuentas"
                " WHERE company_id=%s AND cuenta_id=%s",
                (company_id, cuenta_id)).fetchone()
        return Decimal(str(row[0]))

    def saldo(self, company_id: str, moneda: str) -> Decimal:
        row = self.c.execute(
            "SELECT saldo FROM os_saldos WHERE company_id=%s AND moneda=%s",
            (company_id, moneda)).fetchone()
        return Decimal(str(row[0])) if row else Decimal("0")

    # -- CRM -----------------------------------------------------------
    def guardar_cliente(self, company_id: str, cliente: dict) -> bool:
        cur = self.c.execute(
            """INSERT INTO os_clientes
               (cliente_id, company_id, nombre, email, telefono)
               VALUES (%(cliente_id)s,%(company_id)s,%(nombre)s,%(email)s,
                       %(telefono)s)
               ON CONFLICT (company_id, cliente_id) DO NOTHING""",
            {"company_id": company_id, **cliente})
        return cur.rowcount == 1

    # -- documentos ----------------------------------------------------
    def guardar_documento(self, company_id: str, doc: dict) -> bool:
        """Idempotente por documento_id y por (proveedor, numero)."""
        import json
        try:
            cur = self.c.execute(
                """INSERT INTO os_documentos
                   (documento_id, company_id, numero, proveedor, fecha,
                    moneda, total, formato, extraccion)
                   VALUES (%(documento_id)s,%(company_id)s,%(numero)s,
                           %(proveedor)s,%(fecha)s,%(moneda)s,%(total)s,
                           %(formato)s,%(extraccion)s)
                   ON CONFLICT (company_id, documento_id) DO NOTHING""",
                {"company_id": company_id,
                 "extraccion": json.dumps(doc["extraccion"]), **{
                     k: v for k, v in doc.items() if k != "extraccion"}})
            return cur.rowcount == 1
        except Exception as e:
            self.c.rollback()
            if "ux_os_doc_proveedor_numero" in str(e):
                return False  # factura duplicada
            raise
