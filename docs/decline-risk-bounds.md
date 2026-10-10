# decline_risk — Límites exactos y reglas de clasificación

`prediction/risk.py::decline_risk(trend_info, series, confidence, forecast_pct=None)`

Riesgo de que el indicador **disminuya en el horizonte proyectado**.
NO es una probabilidad calibrada: es una banda de materialidad sobre el
cambio proyectado, ajustada por señales históricas y por la confianza del
pronóstico. `decline_risk` **NO es P(caída)**.

## 1. Modo proyectado (principal): `forecast_pct` no es None

`forecast_pct = (pronóstico − nivel_reciente) / |nivel_reciente| × 100`,
calculado por quien llama (`prediction/report.py`). Si el nivel reciente
es 0, `forecast_pct` es `None` y se usa el modo histórico.

| Condición (evaluada en este orden) | Nivel | Significado |
|---|---|---|
| `pct <= -40.0` | **HIGH** | Caída proyectada severa |
| `-40.0 < pct <= -20.0` | **MEDIUM** | Caída proyectada material |
| `pct > -20.0` | **LOW** | Sin caída material (incluye 0 % y crecimientos) |

Umbrales definidos como constantes: `FORECAST_SEVERE_DROP_PCT = -40.0`,
`FORECAST_MATERIAL_DROP_PCT = -20.0`. Banda ±20 % coherente con el módulo
de atención (`attention.py`: `|pct| >= 20 %` = cambio material).

### Bordes exactos (deterministas, sin solape)

La cadena `if / elif / else` es excluyente: cada valor cae en una sola rama.

| `pct` exacto | Rama que gana | Resultado |
|---|---|---|
| `-40.0` | `pct <= -40.0` | **HIGH** |
| `-40.1` | `pct <= -40.0` | **HIGH** |
| `-39.9` | `pct <= -20.0` | **MEDIUM** |
| `-20.0` | `pct <= -20.0` | **MEDIUM** |
| `-20.1` | `pct <= -20.0` | **MEDIUM** |
| `-19.9` | `else` | **LOW** |
| `0.0` | `else` | **LOW** |
| `+15.0` | `else` | **LOW** |

No hay indeterminismo: los comparadores `<=` hacen que los valores de
borde pertenezcan siempre a la rama superior (más severa).

### Ajuste 1 — señales históricas (solo elevan, nunca reducen)

Si hay `stable_decline` (tendencia DOWNWARD con CV < 0.4) o
`acceleration_negative`, y el nivel no es ya HIGH, sube **un** escalón:
`LOW → MEDIUM`, `MEDIUM → HIGH`. Se documenta en `signals` y `detail`.

### Ajuste 2 — tope por confianza baja

Si `confidence < 40.0` (`LOW_CONFIDENCE_CAP`) y el nivel resultante es
HIGH, se limita a **MEDIUM**: no se afirma riesgo alto con un pronóstico
poco fiable. Se documenta en `signals` y `detail`.

**Garantía verificada por tests:** con `confidence < 40`, el resultado
final nunca supera MEDIUM, venga de la rama que venga
(HIGH→MEDIUM por tope; MEDIUM→HIGH por historial→MEDIUM por tope;
LOW→MEDIUM por historial). El borde `confidence == 40.0` no activa el
tope (`<`, estricto) — determinista.

### Valores no válidos

- `forecast_pct = None` → modo histórico (sección 2).
- `forecast_pct` no numérico (texto, etc.) → `INSUFFICIENT_DATA`.
- `forecast_pct` no finito: `NaN` → `INSUFFICIENT_DATA` (guardia defensiva;
  sin ella, las comparaciones con NaN son falsas y caería en LOW de forma
  engañosa). `±inf` se clasifica por comparación directa (+inf→LOW,
  −inf→HIGH), que es el resultado correcto. El llamador ya evita `NaN`
  porque devuelve `None` cuando el nivel reciente es 0.

## 2. Modo histórico (`forecast_pct is None`)

Fallback de compatibilidad para llamadas sin pronóstico disponible.
Usa solo señales del pasado; se indica en `detail` ("modo histórico").

| Condición | Nivel |
|---|---|
| `trend == "INSUFFICIENT_DATA"` | **INSUFFICIENT_DATA** |
| DOWNWARD + descenso estable + (aceleración negativa o caída severa) | **HIGH** |
| DOWNWARD (otras señales) | **MEDIUM** |
| otro caso | **LOW** |

**Advertencia de lectura:** el modo histórico describe el pasado, no el
futuro. Los textos de `prediction_intelligence/risk.py` están redactados
para el horizonte proyectado; cuando el riesgo venga del modo histórico,
el `detail` lo declara explícitamente.

## 3. Limitaciones declaradas

- Los umbrales ±20 %/±40 % son bandas de materialidad convencionales,
  no probabilidades calibradas.
- El indicador no incorpora estacionalidad ni variables externas.
- Con `confidence < 40`, el nivel máximo es MEDIUM por diseño: la
  incertidumbre alta **reduce** la severidad afirmada, nunca la aumenta.
  Esto es intencional — evita transmitir seguridad injustificada — pero
  significa que una caída real con pronóstico poco fiable se reportará
  como máximo MEDIUM. La incertidumbre queda visible en `signals`.
