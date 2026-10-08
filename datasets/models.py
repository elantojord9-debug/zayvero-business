"""FASE 7A — Modelo de Dataset empresarial.

Un Dataset representa una carga de datos de una empresa:
  DATA_UPLOADED  → archivo recibido, pendiente de revisión
  PROCESSING     → el análisis está en curso
  READY          → inteligencia disponible
  ERROR          → el procesamiento falló (mensaje empresarial)
  INSUFFICIENT_DATA → no hay evidencia suficiente para un análisis completo

El dataset "activo" (is_active) es el que consume el dashboard/Advisor.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

# Estados de datos visibles para el cliente.
DATASET_STATUSES = {
    "DATA_UPLOADED": "Datos recibidos",
    "PROCESSING": "Procesando datos",
    "READY": "Listo",
    "ERROR": "No se pudo procesar",
    "INSUFFICIENT_DATA": "Datos insuficientes",
}

# Mensajes empresariales por estado (nunca jerga técnica).
STATUS_MESSAGES = {
    "NO_DATA": "Tu empresa todavía no tiene datos cargados.",
    "DATA_UPLOADED": "Tus datos fueron recibidos. Revísalos y confirma para analizarlos.",
    "PROCESSING": "Estamos procesando tus datos. Te avisaremos cuando el análisis esté listo.",
    "READY": "ZAYVERO está listo para analizar tu empresa.",
    "ERROR": "No pudimos procesar estos datos.",
    "INSUFFICIENT_DATA": "Los datos no tienen información suficiente para un análisis completo.",
}


@dataclass
class Dataset:
    dataset_id: str
    company_id: str
    nombre: str
    source: str  # "csv" | "xlsx" | "demo"
    created_at: str
    updated_at: str
    status: str = "DATA_UPLOADED"
    is_active: bool = False
    is_demo: bool = False
    base_name: str = ""  # nombre base de los archivos de salida
    # Archivo original (nunca modificado)
    upload: Dict[str, Any] = field(default_factory=dict)
    # {"filename","size_bytes","format","uploaded_at","uploaded_by","path"}
    # Revisión de datos (validación previa)
    review: Dict[str, Any] = field(default_factory=dict)
    # Mapeo de columnas confirmado por el usuario
    mapping: Dict[str, Any] = field(default_factory=dict)
    # {"confirmed": bool, "mapping": {canonical: source_col}, "confirmed_at": str}
    # Métricas del dataset procesado
    row_count: Optional[int] = None
    period_start: Optional[str] = None
    period_end: Optional[str] = None
    # Procesamiento
    processing: Dict[str, Any] = field(default_factory=dict)
    # {"started_at","finished_at","error","error_detail_client","steps":[...]}
    # Rutas de los resultados generados (trazabilidad)
    outputs: Dict[str, str] = field(default_factory=dict)
    warnings: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Dataset":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})

    def status_label(self) -> str:
        return DATASET_STATUSES.get(self.status, self.status)

    def status_message(self) -> str:
        return STATUS_MESSAGES.get(self.status, "")
