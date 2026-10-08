"""FASE 7A — Orquestación del procesamiento de un dataset.

Reutiliza la infraestructura EXISTENTE, en orden:

  1A/1C  run_pipeline()                    → Parquet normalizado
  1B     build_profile() + save_profile()   → perfil empresarial
  2A     detect_anomalies() + save_report() → anomalías
  2B     build_from_parquet() + save_report → hallazgos
  2C     build_context_findings() + save    → contexto
  4A     build_predictions()                → predicciones
  4B     build_prediction_intelligence()    → inteligencia de predicción
  4C     build_validation_report() + save   → validación
  5A     build_business_context() + save    → contexto unificado

NO crea otro pipeline paralelo. NO modifica las fases: las consume.

El procesamiento corre en un hilo en segundo plano para no congelar la
interfaz; el dataset pasa por PROCESSING → READY (o ERROR con mensaje
empresarial, nunca stack trace).
"""

from __future__ import annotations

import json
import os
import threading
import traceback
from typing import Any, Dict, Optional

from .models import Dataset
from .store import DatasetStore, utcnow_iso

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _friendly_error(exc: Exception) -> str:
    """Mensaje empresarial para el cliente (sin jerga técnica)."""
    msg = str(exc)[:400]
    if not msg:
        msg = "error desconocido"
    return (
        "No pudimos procesar estos datos. "
        "Revisa que el archivo tenga columnas de fecha, cantidad y precio, "
        "y vuelve a intentarlo. "
        f"(Detalle: {msg})"
    )


# Etiqueta del dataset de referencia usada por las fases anteriores.
_DEMO_LABEL = "Demo Dataset — UCI Online Retail II"


def _relabel_walk(node: Any, label: str) -> bool:
    """Reemplaza la etiqueta demo por el nombre del reporte del cliente.

    Solo toca valores que coincidan EXACTAMENTE con la etiqueta de
    referencia, en campos de metadatos ('dataset_label', 'dataset').
    Devuelve True si hubo cambios.
    """
    changed = False
    if isinstance(node, dict):
        for key, value in node.items():
            if (key in ("dataset_label", "dataset")
                    and isinstance(value, str)
                    and value == _DEMO_LABEL):
                node[key] = label
                changed = True
            elif _relabel_walk(value, label):
                changed = True
    elif isinstance(node, list):
        for item in node:
            if _relabel_walk(item, label):
                changed = True
    return changed


def _relabel_outputs(outputs: Dict[str, str], label: str) -> None:
    """Etiqueta los JSON generados con el nombre del reporte del cliente."""
    for key, path in outputs.items():
        if not path or key == "parquet" or not path.endswith(".json"):
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            continue
        if _relabel_walk(data, label):
            tmp = path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=1, default=str)
            os.replace(tmp, path)


def _apply_mapping_work_copy(original_path: str, mapping: Dict[str, str],
                             work_dir: str) -> str:
    """Crea una copia de trabajo con las columnas renombradas a canónicas.

    El archivo original NUNCA se modifica: se trabaja sobre una copia.
    El mapeo fue confirmado previamente por el usuario.
    """
    import pandas as pd

    ext = os.path.splitext(original_path)[1].lower()
    os.makedirs(work_dir, exist_ok=True)
    work_path = os.path.join(work_dir, "work" + ext)
    # mapping: {canonical: source_col} → invertir para rename.
    rename = {src: canon for canon, src in (mapping or {}).items() if src}

    if ext == ".csv":
        df = pd.read_csv(original_path)
        if rename:
            df = df.rename(columns=rename)
        df.to_csv(work_path, index=False)
    else:
        sheets = pd.read_excel(original_path, sheet_name=None)
        with pd.ExcelWriter(work_path, engine="openpyxl") as writer:
            for name, df in sheets.items():
                if rename:
                    df = df.rename(columns=rename)
                df.to_excel(writer, sheet_name=str(name)[:31], index=False)
    return work_path


