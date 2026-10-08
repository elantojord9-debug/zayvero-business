"""FASE 3 — PRESENTATION (transporte HTTP).

Servidor con la stdlib de Python (sin dependencias externas):

  /                    → UI del dashboard (index.html)
  /static/<file>       → CSS / JS
  /health              → chequeo de vida
  /api/meta            → resumen: dataset, conteos por prioridad/tipo, buckets de período
  /api/panorama        → valores del bloque "Panorama actual"
  /api/findings        → lista paginada y ordenada (filtros: priority, type,
                         period, q, limit, offset)
  /api/findings/<id>   → detalle completo de un hallazgo (2B + 2C)
"""

from __future__ import annotations

import json
import os
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .adapter import TYPE_LABELS, PRIORITIES, load_dataset
from .service import (
    PERIOD_BUCKETS,
    filter_findings,
    get_detail,
    panorama,
    period_end,
    priority_counts,
    reference_date,
    sort_findings,
    type_counts,
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
DEMO_DISCLAIMER = (
    "Datos de demostración — Demo Dataset: UCI Online Retail II. "
    "No representan un cliente real."
)

DATASET = None


def get_dataset():
    global DATASET
    if DATASET is None:
        DATASET = load_dataset()
    return DATASET


def _card(view):
    """Campos resumidos para la tarjeta de la lista."""
    period = view.get("period") or {}
    return {
        "finding_id": view.get("finding_id"),
        "title": view.get("title"),
        "type": view.get("type"),
        "type_label": view.get("type_label"),
        "business_priority": view.get("business_priority"),
        "impact_score": view.get("impact_score"),
        "confidence_score": view.get("confidence_score"),
        "evidence_quality": view.get("evidence_quality"),
        "period_start": period.get("start"),
        "period_end": period.get("end"),
        "entity_label": (view.get("entity") or {}).get("label"),
        "observed_value": view.get("observed_value"),
        "expected_value": view.get("expected_value"),
        "difference": view.get("difference"),
        "percentage_difference": view.get("percentage_difference"),
        "business_explanation": view.get("business_explanation"),
        "n_recommendations": view.get("n_recommendations"),
        "requires_review": view.get("requires_review"),
    }


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ZayveroDashboard/3.0"

    def log_message(self, fmt, *args):  # silencioso
        pass

    # ---------- helpers ----------
    def _json(self, obj, status=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _static(self, path):
        rel = os.path.normpath(path).lstrip("/")
        full = os.path.join(STATIC_DIR, rel)
        if not full.startswith(STATIC_DIR) or not os.path.isfile(full):
            self.send_error(404, "Not found")
            return
        ctype = {
            ".html": "text/html; charset=utf-8",
            ".css": "text/css; charset=utf-8",
            ".js": "application/javascript; charset=utf-8",
        }.get(os.path.splitext(full)[1], "application/octet-stream")
        with open(full, "rb") as fh:
            body = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # ---------- routes ----------
    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/":
            self._static("index.html")
            return
        if path.startswith("/static/"):
            self._static(path[len("/static/"):])
            return
        if path == "/health":
            self._json({"status": "ok"})
            return

        ds = get_dataset()
        findings = ds["findings"]

        if path == "/api/meta":
            counts = priority_counts(findings)
            tc = type_counts(findings)
            ref = reference_date(findings)
            pcounts = {}
            for bid, _label, _d in PERIOD_BUCKETS:
                pcounts[bid] = len(filter_findings(findings, period=bid, ref=ref))
            self._json({
                "dataset": ds["dataset"],
                "demo_disclaimer": DEMO_DISCLAIMER,
                "total_findings": len(findings),
                "by_priority": {p: counts.get(p, 0) for p in PRIORITIES},
                "types": [
                    {"id": t, "label": TYPE_LABELS.get(t, t), "count": tc.get(t, 0)}
                    for t in sorted(tc)
                ],
                "periods": [
                    {"id": bid, "label": label, "count": pcounts[bid]}
                    for bid, label, _d in PERIOD_BUCKETS
                ],
                "reference_date": ref.isoformat() if ref else None,
            })
            return

        if path == "/api/panorama":
            self._json(panorama(findings))
            return

        if path == "/api/findings":
            def one(name, default=None):
                v = qs.get(name, [default])
                return v[0]
            ref = reference_date(findings)
            filtered = filter_findings(
                findings,
                priority=one("priority"),
                ftype=one("type"),
                period=one("period"),
                query=one("q"),
                ref=ref,
            )
            ordered = sort_findings(filtered)
            total = len(ordered)
            try:
                limit = int(one("limit", "24") or 24)
            except ValueError:
                limit = 24
            try:
                offset = int(one("offset", "0") or 0)
            except ValueError:
                offset = 0
            limit = max(1, min(limit, 200))
            offset = max(0, offset)
            page = ordered[offset:offset + limit]
            self._json({
                "total": total,
                "limit": limit,
                "offset": offset,
                "items": [_card(v) for v in page],
            })
            return

        if path.startswith("/api/findings/"):
            fid = urllib.parse.unquote(path[len("/api/findings/"):])
            detail = get_detail(findings, fid)
            if detail is None:
                self._json({"error": "finding not found"}, status=404)
            else:
                self._json(detail)
            return

        self.send_error(404, "Not found")


def run(port=8501):
    server = ThreadingHTTPServer(("127.0.0.1", port), DashboardHandler)
    print(f"ZAYVERO Business Dashboard MVP → http://127.0.0.1:{port}")
    print(DEMO_DISCLAIMER)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
