"""FASE 7A — Recepción de archivos CSV/XLSX.

El archivo se guarda INTACTO en data/uploads/<company_id>/<dataset_id>/.
Aquí NO se procesa nada: solo se valida el formato y el tamaño.

Eventos de auditoría: DATASET_CREATED, DATASET_UPLOAD_STARTED,
DATASET_UPLOAD_COMPLETED.
"""

from __future__ import annotations

import os

from .models import Dataset
from .store import (
    ALLOWED_EXTENSIONS, DATA_UPLOAD_MAX_BYTES, DatasetStore,
    sanitize_base_name, utcnow_iso,
)


class UploadError(Exception):
    """Error empresarial de carga (mensaje apto para mostrar al cliente)."""


def validate_upload(filename: str, size_bytes: int) -> str:
    """Valida nombre y tamaño. Devuelve el formato ('csv'/'xlsx')."""
    ext = os.path.splitext(filename or "")[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise UploadError(
            "Formato no admitido. Por ahora puedes subir archivos CSV o XLSX."
        )
    if size_bytes is None or size_bytes <= 0:
        raise UploadError("El archivo está vacío.")
    if size_bytes > DATA_UPLOAD_MAX_BYTES:
        raise UploadError(
            "El archivo es demasiado grande. El límite actual es 100 MB. "
            "Si tu reporte es más grande, divídelo por periodos."
        )
    return ext[1:]


def receive_upload(store: DatasetStore, *, filename: str, size_bytes: int,
                   content_iter, user_id: str, nombre: str = "") -> Dataset:
    """Guarda el archivo subido y crea el registro del dataset.

    `content_iter` produce bytes (chunks). El archivo original se conserva
    intacto; el procesamiento ocurre después, sobre una copia de trabajo.
    """
    fmt = validate_upload(filename, size_bytes)
    ext = os.path.splitext(filename)[1].lower()

    ds = store.create_dataset(
        nombre=nombre or filename,
        source=fmt,
        base_name=sanitize_base_name(filename),
    )
    updir = store.upload_dir(ds.dataset_id)
    dest = os.path.join(updir, "original" + ext)

    written = 0
    try:
        with open(dest, "wb") as fh:
            for chunk in content_iter:
                if not chunk:
                    continue
                fh.write(chunk)
                written += len(chunk)
                if written > DATA_UPLOAD_MAX_BYTES:
                    raise UploadError(
                        "El archivo es demasiado grande. El límite actual es 100 MB."
                    )
    except Exception:
        # Limpieza: no dejar archivos parciales.
        try:
            if os.path.exists(dest):
                os.remove(dest)
        except OSError:
            pass
        raise

    ds.upload = {
        "filename": os.path.basename(filename),
        "size_bytes": written,
        "format": fmt,
        "uploaded_at": utcnow_iso(),
        "uploaded_by": user_id,
        "path": dest,
    }
    ds.status = "DATA_UPLOADED"
    store.save_dataset(ds)
    return ds
