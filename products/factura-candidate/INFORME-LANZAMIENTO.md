# ZAYVERO FACTURA — gate de lanzamiento administrativo

28/10/2026. Fuente y pruebas: [PR #13](https://github.com/elantojord9-debug/zayvero-business/pull/13), rama `factura/rc1-qa-20261028`.

**Candidato implementado; NO-GO para pilotos con datos reales hasta cerrar la operación del proveedor.** No equivale a e-CF certificado ni a producción habilitada. Main y staging existentes no se sustituyeron.

## Existente frente a implementado

La base recuperada ya aportaba clientes, catálogo, ventas, caja, pagos, devoluciones, reportes, autenticación, empresas y onboarding. Se verificó la integridad de los archivos recuperados; SOURCE_LOCK.json fija los motores compartidos. No se pudo recuperar una versión completa posterior a r2 del SaaS ni comparar el código de la imagen privada de staging.

Este candidato incorpora unidad de trabajo transaccional por empresa, bloqueos PG, idempotencia durable con validación de payload, rollback financiero y auditoría, límites de stock/devolución/reembolso, alta atómica y CxC completa sin corte en 500 documentos. Fechas comerciales y límites mensuales usan America/Santo_Domingo, conservando timestamps UTC.

Se endurecieron sesiones/CSRF/permisos/aislamiento, tamaño de peticiones, errores HTTP y exportación CSV/HTML. Cloud exige PG con TLS, migraciones, secreto estable y rol de aplicación sin DDL. Gunicorn sirve la aplicación; /health comprueba DB. Hay roles separados para dueño, app y backup; backup/restore incluye hashes, transacción de restauración PG y conciliación. Existe job S3 con lectura remota antes de marcar latest, probado con doble de proveedor; aún no está conectado ni programado en producción.

Los documentos e impresiones muestran SIN VALOR FISCAL. Mutaciones e-CF y seeds de demo están bloqueados en cloud. Los precios preliminares no se muestran ni se cobran.

## Prioridades y gates

| Prioridad | Gate | Evidencia / pendiente |
|---|---|---|
| P0 | Transacciones y aislamiento | Implementado y probado en SQLite y PG real aislado; repetir smoke con configuración final del proveedor. |
| P0 | Persistencia durable | Imagen y reinicio concilian 29 tablas en CI. Falta desplegar DB durable de producción, migrar y validar roles/TLS en destino. |
| P0 | Backup recuperable externo | pg_dump/restore real y rol solo lectura pasan en CI. Falta bucket privado, ejecución programada, alerta y restore desde proveedor; medir RPO/RTO. |
| P0 | Seguridad operacional | Código y regresiones listos. Faltan secretos reales, SMTP/TLS y prueba autorizada de recuperación, dominio HTTPS y supervisión. |
| P0 | Flujo administrativo | E2E móvil/escritorio, POS, factura, inventario, CxC, informes, devoluciones y gate de precios; repetir con dos comercios sintéticos en destino. |
| P0 | Onboarding y piloto | Alta atómica probada. Pendiente aceptación del propietario del flujo completo, soporte y alcance/privacidad/retención del piloto. |
| P0 | Alcance no fiscal | Aviso en documentos y bloqueo cloud e-CF implementados. No prometer comprobantes fiscales ni certificación. |
| P1 | Venta comercial | Definir precio final, condiciones, soporte, capacidad esperada y cobro SaaS antes de vender. Los tres precios del issue continúan preliminares. |
| Separado | e-CF real | Certificados, firma, ambientes/pruebas DGII y homologación requieren proyecto propio; no están incluidos en esta liberación. |

## Pruebas mínimas obligatorias

1. Suite completa sin fallos ni omisiones: autorización/empresa, CSRF, expiración/trial, precios ocultos y e-CF bloqueado.
2. Dos empresas no pueden cruzar lecturas, mutaciones, inventario, documentos, pagos ni auditoría. Rol runtime sin DDL y backup sin escrituras.
3. POS/abono/devolución concurrentes no duplican documentos ni dinero, ni dejan stock negativo. Reintentos conservan resultado; clave repetida con otro contenido se rechaza.
4. Fallo en caja/auditoría y muerte del proceso revierten toda la operación. Alta fallida no deja empresa huérfana.
5. Backup PG real restaurado en DB separada concilia tablas/importes; copia corrupta se rechaza. Repetir desde almacenamiento remoto real antes del piloto.
6. Navegador escritorio 1440×1000 y móvil 390×844 con SQLite y PG: login, alta inicial, POS, ticket no fiscal, inventario, CxC, informes y devoluciones sin errores JavaScript. Reinicio conserva ventas/stock.
7. Imagen de producción arranca con TLS y permisos mínimos; reinicio conserva tablas. /health tiene smoke concurrente, sin interpretar esto como benchmark de carga comercial.
8. En destino: TLS, dominio, recuperación por correo autorizada, backup diario y alerta, restauración, soporte y aceptación de onboarding.

La ejecución CI exige cero tests omitidos. El último resultado y SHA aprobados están en los checks del PR; no usar resultados de un commit anterior para aprobar uno nuevo. La plantilla render.production.template.yaml está validada contra el esquema oficial, pero crea recursos de pago si se aplica y no se ha activado. Ver OPERACION-LANZAMIENTO.md para pasos y credenciales necesarias.
