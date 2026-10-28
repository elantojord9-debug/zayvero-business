"""Panel HTML estático de facturación e-CF (Fase 1 — SIMULACIÓN).

Genera un archivo HTML autocontenido (sin dependencias externas) con
la tabla de comprobantes, resumen por estado, sección de
errores/observaciones, enlaces de descarga (rutas simuladas a XML/PDF)
y un banner permanente de simulación. Incluye toggle ES/EN vía JS
inline; todos los textos de UI viven en los diccionarios `TEXTO`.

Nada de lo mostrado aquí es válido ante la DGII.
"""
from __future__ import annotations

import html
from decimal import Decimal

from . import AVISO_SIMULACION
from .modelos import TIPOS_ECF, Comprobante

AVISO_SIMULACION_EN = "SIMULATION — NOT VALID BEFORE DGII"

TEXTO: dict[str, dict[str, str]] = {
    "es": {
        "lang_attr": "es",
        "title": "ZAYVERO Business OS — Panel e-CF",
        "heading": "Panel de Facturación Electrónica (e-CF)",
        "sim_note": AVISO_SIMULACION,
        "toggle_label": "English",
        "summary_title": "Resumen",
        "total_invoiced": "Total facturado",
        "count_by_state": "Comprobantes por estado",
        "table_title": "Facturas",
        "col_encf": "e-NCF",
        "col_type": "Tipo",
        "col_emisor": "Emisor",
        "col_receptor": "Receptor",
        "col_total": "Total",
        "col_state": "Estado",
        "col_date": "Fecha",
        "col_downloads": "Descargas",
        "obs_title": "Errores / Observaciones",
        "obs_none": "Sin observaciones.",
        "obs_missing_original": "Nota sin e-NCF original enlazado.",
        "footer": "Material de prueba interna con datos sintéticos.",
        "dl_xml": "XML",
        "dl_pdf": "PDF",
        "dl_note": "Rutas simuladas; los archivos se generan bajo demanda.",
    },
    "en": {
        "lang_attr": "en",
        "title": "ZAYVERO Business OS — e-CF Dashboard",
        "heading": "Electronic Invoicing Dashboard (e-CF)",
        "sim_note": AVISO_SIMULACION_EN,
        "toggle_label": "Español",
        "summary_title": "Summary",
        "total_invoiced": "Total invoiced",
        "count_by_state": "Vouchers by status",
        "table_title": "Invoices",
        "col_encf": "e-Tax ID",
        "col_type": "Type",
        "col_emisor": "Issuer",
        "col_receptor": "Receiver",
        "col_total": "Total",
        "col_state": "Status",
        "col_date": "Date",
        "col_downloads": "Downloads",
        "obs_title": "Errors / Remarks",
        "obs_none": "No remarks.",
        "obs_missing_original": "Note without linked original e-NCF.",
        "footer": "Internal test material with synthetic data.",
        "dl_xml": "XML",
        "dl_pdf": "PDF",
        "dl_note": "Simulated paths; files are generated on demand.",
    },
}

ESTADO_PROBLEMA = {"observado", "rechazado"}

ESTILO = """
body{font-family:Arial,Helvetica,sans-serif;margin:0;background:#f4f5f7;color:#222}
.banner{background:#b30000;color:#fff;text-align:center;font-weight:bold;
padding:12px;font-size:15px}
.wrap{max-width:1100px;margin:0 auto;padding:20px}
.card{background:#fff;border-radius:8px;padding:16px;margin:16px 0;
box-shadow:0 1px 3px rgba(0,0,0,.12)}
h1{font-size:22px;margin:6px 0 16px}
h2{font-size:17px;margin:0 0 10px;border-bottom:2px solid #eee;padding-bottom:6px}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{border:1px solid #ddd;padding:8px;text-align:left}
th{background:#eee}
tr:nth-child(even){background:#fafafa}
.badge{display:inline-block;padding:2px 8px;border-radius:10px;font-size:12px;
background:#e3e9f5}
.total{font-weight:bold;font-size:18px}
.meta{color:#666;font-size:12px}
button.lang{float:right;padding:8px 14px;font-size:14px;cursor:pointer}
a.dl{margin-right:8px}
"""


def _esc(valor) -> str:
    return html.escape("" if valor is None else str(valor), quote=True)


def _base_descarga(c: Comprobante) -> str:
    """Base de ruta simulada de descarga (e-NCF o id)."""
    return c.e_ncf or c.id


def _resumen(comprobantes: list[Comprobante]) -> tuple[dict[str, int], Decimal]:
    conteo: dict[str, int] = {}
    total = Decimal("0")
    for c in comprobantes:
        conteo[c.estado] = conteo.get(c.estado, 0) + 1
        total += c.totales.total
    return conteo, total.quantize(Decimal("0.01"))


def _observaciones(c: Comprobante, t: dict[str, str]) -> list[str]:
    obs: list[str] = []
    if c.estado in ESTADO_PROBLEMA:
        obs.append(c.estado)
    if c.tipo in ("33", "34") and not c.encf_original:
        obs.append(t["obs_missing_original"])
    return obs


