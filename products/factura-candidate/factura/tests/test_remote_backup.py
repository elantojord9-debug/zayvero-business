"""Backup transport failure cases. In-memory S3 double, not production proof."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from scripts import backup_remote
from scripts import respaldo


class Storage:
    def __init__(self, corrupt=False):
        self.data = {}
        self.corrupt = corrupt
        self.encryption = []

    def upload_file(self, path, bucket, key, ExtraArgs):
        self.encryption.append(ExtraArgs["ServerSideEncryption"])
        self.data[key] = Path(path).read_bytes() + (b"bad" if self.corrupt else b"")

    def get_object(self, Bucket, Key):
        return {"Body": BytesIO(self.data[Key])}

    def put_object(self, Bucket, Key, Body, **kwargs):
        self.encryption.append(kwargs["ServerSideEncryption"])
        self.data[Key] = Body


class TestRemoteBackup(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "synthetic.dump"
        self.path.write_bytes(b"synthetic-snapshot")
        self.manifest = {"sha256": respaldo._sha256(str(self.path)), "bytes": self.path.stat().st_size,
                         "creado_en": "2026-10-28T00:00:00Z", "backend": "pg"}

    def test_verified_upload_and_fetch_have_identical_bytes(self):
        store = Storage()
        result = backup_remote.publish_backup(store, "private", "qa/factura", self.manifest, self.path)
        latest = json.loads(store.data["qa/factura/latest.json"])
        target = Path(self.tmp.name) / "restored.dump"
        backup_remote.fetch_backup(store, "private", "qa/factura", latest["manifest_key"], target)
        self.assertEqual(target.read_bytes(), self.path.read_bytes())
        self.assertEqual(result["status"], "verified")
        self.assertEqual(store.encryption, ["AES256"] * 3)

    def test_corrupt_remote_object_never_becomes_latest(self):
        store = Storage(corrupt=True)
        with self.assertRaises(RuntimeError):
            backup_remote.publish_backup(store, "private", "qa/factura", self.manifest, self.path)
        self.assertNotIn("qa/factura/latest.json", store.data)

    def test_tampered_download_is_rejected_and_removed(self):
        store = Storage()
        result = backup_remote.publish_backup(store, "private", "qa/factura", self.manifest, self.path)
        store.data[result["object_key"]] = b"altered"
        target = Path(self.tmp.name) / "restored.dump"
        with self.assertRaises(ValueError):
            backup_remote.fetch_backup(store, "private", "qa/factura", result["object_key"] + ".json", target)
        self.assertFalse(target.exists())

    def test_fetch_cannot_cross_environment_or_overwrite_a_file(self):
        store = Storage()
        result = backup_remote.publish_backup(store, "private", "qa/factura", self.manifest, self.path)
        with self.assertRaises(ValueError):
            backup_remote.fetch_backup(store, "private", "production/factura", result["object_key"] + ".json", self.path)
        with self.assertRaises(ValueError):
            backup_remote.fetch_backup(store, "private", "qa/factura", result["object_key"] + ".json", self.path)
