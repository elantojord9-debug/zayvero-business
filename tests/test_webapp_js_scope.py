"""Regresión JS: la vista Resumen no debe invocar funciones fuera de su alcance.

El bug 'showingOf is not defined' dejaba la vista #/resumen trabada en
"Analizando información…": showingOf() estaba definida dentro de
renderDiagnostic y se invocaba desde renderSummary, otro alcance.

Este test verifica estáticamente (sin navegador) que cada función
invocada en el cuerpo de renderSummary esté definida en un alcance que
la contenga (su propio cuerpo o un alcance exterior, con hoisting).
Falla con el código anterior al fix (detecta 'showingOf') y pasa con el
fix aplicado.
"""

import os
import re
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_JS = os.path.join(REPO, "webapp", "static", "app.js")

# Builtins del lenguaje que no son funciones del bundle.
JS_BUILTINS = frozenset(
    "Array Boolean Date Error JSON Map Math Number Object Promise RegExp "
    "Set String".split())

JS_KEYWORDS = frozenset(
    "async await case catch const default delete do else finally for "
    "function if in let new of return switch throw try typeof var void "
    "while with yield".split())


def _function_spans(src):
    """[(nombre, inicio, fin)] de cada 'function [nombre](' con su cuerpo."""
    out = []
    for m in re.finditer(r"function\s*([A-Za-z_$][\w$]*)?\s*\(", src):
        name = m.group(1) or ""
        i = src.index("{", m.end())
        depth, j = 0, i
        in_str, esc, quote = False, False, ""
        while j < len(src):
            ch = src[j]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == quote:
                    in_str = False
            elif ch in "\"'`":
                in_str, quote = True, ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append([name, m.start(), j])
    return out


def _scope_tree(spans):
    """Padre de cada span: el span más pequeño que lo contiene."""
    parent = {}
    for i, (n0, s0, e0) in enumerate(spans):
        best = None
        for j, (n1, s1, e1) in enumerate(spans):
            if i != j and s1 <= s0 and e0 <= e1:
                if best is None or s1 > spans[best][1]:
                    best = j
        parent[i] = best
    return parent


def _resolves(name, pos, spans, parent):
    """¿Hay una definición de `name` visible desde la posición `pos`?"""
    cur = None
    for i, (n, s, e) in enumerate(spans):
        if s <= pos <= e and (cur is None or s > spans[cur][1]):
            cur = i
    scope = cur
    while scope is not None:
        for j, (n, s, e) in enumerate(spans):
            if n == name and parent[j] == scope:
                return True
        scope = parent[scope]
    return any(n == name and parent[j] is None
               for j, (n, s, e) in enumerate(spans))


def unresolved_calls(src, func_name):
    """Nombres invocados en `func_name` sin definición visible.

    Nota: no elimina comentarios; evita escribir `identificador (`
    en comentarios dentro de las funciones analizadas.
    """
    spans = _function_spans(src)
    parent = _scope_tree(spans)
    target = [d for d in spans if d[0] == func_name]
    if not target:
        raise AssertionError(f"no se encontró la función {func_name}")
    _, s0, s1 = target[0]
    bad = set()
    for m in re.finditer(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(", src[s0:s1]):
        name = m.group(1)
        if name in JS_KEYWORDS or name in JS_BUILTINS:
            continue
        if re.search(r"function\s*$", src[s0:s0 + m.start()][-9:]):
            continue  # es una definición, no una llamada
        if not _resolves(name, s0 + m.start(), spans, parent):
            bad.add(name)
    return sorted(bad)


class RenderSummaryScopeTest(unittest.TestCase):
    def test_render_summary_solo_llama_funciones_en_alcance(self):
        with open(APP_JS, encoding="utf-8") as fh:
            src = fh.read()
        self.assertEqual(
            [], unresolved_calls(src, "renderSummary"),
            "renderSummary invoca funciones fuera de su alcance "
            "(la vista Resumen quedaría en blanco)")
