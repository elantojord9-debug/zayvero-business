# Prueba Rutiverso desde una venta de ZAYVERO

En una empresa de pruebas, cobrar una venta en POS, pulsar **Ver factura** y usar **Prueba de facturación electrónica**. Elegir E31 o E32, **Enviar prueba**, luego **Consultar resultado**. El resultado, XML y enlace QR quedan asociados a la venta y sobreviven a una recarga. La creación de ventas, pagos y stock conserva su transacción existente.

## Activación del servidor de pruebas

Aplicar las migraciones normales (`db.migrate()`) y configurar mediante secretos del servidor:

- `FACTURA_RUTIVERSO_SANDBOX_EMPRESA`: ID de una empresa sintética de ZAYVERO.
- `FACTURA_RUTIVERSO_SANDBOX_TOKEN`: credencial del conector TEST ECF 2.
- `FACTURA_RUTIVERSO_E31_INICIO`: siguiente número libre reservado. Después de estas pruebas, 19002.
- `FACTURA_RUTIVERSO_E32_INICIO`: siguiente número libre reservado. Después de estas pruebas, 19102.

Validar la disponibilidad con el proveedor si otro integrador comparte los rangos. Límite 19500. La base es fija, `https://sistema.rutiversotech.com/api/ecf`, confirmada por el dueño como Sandbox DGII. Caducidad 2028-12-31. E31 usa exclusivamente el receptor de prueba indicado por el proveedor, GRUPO VILLAR SRL. La configuración restringe las operaciones a una empresa y al rol administrador. El token se conserva en el servidor.

Tras restaurar una base, conservar las filas de `factura_sandbox` y verificar las secuencias antes de reactivar. Una instalación nueva necesita inicios actualizados para evitar reutilizar números consumidos en otra base. Este adaptador sirve al Sandbox compartido, con configuración de una empresa de pruebas por instalación. La configuración de clientes/emisores de producción y el despliegue de este cambio siguen pendientes.

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
