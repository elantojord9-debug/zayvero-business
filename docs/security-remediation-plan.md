# Plan de remediación de seguridad — `data/tenant/users.json` expuesto en repo público

**Estado:** IMPLEMENTADO EN LOCAL (2026-10-08/09). Sin commit, push, despliegue ni cambios destructivos. Pendiente de aprobación explícita de Miguel para publicar.
**Fecha:** 2026-10-08. **Repo:** público (`elantojord9-debug/zayvero-business`). **Deploy:** push → Render auto-deploy (plan Free).

Implementado:
- `tenant/store.py`: resolución `ZAYVERO_USERS_FILE` → `/etc/secrets/users.json` → local, con aviso en log si falta el archivo (arranque seguro sin usuarios).
- `git rm --cached data/tenant/users.json` (staged) + `.gitignore` ampliado.
- `scripts/generate_prod_seed.py`: genera el seed limpio (14 cuentas de prueba eliminadas, demo/viewer con credenciales nuevas, cuenta de Miguel con marcador pendiente).
- `scripts/rotate_owner_password.py`: el propietario establece su contraseña por getpass, sin pasar por el chat.
- `docs/persistence-notes.md`: persistencia aceptada para demo; PostgreSQL antes de clientes reales.
- `tests/test_auth_config.py`: 13 tests de la nueva configuración (todos verdes).
- Respaldo protegido: `~/workspace/zayvero-security-backups/users.json.bak-2026-10-08` (600).

> Nota: este documento no contiene contraseñas ni hashes. Solo describe procedimientos.

---

## 1. Cómo se usa `users.json` hoy (verificado en código)

| Pregunta | Respuesta (código) |
|---|---|
| ¿Quién lo lee? | Solo `tenant/store.py::TenantStore` (`_users_file = <data_dir>/users.json`). Ningún otro módulo referencia la ruta. |
| ¿Quién lo escribe? | `TenantStore.save_user()` — llamado por `tenant/auth.py::create_user()` (registro en el onboarding paso a paso), `update_user()` (cambio de rol/estado) y `login()` indirectamente vía sesiones. También los scripts de QA locales. |
| ¿Hay seed automático? | **No.** Si el archivo no existe, `_load_list()` devuelve `[]`: la app arranca sin romperse, pero **nadie puede iniciar sesión**. |
| ¿Qué se rompe sin el archivo? | Login (401 para todos), "Explorar demo" (`_demo_viewer()` no encuentra lector → "demo no disponible"), y cualquier flujo que requiera usuario autenticado. El resto (páginas públicas, `/health`) sigue funcionando. |
| Dependencias en tests | Ninguna sobre el archivo del repo: `tests/test_tenant.py` y demás usan `TenantStore(tmp_dir)` con directorios temporales. |
| Permisos | `_save_list()` aplica `chmod 600` al guardar `users.json` (correcto en disco, irrelevante una vez commiteado). |
| Formato del hash | `pbkdf2_sha256$260000$<sal_hex_16B>$<hash_hex>` (`tenant/crypto.py`, solo stdlib). Sal única por usuario. |

**Conclusión:** el archivo es el *único* backend de usuarios. Retirarlo del repo sin reemplazo deja la app sin login en producción. El plan incluye el reemplazo.

---

## 2. Retirar el archivo del repo sin romper nada

### Paso 2.1 — Ignorarlo para que no vuelva
Agregar a `.gitignore`:
```
data/tenant/users.json
```
(Ya existen entradas afines: `data/tenant/sessions.json`, `.env`, `visual-qa-local/`, `data/raw/`, `audit/log/`.)

### Paso 2.2 — Dejar de trackearlo (en el commit futuro del fix de calidad)
```bash
git rm --cached data/tenant/users.json
```
Esto lo saca del índice pero **conserva el archivo en disco** (no destructivo). Debe ir en el mismo commit que el despliegue del fix de calidad, junto con el cambio de código del paso 2.3.

### Paso 2.3 — Cambio de código mínimo (requerido antes del deploy)
`tenant/store.py::TenantStore.__init__` debe resolver la ruta del archivo de usuarios con esta prioridad:

1. Variable de entorno `ZAYVERO_USERS_FILE` (ruta explícita; apunta al Secret File en Render).
2. `/etc/secrets/users.json` (ubicación estándar de Render Secret Files).
3. Fallback actual: `<data_dir>/users.json` (desarrollo local y tests).

