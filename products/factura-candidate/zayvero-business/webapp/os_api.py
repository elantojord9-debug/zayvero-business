"""LOTE 8 — APIs v1 del Business OS integradas en webapp/.

- Un bundle de motores por empresa (en memoria; ver advertencia demo).
- company_id SIEMPRE del contexto de sesión (anti-IDOR): el cuerpo nunca
  puede elegir empresa.
- Lectura -> permiso "os.read"; escritura -> "os.write" + CSRF.
- Archivos validados: PDF (%PDF, <=5MB), CSV (parseable, <=1MB).
- Idempotencia: venta_id / compra_id / recepcion_id / documento_id únicos.

ADVERTENCIA: sin PostgreSQL activo, los datos son de demostración y NO
persistentes (se pierden al reiniciar). El panel lo muestra siempre.
"""
from __future__ import annotations

import os
import sys
import threading
from dataclasses import dataclass, field

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from os_inventory.engine import InventoryEngine
from os_compras.ordenes import OrdenCompra, LineaOC, EstadoOC
from os_compras.recepciones import ComprasService, Recepcion, LineaRecepcion
from os_finanzas.costo import CostoInventario
from os_finanzas.profit import LibroFinanzas
from os_finanzas.tesoreria import Tesoreria
from os_finanzas.gastos import RegistroGastos
from os_crm.clientes import CRM
from os_crm.ventas import VentasService
from os_crm.cobranzas import Cobranzas
from os_docs.documentos import RegistroDocumentos
from os_docs.contador import MesaContador
from oschema.models import Cliente

ADVERTENCIA_DEMO = ("Datos de demostración: no persistentes. "
                    "Se pierden al reiniciar el servidor.")


@dataclass
class OSBundle:
    company_id: str
    inventario: InventoryEngine = None
    compras: ComprasService = None
    costo: CostoInventario = None
    libro: LibroFinanzas = None
    tesoreria: Tesoreria = None
    gastos: RegistroGastos = None
    crm: CRM = None
    ventas: VentasService = None
    cobranzas: Cobranzas = None
    documentos: RegistroDocumentos = None
    mesa: MesaContador = None
    demo_cargada: bool = False
    # BLOQUE 1 (2026-10-12): auditoría en modo memoria (sin PG). Lista
    # acotada de eventos (ver os_persist/auditoria.py); NO es duradera y
    # no participa en snapshots. En PG los eventos viven en os_eventos.
    eventos_memoria: list = field(default_factory=list)

    @classmethod
    def crear(cls, company_id: str) -> "OSBundle":
        inv = InventoryEngine(company_id)
        costo = CostoInventario()
        gastos = RegistroGastos(company_id)
        # INTEGRIDAD FINANCIERA (2026-10-13): el libro lee los gastos
        # operativos DIRECTAMENTE del RegistroGastos (única fuente de
        # verdad). Registrar en bundle.gastos se refleja en
        # bundle.libro.rentabilidad() sin duplicar (idempotencia por
        # gasto_id). Ver os_finanzas/profit.py.
        libro = LibroFinanzas(company_id, costo, gastos=gastos)
        teso = Tesoreria(company_id)
        crm = CRM(company_id)
        ventas = VentasService(company_id, inv, libro, teso, crm)
        compras = ComprasService(company_id, inv)
        return cls(company_id=company_id, inventario=inv, compras=compras,
                   costo=costo, libro=libro, tesoreria=teso, gastos=gastos,
                   crm=crm, ventas=ventas, cobranzas=Cobranzas(teso),
                   documentos=RegistroDocumentos(company_id),
                   mesa=MesaContador(teso))


_BUNDLES: dict[str, OSBundle] = {}
_LOCK = threading.RLock()
_PG_CONN = None  # conexión persistente al backend PG (Lote 9B)


def backend_activo() -> str:
    """'postgres' si ZAYVERO_OS_BACKEND=postgres, si no 'memoria'."""
    return os.environ.get("ZAYVERO_OS_BACKEND", "memoria")


def _pg():
    """Conexión PG perezosa; None si el backend no es postgres."""
    global _PG_CONN
    if backend_activo() != "postgres":
        return None
    if _PG_CONN is None or _PG_CONN.closed:
        from os_persist.pg import connect, migrate
        _PG_CONN = connect()
        _PG_CONN.autocommit = True
        # P0 PRE-PR (issue #8): en staging/producción el runtime NO hace DDL;
        # las migraciones las aplica un rol acotado en predeploy (el preflight
        # las verifica). Solo local conserva auto-migrate.
        from webapp.preflight import auto_migrate_permitido
        if auto_migrate_permitido():
            migrate(_PG_CONN)
        # REFORZAMIENTO: las lecturas (hidratación) no necesitan
        # transacción; autocommit evita "idle in transaction" que
        # bloquea DDL y retiene locks.
    return _PG_CONN


