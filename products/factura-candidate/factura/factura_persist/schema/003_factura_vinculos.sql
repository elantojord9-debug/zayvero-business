-- 003_factura_vinculos.sql — vínculos e-CF y sucursal (nullable).
-- ecf_id: último comprobante e-CF generado desde la venta (una venta puede
--   tener varios e-CF: E32 + notas; ecf_id apunta al más reciente).
-- sucursal_id: nullable; el plan Pro habilita multisucursal (feature gate).
--   La UI del MVP no gestiona sucursales (queda modelado para fase 2).

ALTER TABLE factura_ventas ADD COLUMN ecf_id INTEGER;
ALTER TABLE factura_ventas ADD COLUMN sucursal_id INTEGER;
