"""SEG-04 — Limitación de intentos de login (rate limiting local).

Protege /api/login contra intentos automatizados con dos cubetas
independientes de ventana deslizante:

  - por CUENTA (email normalizado): 5 fallos / 15 min (configurable)
  - por ORIGEN (IP): 20 fallos / 15 min (configurable)

Diseño:

- RESERVA ATÓMICA (anti-bypass concurrente): try_acquire() comprueba
  Y reserva en una sola operación bajo candado, y devuelve un token
  de reserva. Los intentos en vuelo cuentan contra el límite: 20 hilos
  simultáneos con límite 5 obtienen exactamente 5 autorizaciones.
  El candado solo cubre contadores/deques (microsegundos); NUNCA se
  mantiene durante el cálculo PBKDF2.
- EXACTLY-ONCE: settle(token, success=...) liquida la reserva; el
  token se consume al primer settle y cualquier settle posterior (o
  con un token inexistente) es un no-op que no crea registros. El
  llamador usa try/finally: éxito → perdona la cuenta; fallo o
  excepción → registra el fallo (fail-closed: un error no evade el
  límite).
- Solo cuentan los intentos FALLIDOS confirmados. Un login correcto
  limpia la cubeta de la cuenta: el usuario legítimo que recordó su
  clave no queda bloqueado. Nunca hay bloqueos permanentes: las
  ventanas expiran solas.
- La comprobación se hace ANTES de buscar al usuario, y la respuesta
  429 es genérica: el limitador no revela si una cuenta existe
  (coord. SEG-06).
- Almacenamiento en memoria del proceso, protegido con candado
  (el servidor usa ThreadingHTTPServer: un proceso, N hilos).
- ANTI-ABUSO DE MEMORIA:
  a) Las cubetas tienen tope (10.000). Al llenarse, SOLO se desalojan
     cubetas expiradas; un bloqueo activo jamás se desaloja para hacer
     sitio (un atacante no puede desbloquear a su víctima llenando la
     memoria).
  b) Si no hay cubetas expiradas, cada dimensión se evalúa por
     separado: un límite ya registrado en la otra dimensión SIGUE
     aplicándose aunque la cubeta nueva no quepa. Solo las dimensiones
     genuinamente no rastreables usan la vía fail-open.
  c) La vía fail-open está ACOTADA por una cubeta global de saturación
     (60 admisiones / 60 s por defecto, configurable): durante la
     saturación los identificadores nuevos comparten un presupuesto
     explícito en vez de intentos ilimitados. Esto degrada con
     elegancia (los usuarios legítimos siguen entrando) sin permitir
     abuso ilimitado ni denegación total.
  d) `_pending` nunca acumula ceros: las entradas se eliminan al
     llegar a cero y settle() jamás crea registros sin reserva.
- La IP de origen se determina con client_origin_ip(): por defecto el
  peer directo (verificable). Con ZAYVERO_TRUSTED_PROXIES se acepta
  X-Forwarded-For SOLO si el peer es un proxy confiable declarado, y
  se recorre la cadena de derecha a izquierda descartando proxies
  confiables (algoritmo estándar; sin posiciones fijas).
- LIMITACIÓN DOCUMENTADA: en memoria = por proceso. Con múltiples
  workers o instancias cada uno lleva su propio contador; la
  protección global requeriría un almacén compartido (p. ej. Redis).
  En el despliegue actual (Render, 1 instancia, 1 proceso) el límite
  local es efectivo.

Solo stdlib. Sin dependencias nuevas.
"""

from __future__ import annotations

import itertools
import os
import threading
import time
from collections import deque


def _env_int(name: str, default: int) -> int:
    try:
        v = int(os.environ.get(name, "").strip())
        return v if v > 0 else default
    except Exception:
        return default


