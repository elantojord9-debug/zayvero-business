"""ZAYVERO Business OS — LOTE 3: Inventario Inteligente.

Motor profesional de inventario sobre oschema/. Reglas inviolables:
- El stock se DERIVA de movimientos válidos; nunca se guarda un "stock actual".
- Cantidades Decimal; moneda ISO 4217 (heredado de oschema).
- Idempotencia por movimiento_id: re-registrar el mismo ID no duplica.
- Aislamiento: el engine pertenece a UNA empresa; rechaza movimientos ajenos.
- Stock negativo bloqueado por defecto (configurable por empresa).
"""
from __future__ import annotations

import sys
import os
import threading
from decimal import Decimal

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from oschema.contract import TipoMovimiento  # noqa: E402
from oschema.models import MovimientoInventario, _dec  # noqa: E402

# Signo exigido por tipo de movimiento
_SIGNO_POSITIVO = {TipoMovimiento.ENTRADA, TipoMovimiento.TRANSFERENCIA_IN,
                   TipoMovimiento.DEVOLUCION_IN}
_SIGNO_NEGATIVO = {TipoMovimiento.SALIDA, TipoMovimiento.TRANSFERENCIA_OUT,
                    TipoMovimiento.DEVOLUCION_OUT}


class ErrorInventario(Exception):
    """Base de errores del motor."""


class MovimientoDuplicado(ErrorInventario):
    pass


class EmpresaAjena(ErrorInventario):
    pass


class SignoInvalido(ErrorInventario):
    pass


class StockNegativo(ErrorInventario):
    pass


class FechaInvalida(ErrorInventario):
    pass


