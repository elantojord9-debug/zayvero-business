> Referencia histórica de desarrollo local. Para este candidato prevalecen `../../OPERACION-LANZAMIENTO.md` e `../../INFORME-LANZAMIENTO.md`; las rutas, roles y afirmaciones de producción local de abajo no acreditan un servicio comercial activo.

# ZAYVERO FACTURA — Operación sobre PostgreSQL (FASE 6)

> **NOTA DE PORTABILIDAD:** las rutas de esta guía
> (`/home/hatch/.pgdata`, `/var/run/postgresql`, `/etc/postgresql/16/main/`)
> son específicas de la VM de desarrollo original. En otro servidor use sus
> propias rutas de datos/socket de PostgreSQL e inyecte el DSN por
> `FACTURA_DATABASE_URL` (ver `docs/INSTALACION.md`).
>
> Sin secretos en este documento. El DSN vive en `~/.pg_dsn_factura_app`
> (permiso 0600) y se inyecta por la variable `FACTURA_DATABASE_URL`.
> Nunca se imprime en logs ni reportes.

## 1. Arquitectura

- **Instancia única consolidada** en `127.0.0.1:5432` (PostgreSQL 16).
- **Datos durables:** `/home/hatch/.pgdata` (dentro del home, sobrevive
  reinicios de la VM). No usar `/var/lib/postgresql` (efímero).
- **Socket:** `/var/run/postgresql` (efímero: se recrea en cada arranque).
- **Config:** `/etc/postgresql/16/main/postgresql.conf` (efímero; el
  script de arranque tiene fallback autocontenido si falta).
- **Arranque tras reinicio** (como root):
  `bash ~/workspace/postgres-iniciar.sh`

## 2. Bases y roles

| Base                  | Rol dueño     | Uso                                    |
|-----------------------|---------------|----------------------------------------|
| `factura_app`         | `factura_app` | App ZAYVERO FACTURA (producción local) |
| `factura_app_restore` | `factura_app` | Prueba de recuperación (evidencia)    |
| `ecf_endurecimiento_test`, `factura_crm_test` | `factura_test` | Otros workers (e-CF/CRM), no tocar |

- El rol `factura_app` es LOGIN sin CREATEDB/SUPERUSER: solo opera su base.
- Credencial: `~/.pg_dsn_factura_app` (0600). Formato:
  `postgresql://factura_app:<clave>@127.0.0.1:5432/factura_app`
- La app la consume así:
  `FACTURA_DATABASE_URL="$(cat ~/.pg_dsn_factura_app)" python app.py --port 8801`

## 3. Migraciones

Idempotentes, vía `factura_persist.db.migrate()` — **ya no corren solas al
arrancar**: el flujo es `app.py --migrate` primero y `app.py` después
(ver README, sección "Despliegue en staging"; el modo serve exige
migraciones al día y en cloud exige además PG + TLS). Orden en
`factura_persist/schema/_orden.txt` (010 a la fecha).
Particularidades PG implementadas en `db.py`:

- `_traducir_pg`: `INTEGER PRIMARY KEY AUTOINCREMENT` → `BIGSERIAL PRIMARY KEY`.
- `_dividir_sentencias_pg`: splitter consciente de comentarios `--`/`/* */`,
  literales `'...'` e identificadores `"..."` (el `split(";")` ingenuo rompía
  con comentarios que contienen `;`).
- `Fila`: las filas PG se envuelven con la misma interfaz que `sqlite3.Row`
  (`fila[0]`, `fila["col"]`, `dict(fila)`, desempaquetado).
- **Encoding:** las bases deben crearse `UTF8` explícito
  (`TEMPLATE template0`), porque `template0`/`template1` del cluster son
  `SQL_ASCII` y el esquema contiene caracteres no-ASCII en comentarios.

Crear una base nueva para la app (como superusuario OS `postgres`):

```bash
runuser -u postgres -- psql -h /var/run/postgresql -v ON_ERROR_STOP=1 \
  -c "CREATE DATABASE <nombre> OWNER factura_app ENCODING 'UTF8' \
      LC_COLLATE 'C.UTF-8' LC_CTYPE 'C.UTF-8' TEMPLATE template0;"
```

