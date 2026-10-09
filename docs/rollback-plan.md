# Plan de reversión — Despliegue "Corrección de calidad del Diagnóstico + remediación de seguridad"

Fecha de preparación: 2026-10-08. Estado: PRE-DESPLIEGUE (no desplegado, no commiteado).
Actualización 2026-10-08 (noche): análisis de reversión SEGURA con la remediación
de seguridad incluida. Ver sección 5.

## 1. Snapshot de producción (verificado 2026-10-08)

- **Producción actual (Render, servicio `zayvero-business`, auto-deploy desde GitHub):**
  commit `d86e981` — "fix cache: no-cache en archivos estaticos (app.js no se queda viejo)"
- Rama: `main` del repo `elantojord9-debug/zayvero-business`
- Verificación: `git ls-remote origin HEAD` → `d86e981de76f669866762b38f978eddede361d40`
- Incluye: FASE 8 + fix "Explorar demo" (sesión demo paralela) + fix cache no-cache.
- **ADVERTENCIA DE SEGURIDAD:** `d86e981` contiene `data/tenant/users.json` con los
  hashes comprometidos (incluida la contraseña anterior del propietario). Cualquier
  reversión que restaure ese código reactiva esas credenciales. Ver sección 5.

## 2. Versión candidata (validada en local)

Working tree sobre `d86e981` con estos cambios (sin commit). Estructura de
despliegue requerida: **2 commits secuenciales** (ver sección 5).

**Commit A — Remediación de seguridad:**
- `tenant/store.py` (`resolve_users_file` + guard `ZAYVERO_REQUIRE_SECRET_FILE`)
- `git rm --cached data/tenant/users.json` (sale del índice; queda en disco local)
- `.gitignore` (+`data/tenant/users.json`, `visual-qa-local/`, `data/raw/`, `audit/log/`)
- `scripts/generate_prod_seed.py`, `scripts/rotate_owner_password.py` (nuevos)
- `tests/test_auth_config.py` (nuevo)
- `docs/security-remediation-plan.md`, `docs/persistence-notes.md` (nuevos)

**Commit B — Calidad del Diagnóstico Ejecutivo:**
- `prediction/risk.py`, `prediction/report.py`
- `prediction_intelligence/trend.py`, `risk.py`, `interpretation.py`, `report.py`,
  `recommendations.py`, `periods.py` (nuevo)
- `business_context/opportunities.py`
- `diagnostic/builder.py`
- `webapp/data.py`, `webapp/static/app.js`, `webapp/static/i18n.js`

**Tests y docs (en B):**
- `tests/test_diagnostic_quality.py` (nuevo), `tests/test_decline_risk_bounds.py` (nuevo)
- `tests/test_business_context.py`, `tests/test_prediction_intelligence.py`,
  `tests/test_webapp.py`, `tests/test_webapp_6c.py` (ajustes de fixtures)
- `docs/decline-risk-bounds.md` (nuevo), `docs/rollback-plan.md` (este archivo)

**Datos derivados regenerados (demo-retail):**
- `data/predictions/demo-retail/online_retail_II_full_predictions.json`
- `data/prediction_intelligence/demo-retail/online_retail_II_prediction_intelligence.json`
- `data/business_context/demo-retail/online_retail_II_business_context.json`

**EXCLUIDO del paquete:**
- `data/tenant/users.json`: retirado del índice (los usuarios van por Secret File).
- Churn de tests restaurado a `d86e981`: `data/tenant/audit.jsonl`,
  `audit/index.json`, `data/profiles/test-profiling/*.json`.
- NO agregar: `visual-qa-local/` (capturas), `audit/log/*.json` nuevos,
  `data/raw/` (88 MB, excluido intencionalmente del repo).
- `tests/test_demo.py`: byte-idéntico al de producción; se re-agrega al commit.

## 3. Health check post-despliegue

1. `GET https://zayvero-business.onrender.com/health` → 200 `{"status":"ok",...}`
   (sin autenticación; Render puede tardar ~1 min en arrancar en plan Free).
2. Login en `https://getzayvero.com/business` con cuenta válida → debe llegar a `#/inicio`.
3. Abrir `#/diagnostico` → verificar tarjeta de predicción con etiquetas
   "Tendencia histórica" y "Dirección proyectada" por separado.
4. `GET /api/diagnostic` (sesión válida) → `predictions[0].forecast_direction`
   presente y `section_counts.opportunities.total == 60`.