def _load_inputs_for(company_id: str, base: str) -> Dict[str, Any]:
    """Carga los outputs generados para construir el contexto 5A.

    Equivalente a business_context.load_inputs pero con el nombre base del
    dataset de la empresa (sin hardcodear el demo).
    """
    base_dir = os.path.join(PROJECT_ROOT, "data")

    def load(*parts):
        path = os.path.join(base_dir, *parts)
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)

    return {
        "profile": load("profiles", company_id, f"{base}_profile.json"),
        "findings": load("findings", company_id, f"{base}_findings.json"),
        "context": load("context", company_id, f"{base}_context.json"),
        "pi": load("prediction_intelligence", company_id,
                   f"{base}_prediction_intelligence.json"),
        "validation": load("prediction_validation", company_id,
                           f"{base}_prediction_validation.json"),
    }


def process_dataset(store: DatasetStore, dataset_id: str,
                    tenant_store=None, user_id: str = "") -> Dataset:
    """Ejecuta el pipeline completo de forma SINCRÓNICA. Devuelve el dataset.

    Diseñado para correr en un hilo en segundo plano (ver
    process_in_background). Actualiza el registro en cada etapa.
    """
    from tenant import audit as audit_mod

    ds = store.get_dataset(dataset_id)
    if ds is None:
        raise KeyError("dataset no encontrado")
    company_id = store.company_id
    base = ds.base_name

    def _audit(action: str, result: str = "ok", metadata: dict | None = None):
        if tenant_store is not None:
            audit_mod.log_event(
                tenant_store, company_id=company_id, user_id=user_id,
                action=action, resource=f"dataset:{dataset_id}",
                result=result, metadata=metadata or {},
            )

    def _mark(status: str, **kw):
        ds2 = store.get_dataset(dataset_id)
        ds2.status = status
        for k, v in kw.items():
            setattr(ds2, k, v)
        store.save_dataset(ds2)
        return ds2

    ds = _mark("PROCESSING", processing={
        "started_at": utcnow_iso(), "finished_at": None,
        "error": None, "steps": [],
    })
    _audit("DATASET_PROCESSING_STARTED",
           metadata={"filename": (ds.upload or {}).get("filename", "")})
    steps = []

    try:
        original = (ds.upload or {}).get("path", "")
        if not original or not os.path.exists(original):
            raise RuntimeError("No se encontró el archivo cargado.")

        # Copia de trabajo con el mapeo confirmado (el original intacto).
        work_dir = os.path.join(store.upload_dir(dataset_id), "work")
        mapping = (ds.mapping or {}).get("mapping") or {}
        work_path = _apply_mapping_work_copy(original, mapping, work_dir)
        steps.append("Datos preparados")

        # 1A/1C — pipeline existente.
        from pipeline import run_pipeline
        record = run_pipeline(work_path, company_id, output_name=base)
        if record.get("errors"):
            raise RuntimeError(
                "El archivo tiene problemas que impiden el análisis: "
                + "; ".join(str(e) for e in record["errors"][:3])
            )
        parquet_path = os.path.join(
            PROJECT_ROOT, "data", "processed", company_id, f"{base}.parquet")
        steps.append("Datos organizados")
        ds.row_count = int(record.get("n_processed_rows", 0) or 0)

        # 1B — perfil empresarial.
        from profiling.builder import build_profile, save_profile
        # FASE 7A: etiqueta con el nombre del reporte del cliente (nunca como demo).
        profile = build_profile(parquet_path, company_id, dataset_label=ds.nombre)
        profile_path = save_profile(profile, company_id, base)
        steps.append("Perfil empresarial")
        dr = (profile.get("date_range") or {})
        ds.period_start = str(dr.get("min_date", ""))[:10] or None
        ds.period_end = str(dr.get("max_date", ""))[:10] or None

        # 2A — anomalías.
        from anomalies.report import detect_anomalies
        from anomalies.report import save_report as save_anomalies
        anomalies = detect_anomalies(parquet_path, company_id, max_anomalies=1000)
        anomalies_path = save_anomalies(anomalies, company_id, base)
        steps.append("Detección de comportamientos inusuales")

        # 2B — hallazgos (desde el Parquet: 2A sin límite + 2B).
        from findings.report import build_from_parquet
        from findings.report import save_report as save_findings
        findings = build_from_parquet(parquet_path, company_id, top_limit=100)
        findings_path = save_findings(findings, company_id, base)
        steps.append("Análisis de impacto")

        # 2C — contexto empresarial.
        from context.report import build_context_findings
        from context.report import save_report as save_context_findings
        ctx_findings = build_context_findings(
            findings_path, parquet_path, company_id)
        ctx_findings_path = save_context_findings(ctx_findings, company_id, base)
        steps.append("Contexto empresarial")

        # 4A — predicciones.
        from prediction.report import build_predictions
        predictions = build_predictions(
            parquet_path, company_id=company_id,
            output_dir=os.path.join(PROJECT_ROOT, "data", "predictions"),
            top_products=25, top_countries=10,
        )
        predictions_path = predictions["report_metadata"]["output_path"]
        steps.append("Predicciones")

        # 4B — inteligencia de predicción.
        from prediction_intelligence import build_prediction_intelligence
        pi_report = build_prediction_intelligence(predictions)
        pi_path = os.path.join(
            PROJECT_ROOT, "data", "prediction_intelligence", company_id,
            f"{base}_prediction_intelligence.json")
        os.makedirs(os.path.dirname(pi_path), exist_ok=True)
        with open(pi_path, "w", encoding="utf-8") as fh:
            json.dump(pi_report, fh, ensure_ascii=False, indent=1)
        steps.append("Inteligencia de predicción")

        # 4C — validación de predicciones.
        from prediction_validation import build_validation_report
        from prediction_validation.report import save_report as save_validation
        validation = build_validation_report(
            predictions_path=predictions_path,
            parquet_path=parquet_path,
            company_id=company_id,
            insights_path=pi_path,
        )
        validation_path = os.path.join(
            PROJECT_ROOT, "data", "prediction_validation", company_id,
            f"{base}_prediction_validation.json")
        save_validation(validation, validation_path)
        steps.append("Validación de predicciones")

        # 5A — contexto unificado.
        from business_context.report import build_business_context, save_context
        inputs = _load_inputs_for(company_id, base)
        context = build_business_context(inputs, company_id=company_id)
        context_path = os.path.join(
            PROJECT_ROOT, "data", "business_context", company_id,
            f"{base}_business_context.json")
        save_context(context, context_path)
        steps.append("Contexto unificado")

        ds.outputs = {
            "parquet": parquet_path,
            "profile": profile_path,
            "anomalies": anomalies_path,
            "findings": findings_path,
            "context_findings": ctx_findings_path,
            "predictions": predictions_path,
            "prediction_intelligence": pi_path,
            "prediction_validation": validation_path,
            "business_context": context_path,
        }
        # FASE 7A: etiquetar los outputs con el nombre del reporte del
        # cliente. Las fases anteriores usan la etiqueta del dataset de
        # referencia por defecto; aquí se reemplaza en los archivos generados
        # para esta empresa, sin modificar la lógica de esas fases.
        _relabel_outputs(ds.outputs, ds.nombre)
        ds.processing = {
            "started_at": ds.processing.get("started_at"),
            "finished_at": utcnow_iso(),
            "error": None,
            "steps": steps,
        }
        ds.status = "READY"
        # Primer dataset listo → se activa automáticamente.
        others_ready = [d for d in store.list_datasets()
                        if d.status == "READY" and d.dataset_id != dataset_id]
        if not others_ready and not any(d.is_active for d in store.list_datasets()):
            ds.is_active = True
        store.save_dataset(ds)
        _audit("DATASET_PROCESSING_COMPLETED",
               metadata={"row_count": ds.row_count, "steps": len(steps)})
        if ds.is_active:
            _audit("DATASET_ACTIVATED")
        return store.get_dataset(dataset_id)

    except Exception as exc:  # noqa: BLE001 — error empresarial, no stack trace
        ds = store.get_dataset(dataset_id)
        ds.status = "ERROR"
        ds.processing = {
            "started_at": (ds.processing or {}).get("started_at"),
            "finished_at": utcnow_iso(),
            "error": _friendly_error(exc),
            "steps": steps,
        }
        store.save_dataset(ds)
        _audit("DATASET_PROCESSING_FAILED", result="error",
               metadata={"error": _friendly_error(exc)})
        return store.get_dataset(dataset_id)


def process_in_background(store: DatasetStore, dataset_id: str,
                          tenant_store=None, user_id: str = "") -> threading.Thread:
    """Inicia el procesamiento en segundo plano (no congela la interfaz)."""
    t = threading.Thread(
        target=process_dataset,
        args=(store, dataset_id),
        kwargs={"tenant_store": tenant_store, "user_id": user_id},
        daemon=True,
        name=f"zayvero-process-{dataset_id}",
    )
    t.start()
    return t
