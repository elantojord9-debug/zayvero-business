"""FASE 7A — Client Onboarding + Business Data Workspace.

Paquete de datasets empresariales por tenant:

  models.py      Dataset (id, company_id, nombre, source, status, métricas)
  store.py       registro de datasets por empresa + directorio de cargas
  upload.py      recepción y guardado de CSV/XLSX (sin procesar todavía)
  validation.py  "Revisión de datos" antes de procesar (solo lectura)
  mapping.py     sugerencias de mapeo de columnas (el usuario confirma)
  preview.py     primeras 10 filas + conteo total
  processing.py  orquesta el pipeline existente 1A→1B→2A→2B→2C→4A→4B→4C→5A

REGLAS:
- company_id SIEMPRE proviene del TenantContext, nunca del frontend.
- El archivo original nunca se modifica silenciosamente.
- Sin inferencias empresariales sin datos.
- Lenguaje empresarial hacia el cliente (nunca Parquet/pipeline/DataFrame).
"""

from __future__ import annotations

from .models import Dataset, DATASET_STATUSES
from .store import (
    DatasetStore, check_dataset_access, ensure_demo_dataset,
    sanitize_base_name, DATA_UPLOAD_MAX_BYTES,
)
from .upload import receive_upload
from .validation import build_review
from .mapping import suggest_mapping, confirm_mapping
from .preview import build_preview
from .processing import process_dataset, process_in_background

__all__ = [
    "Dataset", "DATASET_STATUSES",
    "DatasetStore", "check_dataset_access", "ensure_demo_dataset",
    "sanitize_base_name", "DATA_UPLOAD_MAX_BYTES",
    "receive_upload",
    "build_review",
    "suggest_mapping", "confirm_mapping",
    "build_preview",
    "process_dataset", "process_in_background",
]