5. Consola del navegador sin errores rojos; `?company_id=otro` sigue ignorado.

**Detección de errores:** Render Dashboard → servicio `zayvero-business` → pestaña
"Logs". Buscar: tracebacks de Python, `401` masivos en `/api/login`,
`500` en `/api/diagnostic`, `KeyError` en `data.py`.

## 4. Criterios que disparan la reversión

- `/health` no responde 200 tras 5 minutos del deploy.
- Login imposible o sesiones rotas.
- `/api/diagnostic` devuelve 500 o sin `forecast_direction`.
- Fuga de datos entre empresas (`?company_id=` devuelve datos ajenos).
- Errores JS que impiden renderizar el diagnóstico.

## 5. Pasos exactos de reversión — REVERSIÓN SEGURA (actualizado)

> **Regla de oro:** NUNCA revertir al commit `d86e981` completo. Ese código lee
> `data/tenant/users.json` del repo (hashes comprometidos, incluida la contraseña
> anterior del propietario) e IGNORA el Secret File. Un rollback completo
> reactivaría las credenciales viejas. Las opciones seguras, en orden:

**Estructura de despliegue requerida (2 commits secuenciales):**
- **Commit A (seguridad):** `tenant/store.py` (resolve_users_file + guard
  `ZAYVERO_REQUIRE_SECRET_FILE`), `git rm --cached data/tenant/users.json`,
  `.gitignore`, `scripts/`, `tests/test_auth_config.py`, docs de seguridad.
- **Commit B (calidad):** fix del Diagnóstico Ejecutivo (motores, builder,
  frontend, datos derivados 4A/4B/5A, tests de calidad).
- Desplegar A, verificar login con el Secret File, y luego desplegar B.

**Opción 1 — Revertir SOLO el commit B (recomendada si el problema es del fix
de calidad):**
1. `git revert <sha-del-commit-B>` (o revert vía GitHub API) y push a `main`.
2. El commit A (seguridad) sigue vigente: el Secret File en
   `/etc/secrets/users.json` sigue siendo la fuente de usuarios; las
   credenciales rotadas siguen válidas; los hashes viejos del repo NO se usan
   (el archivo ya no está trackeado y el guard impide el fallback local si
   `ZAYVERO_REQUIRE_SECRET_FILE=1` está configurado).
3. Verificar: `/health` → 200, login del propietario → 200, login con
   contraseña vieja → 401.
4. NO se re-expone ni se reutiliza ninguna credencial comprometida.

**Opción 2 — Forward-fix (recomendada como práctica general):**
- En lugar de revertir, corregir el problema en un commit nuevo (C) y
  desplegar hacia adelante. Evita por completo el riesgo de reactivar código
  viejo. Es la opción preferida de la industria.

**Opción 3 — Rollback completo a `d86e981` (SOLO como último recurso):**
- ⚠️ Reactiva `data/tenant/users.json` del repo con los hashes comprometidos.
- Si se ejecuta, es OBLIGATORIO inmediatamente después:
  1. Rotar TODAS las credenciales de nuevo (las del Secret File ya no las lee
     el código viejo).
  2. O bien: aplicar de inmediato el commit A (seguridad) encima, restaurando
     la protección, y re-verificar logins.
- Documentar el incidente: qué falló, por qué se eligió esta opción.

**Qué NO hacer en una reversión:**
- No eliminar el Secret File de Render durante una reversión.
- No commitear un `users.json` con contraseñas en claro "para salir del paso".
- No ejecutar `git reset --hard d86e981` + push sin el paso de rotación de la
  opción 3.

## 6. Riesgos pendientes conocidos (actualizado 2026-10-08 noche)

- Los hashes viejos permanecen en el **historial público** de GitHub (decisión
  documentada: no reescribir). Quedan inservibles tras la rotación de
  credenciales, pero visibles. Si alguien clonó el repo en la ventana de
  exposición (~3h), tiene esos hashes: mitigado por la rotación obligatoria.
- El Secret File es **estático por deploy**: cambios en runtime (registros del
  onboarding) se pierden al reiniciar en plan Free. Aceptado para demo;
  **PostgreSQL con backups probados antes de clientes reales** (ver
  `docs/persistence-notes.md`).
- No existe flujo "olvidé mi contraseña": si el propietario pierde la nueva,
  se regenera el Secret File manualmente con `scripts/rotate_owner_password.py`.
