"""FASE 6B — Capa de datos del backend.

Consume los outputs reales existentes SIN duplicar lógica de negocio:

  2B + 2C  → dashboard.adapter.load_dataset()  (vista unificada de findings)
  5A        → BusinessIntelligenceContext JSON
  4A        → predictions JSON
  4B        → prediction_intelligence JSON
  4C        → prediction_validation JSON
  6A        → TenantStore, check_data_access, autorización

TENANT ISOLATION: los datos empresariales solo se sirven cuando el
company_id del TenantContext es el dueño del dataset (verificado con
tenant.authorization.check_data_access). Para cualquier otra empresa la
capa devuelve PermissionDenied — el dataset demo-retail NUNCA se mezcla
con datos de otros tenants.
"""

from __future__ import annotations

import json
import os
import threading
from typing import Any, Dict, List, Optional

from tenant import (
    TenantStore, TenantContext, check_data_access, PermissionDenied,
    DEMO_DATASET_ID,
)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CONTEXT_5A_PATH = os.path.join(
    _PROJECT_ROOT, "data", "business_context", "demo-retail",
    "online_retail_II_business_context.json",
)
PREDICTIONS_4A_PATH = os.path.join(
    _PROJECT_ROOT, "data", "predictions", "demo-retail",
    "online_retail_II_full_predictions.json",
)
INTELLIGENCE_4B_PATH = os.path.join(
    _PROJECT_ROOT, "data", "prediction_intelligence", "demo-retail",
    "online_retail_II_prediction_intelligence.json",
)
VALIDATION_4C_PATH = os.path.join(
    _PROJECT_ROOT, "data", "prediction_validation", "demo-retail",
    "online_retail_II_prediction_validation.json",
)
FINDINGS_2B_PATH = os.path.join(
    _PROJECT_ROOT, "data", "findings", "demo-retail",
    "online_retail_II_full_findings.json",
)
CONTEXT_2C_PATH = os.path.join(
    _PROJECT_ROOT, "data", "context", "demo-retail",
    "online_retail_II_full_context.json",
)


class WebappConfig:
    """Rutas de los outputs existentes. Sin hardcodear resultados."""

    def __init__(self, context_5a_path: str = CONTEXT_5A_PATH,
                 predictions_4a_path: str = PREDICTIONS_4A_PATH,
                 intelligence_4b_path: str = INTELLIGENCE_4B_PATH,
                 validation_4c_path: str = VALIDATION_4C_PATH,
                 findings_2b_path: str = FINDINGS_2B_PATH,
                 context_2c_path: str = CONTEXT_2C_PATH):
        self.context_5a_path = context_5a_path
        self.predictions_4a_path = predictions_4a_path
        self.intelligence_4b_path = intelligence_4b_path
        self.validation_4c_path = validation_4c_path
        self.findings_2b_path = findings_2b_path
        self.context_2c_path = context_2c_path


def config_for_company(company_id: str):
    """Resuelve las rutas de datos de la empresa desde su dataset activo.

    - demo-retail → rutas DEMO existentes (compatibilidad total).
    - otra empresa con dataset READY activo → rutas de su propio dataset.
    - sin dataset activo → rutas None (estado NO_DATA).
    """
    from datasets import DatasetStore, ensure_demo_dataset

    if company_id == DEMO_DATASET_ID:
        ensure_demo_dataset()
        return WebappConfig(), "READY"

    store = DatasetStore(company_id)
    active = store.get_active()
    if active is None or active.status != "READY":
        state = store.dataset_state()
        return WebappConfig(
            context_5a_path=None, predictions_4a_path=None,
            intelligence_4b_path=None, validation_4c_path=None,
            findings_2b_path=None, context_2c_path=None,
        ), state

    base = active.base_name
    root = _PROJECT_ROOT
    return WebappConfig(
        context_5a_path=os.path.join(
            root, "data", "business_context", company_id,
            f"{base}_business_context.json"),
        predictions_4a_path=os.path.join(
            root, "data", "predictions", company_id,
            f"{base}_predictions.json"),
        intelligence_4b_path=os.path.join(
            root, "data", "prediction_intelligence", company_id,
            f"{base}_prediction_intelligence.json"),
        validation_4c_path=os.path.join(
            root, "data", "prediction_validation", company_id,
            f"{base}_prediction_validation.json"),
        findings_2b_path=os.path.join(
            root, "data", "findings", company_id, f"{base}_findings.json"),
        context_2c_path=os.path.join(
            root, "data", "context", company_id, f"{base}_context.json"),
    ), "READY"


