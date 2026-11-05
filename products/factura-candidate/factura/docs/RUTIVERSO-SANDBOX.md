# Prueba Rutiverso desde una venta de ZAYVERO

En una empresa de pruebas, cobrar una venta en POS, pulsar **Ver factura** y usar **Prueba de facturación electrónica**. Elegir E31 o E32, **Enviar prueba**, luego **Consultar resultado**. El resultado, XML y enlace QR quedan asociados a la venta y sobreviven a una recarga. La creación de ventas, pagos y stock conserva su transacción existente.

## Activación del servidor de pruebas

Aplicar las migraciones normales (`db.migrate()`) y configurar mediante secretos del servidor:

- `FACTURA_RUTIVERSO_SANDBOX_EMPRESA`: ID de una empresa sintética de ZAYVERO.
- `FACTURA_RUTIVERSO_SANDBOX_TOKEN`: credencial del conector TEST ECF 2.
- `FACTURA_RUTIVERSO_E31_INICIO`: siguiente número libre reservado. En la base desplegada ya se usó 19002; el siguiente libre local es 19003.
- `FACTURA_RUTIVERSO_E32_INICIO`: siguiente número libre reservado. En la base desplegada ya se usó 19102; el siguiente libre local es 19103.

Validar la disponibilidad con el proveedor si otro integrador comparte los rangos. Límite 19500. La base es fija, `https://sistema.rutiversotech.com/api/ecf`, confirmada por el dueño como Sandbox DGII. Caducidad 2028-12-31. E31 usa exclusivamente el receptor de prueba indicado por el proveedor, GRUPO VILLAR SRL. La configuración restringe las operaciones a una empresa y al rol administrador. El token se conserva en el servidor.

Tras restaurar una base, conservar las filas de `factura_sandbox` y verificar las secuencias antes de reactivar. Una instalación nueva necesita inicios actualizados para evitar reutilizar números consumidos en otra base. Este adaptador sirve al Sandbox compartido, con configuración de una empresa de pruebas por instalación. El cambio está desplegado en staging. La configuración de emisores de producción sigue pendiente del contrato técnico y las credenciales del proveedor.

## Recuperación y límites

Se reserva y guarda el e-NCF y el cuerpo de solicitud antes del envío. Cada venta admite un solo tipo y un solo envío automático. Repetir **Enviar prueba** devuelve el registro local. **Consultar resultado** recupera el ID remoto por e-NCF si hubo timeout y consulta el estado. La búsqueda de recuperación usa las últimas 100 facturas, según el contrato disponible. Si el comprobante ya quedó fuera de esa ventana, solicitar al proveedor su resultado. No se asigna otro número automáticamente.

El endpoint de estado del proveedor puede transmitir documentos en cola. Una respuesta pendiente nunca se presenta como aceptada. Un rechazo incluido en `message_dgii` prevalece sobre `sent`. Los XML disponibles en esta API son sin firma, según su documentación. El enlace QR debe pertenecer al ambiente TesteCF de DGII.

La primera versión admite ventas sin descuentos con ITBIS 18% o exento. Esas restricciones se validan antes de reservar. El QR es un enlace de consulta. La consulta y descarga se accionan desde el detalle de la venta.

## Validación del 4 de noviembre de 2026

Pruebas ejecutadas con base local sintética mediante la aplicación WSGI y `/api/pos/cobrar`, seguidas de `/api/pos/sandbox` autenticado y con CSRF.

| Tipo | e-NCF | ID del proveedor | Total | Resultado |
|---|---|---|---|---|
| E31 | E310000019001 | 1906 | RD$118 | aceptado, XML y QR recuperados |
| E32 | E320000019101 | 1907 | RD$118 | aceptado tras recuperar un corte de conexión, XML y QR recuperados |

Repetir el envío conservó los mismos e-NCF. La base mantuvo exactamente dos ventas. Ningún secreto está incluido en este informe.