- `data/tenant/companies.json` y `sessions.json` siguen en el repo (sin
  secretos, pero con la misma limitación de persistencia del plan Free).
- El token de GitHub está en la config git local (`.git/config`); no forma parte
  del paquete, pero existe como secreto en la máquina.
- Los textos generados del diagnóstico son solo en español (comportamiento
  preexistente); el frontend soporta ES/EN.

## 7. Checklist pre-push (verificable, en orden)

> **Regla: NO hacer push si algún punto falla.**

1. [ ] `git diff d86e981 --stat` contiene SOLO los archivos del fix + seguridad
      (lista exacta en sección 2). Sin `users.json` trackeado, sin churn.
2. [ ] `git check-ignore data/tenant/users.json` → lo ignora.
3. [ ] Escaneo de secretos: `grep -rEn "ghp_|sk-[A-Za-z0-9]{10,}"` en archivos
      nuevos/modificados → vacío. Ningún `password` en claro fuera de fixtures
      de test. Ningún hash `pbkdf2_sha256$` real en archivos trackeados.
4. [ ] Suite completa: 737/737 verde (ver informe de validación).
5. [ ] **Secret File creado en Render ANTES del push:**
      Dashboard → servicio `zayvero-business` → pestaña **Environment** →
      sección **Secret Files** → **Add Secret File** → Filename: `users.json` →
      pegar el contenido generado por `scripts/generate_prod_seed.py` (con la
      contraseña del propietario ya rotada vía `scripts/rotate_owner_password.py`)
      → **Save Changes**. Render lo monta en `/etc/secrets/users.json`
      (los Secret Files no se pueden definir en `render.yaml`; es manual).
6. [ ] Variable de entorno `ZAYVERO_REQUIRE_SECRET_FILE=1` configurada en
      Render (Environment → Environment Variables). Con esto, si el Secret File
      faltara, el arranque aborta en vez de usar una copia local silenciosa.
7. [ ] Verificación post-deploy (en orden):
      a. Render → Logs: buscar `users.json activo: /etc/secrets/users.json
         (origen: render-secret)`.
      b. `GET /health` → 200.
      c. Login del propietario con la contraseña NUEVA → 200 y `#/inicio`.
      d. Login con la contraseña VIEJA → 401.
      e. `#/diagnostico`: tarjetas con "Tendencia histórica" y
         "Dirección proyectada" por separado.
      f. `/api/diagnostic`: `forecast_direction` presente,
         `section_counts.opportunities.total == 60`.
      g. Consola del navegador sin errores rojos.

## 8. Plan de despliegue paso a paso (cuando se autorice)

1. Miguel genera su contraseña con `scripts/rotate_owner_password.py`
   (nunca por chat) y se genera el seed final con `scripts/generate_prod_seed.py`.
2. Crear el Secret File `users.json` en Render (checklist 5).
3. Configurar `ZAYVERO_REQUIRE_SECRET_FILE=1` en Render (checklist 6).
4. Crear **commit A** (seguridad) y push → Render auto-despliega.
   Verificar checklist 7a–7d (login). Si falla: NO continuar al commit B.
5. Crear **commit B** (calidad) y push → Render auto-despliega.
   Verificar checklist 7 completo.
6. Si algo falla en B: revertir SOLO el commit B (sección 5, opción 1) —
   el commit A (seguridad) se mantiene siempre.

- Los hashes viejos permanecen en el **historial público** de GitHub (decisión
  documentada: no reescribir). Quedan inservibles tras la rotación de
  credenciales, pero visibles. Si alguien clonó el repo en la ventana de
  exposición (~3h), tiene esos hashes: mitigado por la rotación obligatoria.
- El Secret File es **estático por deploy**: cambios en runtime (registros del
  onboarding) se pierden al reiniciar en plan Free. Aceptado para demo;
  **PostgreSQL con backups probados antes de clientes reales** (ver
  `docs/persistence-notes.md`).
- No existe flujo "olvidé mi contraseña": si el propietario pierde la nueva,
  se regenera el Secret File manualmente con `scripts/rotate_owner_password.py`.
- `data/tenant/companies.json` y `sessions.json` siguen en el repo (sin
  secretos, pero con la misma limitación de persistencia del plan Free).
- El token de GitHub está en la config git local (`.git/config`); no forma parte
  del paquete, pero existe como secreto en la máquina.
- Los textos generados del diagnóstico son solo en español (comportamiento
  preexistente); el frontend soporta ES/EN.
