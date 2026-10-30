"""
ZAYVERO BUSINESS — Formateo legible y consistente de periodos.

El `period` de una predicción se construye en FASE 4A como
"{etiqueta_inicial} a {etiqueta_final}", donde cada etiqueta es
`str(pd.Period)`:
- mensual:  "2012-01 a 2012-03"
- semanal:  "2011-12-12/2011-12-18 a 2012-01-02/2012-01-08"
- diario:   "2012-01-05 a 2012-01-07"

Este módulo normaliza esas etiquetas a un formato legible y
CONSISTENTE sin alterar las fechas ni los límites originales y sin
convertir granularidades (semanal sigue siendo semanal).

Formatos de salida (abreviaturas de mes en español, consistentes con
el resto de los textos del sistema):
- mensual: "ene 2012 a mar 2012"
- semanal: "12–18 dic 2011 a 2–8 ene 2012"
- diario:  "5 ene 2012 a 7 ene 2012"

Si una etiqueta no puede interpretarse, se devuelve intacta (nunca
se inventa ni se pierde información).
"""

from __future__ import annotations

import re

_MESES = {
    1: "ene", 2: "feb", 3: "mar", 4: "abr", 5: "may", 6: "jun",
    7: "jul", 8: "ago", 9: "sep", 10: "oct", 11: "nov", 12: "dic",
}

_RE_MONTH = re.compile(r"^(\d{4})-(\d{2})$")
_RE_WEEK = re.compile(r"^(\d{4})-(\d{2})-(\d{2})/(\d{4})-(\d{2})-(\d{2})$")
_RE_DAY = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")


def _fmt_month(year: int, month: int) -> str:
    return "%s %d" % (_MESES.get(month, "%02d" % month), year)


def format_period_label(label: str | None) -> str:
    """Formatea UNA etiqueta de periodo (sin el " a ")."""
    if not label:
        return label or ""
    label = label.strip()

    m = _RE_WEEK.match(label)
    if m:
        y1, mo1, d1, y2, mo2, d2 = (int(x) for x in m.groups())
        left = "%d–%d %s %d" % (d1, d2, _MESES.get(mo1, "%02d" % mo1), y1)
        if (y1, mo1) != (y2, mo2):
            # La semana cruza mes o año: se muestra el rango completo.
            left = "%d %s %d–%d %s %d" % (
                d1, _MESES.get(mo1, "%02d" % mo1), y1,
                d2, _MESES.get(mo2, "%02d" % mo2), y2)
        return left

    m = _RE_MONTH.match(label)
    if m:
        return _fmt_month(int(m.group(1)), int(m.group(2)))

    m = _RE_DAY.match(label)
    if m:
        y, mo, d = (int(x) for x in m.groups())
        return "%d %s %d" % (d, _MESES.get(mo, "%02d" % mo), y)

    return label


def format_period_display(period: str | None) -> str:
    """Formatea un periodo completo "{ini} a {fin}" de forma legible."""
    if not period:
        return period or ""
    parts = period.split(" a ")
    if len(parts) != 2:
        return period
    return "%s a %s" % (format_period_label(parts[0]),
                        format_period_label(parts[1]))
