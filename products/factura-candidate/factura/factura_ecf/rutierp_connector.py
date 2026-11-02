"""RutiERP ECF Connector v0.1. Sandbox only; no implicit emission.

API documentation: https://rutierp.com.do/api-ecf
Token is provided only via environment (never stored in repository).
"""
from __future__ import annotations
import json
import os
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit

class RutiERPError(RuntimeError):
    pass

class RutiERPConnector:
    def __init__(self, base_url=None, token=None, timeout=20):
        self.base_url = (base_url or os.getenv("RUTIERP_SANDBOX_URL", "")).rstrip("/")
        self._token = token if token is not None else os.getenv("RUTIERP_API_KEY", "")
        parts = urlsplit(self.base_url)
        if (parts.scheme != "https" or not parts.hostname or parts.username or
                parts.password or parts.query or parts.fragment or parts.path not in ("", "/")):
            raise ValueError("A clean HTTPS sandbox origin is required")
        if not self._token:
            raise ValueError("RUTIERP_API_KEY missing")
        if not isinstance(timeout, (int, float)) or not 1 <= timeout <= 60:
            raise ValueError("Invalid timeout")
        self.timeout = timeout

    def _request(self, method, path, payload=None):
        if not re.fullmatch(r"/(?:auth|taxes|type/document(?:/E(?:31|32))?|invoices|invoice/[0-9]+/status)", path):
            if path != "/send":
                raise ValueError("Unsupported API route")
        if method not in ("GET", "POST"):
            raise ValueError("Unsupported method")
        data = json.dumps(payload).encode("utf-8") if payload is not None else (
            b"" if method == "POST" else None)
        request = urllib.request.Request(
            self.base_url + "/api/ecf" + path,
            data=data,
            method=method,
            headers={"Authorization": self._token, "Accept": "application/json",
                     "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(1048577)
                if len(raw) > 1048576:
                    raise RutiERPError("API response too large")
                result = json.loads(raw)
        except urllib.error.HTTPError as exc:
            raise RutiERPError(f"RutiERP HTTP {exc.code}") from None
        except (urllib.error.URLError, ValueError) as exc:
            raise RutiERPError("RutiERP request failed or returned invalid JSON") from None
        if not isinstance(result, dict) or result.get("code") != 200:
            raise RutiERPError("RutiERP API returned an unsuccessful response")
        return result

    def authenticate(self):
        return self._request("POST", "/auth")

    def taxes(self):
        return self._request("GET", "/taxes")

    def document_types(self):
        return self._request("GET", "/type/document")

    def document_type(self, prefix):
        if prefix not in ("E31", "E32"):
            raise ValueError("Only E31/E32 supported in this adapter")
        return self._request("GET", "/type/document/" + prefix)

    def invoices(self):
        return self._request("GET", "/invoices")

    def status(self, invoice_id):
        if not isinstance(invoice_id, int) or isinstance(invoice_id, bool) or invoice_id < 1:
            raise ValueError("Invalid invoice id")
        # Provider may transmit a queued document when status is requested.
        return self._request("GET", f"/invoice/{invoice_id}/status")

    def send(self, payload, *, approved=False):
        if not approved:
            raise PermissionError("Explicit human approval required to transmit e-CF")
        if not isinstance(payload, dict):
            raise ValueError("JSON invoice required")
        ncf = payload.get("ncf", "")
        if not re.fullmatch(r"E(?:31|32)[0-9]{10}", ncf):
            raise ValueError("Invalid E31/E32 e-NCF")
        if not re.fullmatch(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", str(payload.get("invoice_date_due", ""))):
            raise ValueError("Missing sequence expiration date")
        if not payload.get("items"):
            raise ValueError("Invoice items required")
        return self._request("POST", "/send", payload)