def get_bundle(company_id: str) -> OSBundle:
    with _LOCK:
        b = _BUNDLES.get(company_id)
        if b is None:
            b = OSBundle.crear(company_id)
            # LOTE 9B: hidratar desde PG (fuente de verdad)
            conn = _pg()
            if conn is not None:
                from os_persist.snapshots import cargar_snapshot
                cargar_snapshot(conn, company_id, b)
            _BUNDLES[company_id] = b
        return b


def persistir_bundle(company_id: str):
    """Write-through: snapshot a PG en UNA transacción. Sin PG: no-op."""
    conn = _pg()
    if conn is None:
        return
    with _LOCK:
        b = _BUNDLES.get(company_id)
        if b is None:
            return
        from os_persist.snapshots import guardar_snapshot
        try:
            guardar_snapshot(conn, company_id, b)
        except Exception:
            conn.rollback()
            raise


def cargar_demo(bundle: OSBundle):
    """Flujo sintético completo: compra->recepción->inventario->venta->
    costo->rentabilidad->cobranza->tesorería->score."""
    from os_crm.ventas import SolicitudVenta
    from oschema.models import Gasto
    from os_finanzas.gastos import GastoDetallado

    if bundle.demo_cargada:
        return {"ya_cargada": True}
    c = bundle.company_id

    oc = OrdenCompra(compra_id="OC-DEMO-1", company_id=c,
                     proveedor_id="distribuidora-demo", moneda="DOP",
                     lineas=[LineaOC("taladro-12v", "50", "800"),
                             LineaOC("cemento-42kg", "200", "450")],
                     fecha_emision="2026-10-01T09:00:00Z",
                     fecha_entrega_esperada="2026-10-05T09:00:00Z")
    oc.validate()
    bundle.compras.crear_orden(oc)
    oc.cambiar_estado(EstadoOC.ENVIADA)
    bundle.compras.recibir(Recepcion(
        recepcion_id="R-DEMO-1", company_id=c, compra_id="OC-DEMO-1",
        almacen_id="principal",
        lineas=[LineaRecepcion("taladro-12v", "50"),
                LineaRecepcion("cemento-42kg", "200")],
        fecha="2026-10-03T10:00:00Z"))
    bundle.costo.aplicar_entrada("taladro-12v", "principal", "50", "800", "DOP")
    bundle.costo.aplicar_entrada("cemento-42kg", "principal", "200", "450", "DOP")

    bundle.crm.registrar_cliente(Cliente(
        cliente_id="CLI-DEMO-1", company_id=c, nombre="Ferretería Demo",
        telefono="809-555-0101"))
    bundle.ventas.vender(SolicitudVenta(
        venta_id="V-DEMO-1", company_id=c, cliente_id="CLI-DEMO-1",
        lineas=[{"producto_id": "taladro-12v", "cantidad": "10",
                 "precio_unitario": "1200", "almacen_id": "principal"}],
        moneda="DOP", fecha="2026-10-05T10:00:00Z", a_credito=True,
        dias_credito=30, sucursal_id="principal"))
    bundle.ventas.vender(SolicitudVenta(
        venta_id="V-DEMO-2", company_id=c, cliente_id="CLI-DEMO-1",
        lineas=[{"producto_id": "cemento-42kg", "cantidad": "20",
                 "precio_unitario": "600", "almacen_id": "principal"}],
        moneda="DOP", fecha="2026-10-06T10:00:00Z", a_credito=False,
        sucursal_id="principal"))
    bundle.gastos.registrar(GastoDetallado(
        Gasto(gasto_id="G-DEMO-1", company_id=c, categoria="alquiler",
              monto="25000", moneda="DOP", fecha="2026-10-01T10:00:00Z",
              proveedor_id="propietario")))
    bundle.tesoreria.fijar_saldo_inicial("DOP", "100000")
    bundle.tesoreria.registrar_cobro("cxc-V-DEMO-1", "5000")
    bundle.demo_cargada = True
    return {"ya_cargada": False, "advertencia": ADVERTENCIA_DEMO}


def resumen_estado(bundle: OSBundle) -> dict:
    persistente = backend_activo() == "postgres"
    return {"demo_cargada": bundle.demo_cargada,
            "persistente": persistente,
            "advertencia": (ADVERTENCIA_DEMO if not persistente
                            else "Datos sintéticos de demostración. "
                                 "Persistentes en PostgreSQL local."),
            "modulos": ["inventario", "compras", "ventas", "crm", "finanzas",
                        "tesoreria", "documentos", "contador", "score"]}
