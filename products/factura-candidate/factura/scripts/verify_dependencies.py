"""Verify the shared source bundle, without installing or contacting services."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[2]
lock = json.loads((root / "SOURCE_LOCK.json").read_text())
for relative, expected in lock["dependency_files"].items():
    path = root / relative
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise SystemExit("dependencia faltante o alterada: " + relative)
print("dependencias verificadas:", len(lock["dependency_files"]))
