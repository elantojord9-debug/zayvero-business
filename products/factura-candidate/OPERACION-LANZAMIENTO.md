# Activación de ZAYVERO FACTURA administrativo

Estado al 28/10/2026: candidato en PR #13; no se ha reemplazado staging ni producción. No aplicar el Blueprint hasta aceptar el costo mostrado por Render, SMTP y almacenamiento. La plantilla crea recursos nuevos y de pago, con despliegue automático desactivado; no usa SQLite ni volumen efímero para datos comerciales. Validación de esquema no equivale a despliegue.

## Orden de activación

1. Revisar el SHA aprobado y desplegar primero una instancia aislada. Los nombres nuevos evitan sustituir servicios existentes. No fusionar a main para provocar el despliegue de la aplicación antigua.
2. Crear PostgreSQL dedicado y restringir acceso externo. Ejecutar `python app.py --migrate` con DSN del dueño **solo en una sesión administrativa**. Nunca colocar ese DSN en el servicio web.
3. Ejecutar `scripts/bootstrap_pg_roles.py` con FACTURA_ADMIN_DATABASE_URL, FACTURA_APP_ROLE/PASSWORD y FACTURA_BACKUP_ROLE/PASSWORD. Roles nuevos y distintos; contraseñas aleatorias >=24 caracteres. El dueño conserva DDL para futuras migraciones; app solo DML y backup solo lectura. Retirar credenciales del dueño al terminar. Reaplicar grants explícitos tras futuras migraciones con nuevas tablas.
4. Configurar FACTURA_DATABASE_URL del web con rol app y `sslmode=require` (o verify-full con CA). En el cron usar exclusivamente el rol backup y TLS. Crear FACTURA_SECRET estable, origen HTTPS canónico y superadministrador seguro. Verificar el preflight y /health. No cargar demos en cloud.
5. Configurar SMTP con STARTTLS y dominio autenticado. Probar recuperación y expiración con buzones propios autorizados; no enviar invitaciones ni correos a terceros durante QA. La prueba de transporte real queda pendiente hasta disponer de credenciales y un destinatario autorizado.
6. Crear bucket privado separado del web, bloquear acceso público, activar versionado, cifrado y retención de 30 días. Dar al cron permisos mínimos PutObject/GetObject en su prefijo; el web no recibe claves S3. Ejecutar backup_remote.py, comprobar lectura/hash del proveedor y recuperar en una base distinta con scripts/respaldo.py. Comparar tablas, ventas, pagos, stock, caja y auditoría. Registrar duración y punto recuperado. El doble S3 en tests no demuestra durabilidad remota real.
7. Activar cron diario a 05:00 UTC (01:00 RD), alertar si no hay respaldo válido en 26 horas o falla /health. Objetivo inicial propuesto RPO <=24h y RTO <=4h: deben medirse en el ensayo del proveedor, aún no acreditados.
8. Completar alta real de dos comercios sintéticos: empresa, administrador, primer producto, apertura de caja, venta, impresión no fiscal, abono, devolución y cierre. Comprobar que A no lee ni modifica B. Validar exportación y soporte; acordar privacidad, retención, eliminación y alcance del piloto con el propietario.
9. Habilitar piloto limitado únicamente cuando todos los P0 de INFORME-LANZAMIENTO.md estén acreditados. Conservar rollback de imagen y backup previo; ante error financiero suspender escritura y restaurar en instancia paralela, sin sobrescribir la única copia.

## Restricciones comerciales

Los documentos dicen SIN VALOR FISCAL. e-CF real está bloqueado y requiere proyecto/certificación separados. Los importes RD$790, RD$1,490 y RD$2,490 son preliminares: no publicarlos ni cobrarlos. No hay cobros SaaS habilitados. Un piloto no equivale a lanzamiento comercial general.

## Recursos existentes

El staging de imagen privada y el main antiguos siguen intactos. La base QA gratuita dpg-dbh3i9afngtc73fa567g-a caduca el 27/11/2026 a las 18:04 UTC; no contiene clientes y no constituye infraestructura productiva. El conector no permitió obtener DSN/migrar sin abrir acceso externo; las pruebas PostgreSQL se ejecutaron en CI aislado.

## Evidencia mínima por versión

Suite sin fallos ni omisiones; aislamiento/roles/CSRF; rollback con fallos y proceso interrumpido; concurrencia e idempotencia; restauración y conciliación; navegador móvil/escritorio en PG; arranque de imagen con TLS/rol limitado; persistencia tras reinicio. Conservar SHA, enlace CI y ensayo operacional del proveedor antes de aceptar datos reales. La carga de /health es smoke técnico, no prueba de capacidad de ventas.
