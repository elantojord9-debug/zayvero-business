"""BLOQUE EMPLEADOS/OPERACIONES (2026-10-13) — ZAYVERO Business OS.

Módulos NUEVOS de gestión operativa de personal y sucursales:

- **Employee Analytics** (`analytics.py`): indicadores operativos por
  empleado calculados SOLO desde registros verificables del sistema
  (eventos de auditoría `os_eventos` para ventas/recepciones/cobros,
  tablas propias para tareas y promesas). Fórmulas explícitas.
- **Task Monitor** (`tareas.py` + `almacen.py`): tareas con responsable,
  vencimiento, estado, prioridad; alertas de vencidas; historial.
- **SLA Monitor** (`sla.py` + `almacen.py`): tiempos de respuesta y
  resolución por tipo de operación; cumplimiento vs objetivo
  configurable; operaciones atrasadas con alerta.
- **Control de sucursales** (`sucursales.py` + `almacen.py`):
  supervisión por ubicación; visibilidad por usuario; vista consolidada
  del propietario; comparación entre sucursales.

REGLA ÉTICA DURA (documentada también en cada reporte y en
`docs/bloque-empleados.md`): estas métricas son instrumentos de
GESTIÓN OPERATIVA. NUNCA constituyen prueba de mala conducta ni
autorizan acusaciones automáticas contra trabajadores: requieren
revisión humana con contexto. Sin datos suficientes, el reporte dice
"datos insuficientes" en vez de inventar un juicio.

Integración:
- `company_id` en todo (aislamiento multiempresa; siempre del ctx).
- Persistencia en `os_persist` (migración `011_os_empleados.sql`).
- Mutaciones auditadas en `os_eventos` (tipos 'tarea' y 'empleado',
  misma transacción que la escritura).
- Rutas: `rutas()` en `os_empleados/rutas.py` (prefijo `empleados/`);
  los handlers reciben `(bundle, ctx, payload)` y devuelven dicts
  JSON-serializables. NO toca `webapp/os_routes.py` ni `webapp/static/*`
  (el panel se integra después).

Datos 100 % sintéticos en local. Sin commit/push/deploy.
"""
from .modelo import (  # noqa: F401
    ErrorEmpleados,
    PermissionDeniedEmpleados,
    ESTADOS_TAREA,
    PRIORIDADES,
    TIPOS_SLA_PREDEFINIDOS,
    validar_empleado,
    validar_tarea,
    validar_transicion_estado,
    validar_sla_config,
    validar_promesa,
    validar_sucursal,
)

__all__ = [
    "ErrorEmpleados",
    "PermissionDeniedEmpleados",
    "ESTADOS_TAREA",
    "PRIORIDADES",
    "TIPOS_SLA_PREDEFINIDOS",
    "validar_empleado",
    "validar_tarea",
    "validar_transicion_estado",
    "validar_sla_config",
    "validar_promesa",
    "validar_sucursal",
]
