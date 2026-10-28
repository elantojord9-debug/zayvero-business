"""BLOQUE Documentos/Integraciones — Contrato común de adaptadores.

Todo adaptador (hoy: solo simulados) implementa esta interfaz. Cuando
exista una conexión real, el adaptador real implementará la MISMA
interfaz; el resto del OS no cambia.

REGLA DURA: ningún adaptador de este paquete hace llamadas de red.
"""
from __future__ import annotations

from datetime import datetime, timezone


def _ahora_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class AdaptadorSimulado:
    """Base de los simuladores. MODO declarado y datos sintéticos."""

    MODO = "simulado"          # DECLARADO EN CÓDIGO (no negociable)
    FUENTE = ""               # shopify | odoo | sap | quickbooks | pos
    NOMBRE = ""

    # ---- contrato de lectura (solo lectura; la escritura real exige
    # ---- autorización explícita por adaptador) --------------------

    def listar_productos(self) -> list[dict]:
        raise NotImplementedError

    def listar_pedidos(self) -> list[dict]:
        raise NotImplementedError

    def listar_clientes(self) -> list[dict]:
        raise NotImplementedError

    def leer_stock(self, producto_id: str | None = None) -> list[dict]:
        raise NotImplementedError

    # ---- metadatos del contrato ------------------------------------

    def mapeo_campos(self) -> dict:
        """{entidad: {campo_origen: campo_OS}}."""
        raise NotImplementedError

    @classmethod
    def que_falta_para_real(cls) -> list[dict]:
        """Qué se necesita para una conexión real (credenciales,
        sandbox, autorización...). Lo implementa cada adaptador."""
        raise NotImplementedError

    def info(self) -> dict:
        return {
            "fuente": self.FUENTE,
            "nombre": self.NOMBRE,
            "modo": self.MODO,
            "advertencia": (
                "SIMULADO: datos sintéticos locales; no hay conexión "
                "real con ningún servicio externo ni llamadas de red."),
            "operaciones": ["listar_productos", "listar_pedidos",
                            "listar_clientes", "leer_stock"],
        }

    # ---- utilidades -------------------------------------------------

    @staticmethod
    def _marcar(items: list[dict], entidad: str) -> list[dict]:
        """Marca cada fila como sintética (nunca pasa por real)."""
        return [{**it, "origen": "simulado", "sintetico": True,
                 "entidad": entidad} for it in items]

    @staticmethod
    def _sello() -> str:
        return _ahora_iso()
