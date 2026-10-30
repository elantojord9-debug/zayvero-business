"""BLOQUE Documentos/Integraciones — Contratos de datos del importador.

Cada entidad declara: columnas esperadas (tipo, obligatoria, valor por
defecto), clave natural (para detección de duplicados) y alias de
cabeceras (variantes comunes en español/inglés, insensibles a
mayúsculas/espacios).

Tipos soportados: texto, decimal, entero, moneda (ISO 4217), fecha
(YYYY-MM-DD o ISO 8601), enumeracion.
"""
from __future__ import annotations


CONTRATOS = {
    "productos": {
        "descripcion": (
            "Catálogo de productos. Se valida contra catalogo.modelo.Producto "
            "y se registra en el registro de productos de la empresa. "
            "Si la fila trae stock_inicial > 0 (y almacen_id), se genera una "
            "entrada de inventario inicial."),
        "clave_natural": ["sku"],
        "alias": {
            "sku": "sku", "codigo": "sku", "código": "sku", "code": "sku",
            "nombre": "nombre", "name": "nombre", "producto": "nombre",
            "producto_id": "producto_id", "id": "producto_id",
            "descripcion": "descripcion", "descripción": "descripcion",
            "description": "descripcion",
            "unidad": "unidad", "unit": "unidad",
            "precio": "precio", "price": "precio",
            "costo": "costo", "cost": "costo", "coste": "costo",
            "moneda": "moneda", "currency": "moneda",
            "almacen_id": "almacen_id", "almacen": "almacen_id",
            "warehouse": "almacen_id",
            "stock_inicial": "stock_inicial", "stock inicial": "stock_inicial",
            "stock": "stock_inicial", "existencia_inicial": "stock_inicial",
        },
        "columnas": {
            "sku": {"tipo": "texto", "obligatorio": True,
                    "descripcion": "SKU único por empresa"},
            "nombre": {"tipo": "texto", "obligatorio": True,
                       "descripcion": "Nombre comercial del producto"},
            "producto_id": {"tipo": "texto", "obligatorio": False,
                            "default": "<sku>",
                            "descripcion": "ID interno; si se omite se usa el sku"},
            "descripcion": {"tipo": "texto", "obligatorio": False,
                            "default": ""},
            "unidad": {"tipo": "texto", "obligatorio": False, "default": "und"},
            "precio": {"tipo": "decimal", "obligatorio": False, "default": None,
                       "descripcion": "Precio de venta (requiere moneda)"},
            "costo": {"tipo": "decimal", "obligatorio": False, "default": None,
                      "descripcion": "Costo unitario (requiere moneda)"},
            "moneda": {"tipo": "moneda", "obligatorio": False, "default": "DOP",
                       "descripcion": "ISO 4217; se usa si hay precio o costo"},
            "almacen_id": {"tipo": "texto", "obligatorio": False,
                           "default": "principal",
                           "descripcion": "Almacén del stock inicial"},
            "stock_inicial": {"tipo": "decimal", "obligatorio": False,
                              "default": "0",
                              "descripcion": ">= 0; genera una entrada inicial"},
        },
    },
    "clientes": {
        "descripcion": (
            "Clientes del CRM. La clave natural es el email cuando existe; "
            "si no hay email se exige cliente_id explícito."),
        "clave_natural": ["email"],
        "alias": {
            "cliente_id": "cliente_id", "id": "cliente_id",
            "nombre": "nombre", "name": "nombre", "cliente": "nombre",
            "email": "email", "correo": "email", "e-mail": "email",
            "telefono": "telefono", "teléfono": "telefono", "phone": "telefono",
            "segmento": "segmento", "segment": "segmento",
        },
        "columnas": {
            "cliente_id": {"tipo": "texto", "obligatorio": False,
                           "default": "<derivado del email>",
                           "descripcion": "Obligatorio si la fila no trae email"},
            "nombre": {"tipo": "texto", "obligatorio": True,
                       "descripcion": "Nombre del cliente"},
            "email": {"tipo": "texto", "obligatorio": False, "default": "",
                      "descripcion": "Clave natural; formato usuario@dominio"},
            "telefono": {"tipo": "texto", "obligatorio": False, "default": ""},
            "segmento": {"tipo": "texto", "obligatorio": False, "default": ""},
        },
    },
    "movimientos": {
        "descripcion": (
            "Movimientos de inventario (entradas/salidas/ajustes). El "
            "producto debe existir (importado antes o ya registrado). "
            "Las salidas se expresan en positivo; el motor aplica el signo."),
        "clave_natural": ["movimiento_id"],
        "alias": {
            "movimiento_id": "movimiento_id", "id": "movimiento_id",
            "producto_id": "producto_id", "producto": "producto_id",
            "sku": "producto_id",
            "almacen_id": "almacen_id", "almacen": "almacen_id",
            "warehouse": "almacen_id",
            "tipo": "tipo", "type": "tipo", "operacion": "tipo",
            "cantidad": "cantidad", "quantity": "cantidad", "qty": "cantidad",
            "fecha": "fecha", "date": "fecha",
            "motivo": "motivo", "reason": "motivo", "referencia": "motivo",
        },
        "columnas": {
            "movimiento_id": {"tipo": "texto", "obligatorio": False,
                              "default": "<imp-{import_id}-{n}>",
                              "descripcion": "ID único; si se repite, idempotente"},
            "producto_id": {"tipo": "texto", "obligatorio": True,
                            "descripcion": "producto_id o sku importado antes"},
            "almacen_id": {"tipo": "texto", "obligatorio": True,
                           "descripcion": "Almacén del movimiento"},
            "tipo": {"tipo": "enumeracion", "obligatorio": True,
                     "valores": ["entrada", "salida", "ajuste"]},
            "cantidad": {"tipo": "decimal", "obligatorio": True,
                         "descripcion": "En positivo para entrada/salida; "
                                        "el ajuste admite signo. No puede ser 0."},
            "fecha": {"tipo": "fecha", "obligatorio": False,
                       "default": "<ahora ISO>",
                       "descripcion": "YYYY-MM-DD o ISO 8601"},
            "motivo": {"tipo": "texto", "obligatorio": False, "default": ""},
        },
    },
}


def contrato(entidad: str) -> dict:
    """Devuelve el contrato de una entidad (ValueError si no existe)."""
    if entidad not in CONTRATOS:
        raise ValueError(
            f"entidad desconocida: {entidad!r}; use " +
            ", ".join(sorted(CONTRATOS)))
    return CONTRATOS[entidad]


def normalizar_cabecera(nombre: str, entidad: str) -> str:
    """Mapea una cabecera de archivo a columna canónica ("" si no mapea)."""
    clave = " ".join(str(nombre).strip().lower().split())
    alias = CONTRATOS[entidad]["alias"]
    if clave in alias:
        return alias[clave]
    if clave in CONTRATOS[entidad]["columnas"]:
        return clave
    return ""
