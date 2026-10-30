"""Máquina de estados del ciclo de vida de un e-CF — FASE 1 LOCAL.

=====================================================================
AVISO LEGAL — SIMULACIÓN, NO VÁLIDO ANTE DGII
=====================================================================
Este módulo gestiona el flujo LOCAL de comprobantes sintéticos. La
APROBACIÓN HUMANA es obligatoria: ningún comprobante se transmite sin
que un actor humano la registre con `aprobar()`.

Transiciones permitidas:
    borrador -> validado
    validado -> pendiente_aprobacion
    pendiente_aprobacion -> encolado
    encolado -> transmitido
    transmitido -> recibido | contingencia
    recibido -> aceptado | observado | rechazado | contingencia
    aceptado | observado | rechazado -> anulado (solo con motivo)

Cada transición agrega un evento INMUTABLE al historial del
comprobante, encadenado por hash SHA-256 (cadena verificable con
`verificar_historial`). La anulación exige motivo en el detalle.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from .modelos import ESTADOS, Comprobante, ErrorECF
from .proveedor import FiscalProvider

#: Transiciones permitidas por estado de origen (máquina en memoria).
#: NOTA P0 #11: el mapa canónico que hace cumplir la persistencia es
#: `modelos.TRANSICIONES_VALIDAS`. Este mapa local es más restrictivo
#: en algunos estados (ej. no admite borrador->anulado directo ni
#: contingencia->transmitido) y ningún método de esta clase realiza una
#: transición fuera del mapa canónico: aprobar (validado->
#: pendiente_aprobacion) y emitir (pendiente_aprobacion->encolado->
#: transmitido->recibido->{aceptado|observado|rechazado}) son todas
#: válidas en TRANSICIONES_VALIDAS. No alinear sin revisar los tests
#: de flujo existentes (ej. test_contingencia_desde_transmitido).
TRANSICIONES_PERMITIDAS: dict[str, tuple[str, ...]] = {
    "borrador": ("validado",),
    "validado": ("pendiente_aprobacion",),
    "pendiente_aprobacion": ("encolado",),
    "encolado": ("transmitido",),
    "transmitido": ("recibido", "contingencia"),
    "recibido": ("aceptado", "observado", "rechazado", "contingencia"),
    "aceptado": ("anulado",),
    "observado": ("anulado",),
    "rechazado": ("anulado",),
    "contingencia": (),
    "anulado": (),
}

#: Estados finales: `emitir` es idempotente sobre ellos.
ESTADOS_FINALES = ("aceptado", "observado", "rechazado")

_GENESIS = "eCF:genesis:v1"


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Evento:
    """Evento del historial. El `hash` encadena al evento anterior.

    Por convención el historial no se muta: cualquier alteración se
    detecta con `verificar_historial`.
    """
    seq: int
    comprobante_id: str
    de: str
    a: str
    actor: str
    detalle: str
    timestamp: str
    hash: str


def _hash_evento(prev_hash: str, seq: int, comprobante_id: str, de: str,
                 a: str, actor: str, detalle: str, timestamp: str) -> str:
    contenido = "|".join(
        [prev_hash, str(seq), comprobante_id, de, a, actor, detalle,
         timestamp])
    return hashlib.sha256(contenido.encode("utf-8")).hexdigest()


class Historial:
    """Historial de eventos por comprobante, con cadena de hash."""

    def __init__(self) -> None:
        self._eventos: dict[str, list[Evento]] = {}

    def registrar(self, comprobante_id: str, de: str, a: str, actor: str,
                  detalle: str) -> Evento:
        lista = self._eventos.setdefault(comprobante_id, [])
        prev_hash = lista[-1].hash if lista else _GENESIS
        seq = len(lista) + 1
        timestamp = _ahora()
        evento = Evento(
            seq=seq, comprobante_id=comprobante_id, de=de, a=a,
            actor=actor, detalle=detalle, timestamp=timestamp,
            hash=_hash_evento(prev_hash, seq, comprobante_id, de, a,
                              actor, detalle, timestamp))
        lista.append(evento)
        return evento

    def eventos(self, comprobante_id: str) -> list[Evento]:
        """Devuelve la lista viva de eventos (no copiar: la verificación
        detecta cualquier alteración posterior)."""
        return self._eventos.get(comprobante_id, [])

    def verificar(self, comprobante_id: str) -> bool:
        """Recomputa la cadena de hash. True si está intacta."""
        lista = self._eventos.get(comprobante_id, [])
        if not lista:
            return False
        prev_hash = _GENESIS
        for i, ev in enumerate(lista, start=1):
            if ev.seq != i or ev.comprobante_id != comprobante_id:
                return False
            esperado = _hash_evento(
                prev_hash, ev.seq, ev.comprobante_id, ev.de, ev.a,
                ev.actor, ev.detalle, ev.timestamp)
            if ev.hash != esperado:
                return False
            prev_hash = ev.hash
        return True


class FlujoECF:
    """Máquina de estados + aprobaciones + emisión de e-CF (fase 1)."""

    def __init__(self) -> None:
        self.historial = Historial()
        self._aprobaciones: dict[str, dict] = {}

    # --- Transiciones ----------------------------------------------------
    def transicionar(self, c: Comprobante, nuevo_estado: str, actor: str,
                     detalle: str = "") -> Evento:
        """Valida y aplica una transición, registrando el evento."""
        if nuevo_estado not in ESTADOS:
            raise ErrorECF(f"estado destino inválido: {nuevo_estado!r}")
        actor = (actor or "").strip()
        if not actor:
            raise ErrorECF("la transición requiere un actor")
        permitidos = TRANSICIONES_PERMITIDAS.get(c.estado, ())
        if nuevo_estado not in permitidos:
            raise ErrorECF(
                f"transición no permitida: {c.estado!r} -> {nuevo_estado!r}")
        if nuevo_estado == "anulado" and not (detalle or "").strip():
            raise ErrorECF("la anulación requiere un motivo en el detalle")
        anterior = c.estado
        c.estado = nuevo_estado
        return self.historial.registrar(c.id, anterior, nuevo_estado,
                                        actor, detalle)

    # --- Aprobación humana (obligatoria) ---------------------------------
    def aprobar(self, c: Comprobante, actor: str,
                detalle: str = "") -> Evento:
        """Registra la APROBACIÓN HUMANA: validado -> pendiente_aprobacion.

        Sin actor humano no hay aprobación, y sin aprobación registrada
        `emitir` se niega a transmitir.
        """
        actor = (actor or "").strip()
        if not actor:
            raise ErrorECF(
                "la aprobación requiere un actor humano (sin actor no se "
                "emite)")
        evento = self.transicionar(c, "pendiente_aprobacion", actor, detalle)
        self._aprobaciones[c.id] = {
            "actor": actor, "detalle": detalle, "fecha": _ahora()}
        return evento

    def aprobacion_registrada(self, comprobante_id: str) -> bool:
        return comprobante_id in self._aprobaciones

    # --- Emisión ----------------------------------------------------------
    def emitir(self, c: Comprobante, provider: FiscalProvider, actor: str,
               xml: str = "") -> str:
        """Encola y transmite el comprobante vía el proveedor.

        Solo desde "pendiente_aprobacion" y con aprobación humana
        registrada. Idempotente: si el comprobante ya está en un estado
        final (aceptado/observado/rechazado) devuelve el estado actual
        sin retransmitir ni agregar eventos.
        """
        if c.estado in ESTADOS_FINALES:
            return c.estado  # idempotencia: no retransmitir
        if not isinstance(provider, FiscalProvider):
            raise ErrorECF("se requiere un FiscalProvider válido")
        self._verificar_modo(c.modo)
        actor = (actor or "").strip()
        if not actor:
            raise ErrorECF("la emisión requiere un actor")
        if c.estado != "pendiente_aprobacion":
            raise ErrorECF(
                "emitir requiere estado 'pendiente_aprobacion' "
                f"(actual: {c.estado!r})")
        if not self.aprobacion_registrada(c.id):
            raise ErrorECF(
                "emisión bloqueada: se requiere aprobación humana "
                "registrada con aprobar()")
        self.transicionar(c, "encolado", actor,
                           "comprobante encolado para transmisión")
        self.transicionar(c, "transmitido", actor,
                           "XML enviado al proveedor (simulación)")
        respuesta = provider.transmitir(xml, c)
        self.transicionar(c, "recibido", actor,
                           f"respuesta {respuesta.codigo}: {respuesta.estado}")
        self.transicionar(c, respuesta.estado, actor, respuesta.mensaje)
        return c.estado

    def verificar_historial(self, comprobante_id: str) -> bool:
        """True si la cadena de eventos del comprobante está intacta."""
        return self.historial.verificar(comprobante_id)

    # --- Interno ----------------------------------------------------------
    @staticmethod
    def _verificar_modo(modo: str) -> None:
        if modo == "mock":
            return
        if modo == "disabled":
            raise ErrorECF("modo disabled: transmisión bloqueada")
        if modo in ("sandbox", "certified_provider"):
            raise ErrorECF(
                f"modo {modo!r}: no implementado en fase 1 — "
                "sin conexión real")
        raise ErrorECF(f"modo fiscal desconocido: {modo!r}")