def generar_panel(comprobantes: list[Comprobante], idioma: str = "es",
                  ruta: str = "panel_ecf.html") -> str:
    """Genera el panel HTML estático en `ruta` y devuelve la ruta.

    `idioma` define el idioma inicial ("es" o "en"); el botón de la
    página alterna entre ambos vía JS inline.
    """
    idioma = idioma if idioma in TEXTO else "es"
    t = TEXTO[idioma]
    conteo, total_facturado = _resumen(comprobantes)

    filas: list[str] = []
    for c in comprobantes:
        base = _base_descarga(c)
        nombre_tipo = TIPOS_ECF.get(c.tipo, {}).get("nombre", c.tipo)
        filas.append(
            "<tr>"
            f"<td>{_esc(c.e_ncf or '—')}</td>"
            f"<td>{_esc(c.tipo)} — {_esc(nombre_tipo)}</td>"
            f"<td>{_esc(c.emisor.razon_social)}<br>"
            f"<span class='meta'>RNC {_esc(c.emisor.rnc)}</span></td>"
            f"<td>{_esc(c.receptor.razon_social)}<br>"
            f"<span class='meta'>RNC {_esc(c.receptor.rnc)}</span></td>"
            f"<td>{_esc(f'{c.totales.total:,.2f}')} {_esc(c.moneda)}</td>"
            f"<td><span class='badge'>{_esc(c.estado)}</span></td>"
            f"<td>{_esc((c.creado_en or '')[:10])}</td>"
            f"<td><a class='dl' href='{_esc(base)}.xml'>{_esc(t['dl_xml'])}</a>"
            f"<a class='dl' href='{_esc(base)}.pdf'>{_esc(t['dl_pdf'])}</a></td>"
            "</tr>")

    items_obs: list[str] = []
    for c in comprobantes:
        for o in _observaciones(c, t):
            items_obs.append(
                f"<li><strong>{_esc(c.e_ncf or c.id)}</strong>: {_esc(o)}</li>")
    obs_html = ("<ul>" + "".join(items_obs) + "</ul>"
                if items_obs else f"<p>{_esc(t['obs_none'])}</p>")

    items_estado = "".join(
        f"<li>{_esc(estado)}: {n}</li>"
        for estado, n in sorted(conteo.items()))

    i18n_js = ("const I18N = {es: %s, en: %s};"
               % (_json_dict(TEXTO["es"]), _json_dict(TEXTO["en"])))

    pagina = f"""<!DOCTYPE html>
<html lang="{t['lang_attr']}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(t['title'])}</title>
<style>{ESTILO}</style>
</head>
<body>
<div class="banner">{_esc(AVISO_SIMULACION)} / {_esc(AVISO_SIMULACION_EN)}</div>
<div class="wrap">
<button class="lang" id="btn-idioma" data-i18n="toggle_label">{_esc(t['toggle_label'])}</button>
<h1 data-i18n="heading">{_esc(t['heading'])}</h1>
<p class="meta" data-i18n="sim_note">{_esc(t['sim_note'])}</p>

<div class="card">
<h2 data-i18n="summary_title">{_esc(t['summary_title'])}</h2>
<p><span data-i18n="total_invoiced">{_esc(t['total_invoiced'])}</span>:
<span class="total">{_esc(f'{total_facturado:,.2f}')}</span></p>
<p><span data-i18n="count_by_state">{_esc(t['count_by_state'])}</span>:</p>
<ul>{items_estado}</ul>
</div>

<div class="card">
<h2 data-i18n="table_title">{_esc(t['table_title'])}</h2>
<table>
<thead><tr>
<th data-i18n="col_encf">{_esc(t['col_encf'])}</th>
<th data-i18n="col_type">{_esc(t['col_type'])}</th>
<th data-i18n="col_emisor">{_esc(t['col_emisor'])}</th>
<th data-i18n="col_receptor">{_esc(t['col_receptor'])}</th>
<th data-i18n="col_total">{_esc(t['col_total'])}</th>
<th data-i18n="col_state">{_esc(t['col_state'])}</th>
<th data-i18n="col_date">{_esc(t['col_date'])}</th>
<th data-i18n="col_downloads">{_esc(t['col_downloads'])}</th>
</tr></thead>
<tbody>
{''.join(filas)}
</tbody>
</table>
<p class="meta" data-i18n="dl_note">{_esc(t['dl_note'])}</p>
</div>

<div class="card">
<h2 data-i18n="obs_title">{_esc(t['obs_title'])}</h2>
{obs_html}
</div>

<p class="meta" data-i18n="footer">{_esc(t['footer'])}</p>
</div>
<script>
{i18n_js}
let lang = "{idioma}";
document.getElementById("btn-idioma").addEventListener("click", function () {{
  lang = (lang === "es") ? "en" : "es";
  document.documentElement.lang = lang;
  const dict = I18N[lang];
  document.querySelectorAll("[data-i18n]").forEach(function (el) {{
    const k = el.getAttribute("data-i18n");
    if (dict[k] !== undefined) {{ el.textContent = dict[k]; }}
  }});
}});
</script>
</body>
</html>
"""
    with open(ruta, "w", encoding="utf-8") as f:
        f.write(pagina)
    return ruta


def _json_dict(d: dict[str, str]) -> str:
    import json
    return json.dumps(d, ensure_ascii=False)
