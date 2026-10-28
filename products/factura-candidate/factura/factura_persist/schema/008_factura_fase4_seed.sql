-- 008_factura_fase4_seed.sql — FASE 4: datos iniciales SaaS.
-- Crea la sucursal "Principal" para cada empresa existente (respeta el
-- límite del plan: el plan básico permite 1 sucursal). Idempotente: no
-- duplica si ya hay sucursales para la empresa.

INSERT INTO factura_sucursales (empresa_id, nombre, direccion, telefono,
                                activa, creada)
SELECT e.id, 'Principal', COALESCE(e.direccion, ''), COALESCE(e.telefono, ''),
       1, COALESCE(e.creada, '')
FROM factura_empresas e
WHERE NOT EXISTS (SELECT 1 FROM factura_sucursales s
                  WHERE s.empresa_id = e.id);
