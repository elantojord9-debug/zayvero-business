#!/usr/bin/env python3
"""FASE 5C — CLI del LLM Business Advisor.

Uso:
  .venv/bin/python llm_advisor.py "¿Cuál es el problema más urgente?"
  .venv/bin/python llm_advisor.py --validate        # 8 preguntas de validación real
  .venv/bin/python llm_advisor.py --batch preguntas.txt --out respuestas.json
  .venv/bin/python llm_advisor.py --provider fake "pregunta"
"""
from __future__ import annotations

import argparse
import json
import os
import sys

DEFAULT_CONTEXT = "data/business_context/demo-retail/online_retail_II_business_context.json"

VALIDATION_QUESTIONS = [
    "¿Cuál es el problema más urgente?",
    "¿Dónde estoy perdiendo dinero?",
    "¿Qué oportunidades detectó ZAYVERO?",
    "¿Qué debería revisar primero?",
    "¿Por qué esto aparece como urgente?",
    "¿Las ventas van a subir?",
    "¿Por qué mi proveedor aumentó los precios?",
    "¿Cuál es la capital de Francia?",
]


def build_pipeline(args):
    from llm_advisor import LLMAdvisorPipeline, FakeLLMProvider, ConfigurableLLMProvider

    prov_name = (args.provider or os.environ.get("ZAYVERO_LLM_PROVIDER") or "fake").lower()
    provider = FakeLLMProvider() if prov_name == "fake" else ConfigurableLLMProvider()
    return LLMAdvisorPipeline(args.context, provider=provider)


def cmd_ask(args):
    pipe = build_pipeline(args)
    out = pipe.ask(args.question)
    print("=" * 72)
    print("PREGUNTA:", args.question)
    print("-" * 72)
    print(out["answer"])
    print("-" * 72)
    print("VALIDACIÓN:", out["validation_status"], "| FALLBACK:", out["fallback"])
    print("MODELO:", out["model"], "| PROVEEDOR:", out["provider"])
    print("LATENCIA:", out["latency_seconds"], "s | TOKENS:", json.dumps(out["token_usage"]))
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
        print("Guardado en", args.out)


def cmd_validate(args):
    from llm_advisor import FakeLLMProvider

    pipe = build_pipeline(args)
    results = []
    for q in VALIDATION_QUESTIONS:
        out = pipe.ask(q)
        results.append(out)
        print("=" * 72)
        print("PREGUNTA:", q)
        print("VALIDACIÓN:", out["validation_status"], "| FALLBACK:", out["fallback"])
        print(out["answer"][:600])
        print()
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            json.dump(results, fh, ensure_ascii=False, indent=1)
        print("Guardado en", args.out)


def cmd_batch(args):
    pipe = build_pipeline(args)
    with open(args.batch, encoding="utf-8") as fh:
        questions = [ln.strip() for ln in fh if ln.strip()]
    results = [pipe.ask(q) for q in questions]
    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(results, fh, ensure_ascii=False, indent=1)
    print(f"{len(results)} respuestas guardadas en {args.out}")


def main():
    ap = argparse.ArgumentParser(description="FASE 5C — LLM Business Advisor")
    ap.add_argument("question", nargs="?", help="Pregunta empresarial en lenguaje natural")
    ap.add_argument("--context", default=DEFAULT_CONTEXT)
    ap.add_argument("--provider", choices=["fake", "configurable"], default=None)
    ap.add_argument("--validate", action="store_true", help="8 preguntas de validación real")
    ap.add_argument("--batch", help="Archivo con una pregunta por línea")
    ap.add_argument("--out", help="Guardar JSON de salida")
    args = ap.parse_args()
    if args.validate:
        cmd_validate(args)
    elif args.batch:
        if not args.out:
            sys.exit("--batch requiere --out")
        cmd_batch(args)
    elif args.question:
        cmd_ask(args)
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