Además, al arrancar, si el archivo resuelto **no existe**, el servidor debe registrar un aviso claro en el log (`users.json no encontrado: sin usuarios; provea ZAYVERO_USERS_FILE o Secret File`) en lugar de fallar en silencio. No se crea ningún usuario automáticamente (decisión consciente: crear cuentas por defecto sería otro riesgo).

### Paso 2.4 — Qué debe existir en Render para que la app arranque
Antes del deploy, en el dashboard de Render (Environment → Secret Files) crear el archivo secreto:
- **Nombre:** `users.json`
- **Contenido:** el JSON de usuarios saneado y con contraseñas rotadas (ver secciones 5 y 6). Lo pega Miguel o se sube por el flujo seguro; nunca pasa por el repo.
- **Efecto:** Render lo monta en `/etc/secrets/users.json` y redeploya. El código del paso 2.3 lo detecta solo.

### Paso 2.5 — Verificación de que no se re-agrega (CI)
Agregar un test (`tests/test_no_secrets_in_repo.py`):
- `data/tenant/users.json` está en `.gitignore`.
- `git ls-files data/tenant/users.json` está vacío (no trackeado).
- Ningún archivo trackeado contiene el patrón `pbkdf2_sha256$`.

### Riesgos del paso 2
| Riesgo | Mitigación |
|---|---|
| Deploy sin Secret File → nadie puede entrar | Checklist pre-deploy: verificar en el dashboard que el Secret File existe ANTES del push; el log de arranque avisa. |
| El fallback local enmascara el problema en dev | El aviso en log es explícito; en Render la env var siempre estará seteada. |
| `git rm --cached` olvidado en el commit | El test del paso 2.5 falla si el archivo vuelve a estar trackeado. |

---

## 3. Historial público: el archivo ya estuvo expuesto

### Hechos verificados
- El archivo se commiteó por primera vez el **2026-10-08 17:38 AST** (commit `251c622`, "FASE 8 + corrección de entrada") y también está en `33a9e44` (origin/main, 17:56 AST).
- Ventana de exposición pública: **~3 horas** hasta la redacción de este plan.
- Los 16 hashes originales **nunca rotaron** (sales idénticas entonces vs ahora). La cuenta de Miguel se agregó después, también sin rotar.

### Opción A — Retirar hacia adelante + rotar credenciales (RECOMENDADA)
- Se deja el historial intacto y se retira el archivo en el próximo commit (`git rm --cached`).
- Se **rotan todas las contraseñas activas** (sección 5): los hashes viejos del historial quedan inservibles.
- Es lo que recomienda GitHub para secretos expuestos: una vez público, el secreto se considera comprometido y se rota; reescribir el historial no garantiza nada (cualquiera pudo clonarlo en la ventana de exposición).
- **Decisión requerida de Miguel:** aceptar que el historial conserve los hashes viejos (ya inservibles tras la rotación).

### Opción B — Reescribir el historial (DESCARTADA — prohibida por Miguel)
`git filter-repo` / BFG para purgar el archivo de todos los commits. Riesgos: reescribe todos los hashes de commit, **rompe todos los clones**, invalida el deploy de Render (requiere push forzado), y **no sirve de nada** si alguien ya clonó el repo en la ventana de exposición. Solo se documenta para descartarla explícitamente.

### Riesgo residual (opción A)
Si un atacante copió los hashes en la ventana de ~3h, podría intentar un ataque de diccionario offline contra ellos. Tras la rotación, esos hashes no sirven para entrar. El riesgo real durante la ventana fue bajo (repo recién creado, sin visibilidad), pero no es cero: por eso la rotación es obligatoria, no opcional.

---

## 4. Almacenamiento seguro compatible con Render

### Opción A — Render Secret Files (RECOMENDADA para esta fase)
- **Cómo:** Dashboard → Environment → Secret Files → archivo `users.json` con el contenido saneado. Render lo monta en `/etc/secrets/users.json` (cifrado, fuera del repo, se actualiza en cada redeploy).
- **Código:** el cambio del paso 2.3 (unas 10 líneas en `tenant/store.py`).
- **Seed inicial:** el propio Secret File ES el seed (contiene las 17 cuentas con hashes nuevos).
- **Plan Free:** compatible (los Secret Files no requieren plan pago).
- **Costo:** $0. **Esfuerzo:** bajo (cambio mínimo + 1 test + pegado manual por Miguel).
- **Limitación honesta:** el Secret File es **estático por deploy**. Los cambios en runtime (nuevos registros del onboarding, cambios de contraseña) se escriben al filesystem efímero y **se pierden al reiniciar** (igual que hoy). Resuelve la *exposición*, no la *persistencia* (ver sección 8).

