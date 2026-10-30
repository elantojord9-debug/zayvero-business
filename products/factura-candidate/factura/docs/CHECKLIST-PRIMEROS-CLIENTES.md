# Gate de primeros pilotos administrativos

El gate vigente está en `../../INFORME-LANZAMIENTO.md`; el orden operativo en `../../OPERACION-LANZAMIENTO.md`. Este checklist sustituye las instrucciones de demo local.

- [ ] Confirmar checks CI verdes sobre el SHA exacto desplegado, sin omisiones.
- [ ] PostgreSQL durable, TLS, rol app sin DDL y dueño separado; smoke en destino.
- [ ] Backup privado remoto diario, alerta por antigüedad y restore independiente conciliado.
- [ ] Dos comercios sintéticos completan registro, catálogo, caja, venta, abono, devolución, cierre y exportación sin acceso cruzado.
- [ ] Recuperación de acceso con SMTP/TLS probada en buzón propio autorizado.
- [ ] Privacidad, condiciones, retención/eliminación y canal/horario de soporte aprobados por el propietario.
- [ ] Aviso SIN VALOR FISCAL visible en pantalla e impresión; e-CF real bloqueado.
- [ ] Precios preliminares RD$790/1,490/2,490 ocultos; cobro SaaS desactivado.
- [ ] Infraestructura/costos autorizados y aprobación del uso real de datos antes de invitar pilotos.

El resultado CI no cierra por sí mismo los puntos del proveedor ni autoriza venta fiscal. No existe un canal de soporte operativo verificado por esta implementación.