def _trusted_proxies() -> set[str]:
    """IPs de proxies confiables declarados por el operador.

    ZAYVERO_TRUSTED_PROXIES="10.0.0.1, 10.0.0.2" — deben ser TODOS los
    proxies entre internet y la app, y el puerto de la app debe ser
    alcanzable SOLO a través de ellos. Sin esta variable (por defecto)
    X-Forwarded-For se ignora por completo.
    """
    raw = os.environ.get("ZAYVERO_TRUSTED_PROXIES", "") or ""
    return {p.strip() for p in raw.split(",") if p.strip()}


class LoginRateLimiter:
    """Ventanas deslizantes por cuenta y por IP, en memoria del proceso."""

    # Reservas abandonadas (el llamador nunca las liquidó) se purgan
    # tras este tiempo. Un intento legítimo dura <1 s (PBKDF2).
    _RESERVATION_TTL_S = 300.0

    def __init__(self, *, account_max: int = 5, account_window_s: int = 900,
                 ip_max: int = 20, ip_window_s: int = 900,
                 max_keys: int = 10000,
                 saturation_max: int = 60, saturation_window_s: int = 60):
        self.account_max = account_max
        self.account_window_s = account_window_s
        self.ip_max = ip_max
        self.ip_window_s = ip_window_s
        self.max_keys = max_keys
        self.saturation_max = saturation_max
        self.saturation_window_s = saturation_window_s
        self._lock = threading.Lock()
        self._nonce = itertools.count(1)
        # (kind, key) -> deque[timestamps monotónicos de FALLOS confirmados]
        self._buckets: dict[tuple[str, str], deque] = {}
        # (kind, key) -> nº de reservas en vuelo (nunca se guardan ceros)
        self._pending: dict[tuple[str, str], int] = {}
        # nonce -> (ip_key|None, acct_key|None, acquired_at)
        self._reservations: dict[int, tuple] = {}
        # Orden de inserción para desalojo de expiradas.
        self._order: deque[tuple[str, str]] = deque()
        # Cubeta global que acota la vía fail-open (ver docstring).
        self._saturation: deque[float] = deque()

    # -- internals (llamar con _lock tomado) ------------------------------
    def _purge(self, bucket: deque, window_s: int, now: float) -> None:
        while bucket and bucket[0] <= now - window_s:
            bucket.popleft()

    def _purge_reservations(self, now: float) -> None:
        stale = [n for n, (_, _, at) in self._reservations.items()
                 if at <= now - self._RESERVATION_TTL_S]
        for n in stale:
            rec = self._reservations.pop(n, None)
            if rec is None:
                continue
            for bkey in (rec[0], rec[1]):
                if bkey is not None:
                    self._release_pending(bkey)

    def _release_pending(self, bkey: tuple[str, str]) -> None:
        v = self._pending.get(bkey)
        if v is None:
            return
        if v <= 1:
            del self._pending[bkey]
        else:
            self._pending[bkey] = v - 1

    def _bucket_if_tracked(self, kind: str, key: str,
                           now: float) -> deque | None:
        """Devuelve la cubeta, creándola si hay capacidad.

        Si la capacidad está llena: primero intenta desalojar cubetas
        EXPIRADAS (las más antiguas primero, barrido acotado). Si no hay
        ninguna expirada, devuelve None SIN desalojar bloqueos activos:
        esa dimensión queda no rastreable (vía fail-open acotada).
        """
        bkey = (kind, key)
        bucket = self._buckets.get(bkey)
        if bucket is not None:
            return bucket
        if len(self._buckets) < self.max_keys:
            bucket = deque()
            self._buckets[bkey] = bucket
            self._order.append(bkey)
            return bucket
        # Capacidad llena: desalojar solo expiradas (barrido acotado).
        for _ in range(min(128, len(self._order))):
            oldest = self._order[0]
            ob = self._buckets.get(oldest)
            if ob is None:
                self._order.popleft()
                continue
            okind, _okey = oldest
            window = (self.account_window_s if okind == "account"
                      else self.ip_window_s)
            self._purge(ob, window, now)
            if not ob and not self._pending.get(oldest, 0):
                # Expirada y sin reservas en vuelo: reutilizable.
                self._order.popleft()
                del self._buckets[oldest]
                self._pending.pop(oldest, None)
                bucket = deque()
                self._buckets[bkey] = bucket
                self._order.append(bkey)
                return bucket
            # No expirada: rotarla al final y seguir buscando.
            self._order.popleft()
            self._order.append(oldest)
        # Sin cubetas expiradas: no desalojar bloqueos activos.
        return None

    def _effective(self, bkey: tuple[str, str], bucket: deque) -> int:
        return len(bucket) + self._pending.get(bkey, 0)

    # -- API pública ----------------------------------------------------
    def try_acquire(self, *, ip: str, account: str
                    ) -> tuple[bool, float, int | None]:
        """Reserva atómica de un intento de login.

        Comprueba y reserva bajo el mismo candado. Devuelve
        (permitido, retry_after_s, token_reserva). El token DEBE
        liquidarse exactamente una vez con settle() (try/finally en el
        llamador); si no se permite, el token es None.

        Si una dimensión no es rastreable (sin capacidad), el límite de
        la OTRA dimensión sigue aplicándose; solo lo no rastreable usa
        la vía fail-open, acotada por la cubeta global de saturación.
        """
        now = time.monotonic()
        ip_key = ("ip", ip or "unknown")
        acct_key = ("account", account or "unknown")
        with self._lock:
            self._purge_reservations(now)
            ip_bucket = self._bucket_if_tracked("ip", ip_key[1], now)
            acct_bucket = self._bucket_if_tracked("account", acct_key[1], now)
            # FIX-4: cada dimensión rastreable se verifica por separado;
            # una cubeta inexistente NO salta el límite de la otra.
            if ip_bucket is not None:
                self._purge(ip_bucket, self.ip_window_s, now)
                if self._effective(ip_key, ip_bucket) >= self.ip_max:
                    retry = (ip_bucket[0] + self.ip_window_s - now) \
                        if ip_bucket else 1.0
                    return False, max(0.0, retry), None
            if acct_bucket is not None:
                self._purge(acct_bucket, self.account_window_s, now)
                if self._effective(acct_key, acct_bucket) >= self.account_max:
                    retry = (acct_bucket[0] + self.account_window_s - now) \
                        if acct_bucket else 1.0
                    return False, max(0.0, retry), None
            # Vía fail-open acotada (solo si algo quedó no rastreable).
            if ip_bucket is None or acct_bucket is None:
                self._purge(self._saturation, self.saturation_window_s, now)
                if len(self._saturation) >= self.saturation_max:
                    retry = (self._saturation[0] + self.saturation_window_s
                             - now)
                    return False, max(0.0, retry), None
                self._saturation.append(now)
            nonce = next(self._nonce)
            tracked_ip = ip_key if ip_bucket is not None else None
            tracked_acct = acct_key if acct_bucket is not None else None
            self._reservations[nonce] = (tracked_ip, tracked_acct, now)
            if tracked_ip is not None:
                self._pending[tracked_ip] = \
                    self._pending.get(tracked_ip, 0) + 1
            if tracked_acct is not None:
                self._pending[tracked_acct] = \
                    self._pending.get(tracked_acct, 0) + 1
            return True, 0.0, nonce

    def settle(self, reservation: int | None, *, success: bool) -> None:
        """Liquida una reserva de try_acquire() exactamente una vez.

        El token se consume al primer settle; un segundo settle (o un
        token inexistente/None) es un no-op que NO crea registros.
        success=True: perdona la cubeta de la cuenta. success=False
        (fallo o excepción): registra el fallo (fail-closed).
        """
        if reservation is None:
            return
        now = time.monotonic()
        with self._lock:
            rec = self._reservations.pop(reservation, None)
            if rec is None:
                return  # Ya liquidada o nunca existió: no crear nada.
            ip_key, acct_key, _at = rec
            for bkey in (ip_key, acct_key):
                if bkey is not None:
                    self._release_pending(bkey)
            if success:
                if acct_key is not None:
                    bucket = self._buckets.get(acct_key)
                    if bucket is not None:
                        bucket.clear()
            else:
                for bkey, kind in ((ip_key, "ip"), (acct_key, "account")):
                    if bkey is None:
                        continue
                    bucket = self._buckets.get(bkey)
                    if bucket is not None:
                        bucket.append(now)

    def bucket_sizes(self) -> int:
        """Solo diagnóstico/tests: nº de cubetas en memoria."""
        with self._lock:
            return len(self._buckets)

    def pending_size(self) -> int:
        """Solo diagnóstico/tests: nº de entradas en _pending."""
        with self._lock:
            return len(self._pending)

    def reservations_size(self) -> int:
        """Solo diagnóstico/tests: reservas en vuelo."""
        with self._lock:
            return len(self._reservations)


