"""BLOQUE Documentos/Integraciones — Motor del importador.

vista_previa(bundle, ctx, body) -> dict   # dry-run: valida sin escribir
aplicar(bundle, ctx, body)      -> dict   # aplica con auditoría

Política de aplicación (documentada, fail-closed):
- Todo o nada por importación: si alguna fila tiene error de
  validación o duplicado en el archivo, NO se escribe nada y se
  devuelve el reporte completo de errores.
- Filas cuya clave natural ya existe en la empresa se omiten como
  "ya_existentes" (idempotencia de contenido), no son error.
- Re-aplicar el mismo import_id -> replay idempotente (0 escrituras),
  mismo resultado sin duplicar.
- Cada aplicación exitosa deja UN evento 'importacion' en os_eventos
  con el resumen (actor, entidad, import_id, filas aplicadas/omitidas).
- Aislamiento: todo se escribe y se lee con el company_id del ctx.
"""
from __future__ import annotations

import base64
import hashlib
import os
import sys
import threading
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from os_import.contratos import CONTRATOS, contrato  # noqa: E402
from os_import.lector import leer, ErrorLectura, MAX_BYTES  # noqa: E402
from os_import.validador import valida_filas  # noqa: E402
from os_import.duplicados import (  # noqa: E402
    marcar_duplicados_archivo, existe_en_empresa, clave_natural)
from os_import.registro_productos import (  # noqa: E402
    obtener_registro, ProductoImportado)
from os_persist.auditoria import auditar  # noqa: E402
from oschema.models import Cliente, MovimientoInventario  # noqa: E402
from oschema.contract import TipoMovimiento  # noqa: E402

_IMPORTS: dict[tuple[str, str], set[str]] = {}
_LOCK = threading.RLock()


class ErrorImportacion(Exception):
    pass


def _ya_aplicado(company_id: str, entidad: str, import_id: str) -> bool:
    with _LOCK:
        return import_id in _IMPORTS.get((company_id, entidad), set())


def _marcar_aplicado(company_id: str, entidad: str, import_id: str):
    with _LOCK:
        _IMPORTS.setdefault((company_id, entidad), set()).add(import_id)


def _ctx_company(ctx) -> str:
    cid = getattr(ctx, "company_id", "") or ""
    if not cid:
        raise ErrorImportacion("ctx sin company_id")
    return cid


def _ctx_actor(ctx) -> str:
    return getattr(ctx, "user_id", "") or "sistema"


def _decodificar_body(body: dict) -> tuple[str, bytes, str]:
    entidad = str(body.get("entidad", "")).strip().lower()
    contrato(entidad)  # ValueError si no existe
    formato = str(body.get("formato", "csv")).strip().lower()
    b64 = body.get("contenido_base64", "")
    if not b64:
        raise ErrorImportacion(
            "falta contenido_base64 (archivo en base64, máx 1 MB)")
    try:
        raw = base64.b64decode(b64, validate=True)
    except Exception:
        raise ErrorImportacion("contenido_base64 no es base64 válido")
    if len(raw) > MAX_BYTES:
        raise ErrorImportacion(
            f"archivo de {len(raw)} bytes supera el máximo de {MAX_BYTES}")
    return entidad, raw, formato


def _preparar(bundle, company_id: str, entidad: str, raw: bytes,
              formato: str, import_id: str) -> dict:
    """Parsea + valida + detecta duplicados. Sin escrituras."""
    filas, meta = leer(raw, formato, entidad)
    validas, errores = valida_filas(entidad, filas, import_id=import_id)
    errores += marcar_duplicados_archivo(entidad, validas)
    # Las filas con duplicado en archivo se quitan de las válidas.
    lineas_dup = {e["fila"] for e in errores
                  if e["error"].startswith("duplicado en el archivo")}
    candidatas = [f for f in validas if f["_linea"] not in lineas_dup]
    if entidad == "movimientos":
        # Pre-chequeo: el producto debe existir ANTES de escribir nada
        # (todo o nada: sin esto una fila tardía rompería la atomicidad).
        from os_import.registro_productos import obtener_registro
        registro = obtener_registro(company_id)
        aun_validas = []
        for f in candidatas:
            if registro.por_producto_id(str(f["producto_id"]).strip()) is None:
                errores.append({
                    "fila": f["_linea"], "columna": "producto_id",
                    "valor": str(f["producto_id"]),
                    "error": ("producto desconocido: importe productos "
                              "primero")})
            else:
                aun_validas.append(f)
        candidatas = aun_validas
    nuevas, ya_existentes = [], []
    for f in candidatas:
        if existe_en_empresa(entidad, f, company_id, bundle):
            ya_existentes.append({
                "fila": f["_linea"],
                "clave_natural": "|".join(clave_natural(entidad, f))})
        else:
            nuevas.append(f)
    extras = sorted({c[6:] for f in filas for c in f
                     if c.startswith("extra:") and f[c] != ""})
    return {
        "entidad": entidad, "formato": meta["formato"],
        "total_filas": len(filas),
        "filas_validas": len(nuevas),
        "filas_ya_existentes": len(ya_existentes),
        "errores": sorted(errores, key=lambda e: e["fila"]),
        "ya_existentes": ya_existentes,
        "columnas_no_reconocidas": extras,
        "_nuevas": nuevas,  # interno: no sale en la respuesta pública
    }


