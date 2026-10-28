"""BLOQUE IA — utilidades compartidas.

Convenciones del bloque (ver docs/bloque-ia.md):
- Todo trabaja sobre el OSBundle de UNA empresa; el aislamiento se
  verifica con `verificar_aislamiento(bundle, ctx)`: el company_id
  SIEMPRE viene del ctx autenticado, nunca de la query ni del body.
- Salidas 100 % JSON-serializables: Decimal -> str, date -> ISO.
- Anti-alucinación: sin datos suficientes los módulos devuelven el
  estado "datos_insuficientes" con la explicación; nunca inventan
  números.
- Idioma: explicaciones ES/EN como en shield/reglas.py.
"""
from __future__ import annotations

import os as _os
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal

sys.path.insert(0, _os.path.dirname(_os.path.dirname(
    _os.path.abspath(__file__))))

from oschema.models import _dec  # noqa: E402

SEVERIDADES = ("info", "alerta", "critico")
ESTADO_SIN_DATOS = "datos_insuficientes"


def verificar_aislamiento(bundle, ctx) -> str:
    """El company_id del ctx manda; el bundle debe pertenecerle.

    Acepta dict (tests) o TenantContext real (cableado HTTP).
    Lanza ValueError si no coincide (el integrador de rutas lo
    traduce a 400/403). Devuelve el company_id verificado.
    """
    cid = ""
    if ctx is not None:
        cid = getattr(ctx, "company_id", None) or ""
        if not cid and isinstance(ctx, dict):
            cid = ctx.get("company_id", "") or ""
    if not cid:
        raise ValueError("ctx.company_id es obligatorio")
    if getattr(bundle, "company_id", None) != cid:
        raise ValueError("el bundle no pertenece a la empresa del contexto")
    return cid


def q1(query, name: str, default: str = "") -> str:
    """Primer valor de un parámetro de query (?a=1&a=2 -> '1')."""
    if not isinstance(query, dict):
        return default
    v = query.get(name)
    if not v:
        return default
    return v[0] if isinstance(v, (list, tuple)) else v


def hoy(fecha) -> date:
    """Normaliza 'YYYY-MM-DD' | date | None -> date (UTC si es None)."""
    if fecha is None:
        return datetime.now(timezone.utc).date()
    if isinstance(fecha, date) and not isinstance(fecha, datetime):
        return fecha
    return datetime.strptime(str(fecha)[:10], "%Y-%m-%d").date()


def dia(fecha_iso: str) -> date | None:
    """'2026-10-01T10:00:00Z' -> date; '' o inválido -> None."""
    try:
        return datetime.strptime(str(fecha_iso or "")[:10],
                                 "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def dinero(v, moneda: str) -> str:
    return f"{Decimal(str(v)):,.2f} {moneda}"


def dec_str(v) -> str:
    """Decimal -> str exacto para JSON."""
    return str(_dec(v))


def mad(vals: list[float]) -> float:
    """Desviación absoluta mediana (misma primitiva que anomalies)."""
    if not vals:
        return 0.0
    s = sorted(vals)
    med = s[len(s) // 2]
    desv = sorted(abs(x - med) for x in vals)
    return desv[len(desv) // 2]


_MAD_Z = 0.6745  # misma constante que shield/reglas.py y anomalies


def z_robusto(x: float, base: list[float]) -> tuple[float, float]:
    """(z, mediana) del valor x contra la base con MAD.

    z = 0.6745 * (x - mediana) / MAD. Si MAD == 0: inf si x != mediana,
    0.0 si son iguales.
    """
    if not base:
        return 0.0, 0.0
    s = sorted(base)
    mediana = s[len(s) // 2]
    m = mad(base)
    if m > 0:
        return _MAD_Z * (x - mediana) / m, mediana
    return (float("inf") if x != mediana else 0.0), mediana


@dataclass
class Hallazgo:
    """Hallazgo del Inspector: problema + evidencia + impacto.

    - evidencia: números concretos que lo respaldan (nunca texto vago).
    - impacto_estimado: {"monto": str, "moneda": str, "base": str} o
      None cuando no se puede cuantificar con los datos.
    - suficiencia: "ok" | "insuficiente". Con "insuficiente" el
      hallazgo es de tipo "datos_insuficientes" y no afirma nada.
    """
    id: str
    modulo: str = "inspector"
    tipo: str = ""
    severidad: str = "info"          # info | alerta | critico
    titulo_es: str = ""
    titulo_en: str = ""
    explicacion_es: str = ""
    explicacion_en: str = ""
    evidencia: dict = field(default_factory=dict)
    impacto_estimado: dict | None = None
    suficiencia: str = "ok"
    fecha: str = ""

    def to_dict(self) -> dict:
        return {"id": self.id, "modulo": self.modulo, "tipo": self.tipo,
                "severidad": self.severidad, "titulo_es": self.titulo_es,
                "titulo_en": self.titulo_en,
                "explicacion_es": self.explicacion_es,
                "explicacion_en": self.explicacion_en,
                "evidencia": self.evidencia,
                "impacto_estimado": self.impacto_estimado,
                "suficiencia": self.suficiencia, "fecha": self.fecha}


def ordenar_por_impacto(hallazgos: list[Hallazgo]) -> list[Hallazgo]:
    """Priorización: mayor impacto estimado primero; sin impacto, al
    final (ordenados por severidad y tipo para estabilidad)."""
    peso = {"critico": 0, "alerta": 1, "info": 2}

    def clave(h: Hallazgo):
        imp = h.impacto_estimado or {}
        try:
            monto = abs(Decimal(str(imp.get("monto", "0"))))
        except Exception:
            monto = Decimal("0")
        # Los "datos_insuficientes" siempre al final.
        sin_datos = 1 if h.suficiencia != "ok" else 0
        return (sin_datos, -monto, peso.get(h.severidad, 9), h.tipo, h.id)

    return sorted(hallazgos, key=clave)


def impacto(monto, moneda: str, base: str) -> dict:
    """Constructor del dict impacto_estimado (JSON-serializable)."""
    return {"monto": dec_str(monto), "moneda": moneda, "base": base}