def _load_json(path: str) -> Dict[str, Any]:
    if not path:
        raise FileNotFoundError("sin datos para esta empresa")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


class TenantData:
    """Datos empresariales de UNA empresa, verificados contra el tenant.

    Se construye una instancia por company_id con acceso verificado.
    """

    def __init__(self, store: TenantStore, ctx: TenantContext,
                 config: Optional[WebappConfig] = None):
        # Verificación de tenant: el dataset DEMO solo pertenece a la
        # empresa demo. Para otras empresas, el aislamiento viene de que
        # TODAS las rutas se resuelven desde el company_id del contexto
        # (config_for_company) — nunca de parámetros del usuario.
        if ctx.company_id == DEMO_DATASET_ID:
            check_data_access(store, ctx, DEMO_DATASET_ID)
        self.company_id = ctx.company_id
        self.config = config or WebappConfig()
        self._lock = threading.Lock()
        self._context_5a: Optional[Dict[str, Any]] = None
        self._findings_view: Optional[Dict[str, Any]] = None
        self._predictions_4a: Optional[Dict[str, Any]] = None
        self._intelligence_4b: Optional[Dict[str, Any]] = None
        self._validation_4c: Optional[Dict[str, Any]] = None
        self._diagnostic: Optional[Dict[str, Any]] = None
        self._advisor_pipeline = None

    # ---- estado de datos (FASE 7A) ----------------------------------
    def dataset_state(self) -> str:
        """NO_DATA | DATA_UPLOADED | PROCESSING | READY | ERROR | ..."""
        if self.config.context_5a_path is None:
            from datasets import DatasetStore
            return DatasetStore(self.company_id).dataset_state()
        return "READY"

    @staticmethod
    def _company_config_view(company) -> Dict[str, Any] | None:
        """FASE 7C: vista pública de la configuración internacional."""
        if company is None:
            return None
        from intl import config as intl_config_mod
        return intl_config_mod.config_view(company)

    def workspace(self, store: TenantStore, ctx: TenantContext) -> Dict[str, Any]:
        """Sección 'Mi empresa': datos reales, sin hardcodear."""
        from datasets import DatasetStore, ensure_demo_dataset
        from datasets.models import STATUS_MESSAGES

        company = store.get_company(ctx.company_id)
        ds_store = DatasetStore(ctx.company_id)
        if ctx.company_id == DEMO_DATASET_ID:
            ensure_demo_dataset()
        datasets = ds_store.list_datasets()
        active = ds_store.get_active()
        state = self.dataset_state()

        def _ds_view(d):
            up = d.upload or {}
            return {
                "dataset_id": d.dataset_id,
                "nombre": d.nombre,
                "source": d.source,
                "status": d.status,
                "status_label": d.status_label(),
                "is_active": d.is_active,
                "is_demo": d.is_demo,
                "row_count": d.row_count,
                "period_start": d.period_start,
                "period_end": d.period_end,
                "created_at": d.created_at,
                "updated_at": d.updated_at,
                "filename": up.get("filename"),
                "file_size": up.get("size_bytes"),
                "processing_error": (d.processing or {}).get("error"),
                "mapping_confirmed": bool((d.mapping or {}).get("confirmed")),
                "review_done": bool(d.review),
            }

        return {
            "company": {
                "company_id": ctx.company_id,
                "name": ctx.company_name or (company.name if company else ""),
                "status": company.status if company else "unknown",
                "is_demo": bool(company and company.is_demo),
                # FASE 7C: configuración internacional (del TenantContext).
                "config": self._company_config_view(company),
            },
            "user": {
                "name": ctx.user_name, "email": ctx.email, "role": ctx.role,
                "permissions": ctx.permissions,
            },
            "dataset_state": state,
            "dataset_state_message": STATUS_MESSAGES.get(state, ""),
            "active_dataset": _ds_view(active) if active else None,
            "datasets": [_ds_view(d) for d in sorted(
                datasets, key=lambda d: d.created_at, reverse=True)],
            "can_upload": "data.admin" in (ctx.permissions or []),
        }

    # ---- cargadores (lazy, cacheados, thread-safe) --------------------
    def context_5a(self) -> Dict[str, Any]:
        with self._lock:
            if self._context_5a is None:
                self._context_5a = _load_json(self.config.context_5a_path)
            return self._context_5a

    def findings_view(self) -> Dict[str, Any]:
        """Vista unificada 2B+2C reutilizando el adapter del Dashboard (FASE 3)."""
        with self._lock:
            if self._findings_view is None:
                # Import diferido: reutiliza FASE 3 sin modificarla.
                from dashboard.adapter import load_dataset
                self._findings_view = load_dataset(
                    self.config.findings_2b_path, self.config.context_2c_path)
            return self._findings_view

    def predictions_4a(self) -> Dict[str, Any]:
        with self._lock:
            if self._predictions_4a is None:
                self._predictions_4a = _load_json(self.config.predictions_4a_path)
            return self._predictions_4a

    def intelligence_4b(self) -> Dict[str, Any]:
        with self._lock:
            if self._intelligence_4b is None:
                self._intelligence_4b = _load_json(self.config.intelligence_4b_path)
            return self._intelligence_4b

    def validation_4c(self) -> Dict[str, Any]:
        with self._lock:
            if self._validation_4c is None:
                self._validation_4c = _load_json(self.config.validation_4c_path)
            return self._validation_4c

    def advisor_pipeline(self):
        """Pipeline 5B+5C (lazy)."""
        with self._lock:
            if self._advisor_pipeline is None:
                # Import diferido: no modifica fases anteriores.
                from llm_advisor.pipeline import LLMAdvisorPipeline
                self._advisor_pipeline = LLMAdvisorPipeline(self.config.context_5a_path)
            return self._advisor_pipeline

    # ---- FASE 7B: Diagnóstico Ejecutivo --------------------------------
    def diagnostic(self, store: TenantStore, ctx: TenantContext):
        """Diagnóstico Ejecutivo ZAYVERO (FASE 7B).

        Devuelve (payload, generated_fresh). Consume únicamente outputs
        existentes (5A, 4B, 4C); no crea inteligencia nueva. El resultado
        se cachea por empresa mientras el dataset no cambie (la caché se
        invalida al subir, procesar o activar datasets). Los estados sin
        datos listos (INSUFFICIENT) no se cachean porque el estado puede
        cambiar mientras el procesamiento está en curso.
        """
        from diagnostic import build_diagnostic

        # Chequeo de caché (rápido, con lock).
        with self._lock:
            if self._diagnostic is not None:
                return self._diagnostic, False

        # Carga de inputs SIN mantener el lock: los loaders (context_5a,
        # intelligence_4b, validation_4c) adquieren self._lock internamente
        # (threading.Lock no reentrante) y workspace()/dataset_state() no
        # lo usan. Mantener el lock aquí causaría deadlock.
        state = self.dataset_state()
        company = store.get_company(ctx.company_id)
        company_name = (ctx.company_name
                        or (company.name if company else ctx.company_id))
        is_demo = bool(company and company.is_demo)
        ws = self.workspace(store, ctx)
        active = ws.get("active_dataset") or {}
        dataset_info = None
        if active:
            dataset_info = {
                "row_count": active.get("row_count"),
                "period_start": active.get("period_start"),
                "period_end": active.get("period_end"),
                "source": ("Datos de demostración" if active.get("is_demo")
                           else (active.get("filename")
                                 or active.get("source"))),
                "updated_at": active.get("updated_at"),
            }
        source_files = [p for p in (
            self.config.context_5a_path,
            self.config.intelligence_4b_path,
            self.config.validation_4c_path) if p]

        if state == "READY":
            ctx5a = self.context_5a()
            i4b = self.intelligence_4b()
            v4c = self.validation_4c()
        else:
            ctx5a = i4b = v4c = None

        payload = build_diagnostic(
            ctx5a, i4b, v4c, company_id=ctx.company_id,
            company_name=company_name, is_demo=is_demo,
            dataset_state=state, dataset_info=dataset_info,
            source_files=source_files)

        if state != "READY":
            # No cachear: el estado puede cambiar (procesamiento en curso).
            return payload, True

        # Guardar en caché con doble chequeo (otro hilo pudo generarlo).
        with self._lock:
            if self._diagnostic is None:
                self._diagnostic = payload
            if self._diagnostic is payload:
                return payload, True
            return self._diagnostic, False

    # ---- resúmenes para la UI ------------------------------------------
    def executive_summary(self) -> Dict[str, Any]:
        """Resumen ejecutivo REAL desde el BusinessIntelligenceContext (5A).

        Sin KPIs inventados: todo valor proviene del contexto existente.
        Si la empresa no tiene datos listos, devuelve el estado (NO_DATA,
        PROCESSING, ERROR...) para que el dashboard muestre onboarding.
        """
        state = self.dataset_state()
        if state != "READY":
            return {"dataset_state": state, "ready": False}
        ctx = self.context_5a()
        attention = ctx.get("attention_summary") or {}
        signals = attention.get("signals") or {}
        confidence = ctx.get("context_confidence") or {}
        snapshot = ctx.get("business_snapshot") or {}
        metrics = {m.get("metric_name"): m for m in (snapshot.get("metrics") or [])}
        pi_summary = ((ctx.get("prediction_intelligence") or {}).get("summary")) or {}
        pv = ctx.get("prediction_validation") or {}
        pv_summary = pv.get("summary") or {}
        findings = ctx.get("critical_findings") or []

        def _metric(name):
            m = metrics.get(name)
            return {"value": m.get("value"), "period": m.get("period"),
                    "source": m.get("source")} if m else None

        # Top urgentes: ordenados por prioridad e impacto (sin recalcular).
        priority_rank = {"URGENT": 0, "IMPORTANT": 1, "REVIEW": 2, "MONITOR": 3}
        top = sorted(
            findings,
            key=lambda f: (priority_rank.get(f.get("business_priority"), 99),
                           -(f.get("impact_score") or 0)),
        )[:5]
        top_urgent = [
            {"finding_id": f.get("finding_id"), "title": f.get("title"),
             "business_priority": f.get("business_priority"),
             "impact_score": f.get("impact_score"),
             "confidence_score": f.get("confidence_score"),
             "finding_type": f.get("finding_type")}
            for f in top
        ]

        trends = ctx.get("key_trends") or []
        trends_view = [
            {"trend_id": t.get("trend_id"), "trend_type": t.get("trend_type"),
             "direction": t.get("direction"), "period": t.get("period"),
             "interpretation": t.get("interpretation")}
            for t in trends[:6]
        ]

        limits = ctx.get("limitations") or []
        limits_view = [
            l.get("text") if isinstance(l, dict) else str(l) for l in limits[:6]
        ]

        return {
            "ready": True,
            "dataset_state": "READY",
            "company_id": ctx.get("identity", {}).get("company_id"),
            "dataset_label": ctx.get("identity", {}).get("dataset_label"),
            "business_period": snapshot.get("business_period"),
            "attention_level": attention.get("attention_level"),
            "cards": {
                "urgent_findings": signals.get("urgent_findings"),
                "important_findings": signals.get("important_findings"),
                "opportunities": len(ctx.get("key_opportunities") or []),
                "predictions": pi_summary.get("total_predictions"),
                "data_quality_score": signals.get("data_quality_score"),
                "context_confidence": confidence.get("context_confidence_score"),
            },
            "snapshot": {
                "gross_revenue": _metric("gross_revenue"),
                "net_revenue": _metric("net_revenue"),
                "unique_transactions": _metric("unique_transactions"),
                "total_customers": _metric("total_customers"),
                "total_products": _metric("total_products"),
                "total_countries": _metric("total_countries"),
                "cancelled_rows": _metric("cancelled_rows"),
            },
            "top_urgent_findings": top_urgent,
            "trends": trends_view,
            "prediction_intelligence": {
                "total": pi_summary.get("total_predictions"),
                "high_quality": pi_summary.get("high_quality"),
                "moderate_quality": pi_summary.get("moderate_quality"),
                "low_quality": pi_summary.get("low_quality"),
                "insufficient": pi_summary.get("insufficient"),
                "high_decline_risk": pi_summary.get("high_decline_risk"),
                "urgent": pi_summary.get("urgent"),
                "important": pi_summary.get("important"),
                "average_confidence": pi_summary.get("average_confidence"),
            },
            "prediction_validation": {
                "validated": pv_summary.get("validated"),
                "pending": pv_summary.get("pending"),
                "not_available": pv_summary.get("not_available"),
                "model_performance_status": pv.get("model_performance_status"),
                "note": pv.get("validation_note"),
            },
            "limitations": limits_view,
        }

    def opportunities(self) -> List[Dict[str, Any]]:
        """Oportunidades reales de 5A. Siempre presentadas como 'posibles'."""
        return self.context_5a().get("key_opportunities") or []

    def predictions(self) -> Dict[str, Any]:
        """Predicciones: 4A + inteligencia 4B + validación 4C."""
        intel = self.intelligence_4b()
        val = self.validation_4c()
        val_by_id = {}
        for v in (val.get("validations") or []):
            pid = v.get("prediction_id")
            if pid:
                val_by_id[pid] = v
        insights = []
        for ins in (intel.get("prediction_insights") or []):
            v = val_by_id.get(ins.get("prediction_id")) or {}
            insights.append({
                "insight_id": ins.get("insight_id"),
                "prediction_id": ins.get("prediction_id"),
                "prediction_type": ins.get("prediction_type"),
                "entity": ins.get("entity"),
                "period": ins.get("period"),
                "forecast_horizon": ins.get("forecast_horizon"),
                "prediction_status": ins.get("prediction_status"),
                "predicted_value": ins.get("predicted_value"),
                "lower_bound": ins.get("lower_bound"),
                "upper_bound": ins.get("upper_bound"),
                "confidence_score": ins.get("confidence_score"),
                "forecast_quality": ins.get("forecast_quality"),
                "uncertainty_level": ins.get("uncertainty_level"),
                "decline_risk": ins.get("decline_risk"),
                "trend": ins.get("trend"),
                "forecast_direction": ins.get("forecast_direction"),
                "forecast_direction_text": ins.get("forecast_direction_text"),
                "trend_discrepancy_note": ins.get("trend_discrepancy_note"),
                "method": ins.get("method"),
                "attention_score": ins.get("attention_score"),
                "attention_level": ins.get("attention_level"),
                "business_interpretation": ins.get("business_interpretation"),
                "recommendations": ins.get("recommendations") or [],
                "limitations": ins.get("limitations") or [],
                "validation_status": v.get("validation_status", "PENDING"),
            })
        return {
            "summary": intel.get("summary") or {},
            "validation_summary": val.get("summary") or {},
            "model_performance_status": val.get("model_performance_status"),
            "insights": insights,
        }

    def audit_events(self, store: TenantStore, ctx: TenantContext,
                     limit: int = 100) -> List[Dict[str, Any]]:
        """Eventos de auditoría de la empresa del contexto (sin secretos)."""
        events = []
        audit_file = os.path.join(store.data_dir, "audit.jsonl")
        if not os.path.exists(audit_file):
            return events
        with open(audit_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get("company_id") != ctx.company_id:
                    continue
                events.append(ev)
        return events[-limit:][::-1]
