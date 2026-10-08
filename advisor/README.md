# FASE 5B — AI Business Advisor Engine (Foundation)

Motor interno determinista del AI Business Advisor. Recibe un
`BusinessIntelligenceContext` real de FASE 5A y una pregunta empresarial en
lenguaje natural, y produce una `BusinessAdvisorResponse` estructurada basada
**exclusivamente** en la evidencia disponible.

**Sin LLM en esta fase.** El futuro LLM recibirá únicamente la respuesta
estructurada ya validada.

```
BusinessIntelligenceContext (FASE 5A)
        ↓
AdvisorEngine (este paquete)
        ↓
BusinessAdvisorResponse (JSON)
        ↓
Future LLM = NO IMPLEMENTADO
```

## Arquitectura

| Módulo | Responsabilidad |
|---|---|
| `models.py` | `BusinessQuestion`, `RetrievedEvidence`, `BusinessAdvisorResponse` (JSON-serializables) |
| `question.py` | Normalización + clasificación determinista + extracción de entidades/métrica/periodo |
| `retrieval.py` | Carga del contexto (una sola vez) + índice + relevance scoring + retrieval con cuotas por sección |
| `evidence.py` | (lógica integrada en `retrieval.py`: scoring explicable) |
| `reasoning.py` | Mapeo de evidencia a FACTS / OBSERVATIONS / RISKS / OPPORTUNITIES / PREDICTIONS; guardia anti-causalidad |
| `uncertainty.py` | Niveles LOW / MEDIUM / HIGH / UNKNOWN según calidad de evidencia |
| `recommendations.py` | `EXISTING_RECOMMENDATION` vs `ADVISORY_RECOMMENDATION` |
| `response.py` | Plantillas deterministas de respuesta por tipo de pregunta |
| `trace.py` | Trazabilidad: question_id, context_id, evidence_ids, métodos, reglas, timestamp, versión |
| `engine.py` | `AdvisorEngine.ask(pregunta)` — orquesta el flujo completo |

`advisor.py` es el CLI.

## Flujo

1. **BusinessQuestion**: la pregunta se normaliza (minúsculas, sin acentos) y se
   clasifica por reglas de palabras clave en orden de prioridad:
   `URGENT_ISSUE`, `FINANCIAL_PROBLEM`, `OPPORTUNITY`, `RECOMMENDATION`,
   `EXPLANATION`, `PREDICTION`, `TREND`, `RISK`, `CUSTOMER`, `PRODUCT`,
   `PRICE`, `SALES`, `GENERAL_BUSINESS`, `UNKNOWN`. No se asume una categoría
   si la pregunta no lo permite. Se extraen entidades (productos, clientes),
   métrica y periodo mencionados.
2. **Retrieval**: el contexto se indexa (findings, riesgos, oportunidades,
   tendencias, predicciones individuales de 4B, recomendaciones, snapshot,
   limitaciones). El relevance score combina: coincidencia de tokens (con
   stemming de plurales), coincidencia de entidad, coincidencia de tipo de
   finding, prioridad/severidad, impact_score, confidence y un multiplicador por
   sección según el tipo de pregunta. Cuota mínima por sección prioritaria para
   no ahogar evidencia débil pero relevante. **No se crea evidencia nueva.**
3. **Reasoning**: la evidencia se clasifica en FACTS, OBSERVATIONS, RISKS,
   OPPORTUNITIES, PREDICTIONS. Una guardia impide convertir correlación en
   causalidad.
4. **Uncertainty**: LOW / MEDIUM / HIGH / UNKNOWN según la puntuación de la
   mejor evidencia y la calidad disponible. Con evidencia insuficiente no se
   produce una conclusión fuerte.
5. **BusinessAdvisorResponse**: respuesta en lenguaje para dueño/gerente, con
   `answer`, `executive_summary`, hechos, hallazgos, riesgos, oportunidades,
   predicciones, recomendaciones, incertidumbre, limitaciones, `evidence_used`
   (IDs reales del EvidenceIndex) y trace completo.

## Principio fundamental

**El Advisor no inventa.** Si la evidencia no permite responder:

> "No tengo suficiente evidencia para determinarlo."

más una explicación de qué información falta. Nunca se completan vacíos con
suposiciones. Nunca se afirma fraude, robo, pérdida, ganancia, causa o
culpabilidad. Una predicción nunca se presenta como certeza.

## Seguridad

Solo se consume `advisor_safe_data`. El output no contiene credenciales,
tokens, API keys ni secretos.

## Determinismo

Misma pregunta + mismo contexto = misma respuesta (IDs por SHA-256, sin
`random`, sin timestamps en el contenido lógico).

## Cómo ejecutar

```bash
.venv/bin/python advisor.py "¿Cuál es el problema más urgente?"
.venv/bin/python advisor.py --validate          # 8 preguntas de validación real
.venv/bin/python advisor.py --batch preguntas.txt --out respuestas.json
```

## Cómo probar

```bash
.venv/bin/python -m unittest tests.test_advisor   # 41 pruebas
```

## Limitaciones

- Solo responde con la evidencia del contexto de FASE 5A; no razona más allá.
- Las plantillas son deterministas: el lenguaje es estructurado, no conversacional.
- La relevancia depende de coincidencias léxicas (con stemming), no de
  comprensión semántica profunda — eso llegará con el futuro LLM.
- Future LLM = NO IMPLEMENTADO en esta fase.
