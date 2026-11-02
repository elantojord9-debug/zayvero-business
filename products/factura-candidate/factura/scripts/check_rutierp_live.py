#!/usr/bin/env python3
"""Read-only live check of the RutiERP connector. Never sends an e-CF.

Set RUTIERP_SANDBOX_URL and RUTIERP_API_KEY as secret environment variables.
Prints only non-sensitive diagnostic status. Exit code 0 means authenticated
and E31/E32 endpoints reachable, NOT DGII fiscal certification.
"""
import sys
from factura_ecf.rutierp_connector import RutiERPConnector, RutiERPError

def main():
    try:
        api = RutiERPConnector()
        auth = api.authenticate().get("data") or {}
        if auth.get("state") != "active":
            raise RutiERPError("Connector is not active")
        for prefix in ("E31", "E32"):
            api.document_type(prefix)
        api.taxes()
        print("OK: token active; E31/E32 document types and taxes reachable.")
        print("NO e-CF was sent; DGII acceptance is NOT verified.")
        return 0
    except (RutiERPError, ValueError) as exc:
        print("FAILED: live RutiERP validation:", type(exc).__name__)
        return 1

if __name__ == "__main__":
    sys.exit(main())
