from datetime import datetime, timezone, timedelta
from io import BytesIO
import json
import unittest
from unittest.mock import patch
from scripts import monitor_operacion as monitor


class Store:
    def __init__(self):
        self.now = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)
        self.key = "production/factura/snapshots/one/db.dump"
        self.manifest = {"object_key": self.key, "creado_en": self.now.isoformat(),
                         "sha256": "a" * 64, "bytes": 123, "backend": "pg", "verified_remote": True}
        self.pointer = {"manifest_key": self.key + ".json", "sha256": "a" * 64,
                        "created_at": self.manifest["creado_en"]}
        self.head = {"ContentLength": 123, "Metadata": {"sha256": "a" * 64},
                     "ServerSideEncryption": "AES256"}

    def get_object(self, Bucket, Key):
        value = self.pointer if Key.endswith("latest.json") else self.manifest
        return {"Body": BytesIO(json.dumps(value).encode())}

    def head_object(self, **kwargs):
        return self.head


class MonitorTests(unittest.TestCase):
    def check(self, store, now=None):
        return monitor.check_backup(store, "private", "production/factura", now or store.now)

    def test_fresh_verified_snapshot_passes(self):
        self.assertTrue(self.check(Store())["ok"])

    def test_stale_or_future_backup_fails(self):
        for delta in (timedelta(hours=27), timedelta(minutes=-6)):
            s = Store()
            with self.assertRaises(ValueError):
                self.check(s, s.now + delta)

    def test_cross_environment_pointer_rejected(self):
        s = Store(); s.pointer["manifest_key"] = "staging/factura/snapshots/x.json"
        with self.assertRaises(ValueError): self.check(s)

    def test_unverified_or_changed_snapshot_fails(self):
        for field, value in (("verified_remote", False), ("sha256", "b" * 64), ("bytes", 999)):
            s = Store(); s.manifest[field] = value
            with self.assertRaises(ValueError): self.check(s)

    def test_unencrypted_snapshot_fails(self):
        s = Store(); s.head.pop("ServerSideEncryption")
        with self.assertRaises(ValueError): self.check(s)

    def test_failed_health_or_backup_exits_nonzero_without_secret_leak(self):
        with patch.object(monitor, "check_health", side_effect=RuntimeError("SECRET")), \
             patch.object(monitor, "backup_from_environment", return_value={"ok": True}), \
             patch.dict("os.environ", {"FACTURA_PUBLIC_ORIGIN": "https://example.invalid"}), \
             patch("sys.stdout", new_callable=__import__('io').StringIO) as output:
            self.assertEqual(monitor.main(), 2)
            self.assertNotIn("SECRET", output.getvalue())
            self.assertTrue(json.loads(output.getvalue())["backup"]["ok"])

    def test_insecure_health_origin_rejected_without_network(self):
        for origin in ("http://example.invalid", "https://user:pass@example.invalid", "https://example.invalid/a"):
            with self.assertRaises(ValueError): monitor.check_health(origin)
