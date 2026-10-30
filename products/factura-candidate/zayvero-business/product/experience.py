"""FASE 8 — Constructores deterministas de la experiencia de producto.

Funciones puras: mismos inputs → mismos outputs. Consumen únicamente
datos ya calculados por las fases 1A–7C (vista de configuración 7C,
workspace 7A, diagnóstico 7B). No generan inteligencia nueva, no
recalculan métricas, no duplican lógica de negocio.

Entradas típicas:
  config_view  dict de intl_config_mod.config_view(company)  (tiene
               "complete" y "missing")
  workspace    dict de TenantData.workspace()  (dataset_state, datasets,
               active_dataset, can_upload, company)
  diagnostic   dict del payload de TenantData.diagnostic()  (o None)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from .models import (
    ADVISOR_LINE_KEY,
    BENEFITS,
    DATA_SOURCES,
    DEMO_ENTRY,
    EXPERIENCE_HIERARCHY,
    FIRST_ENTRY,
    ONBOARDING_STEPS,
    PLANS,
    POST_SEQUENCE,
    PRODUCT_STATES,
    STATE_ACTIONS,
    STATE_MESSAGE_KEYS,
    STEP_STATUS_CURRENT,
    STEP_STATUS_DONE,
    STEP_STATUS_PENDING,
)


def build_product_state(config_view: Optional[Dict[str, Any]],
                        dataset_state: str,
                        diagnostic_status: Optional[str] = None
                        ) -> Dict[str, Any]:
    """Calcula el estado del producto para la empresa.

    Prioridad: configuración incompleta > error > procesamiento >
    sin datos > listo. La demo siempre resuelve a READY (dataset activo).
    """
    complete = bool((config_view or {}).get("complete"))
    state = "READY"
    if not complete:
        state = "INCOMPLETE_CONFIGURATION"
    elif dataset_state == "ERROR":
        state = "ERROR"
    elif dataset_state == "PROCESSING":
        state = "PROCESSING"
    elif dataset_state in ("NO_DATA", "DATA_UPLOADED", "INSUFFICIENT_DATA"):
        state = "NO_DATA"
    elif dataset_state == "READY":
        state = "READY"
    else:
        # Estados intermedios (mapeo pendiente, revisión, etc.): la empresa
        # ya cargó datos pero aún no están listos → se trata como NO_DATA
        # con acciones de continuación.
        state = "NO_DATA"

    return {
        "state": state,
        "message_key": STATE_MESSAGE_KEYS[state],
        "actions": [dict(a) for a in STATE_ACTIONS[state]],
        "config_complete": complete,
        "diagnostic_status": diagnostic_status,
    }


def build_onboarding(config_view: Optional[Dict[str, Any]],
                     datasets: List[Dict[str, Any]],
                     dataset_state: str,
                     diagnostic_status: Optional[str] = None
                     ) -> Dict[str, Any]:
    """Progreso del onboarding en 7 pasos.

    Cada paso: done | current | pending. `current_step` es el primer paso
    no completado (o 7 si todos están completos).
    """
    cfg = config_view or {}
    company_name = str(cfg.get("name") or "").strip()
    step1_done = bool(company_name)
    step2_done = bool(cfg.get("complete"))

    has_datasets = bool(datasets)
    step3_done = has_datasets
    step4_done = any(bool(d.get("review_done")) for d in datasets)
    step5_done = any(bool(d.get("mapping_confirmed")) for d in datasets)
    step6_done = dataset_state == "READY"
    step7_done = diagnostic_status in ("AVAILABLE", "LIMITED")

    done_flags = [step1_done, step2_done, step3_done, step4_done,
                  step5_done, step6_done, step7_done]

    steps = []
    current_step = 8  # 8 = todos completos
    for i, meta in enumerate(ONBOARDING_STEPS):
        if done_flags[i]:
            status = STEP_STATUS_DONE
        elif current_step == 8:
            status = STEP_STATUS_CURRENT
            current_step = meta["step"]
        else:
            status = STEP_STATUS_PENDING
        steps.append({
            "step": meta["step"],
            "key": meta["key"],
            "title_key": meta["title_key"],
            "desc_key": meta["desc_key"],
            "status": status,
        })
    if current_step == 8:
        current_step = 7

    return {
        "steps": steps,
        "current_step": current_step,
        "completed": sum(1 for f in done_flags if f),
        "total": len(ONBOARDING_STEPS),
        "all_done": all(done_flags),
    }


def build_post_sequence(config_complete: bool,
                        data_received: bool,
                        data_processed: bool,
                        analysis_available: bool,
                        diagnostic_ready: bool) -> List[Dict[str, Any]]:
    """Secuencia visible post-onboarding (5 hitos con estado)."""
    flags = [config_complete, data_received, data_processed,
             analysis_available, diagnostic_ready]
    out = []
    for meta, done in zip(POST_SEQUENCE, flags):
        out.append({
            "key": meta["key"],
            "label_key": meta["label_key"],
            "status": STEP_STATUS_DONE if done else STEP_STATUS_PENDING,
        })
    return out


def build_product_overview(company_view: Dict[str, Any],
                           config_view: Optional[Dict[str, Any]],
                           workspace: Dict[str, Any],
                           diagnostic_status: Optional[str],
                           is_demo: bool) -> Dict[str, Any]:
    """Ensambla la vista completa de primera entrada / producto.

    Todo el contenido es presentacional y determinista. No toca los
    motores: solo ordena lo que 7A/7B/7C ya calcularon.
    """
    datasets = workspace.get("datasets") or []
    dataset_state = workspace.get("dataset_state") or "NO_DATA"

    state = build_product_state(config_view, dataset_state, diagnostic_status)
    onboarding = build_onboarding(config_view, datasets, dataset_state,
                                  diagnostic_status)

    data_received = bool(datasets)
    data_processed = dataset_state == "READY"
    analysis_available = data_processed
    diagnostic_ready = diagnostic_status in ("AVAILABLE", "LIMITED")
    post_sequence = build_post_sequence(
        state["config_complete"], data_received, data_processed,
        analysis_available, diagnostic_ready)

    active = workspace.get("active_dataset") or {}

    return {
        "version": "8-1.0.0",
        "company": {
            "company_id": company_view.get("company_id"),
            "name": company_view.get("name"),
            "is_demo": bool(is_demo),
            "config_complete": state["config_complete"],
            "config_missing": (config_view or {}).get("missing", []),
        },
        "product_state": state,
        "onboarding": onboarding,
        "post_sequence": post_sequence,
        "first_entry": dict(FIRST_ENTRY),
        "data_sources": [dict(s) for s in DATA_SOURCES],
        "data_flow": [
            {"key": "received", "label_key": "dataflow.received"},
            {"key": "validation", "label_key": "dataflow.validation"},
            {"key": "mapping", "label_key": "dataflow.mapping"},
            {"key": "processing", "label_key": "dataflow.processing"},
            {"key": "intelligence", "label_key": "dataflow.intelligence"},
            {"key": "diagnostic", "label_key": "dataflow.diagnostic"},
        ],
        "hierarchy": [dict(h) for h in EXPERIENCE_HIERARCHY],
        "benefits": [dict(b) for b in BENEFITS],
        "plans": [dict(p) for p in PLANS],
        "demo": dict(DEMO_ENTRY),
        "advisor_line_key": ADVISOR_LINE_KEY,
        "active_dataset": {
            "dataset_id": active.get("dataset_id"),
            "nombre": active.get("nombre"),
            "status": active.get("status"),
            "is_demo": bool(active.get("is_demo")),
            "row_count": active.get("row_count"),
        } if active else None,
        "can_upload": bool(workspace.get("can_upload")),
        "states_catalog": list(PRODUCT_STATES),
    }