def _reporte_publico(prep: dict, extra: dict | None = None) -> dict:
    r = {k: v for k, v in prep.items() if not k.startswith("_")}
    if extra:
        r.update(extra)
    return r


# ------------------------------------------------------------------ vistas

def vista_previa(bundle, ctx, body: dict) -> dict:
    """Dry-run: parsea y valida sin escribir nada en la empresa."""
    try:
        company_id = _ctx_company(ctx)
        entidad, raw, formato = _decodificar_body(body or {})
        import_id = str((body or {}).get("import_id", "") or "vista-previa")
        prep = _preparar(bundle, company_id, entidad, raw, formato,
                         import_id)
    except (ErrorImportacion, ErrorLectura, ValueError) as e:
        return {"ok": False, "error": str(e)}
    return {"ok": True, "modo": "vista_previa",
            "reporte": _reporte_publico(prep)}


def aplicar(bundle, ctx, body: dict) -> dict:
    """Aplica la importación: todo o nada + auditoría + idempotencia."""
    try:
        company_id = _ctx_company(ctx)
        actor = _ctx_actor(ctx)
        entidad, raw, formato = _decodificar_body(body or {})
        import_id = str((body or {}).get("import_id", "")).strip()
        if not import_id:
            return {"ok": False,
                    "error": ("falta import_id: la aplicación exige un ID "
                              "de importación para idempotencia")}
        if _ya_aplicado(company_id, entidad, import_id):
            return {"ok": True, "modo": "aplicar", "replay": True,
                    "import_id": import_id, "entidad": entidad,
                    "aplicadas": 0, "omitidas_ya_existentes": 0,
                    "nota": "importación ya aplicada antes (idempotente)"}
        prep = _preparar(bundle, company_id, entidad, raw, formato,
                         import_id)
    except (ErrorImportacion, ErrorLectura, ValueError) as e:
        return {"ok": False, "error": str(e)}

    if prep["errores"]:
        return {"ok": False, "modo": "aplicar",
                "error": ("la importación tiene errores: no se aplicó "
                          "ninguna fila (todo o nada)"),
                "reporte": _reporte_publico(prep)}

    aplicadas, omitidas = _escribir(bundle, company_id, entidad,
                                    prep["_nuevas"], import_id)
    _marcar_aplicado(company_id, entidad, import_id)
    detalle = {
        "entidad": entidad, "formato": prep["formato"],
        "import_id": import_id,
        "total_filas": prep["total_filas"],
        "aplicadas": aplicadas,
        "omitidas_ya_existentes": prep["filas_ya_existentes"] + omitidas,
        "columnas_no_reconocidas": prep["columnas_no_reconocidas"],
    }
    auditar(company_id, "importacion", actor, import_id, detalle)
    return {"ok": True, "modo": "aplicar", "replay": False,
            "reporte": _reporte_publico(prep, {
                "aplicadas": aplicadas,
                "omitidas_ya_existentes":
                    prep["filas_ya_existentes"] + omitidas,
                "import_id": import_id})}


# ------------------------------------------------------------------ escritura

def _escribir(bundle, company_id: str, entidad: str, filas: list[dict],
              import_id: str) -> tuple[int, int]:
    """Escribe las filas nuevas. Devuelve (aplicadas, omitidas)."""
    if entidad == "productos":
        return _escribir_productos(bundle, company_id, filas, import_id)
    if entidad == "clientes":
        return _escribir_clientes(bundle, company_id, filas, import_id)
    if entidad == "movimientos":
        return _escribir_movimientos(bundle, company_id, filas, import_id)
    raise ErrorImportacion(f"entidad sin escritor: {entidad}")