### Opción B — Variables de entorno
- El JSON completo como env var. **Descartada:** Render limita el tamaño de las env vars; el archivo crecerá con cada usuario; es inmanejable para pegar/rotar.

### Opción C — PostgreSQL externo (destino final, no para este deploy)
- Supabase/Neon (tier gratuito) o Render Postgres (~$6/mes).
- **Código:** reescribir `tenant/store.py` contra una base de datos (cambio mayor: users, companies, sessions, audit).
- **Costo:** $0–$6/mes. **Esfuerzo:** alto (días, no horas). Requiere migración de datos y ventana de mantenimiento.
- **Cuándo:** antes de clientes reales, como parte de la solución de persistencia (sección 8).

**Recomendación:** Opción A ahora (desbloquea el deploy seguro del fix de calidad con esfuerzo mínimo), Opción C como proyecto aparte antes de clientes reales.

---

## 5. Rotación de credenciales (paso a paso)

**Principio:** ninguna contraseña viaja por el chat ni por el repo. Miguel la define; el sistema solo maneja hashes.

1. **Miguel define** la nueva contraseña de su cuenta (`demiguel099@gmail.com`) — la escribe solo en el formulario seguro / canal que se acuerde, nunca en el chat.
2. **Cuentas demo** (`demo@zayvero.com`, `viewer@zayvero.com`): se les asignan contraseñas nuevas generadas localmente y se entregan a Miguel por el canal seguro (las necesita para probar la demo).
3. **Cuentas de prueba** (4 activas `owner_a/b`, `ownera/b@e2e.test` + 10 deshabilitadas): **recomendación: eliminarlas** del seed de producción (no sirven en prod). **Decisión requerida de Miguel:** eliminar vs conservar deshabilitadas.
4. **Generación de hashes:** script local (usa `tenant/crypto.py::hash_password`, el mismo algoritmo) que toma el nuevo `users.json` y reemplaza los hashes. El script nunca imprime ni guarda contraseñas.
5. **Verificación local:** levantar el servidor con el archivo rotado (vía `ZAYVERO_USERS_FILE`), login con la contraseña nueva → 200; login con la vieja → 401.
6. **Publicación del secreto:** Miguel pega el contenido del `users.json` rotado como Secret File en Render (o lo sube por el flujo seguro acordado).
7. **Verificación post-deploy:** login en `https://zayvero-business.onrender.com` con la cuenta de Miguel; `/health` → 200; `#/diagnostico` carga con ambas etiquetas de tendencia.
8. **Limpieza:** borrar de la máquina local cualquier copia intermedia del archivo con contraseñas nuevas (solo queda la versión con hashes, que es lo que va al Secret File).

### Riesgos del paso 5
| Riesgo | Mitigación |
|---|---|
| Miguel pierde/olvida la nueva contraseña | Se verifica el login local ANTES del deploy; existe el flujo de "olvidé mi contraseña" por definir (hoy no hay recovery: documentarlo como pendiente). |
| El Secret File queda con el contenido viejo | El paso 7 lo detecta (login con la nueva falla) → se repite el paso 6 y redeploy. |
| Cuentas de prueba con acceso activo en prod | Se eliminan en el paso 3 (decisión de Miguel). |

---

## 6. Preservar las 17 cuentas (migración no destructiva)

Cuentas actuales en producción (17): `demiguel099@gmail.com` (owner, activa — Miguel), `demo@`/`viewer@` (demo, activas), 4 de prueba activas, 10 de prueba deshabilitadas.

Procedimiento:
1. **Respaldo previo:** copiar `data/tenant/users.json` actual a una ubicación segura local (no al repo).
2. **Exportar registros sin secretos:** para cada cuenta, conservar `user_id`, `company_id`, `email`, `name`, `role_id`, `status`, `created_at`, `updated_at`. **Descartar** todos los `password_hash` viejos.
3. **Regenerar hashes:** solo para las cuentas que se conservan (Miguel + demo/viewer + las que Miguel decida), con las contraseñas nuevas de la sección 5.
4. **Verificación cuenta por cuenta:** script que compara email/rol/estado/empresa del archivo nuevo vs el respaldo; reporta altas, bajas y cambios. Cero sorpresas.
5. Las cuentas eliminadas (pruebas) simplemente no aparecen en el archivo nuevo; sus `user_id` no se reutilizan.

**Decisiones requeridas de Miguel:**
- ¿Eliminar las 14 cuentas de prueba del seed de producción? (recomendado: sí)
- ¿Conservar `demo@`/`viewer@` con contraseñas nuevas conocidas por él? (recomendado: sí, la demo las necesita para "Explorar demo")

