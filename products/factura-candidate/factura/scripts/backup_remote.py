"""Scheduled PostgreSQL backup to a private, encrypted S3 destination.

Credentials use the AWS credential chain. This job belongs in a separate
runtime with its own read-only DB role and bucket permissions, not the web
service. It never deletes snapshots or restores over an operational DB.
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import respaldo


def _remote_hash(client, bucket, key):
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    digest = hashlib.sha256()
    size = 0
    try:
        while True:
            chunk = body.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            digest.update(chunk)
    finally:
        body.close()
    return digest.hexdigest(), size


def publish_backup(client, bucket, prefix, manifest, path):
    """Publish the latest pointer only after a complete remote readback."""
    prefix = prefix.strip("/")
    if not prefix or ".." in prefix.split("/"):
        raise ValueError("prefijo de respaldo inválido")
    if respaldo._sha256(str(path)) != manifest["sha256"]:
        raise ValueError("respaldo local no coincide con su manifiesto")
    key = prefix + "/snapshots/" + uuid.uuid4().hex + "/" + Path(path).name
    client.upload_file(str(path), bucket, key,
        ExtraArgs={"ServerSideEncryption": "AES256", "Metadata": {"sha256": manifest["sha256"]}})
    digest, size = _remote_hash(client, bucket, key)
    if digest != manifest["sha256"] or size != manifest["bytes"]:
        raise RuntimeError("el objeto remoto no supera verificación; latest no actualizado")
    record = dict(manifest, object_key=key, verified_remote=True)
    metadata_key = key + ".json"
    client.put_object(Bucket=bucket, Key=metadata_key, Body=json.dumps(record).encode(),
                      ContentType="application/json", ServerSideEncryption="AES256")
    pointer = {"manifest_key": metadata_key, "sha256": digest, "created_at": manifest["creado_en"]}
    client.put_object(Bucket=bucket, Key=prefix + "/latest.json", Body=json.dumps(pointer).encode(),
                      ContentType="application/json", ServerSideEncryption="AES256")
    return {"status": "verified", "object_key": key, "sha256": digest, "bytes": size}


def fetch_backup(client, bucket, prefix, manifest_key, destination):
    """Download a selected snapshot for an independent restore rehearsal."""
    prefix = prefix.strip("/") + "/"
    if not manifest_key.startswith(prefix) or ".." in manifest_key.split("/"):
        raise ValueError("manifiesto fuera del espacio de respaldo")
    body = client.get_object(Bucket=bucket, Key=manifest_key)["Body"]
    try:
        manifest = json.loads(body.read())
    finally:
        body.close()
    key = manifest["object_key"]
    if not key.startswith(prefix) or ".." in key.split("/"):
        raise ValueError("objeto fuera del espacio de respaldo")
    destination = Path(destination)
    if destination.exists():
        raise ValueError("el destino ya existe; no se sobrescribe")
    destination.parent.mkdir(parents=True, exist_ok=True)
    body = client.get_object(Bucket=bucket, Key=key)["Body"]
    try:
        with destination.open("xb") as out:
            os.chmod(destination, 0o600)
            while True:
                chunk = body.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)
    finally:
        body.close()
    if respaldo._sha256(str(destination)) != manifest["sha256"] or destination.stat().st_size != manifest["bytes"]:
        destination.unlink()
        raise ValueError("copia descargada corrupta; restauración bloqueada")
    sidecar = destination.with_name(destination.name + ".json")
    sidecar.write_text(json.dumps(manifest))
    os.chmod(sidecar, 0o600)
    return manifest


def main():
    from factura_persist import db
    if os.environ.get("FACTURA_ENV") not in ("staging", "production") or db.backend() != "pg":
        raise RuntimeError("el job requiere entorno staging/production y PostgreSQL")
    if db._sslmode_dsn(db.dsn_configurado()) not in db._TLS_SEGUROS:
        raise RuntimeError("el job requiere DSN PostgreSQL con TLS explícito")
    bucket = os.environ["FACTURA_BACKUP_BUCKET"]
    prefix = os.environ["FACTURA_ENV"] + "/" + os.environ.get("FACTURA_BACKUP_PREFIX", "zayvero-factura")
    import boto3
    from botocore.config import Config
    client = boto3.client("s3", config=Config(connect_timeout=10, read_timeout=120,
                                            retries={"mode": "standard", "max_attempts": 3}))
    with tempfile.TemporaryDirectory(prefix="factura-backup-") as tmp:
        respaldo.RESPALDOS_DIR = tmp
        manifest = respaldo.crear("scheduled")
        path = Path(tmp) / manifest["archivo"]
        if respaldo.verificar(str(path))["verificacion"] != "OK":
            raise RuntimeError("respaldo local inválido")
        print(json.dumps(publish_backup(client, bucket, prefix, manifest, path)))


if __name__ == "__main__":
    main()
