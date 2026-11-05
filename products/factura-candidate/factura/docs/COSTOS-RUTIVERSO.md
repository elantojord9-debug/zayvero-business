# Costos de Rutiverso para ZAYVERO

Cálculos a partir de la cotización de WhatsApp compartida por Miguel; no son un contrato
actualizado ni precios de venta de ZAYVERO. Se usa 18% de ITBIS para el cálculo del desembolso.
El tratamiento contable de ese impuesto debe determinarse con el contador.

| Plan | Cuota sin ITBIS | Con 18% ITBIS | e-CF/mes compartidos | Costo unitario sin ITBIS al usar todo |
|---|---:|---:|---:|---:|
| Básico | RD$1,500.00 | RD$1,770.00 | 600 | RD$2.5000 |
| Emprendedor | RD$2,800.00 | RD$3,304.00 | 1,500 | RD$1.8667 |
| Profesional | RD$4,800.00 | RD$5,664.00 | 3,000 | RD$1.6000 |
| Empresarial | RD$8,500.00 | RD$10,030.00 | 6,000 | RD$1.4167 |
| Corporativo | RD$15,000.00 | RD$17,700.00 | 12,000 | RD$1.2500 |

Los límites son del plan completo, sumando todos los clientes de ZAYVERO. No se multiplican por
cada empresa conectada. El costo unitario de la tabla solo se alcanza usando toda la capacidad;
si se usa menos, la cuota fija no disminuye.

## Ejemplos de volumen

| Escenario, 30 días | Documentos/mes | Plan que cubre el volumen de ventas | Cuota sin ITBIS |
|---|---:|---|---:|
| Un colmado con 100 ventas facturadas diarias | 3,000 | Profesional | RD$4,800 |
| Una ferretería con 150 ventas facturadas diarias | 4,500 | Empresarial | RD$8,500 |
| Un negocio con 200 ventas facturadas diarias | 6,000 | Empresarial | RD$8,500 |
| Dos negocios con 100 ventas facturadas diarias cada uno | 6,000 | Empresarial compartido | RD$8,500 |
| Diez negocios con 60 facturas mensuales cada uno | 600 | Básico compartido | RD$1,500 |

Los ejemplos contemplan solo las facturas de venta: se debe confirmar cómo contabiliza el
proveedor notas de crédito/débito, rechazos y otros documentos. Con exactamente 3,000 facturas
se agota Profesional y no queda margen para documentos adicionales. El Básico dura seis días
con 100 facturas diarias, si ningún otro cliente consume capacidad.

En la aplicación actual cobrar guarda la venta; el botón Sandbox envía la prueba. No está
activada la emisión fiscal automática. En un emisor electrónico deben contemplarse las facturas
E32 de consumidor final, además de E31 para crédito fiscal. No se debe dimensionar el plan solo
por los compradores que solicitan factura con RNC. Fuentes de tipos y estructura:
https://dgii.gov.do/cicloContribuyente/facturacion/comprobantesFiscalesElectronicosE-CF/Paginas/TipoyEstructurae-CF.aspx

## Pagos iniciales de la cotización

| Concepto | Sin ITBIS | Con 18% ITBIS | Frecuencia indicada |
|---|---:|---:|---|
| Acceso a API | RD$6,000 | RD$7,080 | Una vez |
| Certificación gestionada | RD$3,500 | RD$4,130 | Servicio de certificación |
| Firma digital | RD$2,700 | RD$3,186 | Anual |

Acceso más primer mes Básico: RD$8,850 con ITBIS. Si se contratan también una certificación
y una firma: RD$16,166. Confirmar por escrito si los últimos dos conceptos se cobran por cada
RNC, qué incluyen y si la empresa ya cuenta con ellos.

El excedente Básico fue cotizado en RD$2.89 + ITBIS por emisión: RD$3.4102 antes de redondear
la factura. El proveedor también dijo 15% sobre RD$2.50, que da RD$2.875; solicitar su regla
contractual de cálculo y redondeo. No aplicar RD$2.89 a los demás planes sin confirmación.

## Definición comercial pendiente

No publicar una tarifa ilimitada. Cada oferta de ZAYVERO necesita una cantidad mensual incluida,
precio de excedentes y reglas de ampliación. La cuota del proveedor es solo un costo: faltan
hosting, respaldo recurrente, soporte, cobros, comisiones y el margen de ZAYVERO. No se han
activado cobros ni modificado tarifas a clientes con estos cálculos.

## Confirmaciones necesarias del proveedor para producción

- Base URL, credencial y separación de emisores/RNC en una cuenta B2B.
- Alta de cada emisor, certificado, vencimiento y secuencias autorizadas.
- Qué consume el plan: E31, E32, E34, rechazos, duplicados, consultas y pruebas.
- Tarifario definitivo de excedentes por plan, ampliaciones y soporte ante interrupciones.
- Entrega/conservación del XML firmado; la documentación del endpoint XML describe XML sin firma.

El token compartido de pruebas y sus secuencias no habilitan la producción de los clientes.