## 4. Respaldo y recuperación (`scripts/respaldo.py`)

El script detecta el backend por `FACTURA_DATABASE_URL`. En PG usa
`pg_dump -Fc` (formato custom) y genera manifiesto JSON con SHA-256.

```bash
export FACTURA_DATABASE_URL="$(cat ~/.pg_dsn_factura_app)"
cd ~/workspace/zayvero-factura

# Crear respaldo
.venv/bin/python scripts/respaldo.py crear <etiqueta>
# -> backups/factura-<ts>-<etiqueta>.dump (+ .dump.json con sha256)

# Verificar integridad (SHA-256 + listado del archivo custom)
.venv/bin/python scripts/respaldo.py verificar <archivo.dump>

# Restaurar en una base de PRUEBA (la base debe existir, vacía)
.venv/bin/python scripts/respaldo.py restaurar <archivo.dump> --confirmo --db factura_app_restore

# Restaurar sobre la base productiva (requiere --confirmo explícito)
.venv/bin/python scripts/respaldo.py restaurar <archivo.dump> --confirmo
```

### Procedimiento de recuperación verificado (2026-10-14)

1. `respaldo.py crear fase6-pg` → `backups/factura-20261014T141358Z-fase6-pg.dump`
   (91 646 bytes, SHA-256
   `a8525d4cc4b55a05b53f6a4cc26f8c6df8b25504ca1b3c6aaf2b2d6354ac73f2`
   verificado contra el manifiesto).
2. `respaldo.py verificar` → `{"sha_ok": true, "tablas_factura": 25,
   "verificacion": "OK"}`.
3. Base `factura_app_restore` creada vacía (UTF8, dueño `factura_app`).
4. `respaldo.py restaurar … --confirmo --db factura_app_restore` → OK.
5. Comparación tabla por tabla (conteos + `md5(string_agg(...))`):
   10 tablas comparadas, **todas idénticas** entre `factura_app` y
   `factura_app_restore` (empresas, usuarios, clientes, productos, ventas,
   cotizaciones, pagos, migraciones, sucursales, caja_movimientos).

## 5. Aislamiento entre empresas (verificado 2026-10-14)

- Empresa 1 "Tienda La Esquina" (demo, 6 clientes) y empresa 3
  "Colmado El Faro (prueba)" (1 cliente) creadas como datos sintéticos.
- Login como `faro@prueba.do` → `/clientes` muestra **solo**
  "Cliente Solo Faro"; `/panel` muestra solo su empresa.
- Login como `admin@demo.do` → no ve "Cliente Solo Faro".
- Toda consulta filtra por `empresa_id` de la sesión; verificado por HTTP
  y a nivel SQL.

## 6. Estado operativo actual

- App corriendo en `http://127.0.0.1:8801/` sobre `factura_app` (PG).
- Login demo: `admin@demo.do` / `Demo1234!` (datos 100% ficticios).
- Datos demo: 1 empresa, 12 productos, 5 clientes, 3 ventas, 1 cotización,
  1 CxC con abono; más 1 cliente y 1 producto de prueba ("…Prueba PG") y
  la empresa 3 de la prueba de aislamiento.
- Respaldo verificado en `backups/factura-20261014T141358Z-fase6-pg.dump`.

## 7. Notas y lecciones

- 2026-10-14 ~13:50: la VM se reinició y el cluster PG anterior (en
  almacenamiento efímero) se perdió con sus paquetes. Se reinstaló
  `postgresql-16` desde la caché local de apt y se consolidó la instancia
  con datos en `/home/hatch/.pgdata` (durable). **Lección:** nada de PG
  fuera del home; tras cada reinicio correr `postgres-iniciar.sh`.
- El DSN antiguo `~/.pg_dsn_zayvero` (usuario `zayvero_os`) quedó inválido:
  ese rol/base ya no existen en la instancia consolidada.
- `db.migrate()` en PG fallaba por el splitter `split(";")`; corregido con
  `_dividir_sentencias_pg`.
- El código asumía `sqlite3.Row`; corregido con `db.Fila` (sin tocar los
  24 sitios que usan `dict(fila)`).
