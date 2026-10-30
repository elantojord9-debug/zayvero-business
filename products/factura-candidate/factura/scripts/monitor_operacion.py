"""Read-only operational checks. Exit nonzero for cron/monitor alerting.

No database writes, email delivery, restore or secret values in output.
A passing monitor does not replace a restore rehearsal or pilot acceptance.
"""
from datetime import datetime, timezone
import json
import os
import sys
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def read_json(client, bucket, key):
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        raw = body.read(65537)
        if len(raw) > 65536:
            raise ValueError("metadata too large")
        return json.loads(raw)
    finally:
        body.close()


def check_backup(client, bucket, prefix, now=None):
    """Check freshness and pointer/manifest/object consistency, without writes."""
    prefix = prefix.strip("/")
    if not prefix or ".." in prefix.split("/"):
        raise ValueError("invalid prefix")
    pointer = read_json(client, bucket, prefix + "/latest.json")
    key = pointer["manifest_key"]
    if not key.startswith(prefix + "/snapshots/") or ".." in key.split("/"):
        raise ValueError("manifest outside backup prefix")
    manifest = read_json(client, bucket, key)
    created = datetime.fromisoformat(manifest["creado_en"].replace("Z", "+00:00"))
    if created.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    age = ((now or datetime.now(timezone.utc)) - created).total_seconds()
    if age < -300 or age > 26 * 3600:
        raise ValueError("backup stale or future dated")
    object_key = manifest["object_key"]
    if key != object_key + ".json":
        raise ValueError("manifest/object mismatch")
    digest = manifest["sha256"]
    if (manifest.get("verified_remote") is not True or manifest.get("backend") != "pg"
            or pointer.get("sha256") != digest or pointer.get("created_at") != manifest["creado_en"]
            or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
        raise ValueError("unverified backup metadata")
    head = client.head_object(Bucket=bucket, Key=object_key)
    if (head["ContentLength"] != manifest["bytes"] or manifest["bytes"] <= 0
            or head.get("Metadata", {}).get("sha256") != digest
            or head.get("ServerSideEncryption") not in ("AES256", "aws:kms")):
        raise ValueError("object missing, changed or unencrypted")
    return {"ok": True, "age_hours": round(max(0, age) / 3600, 2)}


def check_health(origin, opener=urlopen):
    parsed = urlsplit(origin)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username
            or parsed.password or parsed.query or parsed.fragment or parsed.path not in ("", "/")):
        raise ValueError("canonical HTTPS origin required")
    url = origin.rstrip("/") + "/health"
    with opener(Request(url, headers={"Accept": "application/json"}), timeout=10) as response:
        if response.geturl() != url or response.status != 200:
            raise ValueError("health redirect or HTTP failure")
        raw = response.read(65537)
        if len(raw) > 65536 or json.loads(raw).get("ok") is not True:
            raise ValueError("database health failed")
    return {"ok": True}


def main():
    results = {}
    for name, action in (
        ("health", lambda: check_health(os.environ["FACTURA_PUBLIC_ORIGIN"])),
        ("backup", backup_from_environment),
    ):
        try:
            results[name] = action()
        except Exception as exc:
            # Provider exceptions can include signed URLs or credentials.
            results[name] = {"ok": False, "error_type": type(exc).__name__}
    print(json.dumps(results))
    return 0 if all(item["ok"] for item in results.values()) else 2


def backup_from_environment():
    import boto3
    from botocore.config import Config
    env = os.environ["FACTURA_ENV"]
    if env not in ("staging", "production"):
        raise ValueError("explicit cloud environment required")
    client = boto3.client("s3", config=Config(connect_timeout=10, read_timeout=20,
                                             retries={"max_attempts": 2}))
    prefix = env + "/" + os.environ.get("FACTURA_BACKUP_PREFIX", "zayvero-factura")
    return check_backup(client, os.environ["FACTURA_BACKUP_BUCKET"], prefix)


if __name__ == "__main__":
    sys.exit(main())