def _escribir_productos(bundle, company_id, filas, import_id):
    registro = obtener_registro(company_id)
    aplicadas = omitidas = 0
    for f in filas:
        p = ProductoImportado(
            company_id=company_id,
            producto_id=str(f["producto_id"]).strip(),
            sku=str(f["sku"]).strip(),
            nombre=str(f["nombre"]).strip(),
            descripcion=str(f.get("descripcion", "") or ""),
            unidad=str(f.get("unidad", "und") or "und"),
            precio=str(f["precio"]) if f.get("precio") is not None else "",
            costo=str(f["costo"]) if f.get("costo") is not None else "",
            moneda=str(f.get("moneda", "DOP") or "DOP"),
            import_id=import_id)
        if registro.registrar(p):
            aplicadas += 1
            stock = f.get("stock_inicial")
            if stock is not None and stock > 0:
                mid = f"imp-{import_id}-stock-{p.sku}"
                mov = MovimientoInventario(
                    movimiento_id=mid, company_id=company_id,
                    producto_id=p.producto_id,
                    almacen_id=str(f.get("almacen_id", "principal")
                                   or "principal"),
                    tipo=TipoMovimiento.ENTRADA, cantidad=str(stock),
                    costo_unitario=str(f["costo"])
                    if f.get("costo") is not None else "0",
                    moneda=p.moneda, motivo=f"stock inicial ({import_id})")
                # Idempotente: si el movimiento ya existe no se duplica.
                from os_inventory.engine import MovimientoDuplicado
                try:
                    bundle.inventario.registrar(mov)
                except MovimientoDuplicado:
                    pass
        else:
            omitidas += 1
    return aplicadas, omitidas


def _derivar_cliente_id(email: str) -> str:
    h = hashlib.sha1(email.strip().lower().encode("utf-8")).hexdigest()[:12]
    return f"cli-{h}"


def _escribir_clientes(bundle, company_id, filas, import_id):
    aplicadas = omitidas = 0
    for f in filas:
        email = str(f.get("email", "") or "").strip()
        cid = str(f.get("cliente_id", "") or "").strip() or (
            _derivar_cliente_id(email) if email
            else f"cli-{uuid.uuid4().hex[:12]}")
        c = Cliente(cliente_id=cid, company_id=company_id,
                    nombre=str(f["nombre"]).strip(), email=email,
                    telefono=str(f.get("telefono", "") or ""),
                    segmento=str(f.get("segmento", "") or ""))
        try:
            bundle.crm.registrar_cliente(c)
            aplicadas += 1
        except Exception:
            # Ya registrado (carrera o chequeo previo): idempotente.
            omitidas += 1
    return aplicadas, omitidas


def _iso_fecha(valor: str) -> str:
    v = valor.strip()
    if len(v) == 10:  # YYYY-MM-DD -> medianoche UTC
        return v + "T00:00:00Z"
    return v


def _escribir_movimientos(bundle, company_id, filas, import_id):
    from os_inventory.engine import (MovimientoDuplicado, ErrorInventario)
    registro = obtener_registro(company_id)
    aplicadas = omitidas = 0
    for f in filas:
        prod = registro.por_producto_id(str(f["producto_id"]).strip())
        assert prod is not None, "pre-chequeo omitido"  # defensa; no ocurre
        cantidad = f["cantidad"]
        tipo = f["tipo"]
        # El engine exige signo según tipo (entrada > 0, salida < 0,
        # ajuste con signo); el CSV acepta la cantidad en positivo para
        # entrada/salida y el motor aplica el signo.
        if tipo == TipoMovimiento.ENTRADA:
            cantidad = abs(cantidad)
        elif tipo == TipoMovimiento.SALIDA:
            cantidad = -abs(cantidad)
        mov = MovimientoInventario(
            movimiento_id=str(f["movimiento_id"]).strip(),
            company_id=company_id, producto_id=prod.producto_id,
            almacen_id=str(f["almacen_id"]).strip(), tipo=tipo,
            cantidad=str(cantidad),
            # El engine valida fecha como ISO datetime; el contrato
            # acepta YYYY-MM-DD y aquí se completa a medianoche UTC.
            fecha=(_iso_fecha(str(f.get("fecha", "") or ""))),
            motivo=str(f.get("motivo", "") or ""),
            referencia_id=import_id)
        try:
            if bundle.inventario.registrar_idempotente(mov):
                aplicadas += 1
            else:
                omitidas += 1
        except ErrorInventario as e:
            raise ErrorImportacion(f"línea {f['_linea']}: {e}")
    return aplicadas, omitidas
