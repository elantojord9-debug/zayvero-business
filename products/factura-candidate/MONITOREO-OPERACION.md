# Vigilancia operacional de FACTURA

## Comprobación implementada

Ejecutar en un proceso separado del servicio web:

```sh
python scripts/monitor_operacion.py
```

Variables: `FACTURA_ENV=production`, `FACTURA_PUBLIC_ORIGIN` (origen HTTPS),
`FACTURA_BACKUP_BUCKET` y, opcionalmente, `FACTURA_BACKUP_PREFIX` (por defecto
`zayvero-factura`). Credenciales S3 mediante la cadena del proveedor; el monitor
solo necesita GetObject y acceso a metadatos de los objetos en su prefijo.
No necesita credenciales PostgreSQL ni permiso para escribir o borrar.

Comprueba `/health`, rechaza redirecciones, verifica que `latest.json` apunta
a un manifiesto del mismo entorno y que la copia verificada tiene menos de
26 horas. Comprueba tamaño, hash declarado y cifrado del objeto mediante
metadatos. Rechaza fechas futuras con más de cinco minutos de tolerancia.
La salida JSON contiene resultados por comprobación; el código de salida es
2 ante fallo o configuración incompleta y 0 cuando ambas pasan. Los errores
no imprimen URLs firmadas, contraseñas ni detalles de excepciones del proveedor.

## Activación pendiente

Programar comprobaciones horarias en el proveedor elegido y configurar una
notificación efectiva al responsable ante salida distinta de cero. La salida
del script por sí sola NO entrega una alerta ni demuestra que el cron esté
funcionando. Probar fallo de salud, copia vencida y recuperación antes del piloto.
Mantener la vigilancia fuera del proceso web para detectar también su caída.

Esta comprobación de metadatos NO recalcula el contenido remoto, NO restaura
la copia ni acredita RPO/RTO. El job de backup hace lectura y hash antes de
publicar `latest`; el ensayo de restauración desde el proveedor sigue siendo
obligatorio. Ningún resultado habilita e-CF real ni cobro comercial por sí solo.

## Estado del 30 de octubre de 2026

- Pruebas unitarias del monitor: siete aprobadas en entorno local.
- Infraestructura, SMTP real, copia remota y restauración: sin nueva evidencia
  operacional; no declararlos cerrados.
- Render no tiene workspace seleccionado en esta sesión. La cuenta devuelve
  `Luis Miguel's workspace`; confirmar ese destino antes de operar allí.
- No se crearon servicios de pago ni se modificaron producción/main/staging.
- El precio final, soporte y aceptación del piloto requieren decisión del propietario.
