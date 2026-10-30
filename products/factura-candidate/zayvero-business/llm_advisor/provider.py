"""FASE 5C — Proveedores LLM.

- LLMProvider: interfaz desacoplada (generate(request) -> ProviderResult).
- FakeLLMProvider: proveedor determinista para tests y para uso sin API real.
  Construye la respuesta natural SOLO con la información del request validado;
  nunca inventa cifras, nombres ni fechas.
- ConfigurableLLMProvider: lee configuración de variables de entorno
  (ZAYVERO_LLM_PROVIDER, ZAYVERO_LLM_MODEL, ZAYVERO_LLM_API_KEY,
  ZAYVERO_LLM_BASE_URL). Si no hay credencial configurada, devuelve
  LLM_UNAVAILABLE (el pipeline usa fallback). Las credenciales nunca se
  imprimen ni se registran en logs.

Variables de entorno (ninguna contiene secretos en el código):
  ZAYVERO_LLM_PROVIDER  -> "fake" | "openai" (compatibilidad OpenAI /chat/completions)
  ZAYVERO_LLM_MODEL     -> nombre del modelo (por defecto del proveedor)
  ZAYVERO_LLM_API_KEY   -> clave de API (secreta; solo en entorno)
  ZAYVERO_LLM_BASE_URL  -> base opcional (por defecto https://api.openai.com/v1)
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.request
from abc import ABC, abstractmethod
from typing import Any, Dict, Optional

from .models import LLMAdvisorRequest, ProviderResult


class LLMUnavailableError(Exception):
    """El proveedor LLM no está disponible."""


class LLMProvider(ABC):
    """Interfaz desacoplada del proveedor LLM."""

    name: str = "base"

    @abstractmethod
    def generate(self, request: LLMAdvisorRequest, system_prompt: str) -> ProviderResult:
        """Genera texto natural a partir del request validado."""


class FakeLLMProvider(LLMProvider):
    """Proveedor determinista para tests y entornos sin API real.

    Redacta una respuesta natural exclusivamente con la información del
    LLMAdvisorRequest (answer/executive_summary de 5B + listas estructuradas).
    Nunca inventa cifras, nombres, fechas ni evidence_ids.
    """

    name = "fake"

    def generate(self, request: LLMAdvisorRequest, system_prompt: str) -> ProviderResult:
        t0 = time.time()
        ar = request.advisor_response
        ev = request.evidence
        parts = []
        parts.append((ar.get("answer_determinista") or "").strip())
        facts = ev.get("facts", []) or []
        if facts:
            parts.append("Qué muestran los datos:")
            for f in facts[:6]:
                parts.append("- " + str(f))
        preds = ev.get("predictions", []) or []
        if preds:
            parts.append("Qué proyecta el modelo (no son certezas):")
            for p in preds[:3]:
                parts.append("- " + (p.get("claim") if isinstance(p, dict) else str(p)))
        recs = ev.get("recommendations", []) or []
        if recs:
            parts.append("Qué conviene revisar:")
            for r in recs[:4]:
                if isinstance(r, dict):
                    kind = r.get("kind", "EXISTING_RECOMMENDATION")
                    label = " (recomendación de asesoría)" if kind == "ADVISORY_RECOMMENDATION" else ""
                    parts.append("- " + str(r.get("text", "")) + label)
                else:
                    parts.append("- " + str(r))
        lims = ar.get("limitations", []) or []
        if lims:
            parts.append("Limitaciones: " + "; ".join(str(x) for x in lims[:4]))
        elif request.uncertainty.get("uncertainty_level") in ("HIGH", "UNKNOWN"):
            parts.append("Limitaciones: la evidencia disponible es limitada para esta pregunta.")
        text = "\n".join(p for p in parts if p).strip()
        if not text:
            text = "No tengo suficiente evidencia para determinarlo."
        latency = time.time() - t0
        return ProviderResult(
            text=text,
            model="fake-llm-5c",
            input_tokens=None,
            output_tokens=None,
            total_tokens=None,
            latency_seconds=round(latency, 4),
            provider_name=self.name,
        )


def _env(name: str) -> Optional[str]:
    v = os.environ.get(name)
    return v if v and v.strip() else None


class ConfigurableLLMProvider(LLMProvider):
    """Proveedor configurable por variables de entorno.

    Sin ZAYVERO_LLM_API_KEY -> LLMUnavailableError (fallback seguro).
    Con provider=openai -> llamada mínima HTTPS a /chat/completions.
    Nunca imprime ni registra la clave.
    """

    name = "configurable"

    def __init__(self):
        self.provider = (_env("ZAYVERO_LLM_PROVIDER") or "fake").lower()
        self.model = _env("ZAYVERO_LLM_MODEL") or "gpt-4o-mini"
        self.base_url = _env("ZAYVERO_LLM_BASE_URL") or "https://api.openai.com/v1"
        # La clave solo se lee del entorno; nunca se guarda en logs ni se imprime.
        self._api_key = _env("ZAYVERO_LLM_API_KEY")

    def is_configured(self) -> bool:
        return self.provider != "fake" and self._api_key is not None

    def generate(self, request: LLMAdvisorRequest, system_prompt: str) -> ProviderResult:
        if not self.is_configured():
            raise LLMUnavailableError(
                "LLM no configurado (ZAYVERO_LLM_API_KEY ausente o provider=fake)."
            )
        if self.provider == "openai":
            return self._generate_openai(request, system_prompt)
        raise LLMUnavailableError(f"Proveedor '{self.provider}' no soportado.")

    def _generate_openai(self, request: LLMAdvisorRequest, system_prompt: str) -> ProviderResult:
        from .request_builder import render_messages

        messages = render_messages(request, system_prompt)
        payload = json.dumps({"model": self.model, "messages": messages, "temperature": 0.2}).encode()
        req = urllib.request.Request(
            self.base_url.rstrip("/") + "/chat/completions",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer " + (self._api_key or ""),
            },
        )
        t0 = time.time()
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:  # red o API: degradar a no disponible
            raise LLMUnavailableError(f"Llamada al proveedor falló: {type(exc).__name__}")
        latency = time.time() - t0
        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
        except (KeyError, IndexError, TypeError):
            raise LLMUnavailableError("Respuesta del proveedor sin contenido utilizable.")
        usage = data.get("usage") or {}
        return ProviderResult(
            text=text,
            model=data.get("model") or self.model,
            input_tokens=usage.get("prompt_tokens"),
            output_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            latency_seconds=round(latency, 3),
            provider_name="openai",
        )


def provider_from_env() -> LLMProvider:
    """Fabrica el proveedor según ZAYVERO_LLM_PROVIDER (fake por defecto)."""
    prov = (_env("ZAYVERO_LLM_PROVIDER") or "fake").lower()
    if prov == "fake":
        return FakeLLMProvider()
    return ConfigurableLLMProvider()
