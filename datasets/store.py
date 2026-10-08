"""FASE 7A — Registro de datasets por empresa + directorio de cargas.

Almacenamiento:
  data/datasets/<company_id>/datasets.json   — registro de datasets
  data/uploads/<company_id>/<dataset_id>/    — archivos originales (intactos)

El registro vive separado por empresa: la consulta SIEMPRE se hace con el
company_id del TenantContext, nunca con un parámetro del cliente.
"""

from __future__ import annotations

import json
import os
import re
import uuid

from .models import Dataset

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATASETS_DIR = os.path.join(PROJECT_ROOT, "data", "datasets")
UPLOADS_DIR = os.path.join(PROJECT_ROOT, "data", "uploads")

# Límite de carga por archivo: 100 MB.
DATA_UPLOAD_MAX_BYTES = 100 * 1024 * 1024
ALLOWED_EXTENSIONS = {".csv", ".xlsx"}


def utcnow_iso() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sanitize_base_name(filename: str) -> str:
    """Nombre base seguro para archivos de salida (sin espacios ni símbolos)."""
    stem = os.path.splitext(os.path.basename(filename or ""))[0]
    stem = stem.strip().lower()
    stem = re.sub(r"[^a-z0-9áéíóúñü_-]+", "-", stem)
    stem = stem.strip("-") or "datos"
    return stem[:60]


class DatasetStore:
    """Registro de datasets de UNA empresa (aislado por company_id)."""

    def __init__(self, company_id: str, base_dir: str | None = None):
        if not company_id:
            raise ValueError("company_id es obligatorio")
        self.company_id = company_id
        root = base_dir or DATASETS_DIR
        self.dir = os.path.join(root, company_id)
        os.makedirs(self.dir, exist_ok=True)
        self._file = os.path.join(self.dir, "datasets.json")

    # ---- persistencia ------------------------------------------------
    def _load(self) -> list:
        if not os.path.exists(self._file):
            return []
        with open(self._file, encoding="utf-8") as f:
            return json.load(f)

    def _save(self, items: list):
        tmp = self._file + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self._file)

    # ---- CRUD ---------------------------------------------------------
    def list_datasets(self) -> list[Dataset]:
        return [Dataset.from_dict(d) for d in self._load()]

    def get_dataset(self, dataset_id: str) -> Dataset | None:
        for d in self.list_datasets():
            if d.dataset_id == dataset_id:
                return d
        return None

    def save_dataset(self, dataset: Dataset) -> Dataset:
        if dataset.company_id != self.company_id:
            raise ValueError("el dataset no pertenece a esta empresa")
        items = [d for d in self._load() if d["dataset_id"] != dataset.dataset_id]
        dataset.updated_at = utcnow_iso()
        items.append(dataset.to_dict())
        self._save(items)
        return dataset

    def create_dataset(self, *, nombre: str, source: str, is_demo: bool = False,
                       base_name: str = "") -> Dataset:
        now = utcnow_iso()
        ds = Dataset(
            dataset_id="DS-" + uuid.uuid4().hex[:12],
            company_id=self.company_id,
            nombre=(nombre or "").strip()[:160] or "Datos de la empresa",
            source=source,
            created_at=now,
            updated_at=now,
            status="DATA_UPLOADED",
            is_demo=is_demo,
            base_name=base_name or sanitize_base_name(nombre),
        )
        self.save_dataset(ds)
        return ds

    def set_active(self, dataset_id: str) -> Dataset:
        """Marca un dataset READY como activo. Solo uno activo por empresa."""
        target = self.get_dataset(dataset_id)
        if target is None:
            raise KeyError("dataset no encontrado")
        if target.status != "READY":
            raise ValueError("solo un conjunto de datos listo puede activarse")
        items = self._load()
        for d in items:
            d["is_active"] = (d["dataset_id"] == dataset_id)
            d["updated_at"] = utcnow_iso()
        self._save(items)
        return self.get_dataset(dataset_id)

    def get_active(self) -> Dataset | None:
        actives = [d for d in self.list_datasets() if d.is_active]
        return actives[0] if actives else None

    def dataset_state(self) -> str:
        """Estado agregado de los datos de la empresa para el dashboard."""
        datasets = self.list_datasets()
        if not datasets:
            return "NO_DATA"
        active = self.get_active()
        if active is None:
            # Hay datasets pero ninguno activo: usar el más reciente.
            latest = max(datasets, key=lambda d: d.created_at)
            return latest.status
        return active.status

    # ---- directorio de cargas -----------------------------------------
    def upload_dir(self, dataset_id: str) -> str:
        path = os.path.join(UPLOADS_DIR, self.company_id, dataset_id)
        os.makedirs(path, exist_ok=True)
        return path


def check_dataset_access(store_company_id: str, dataset: Dataset) -> None:
    """Verifica que un dataset pertenezca a la empresa del contexto.

    Lanza PermissionError si no coincide (el llamador lo convierte a
    PermissionDenied/403). El company_id viene del TenantContext.
    """
    if dataset is None or dataset.company_id != store_company_id:
        raise PermissionError("acceso denegado")


def ensure_demo_dataset() -> Dataset:
    """Registra el dataset DEMO (demo-retail) si no existe.

    No modifica ningún resultado existente: solo crea el registro que
    apunta a los archivos ya generados por las fases anteriores.
    """
    store = DatasetStore("demo-retail")
    existing = [d for d in store.list_datasets() if d.is_demo]
    if existing:
        return existing[0]
    now = utcnow_iso()
    ds = Dataset(
        dataset_id="DS-demo-retail-001",
        company_id="demo-retail",
        nombre="Online Retail II — Datos de demostración",
        source="demo",
        created_at=now,
        updated_at=now,
        status="READY",
        is_active=True,
        is_demo=True,
        base_name="online_retail_II_full",
        row_count=1067371,
        period_start="2009-12-01",
        period_end="2011-12-09",
        upload={
            "filename": "online_retail_II.xlsx",
            "format": "xlsx",
            "source": "Demo Dataset — UCI Online Retail II",
        },
        review={"checks": ["demo"]},
        mapping={"confirmed": True, "mapping": {}, "note": "demo"},
    )
    store.save_dataset(ds)
    return ds