def limiter_from_env() -> LoginRateLimiter:
    """Construye el limitador con la configuración del entorno."""
    return LoginRateLimiter(
        account_max=_env_int("ZAYVERO_RL_ACCOUNT_MAX", 5),
        account_window_s=_env_int("ZAYVERO_RL_ACCOUNT_WINDOW", 900),
        ip_max=_env_int("ZAYVERO_RL_IP_MAX", 20),
        ip_window_s=_env_int("ZAYVERO_RL_IP_WINDOW", 900),
        saturation_max=_env_int("ZAYVERO_RL_SATURATION_MAX", 60),
        saturation_window_s=_env_int("ZAYVERO_RL_SATURATION_WINDOW", 60),
    )


_limiter: LoginRateLimiter | None = None
_limiter_lock = threading.Lock()


def get_limiter() -> LoginRateLimiter:
    """Singleton por proceso para los servidores HTTP."""
    global _limiter
    if _limiter is None:
        with _limiter_lock:
            if _limiter is None:
                _limiter = limiter_from_env()
    return _limiter


def client_origin_ip(handler) -> str:
    """Identifica el origen de la petición para la cubeta por IP.

    Por defecto: el peer directo de la conexión TCP. Es lo único
    verificable sin confiar en cabeceras del cliente.

    Con ZAYVERO_TRUSTED_PROXIES="ip1, ip2" (todas las IPs de proxies
    entre internet y la app; el puerto de la app alcanzable SOLO a
    través de ellos), y SOLO si el peer directo es uno de esos proxies,
    se recorre X-Forwarded-For de derecha a izquierda descartando IPs
    de proxies confiables: la primera IP NO confiable es el cliente.
    Este es el algoritmo estándar (sin posiciones fijas): funciona con
    uno o varios proxies y neutraliza XFF falsificados — un cliente
    que conecte directo (peer no confiable) nunca logra que se use su
    XFF.

    Si no puede determinarse la IP del cliente de forma confiable
    (peer confiable pero XFF vacía o toda la cadena son proxies), se
    usa el peer: alternativa segura (cubeta compartida del proxy,
    fail-closed para la cubeta IP) y documentada.
    """
    peer = ""
    try:
        peer = (handler.client_address[0] or "").strip()
    except Exception:
        peer = ""
    if not peer:
        peer = "unknown"
    trusted = _trusted_proxies()
    if peer not in trusted:
        return peer
    try:
        xff = handler.headers.get("X-Forwarded-For", "") or ""
    except Exception:
        xff = ""
    parts = [p.strip() for p in xff.split(",") if p.strip()]
    for ip in reversed(parts):
        if ip not in trusted:
            return ip
    # Cadena vacía o toda confiable: no se puede determinar; usar peer.
    return peer