Las pruebas específicas cubren permisos, CSRF, aislamiento de empresa, numeración, concurrencia, reinicio después de timeout, rango agotado, QR fuera de pruebas y rechazo DGII. También se verificó la sintaxis JavaScript del panel.

La suite ampliada encontró una dependencia pytest ausente y una prueba antigua del Contador IA dependiente del mes. Esta última falla también en el commit base 046c5ee. Las pruebas de PostgreSQL requieren su entorno propio y no se ejecutaron en esta validación SQLite.


## Configuración separada por empresa (solo Sandbox)

Se conserva la configuración anterior para no interrumpir la empresa de prueba 2.
Para instalaciones con varias empresas sintéticas, definir `FACTURA_RUTIVERSO_SANDBOX_CONFIG`
como JSON, con IDs de empresa como claves. Ejemplo sin secretos:

```json
{"10":{"token_env":"FACTURA_RUTIVERSO_TOKEN_EMPRESA_10","inicio":{"31":19003,"32":19103},"vencimiento":"2028-12-31"}}
```

El secreto reside únicamente en la variable de entorno referenciada. Una empresa ausente del
mapa queda deshabilitada, sin recurrir al token compartido anterior. Las reservas nuevas guardan
una huella de la credencial, sin guardar el secreto en la venta. Cambiarla bloquea la consulta
del documento hasta conciliar con el proveedor. Los registros anteriores al mapa requieren
conciliación antes de cambiar de configuración. El rango sigue siendo el Sandbox reservado
hasta 19500 y la numeración es global en esta instalación; no representa aún un asignador por
RNC de producción. Dos credenciales distintas se verificaron con pruebas automatizadas; solo
se cuenta con una credencial real de Sandbox para la prueba externa.

## Validación del despliegue, 5 de noviembre de 2026 UTC

La aplicación publicada se probó por sus endpoints HTTP autenticados, incluidos CSRF e
idempotencia, usando una empresa y un servicio sintéticos. No se automatizaron clics del navegador.

| Tipo | e-NCF | Venta local | ID proveedor | Total | Resultado |
|---|---|---:|---:|---:|---|
| E31 | E310000019002 | 3 | 1925 | RD$118 | aceptado, XML y QR |
| E32 | E320000019102 | 4 | 1926 | RD$118 | aceptado, XML y QR |
| E34 | E340000019003 | referencia a venta 3 | 1927 | RD$118 | aceptado por el proveedor en Sandbox |

La E34 se emitió mediante una prueba directa del conector contra la E31 sintética anterior;
no está integrada al flujo de devoluciones del POS ni altera su caja o stock. No confundir esta
validación del proveedor con una función E34 disponible en la interfaz. La E31 original conserva
su aceptación: la modificación se documenta con un comprobante aparte.

Las pruebas automatizadas cubren rechazo DGII, timeouts, reinicio, concurrencia, consulta sin
resultados, permisos, cambio de credencial, selección de tipo y aislamiento de configuración.
Un resultado incierto conserva el mismo e-NCF; no se vuelve a enviar automáticamente.

## Respaldo y restauración comprobados

Se creó el snapshot manual `snap-ancient-cake-b4eiklbg` de la rama operativa de Neon y se
restauró a una rama separada `br-royal-darkness-b4t9ux4g`. La copia recuperó 3 empresas,
4 ventas, 2 comprobantes Sandbox y 14 registros de migración; coinciden con la base origen.
Ambos comprobantes recuperaron sus XML, QR, ID remoto y aceptación. No se sustituyó la base
operativa. La rama de ensayo vence el 6 de noviembre de 2026 a las 05:06:20 UTC.

El proyecto rechazó la programación de snapshots con `backup schedule creation is not enabled
for this project`. No hay respaldos automáticos activados mediante esta operación. Tampoco se
ha verificado un respaldo externo cifrado independiente de Neon: los scripts existentes necesitan
un destino S3 y credenciales de respaldo que no están configurados. Antes de producción se debe
resolver el respaldo recurrente y ensayar su restauración.