class InventoryEngine:
    """Motor de inventario de UNA empresa.

    Uso:
        eng = InventoryEngine(company_id="emp-a")
        eng.registrar(mov)                      # True=nuevo, lanza MovimientoDuplicado si existe
        eng.registrar_idempotente(mov)          # True=nuevo, False=ya existía (no duplica)
        eng.stock("prod-1", "alm-1")            # Decimal derivado
        eng.transferir("prod-1", "alm-1", "alm-2", "10", motivo="reposición")
    """

    def __init__(self, company_id: str, permitir_negativo: bool = False,
                 reloj=None, catalogo=None):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        self.company_id = company_id
        self.permitir_negativo = permitir_negativo
        self._reloj = reloj
        # BLOQUE 5 (2026-10-12) — Catálogo maestro (opcional): si se
        # entrega un `catalogo.api.Catalogo` de LA MISMA empresa, el
        # engine puede enriquecer lecturas con la ficha maestra
        # (nombre, sku, categoría, unidad). None = comportamiento
        # anterior, sin cambios. Ver docs/catalogo.md.
        if catalogo is not None and (
                getattr(catalogo, "company_id", None) != company_id):
            raise EmpresaAjena(
                "catálogo de otra empresa en engine de " + company_id)
        self._catalogo = catalogo
        self._lock = threading.RLock()
        self._movs: dict[str, MovimientoInventario] = {}
        self._niveles: dict[tuple[str, str], Decimal] = {}

    # ---- registro ---------------------------------------------------
    def _validar(self, mov: MovimientoInventario):
        mov.validate()
        if mov.company_id != self.company_id:
            raise EmpresaAjena(
                f"movimiento de {mov.company_id} en engine de {self.company_id}")
        cant = _dec(mov.cantidad)
        if mov.tipo in _SIGNO_POSITIVO and cant <= 0:
            raise SignoInvalido(f"{mov.tipo} exige cantidad > 0 (recibido {cant})")
        if mov.tipo in _SIGNO_NEGATIVO and cant >= 0:
            raise SignoInvalido(f"{mov.tipo} exige cantidad < 0 (recibido {cant})")
        if mov.tipo == TipoMovimiento.AJUSTE and cant == 0:
            raise SignoInvalido("ajuste con cantidad 0 no tiene efecto")
        if mov.fecha:
            from datetime import datetime
            try:
                datetime.strptime(mov.fecha[:19], "%Y-%m-%dT%H:%M:%S")
            except ValueError:
                raise FechaInvalida(f"fecha inválida: {mov.fecha}")

    def _proyectar(self, mov: MovimientoInventario) -> Decimal:
        key = (mov.producto_id, mov.almacen_id)
        actual = self._stock_sin_lock(key)
        return actual + _dec(mov.cantidad)

    def _stock_sin_lock(self, key: tuple[str, str]) -> Decimal:
        tot = Decimal("0")
        for m in self._movs.values():
            if (m.producto_id, m.almacen_id) == key:
                tot += _dec(m.cantidad)
        return tot

    def registrar(self, mov: MovimientoInventario) -> bool:
        """Registra un movimiento. True si es nuevo; lanza MovimientoDuplicado
        si el ID ya existe (idempotencia estricta)."""
        with self._lock:
            self._validar(mov)
            if mov.movimiento_id in self._movs:
                raise MovimientoDuplicado(mov.movimiento_id)
            if not self.permitir_negativo and self._proyectar(mov) < 0:
                raise StockNegativo(
                    f"{mov.producto_id} en {mov.almacen_id} quedaría en "
                    f"{self._proyectar(mov)}")
            self._movs[mov.movimiento_id] = mov
            return True

    def registrar_idempotente(self, mov: MovimientoInventario) -> bool:
        """Como registrar(), pero el duplicado se ignora (False) en vez de lanzar."""
        try:
            return self.registrar(mov)
        except MovimientoDuplicado:
            return False

    # ---- operaciones de alto nivel ----------------------------------
    def _nuevo_mov(self, tipo: str, producto_id: str, almacen_id: str,
                   cantidad: str, **kw) -> MovimientoInventario:
        import uuid
        fecha = kw.pop("fecha", None) or (
            self._reloj.ahora_iso() if self._reloj else
            __import__("datetime").datetime.now(
                __import__("datetime").timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
        return MovimientoInventario(
            movimiento_id=kw.pop("movimiento_id", f"mov-{uuid.uuid4().hex[:12]}"),
            company_id=self.company_id, producto_id=producto_id,
            almacen_id=almacen_id, tipo=tipo, cantidad=str(cantidad),
            fecha=fecha, **kw)

    def entrada(self, producto_id: str, almacen_id: str, cantidad: str,
                **kw) -> str:
        m = self._nuevo_mov(TipoMovimiento.ENTRADA, producto_id, almacen_id,
                            cantidad, **kw)
        self.registrar(m)
        return m.movimiento_id

    def salida(self, producto_id: str, almacen_id: str, cantidad: str,
               **kw) -> str:
        """cantidad en positivo; el engine aplica el signo negativo."""
        m = self._nuevo_mov(TipoMovimiento.SALIDA, producto_id, almacen_id,
                            f"-{abs(_dec(cantidad))}", **kw)
        self.registrar(m)
        return m.movimiento_id

    def ajuste(self, producto_id: str, almacen_id: str, cantidad: str,
               **kw) -> str:
        m = self._nuevo_mov(TipoMovimiento.AJUSTE, producto_id, almacen_id,
                            cantidad, **kw)
        self.registrar(m)
        return m.movimiento_id

    def transferir(self, producto_id: str, origen_id: str, destino_id: str,
                   cantidad: str, **kw) -> tuple[str, str]:
        """Transferencia atómica entre almacenes: OUT + IN con misma referencia.

        Si el OUT falla (p. ej. stock insuficiente), el IN no se crea.
        """
        import uuid
        grupo = kw.pop("referencia_id", f"trf-{uuid.uuid4().hex[:12]}")
        cant = abs(_dec(cantidad))
        if cant <= 0:
            raise SignoInvalido("la transferencia exige cantidad > 0")
        with self._lock:
            out = self._nuevo_mov(TipoMovimiento.TRANSFERENCIA_OUT, producto_id,
                                  origen_id, f"-{cant}", referencia_id=grupo,
                                  motivo=kw.pop("motivo", f"transferencia {grupo}"),
                                  **kw)
            self.registrar(out)  # si falla, no se crea el IN
            inn = self._nuevo_mov(TipoMovimiento.TRANSFERENCIA_IN, producto_id,
                                  destino_id, str(cant), referencia_id=grupo,
                                  motivo=f"transferencia {grupo}", **kw)
            self.registrar(inn)
            return out.movimiento_id, inn.movimiento_id

    def devolucion_cliente(self, producto_id: str, almacen_id: str,
                           cantidad: str, **kw) -> str:
        m = self._nuevo_mov(TipoMovimiento.DEVOLUCION_IN, producto_id, almacen_id,
                            cantidad, **kw)
        self.registrar(m)
        return m.movimiento_id

    def devolucion_proveedor(self, producto_id: str, almacen_id: str,
                             cantidad: str, **kw) -> str:
        m = self._nuevo_mov(TipoMovimiento.DEVOLUCION_OUT, producto_id, almacen_id,
                            f"-{abs(_dec(cantidad))}", **kw)
        self.registrar(m)
        return m.movimiento_id

    # ---- consultas ---------------------------------------------------
    def stock(self, producto_id: str, almacen_id: str) -> Decimal:
        with self._lock:
            return self._stock_sin_lock((producto_id, almacen_id))

    def stock_empresa(self) -> dict[tuple[str, str], Decimal]:
        with self._lock:
            keys = {(m.producto_id, m.almacen_id) for m in self._movs.values()}
            return {k: self._stock_sin_lock(k) for k in keys}

    def movimientos(self, producto_id: str | None = None,
                    almacen_id: str | None = None) -> list[MovimientoInventario]:
        with self._lock:
            return [m for m in self._movs.values()
                    if (producto_id is None or m.producto_id == producto_id)
                    and (almacen_id is None or m.almacen_id == almacen_id)]

    def get(self, movimiento_id: str) -> MovimientoInventario | None:
        with self._lock:
            return self._movs.get(movimiento_id)

    # ---- niveles mínimos ---------------------------------------------
    def fijar_nivel_minimo(self, producto_id: str, almacen_id: str, nivel: str):
        with self._lock:
            self._niveles[(producto_id, almacen_id)] = _dec(nivel)

    def nivel_minimo(self, producto_id: str, almacen_id: str) -> Decimal | None:
        with self._lock:
            return self._niveles.get((producto_id, almacen_id))

    # ---- catálogo maestro (BLOQUE 5; solo lectura) ----------------------
    def ficha_producto(self, producto_id: str) -> dict | None:
        """Ficha maestra del catálogo (nombre, sku, categoría, unidad,
        activo) o None si no hay catálogo / no hay ficha.

        Solo lectura: el stock se sigue derivando de movimientos; el
        catálogo no duplica ni reemplaza el estado del engine."""
        cat = self._catalogo
        if cat is None:
            return None
        try:
            return cat.obtener_producto(producto_id)
        except Exception:
            return None
