"""RutiERP connector. Authentication token comes only from environment.

Read-only by default: POST /send requires an explicit opt-in in application code.
Do not use in production without separate fiscal authorization.
"""
import json
import os
import urllib.request
from urllib.parse import urlsplit

class RutiERPConnector:
    def __init__(self, base_url=None, token=None):
        self.base_url = (base_url or os.getenv("RUTIERP_SANDBOX_URL", "")).rstrip("/")
        self.token = token if token is not None else os.getenv("RUTIERP_API_KEY", "")
        parts = urlsplit(self.base_url)
        if parts.scheme != "https" or not parts.netloc or parts.username or parts.password:
            raise ValueError("HTTPS base URL required")
        if not self.token:
            raise ValueError("RUTIERP_API_KEY missing")

    def request(self, path, payload=None, allow_send=False):
        if path not in ("/auth", "/taxes", "/type/document", "/invoices", "/send") and not (
            path.startswith("/invoice/") and path.endswith("/status")
        ):
            raise ValueError("Endpoint not allowed")
        if path == "/send" and not allow_send:
            raise PermissionError("Sending fiscal documents requires explicit approval")
        if path == "/send" and not isinstance(payload, dict):
            raise ValueError("Invoice JSON required")
        if payload is not None and path != "/send":
            raise ValueError("Unexpected request body")
        data = json.dumps(payload).encode("utf-8") if payload is not None else (b"" if path == "/auth" else None)
        req = urllib.request.Request(
            self.base_url + "/api/ecf" + path,
            data=data,
            method="POST" if path in ("/auth", "/send") else "GET",
            headers={"Authorization": self.token, "Accept": "application/json", "Content-Type": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=20) as response:
            body = response.read(1048577)
            if len(body) > 1048576:
                raise ValueError("Oversized response")
            return json.loads(body)
