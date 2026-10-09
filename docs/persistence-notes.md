# Notas de persistencia — ZAYVERO Business en Render

**Fecha:** 2026-10-08. **Estado:** documento informativo, sin cambios de infraestructura.

## Situación actual (plan Free de Render)

Todo el estado mutable de la aplicación vive en archivos JSON bajo `data/`:

| Archivo | Qué guarda | Se pierde al reiniciar |
|---|---|---|
| `data/tenant/users.json` | Cuentas de usuario (email, rol, estado, hash) | **Sí** (si se crea/modifica en runtime) |
| `data/tenant/companies.json` | Empresas/tenants | **Sí** (si se crean en runtime) |
| `data/tenant/sessions.json` | Sesiones activas | **Sí** |
| `data/tenant/audit.jsonl` | Auditoría | **Sí** (lo escrito en runtime) |
| Uploads de datasets | CSV/XLSX subidos por clientes | **Sí** |
| JSON derivados regenerados | Predicciones, diagnósticos | **Sí** (se pueden regenerar) |

**Se conserva:** todo lo que viene del repo git (código, demo precargada, `users.json` commiteado — que se está retirando del repo, ver `docs/security-remediation-plan.md`).

## Decisión aprobada para la demo

Para la **demo** se acepta temporalmente la persistencia actual, con estas condiciones:

1. Está **documentado y comunicado**: los datos creados en vivo (cuentas, empresas, sesiones) se pierden cuando Render reinicia el servicio (plan Free, filesystem efímero).
2. **No se utilizan datos empresariales reales** en la demo pública.
3. El *seed* de usuarios sobrevive a los reinicios porque se re-monta desde el **Secret File** en cada deploy (no depende del filesystem efímero).

## Antes de clientes reales: PostgreSQL administrado

Antes de aceptar el primer cliente real se evaluará **PostgreSQL administrado**:

- **Opción recomendada:** Supabase o Neon (tier gratuito para empezar) o Render Postgres (~$6/mes).
- **Requisitos:** backups automáticos y **recuperación probada** (restore ensayado, no solo configurado).
- **Alcance del cambio:** reescribir `tenant/store.py` contra la base de datos (users, companies, sessions, audit). Es un cambio mayor: requiere diseño, migración de datos y ventana de mantenimiento. **No empezar sin aprobación explícita.**
- **El Secret File seguirá siendo** el mecanismo correcto para el *seed* inicial y las credenciales, no para los datos vivos.

## Alternativa a corto plazo (si se necesita antes)

Disco persistente de Render (~$7+/mes por servicio + disco): monta el disco en `data/` y los JSON sobreviven a reinicios con cambio mínimo de código. Requiere salir del plan Free. Útil como puente si hay pocos clientes antes de la migración a PostgreSQL.
