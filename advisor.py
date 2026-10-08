#!/usr/bin/env python3
"""FASE 5B — AI Business Advisor Engine. CLI.

Uso:
    python advisor.py "¿Cuál es el problema más urgente?"
    python advisor.py --context data/business_context/demo-retail/online_retail_II_business_context.json --batch preguntas.txt
    python advisor.py --validate  (ejecuta las 8 preguntas de validación real)
"""
from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from advisor import AdvisorEngine

DEFAULT_CONTEXT = "data/business_context/demo-retail/online_retail_II_business_context.json"

VALIDATION_QUESTIONS = [
    "¿Cuál es el problema más urgente?",
    "¿Dónde estoy perdiendo dinero?",
    "¿Qué oportunidades detectó ZAYVERO?",
    "¿Qué debería revisar primero?",
    "¿Por qué esto aparece como urgente?",
    "¿Las ventas van a subir?",
    "¿Por qué mi proveedor aumentó los precios?",
    "¿Cómo está el clima en Marte?",
]


def main() -> None:
    parser = argparse.ArgumentParser(description="FASE 5B — AI Business Advisor Engine")
    parser.add_argument("question", nargs="?", default=None, help="Pregunta en lenguaje natural")
    parser.add_argument("--context", default=DEFAULT_CONTEXT)
    parser.add_argument("--batch", default=None, help="Archivo con una pregunta por línea")
    parser.add_argument("--validate", action="store_true", help="Ejecuta las 8 preguntas de validación")
    parser.add_argument("--out", default=None, help="Guardar respuesta(s) en JSON")
    args = parser.parse_args()

    engine = AdvisorEngine(args.context)

    questions: list[str]
    if args.validate:
        questions = VALIDATION_QUESTIONS
    elif args.batch:
        with open(args.batch, encoding="utf-8") as f:
            questions = [l.strip() for l in f if l.strip()]
    elif args.question:
        questions = [args.question]
    else:
        parser.error("Indique una pregunta, --batch o --validate")

    results = []
    for q in questions:
        resp = engine.ask(q)
        d = resp.to_dict()
        results.append(d)
        print("=" * 72)
        print("PREGUNTA:", q)
        print("TIPO:", resp.question.question_type)
        print("-" * 72)
        print("RESPUESTA:", resp.answer)
        print()
        print("RESUMEN:", resp.executive_summary)
        print("EVIDENCIA:", ", ".join(resp.evidence_used[:8]))
        print("INCERTIDUMBRE:", resp.uncertainty.get("uncertainty_level"))
        print("RUNTIME:", resp.trace.get("runtime_seconds"), "s")

    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2, default=str)
        print("\nGuardado en", args.out)


if __name__ == "__main__":
    main()