---

## 7. Verificación de no-dependencia (qué cambiar en código)

Búsqueda exhaustiva (`grep -rn "users.json"`): **solo** `tenant/store.py` referencia la ruta (constructor + `chmod 600` al guardar). `tests/test_tenant.py:103` usa `_load_list` sobre un store temporal (no depende del repo).

Cambios necesarios (todos en `tenant/store.py`, ~15 líneas):
1. `__init__`: resolver `self._users_file` con la prioridad `ZAYVERO_USERS_FILE` → `/etc/secrets/users.json` → `<data_dir>/users.json`.
2. Aviso en log al arrancar si el archivo no existe (visible en logs de Render).
3. Documentar en el docstring la nueva resolución.

Nada más depende del archivo. `webapp/server.py` crea `TenantStore()` sin argumentos: funciona sin cambios.

---

## 8. Persistencia antes de clientes reales (recomendación)

**Hecho:** en plan Free, todo lo que la app escribe en runtime (`users.json`, `companies.json`, `sessions.json`, `audit.jsonl`, uploads) vive en filesystem efímero y **se pierde en cada reinicio**. Con Secret Files, el *seed* de usuarios sobrevive (se re-monta en cada deploy), pero los cambios posteriores (un cliente que se registra) no.

| Opción | Qué resuelve | Costo aprox. | Esfuerzo | Cuándo |
|---|---|---|---|---|
| A. Disco persistente de Render | Persistencia de los JSON (cambio mínimo de código: montar el disco en `data/`) | ~$7+/mes por servicio + disco | Bajo | Corto plazo, volumen bajo |
| B. PostgreSQL (Supabase/Neon gratis o Render ~$6/mes) | Persistencia + consultas reales + elimina los JSON | $0–$6/mes | Alto (reescribir `tenant/store.py`) | Destino final recomendado |
| C. Respaldo periódico a almacenamiento externo | Solo acota la pérdida, no la evita | ~$0 | Medio | No recomendada como solución |

**Recomendación:** A si se necesita algo ya con pocos clientes; **B como destino final antes de escalar**. En ambos casos, el Secret File de la sección 4 sigue siendo el mecanismo correcto para el *seed* inicial y las credenciales, no para los datos vivos.

---

## 9. Tests nuevos (los 721 actuales deben seguir verdes)

Agregar `tests/test_auth_config.py`:
1. `TenantStore` respeta `ZAYVERO_USERS_FILE` (apunta a un archivo temporal con usuarios; login funciona contra él).
2. Si el archivo no existe, el store arranca vacío y el log emite el aviso (sin excepción).
3. `data/tenant/users.json` está en `.gitignore` y no está trackeado (`git ls-files` vacío).
4. Ningún archivo trackeado contiene el patrón `pbkdf2_sha256$`.
5. Rotación: hash nuevo verifica con la contraseña nueva y rechaza la vieja (`verify_password`).
6. `chmod 600` se mantiene al guardar el archivo de usuarios.

Re-ejecutar la suite completa al final: objetivo 721 + nuevos, 0 fallos.

---

## Decisiones que requieren a Miguel (resumen)

1. **Historial:** aceptar la opción A (dejar el historial, rotar credenciales). La opción B está prohibida por él mismo; queda descartada.
2. **Cuentas de prueba:** ¿eliminar las 14 del seed de producción? (recomendado: sí)
3. **Cuentas demo:** ¿conservar `demo@`/`viewer@` con contraseñas nuevas que él conocerá? (recomendado: sí)
4. **Canal seguro:** ¿cómo me entrega su nueva contraseña para generar el hash? (formulario/vault; nunca el chat)
5. **Persistencia:** ¿disco de Render (~$7/mes) a corto plazo, o ir directo a PostgreSQL? (recomendado: decidir antes del primer cliente real, no hoy)
6. **Autorización final:** crear el commit (con `git rm --cached`), push y deploy — solo con su OK explícito después de revisar este plan.

## Riesgos residuales (después de ejecutar el plan)

- Los hashes viejos seguirán en el historial público de GitHub para siempre (inservibles tras la rotación, pero visibles).
- Si alguien copió el repo en la ventana de exposición (~3h), tiene los hashes viejos: mitigado por la rotación.
- Sin persistencia (sección 8), los datos creados en runtime se pierden al reiniciar Render — aceptable para demo, bloqueante para clientes reales.
- Hoy no existe flujo de "olvidé mi contraseña": si Miguel pierde la nueva, hay que regenerar el Secret File manualmente (documentar a futuro).
