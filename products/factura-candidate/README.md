# ZAYVERO FACTURA — candidato administrativo RC1

Producto administrativo **SIN VALOR FISCAL**. Incluye la aplicación y los
motores compartidos fijados por hash. No necesita clonar una rama remota
incompleta. Este candidato no está autorizado técnicamente para clientes
reales hasta completar `INFORME-LANZAMIENTO.md`.

## Instalación local reproducible

Requisitos: Python 3.12. Desde esta carpeta:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r factura/requirements-dev.txt
cd factura
../.venv/bin/python app.py --migrate
../.venv/bin/python app.py --preflight
../.venv/bin/python app.py --port 8801
```

Acceso local: http://127.0.0.1:8801/registro. Usar solo datos sintéticos
en desarrollo. No publicar esta instancia ni las credenciales de demo.

## Pruebas

```bash
cd factura
../.venv/bin/python -m pytest tests -q --junitxml=resultados.xml
../.venv/bin/python -m playwright install chromium
../.venv/bin/python tests/e2e_admin.py
```

La E2E inicia Gunicorn con dos procesos y crea/borrar su propia base SQLite
sintética. Si Chromium está instalado en otra ruta, definir
`FACTURA_BROWSER_PATH`. No ejecuta pagos ni mensajes externos.

Las pruebas PG se habilitan exclusivamente con una base **de pruebas vacía**:

```bash
export FACTURA_TEST_ALLOW_DDL=1
# FACTURA_TEST_DATABASE_URL se configura por un canal secreto del entorno.
../.venv/bin/python -m pytest tests -q --junitxml=resultados-pg.xml
```

La cuenta de prueba necesita crear esquemas, bases y roles para las pruebas
de permisos y restauración. No usar una cuenta ni una base de clientes.
El workflow `.github/workflows/qa.yml` prepara PG 16 efímero y rechaza skips.
Ese workflow se entrega como código; no ha sido ejecutado en GitHub.

## Servicio desplegable

La entrada HTTP de despliegue es:

```bash
python -m gunicorn -c gunicorn.conf.py wsgi:application
```

Aplicar migraciones **antes**, con credenciales de migración separadas.
El rol runtime no debe tener DDL. Variables obligatorias de cloud:

- `FACTURA_ENV=staging` o `production`.
- `FACTURA_DATABASE_URL`: PG con TLS y esquema al día.
- `FACTURA_SECRET`: secreto estable de al menos 32 caracteres.
- `FACTURA_HTTPS=1` y HTTPS real delante del servicio.
- `FACTURA_PUBLIC_ORIGIN`: origen HTTPS fijo de la app.
- `FACTURA_SUPERADMIN_EMAIL` y `FACTURA_SUPERADMIN_PASS`: propios, sin valores
  de desarrollo y con contraseña de al menos 16 caracteres.
- SMTP real mediante las variables descritas en `factura/docs/SEGURIDAD.md`.

`/health` comprueba conectividad de BD sin divulgar datos. La app sirve en
el `PORT` del proveedor. No conectar suscripciones ni publicar los importes
de planes internos.

## Backups y actualización

`factura/scripts/respaldo.py` admite `FACTURA_BACKUP_DIR` para un destino
montado fuera de la app. Sus permisos se restringen; cada copia lleva hash.
La copia externa automática, su horario, retención y alertas deben
configurarse y probarse en el entorno objetivo. No se consideran activadas
por incluir este script. PG restore exige una base distinta de la operativa.

Migraciones nuevas: 011 (replay durable) y 012 (créditos/reembolsos). Las
devoluciones históricas quedan `financiera=0`: requieren conciliación
explícita antes de migrar datos reales; no se convierten retroactivamente
en créditos ni reembolsos.

Los importes preliminares permanecen solo en configuración interna. La
emisión e-CF está bloqueada en cloud; la simulación se mantiene local.
