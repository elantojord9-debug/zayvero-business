"""Servicio e-CF de TALENTO.

=====================================================================
SIMULACIÓN — SIN VALIDEZ FISCAL ANTE DGII
=====================================================================
Reutiliza por IMPORTACIÓN (cero código copiado):
- os_ecf.modelos: Emisor, Receptor, Linea, Comprobante, calcular_totales,
  formatear_e_ncf, parsear_e_ncf, validar_rnc, TIPOS_ECF, ESTADOS, ErrorECF
- os_ecf.flujo: FlujoECF (máquina de estados + aprobación humana + emitir),
  TRANSICIONES_PERMITIDAS, Evento, _hash_evento
- os_ecf.proveedor: MockProvider (simulador offline; bloquea modo disabled)
- os_ecf.xml_ecf: generar_xml
- os_ecf.validacion: validar_comprobante, puede_editarse

La persistencia NO usa os_ecf.almacen_pg (requiere PostgreSQL; este
despliegue usa SQLite por defecto): vive en factura_ecf y
factura_ecf_eventos, con la cadena de hash de eventos verificable.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import config
from factura_persist import db
from factura_core import ventas

from os_ecf.modelos import (
    Emisor, Receptor, Linea, Comprobante, calcular_totales,
    formatear_e_ncf, parsear_e_ncf, validar_rnc, TIPOS_ECF, ESTADOS, ErrorECF,
)
from os_ecf.flujo import (
    FlujoECF, TRANSICIONES_PERMITIDAS, Evento, _hash_evento,
)
from os_ecf.proveedor import MockProvider
from os_ecf.xml_ecf import generar_xml
from os_ecf.validacion import validar_comprobante, puede_editarse

TIPOS_PERMITIDOS = ("31", "32", "33", "34")
AVISO = config.AVISO_SIMULACION_ECF


def _ahora() -> str:
    return datetime.now(timezone.utc).isoformat()


def _empresa(conn, empresa_id: int) -> dict:
    cur = conn.cursor()
    fila = db.fetchone(cur, "SELECT * FROM factura_empresas WHERE id=%s",
                       (empresa_id,))
    if not fila:
        raise ValueError("empresa no encontrada")
    return dict(fila)


def _cliente(conn, empresa_id: int, cliente_id: int | None) -> dict | None:
    if not cliente_id:
        return None
    cur = conn.cursor()
    fila = db.fetchone(cur, "SELECT * FROM factura_clientes"
                            " WHERE id=%s AND empresa_id=%s",
                       (cliente_id, empresa_id))
    return dict(fila) if fila else None


# --- secuencias e-NCF (issue #11: por emisor fiscal, no por empresa) --------
#
# La unicidad canónica es UNIQUE(emisor_rnc_norm, e_ncf): dos cuentas que
# compartan el mismo RNC emisor NO pueden generar el mismo e-NCF, pero dos
# RNC distintos sí pueden usar el mismo número sin conflicto.
# Las secuencias son atómicas por (emisor_rnc_norm, tipo, espacio), donde
# espacio='mock' (simulación) vs 'real' (futura vía DGII/proveedor):
# namespaces separados — el mock JAMÁS consume rango real.

def _rnc_norm(rnc: str) -> str:
    """RNC normalizado canónico (solo dígitos) vía motor os_ecf."""
    try:
        return validar_rnc(rnc)
    except ErrorECF as exc:
        raise ValueError(f"RNC del emisor inválido: {exc}")


def _espacio(modo: str) -> str:
    return "mock" if modo == "mock" else "real"


def _reservar_e_ncf(conn, emisor_rnc_norm: str, tipo: str,
                    espacio: str) -> str:
    """Reserva atómica del siguiente e-NCF por (emisor fiscal, tipo,
    espacio). Debe llamarse dentro de una transacción con bloqueo."""
    tipo = (tipo or "").strip()
    if tipo not in TIPOS_PERMITIDOS:
        raise ValueError(f"tipo e-CF no habilitado: {tipo!r}")
    if espacio not in ("mock", "real"):
        raise ValueError(f"espacio inválido: {espacio!r}")
    cur = conn.cursor()
    if db.backend() == "pg":
        db.execute(cur, "INSERT INTO factura_ecf_secuencias (emisor_rnc_norm,"
                        " tipo, espacio, ultimo_numero) VALUES (%s,%s,%s,0)"
                        " ON CONFLICT (emisor_rnc_norm, tipo, espacio)"
                        " DO NOTHING",
                   (emisor_rnc_norm, tipo, espacio))
        fila = db.fetchone(cur, "SELECT ultimo_numero FROM"
                                " factura_ecf_secuencias"
                                " WHERE emisor_rnc_norm=%s AND tipo=%s"
                                " AND espacio=%s FOR UPDATE",
                           (emisor_rnc_norm, tipo, espacio))
    else:
        fila = db.fetchone(cur, "SELECT ultimo_numero FROM"
                                " factura_ecf_secuencias"
                                " WHERE emisor_rnc_norm=%s AND tipo=%s"
                                " AND espacio=%s",
                           (emisor_rnc_norm, tipo, espacio))
        if not fila:
            db.execute(cur, "INSERT INTO factura_ecf_secuencias"
                            " (emisor_rnc_norm, tipo, espacio, ultimo_numero)"
                            " VALUES (%s,%s,%s,0)",
                       (emisor_rnc_norm, tipo, espacio))
            fila = (0,)
    nuevo = int(fila[0]) + 1
    db.execute(cur, "UPDATE factura_ecf_secuencias SET ultimo_numero=%s,"
                    " actualizado_en=%s"
                    " WHERE emisor_rnc_norm=%s AND tipo=%s AND espacio=%s",
               (nuevo, _ahora(), emisor_rnc_norm, tipo, espacio))
    return formatear_e_ncf(tipo, nuevo)


# --- construcción / reconstrucción ------------------------------------------

def _construir_desde_venta(conn, empresa_id: int, venta_id: int,
                            tipo: str, encf_original: str = "") -> Comprobante:
    emp = _empresa(conn, empresa_id)
    v = ventas.obtener(empresa_id, venta_id)
    if not v:
        raise ValueError("venta no encontrada")
    if v["estado"] == "anulada":
        raise ValueError("no se puede emitir e-CF de una venta anulada")
    cli = _cliente(conn, empresa_id, v["cliente_id"])
    if not cli or not (cli.get("rnc") or "").strip():
        raise ValueError("el cliente necesita un RNC válido para emitir e-CF")
    try:
        emisor = Emisor(rnc=emp["rnc"], razon_social=emp["nombre"],
                        nombre_comercial=emp["nombre"])
        receptor = Receptor(rnc=cli["rnc"], razon_social=cli["nombre"])
    except ErrorECF as exc:
        raise ValueError(str(exc))
    lineas = []
    for it in v["items"]:
        exento = bool(it.get("itbis_exento"))
        try:
            lineas.append(Linea(
                descripcion=it["nombre"],
                cantidad=Decimal(str(it["cantidad"])),
                precio_unitario=Decimal(str(it["precio"])),
                tasa_itbis=None if exento else Decimal(str(emp["itbis_tasa"])),
                descuento=Decimal(str(it.get("descuento", 0)))))
        except ErrorECF as exc:
            raise ValueError(str(exc))
    if tipo in ("33", "34") and not (encf_original or "").strip():
        raise ValueError("las notas 33/34 exigen el e-NCF original")
    modo = emp.get("e_cf_mode") or "disabled"
    if modo not in config.ECF_MODES:
        modo = "disabled"
    return Comprobante(
        id=f"ecf-{uuid.uuid4().hex[:12]}",
        company_id=str(empresa_id),
        tipo=tipo,
        emisor=emisor,
        receptor=receptor,
        lineas=lineas,
        estado="borrador",
        modo=modo,
        encf_original=(encf_original or "").strip().upper(),
        descuento_global=Decimal(str(v.get("descuento", 0))),
        moneda="DOP",
        aviso=AVISO,
    )


def _reconstruir(d: dict) -> Comprobante:
    em, rc = d["emisor"], d["receptor"]
    lineas = [Linea(
        descripcion=l["descripcion"],
        cantidad=Decimal(str(l["cantidad"])),
        precio_unitario=Decimal(str(l["precio_unitario"])),
        tasa_itbis=(Decimal(str(l["tasa_itbis"]))
                    if l.get("tasa_itbis") is not None else None),
        descuento=Decimal(str(l.get("descuento", 0))))
        for l in d["lineas"]]
    return Comprobante(
        id=d["id"], company_id=d["company_id"], tipo=d["tipo"],
        emisor=Emisor(rnc=em["rnc"], razon_social=em["razon_social"],
                      nombre_comercial=em.get("nombre_comercial", "")),
        receptor=Receptor(rnc=rc["rnc"], razon_social=rc["razon_social"]),
        lineas=lineas, estado=d["estado"], modo=d["modo"],
        e_ncf=d.get("e_ncf", ""), encf_original=d.get("encf_original", ""),
        descuento_global=Decimal(str(d["totales"]["descuento_global"])),
        moneda=d.get("moneda", "DOP"), creado_en=d.get("creado_en", ""),
        aviso=d.get("aviso", AVISO))


# --- persistencia de eventos con cadena de hash ------------------------------

def _guardar_eventos(conn, comp_id: int, empresa_id: int,
                     flujo: FlujoECF, comprobante_uuid: str) -> None:
    cur = conn.cursor()
    fila = db.fetchone(cur, "SELECT COALESCE(MAX(seq),0)"
                             " FROM factura_ecf_eventos WHERE comprobante_id=%s",
                       (comp_id,))
    ya = int(fila[0]) if fila else 0
    for ev in flujo.historial.eventos(comprobante_uuid)[ya:]:
        db.execute(
            cur, "INSERT INTO factura_ecf_eventos (comprobante_id, empresa_id,"
                 " seq, de_estado, a_estado, actor, detalle, hash, timestamp,"
                 " creado_en) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (comp_id, empresa_id, ev.seq, ev.de or None, ev.a, ev.actor,
             ev.detalle, ev.hash, ev.timestamp, _ahora()))


def _persistir_estado(conn, comp_id: int, c: Comprobante,
                      flujo: FlujoECF) -> None:
    cur = conn.cursor()
    eventos = [
        {"seq": ev.seq, "de": ev.de, "a": ev.a, "actor": ev.actor,
         "detalle": ev.detalle, "timestamp": ev.timestamp, "hash": ev.hash}
        for ev in flujo.historial.eventos(c.id)
    ]
    db.execute(cur, "UPDATE factura_ecf SET estado=%s, xml=%s, datos=%s,"
                    " historial=%s, actualizado_en=%s"
                    " WHERE id=%s",
               (c.estado, generar_xml(c),
                json.dumps(c.to_dict(), ensure_ascii=False),
                json.dumps(eventos, ensure_ascii=False), _ahora(), comp_id))
    _guardar_eventos(conn, comp_id, int(c.company_id), flujo, c.id)


def _cargar(empresa_id: int, comp_id: int) -> tuple[dict, Comprobante, FlujoECF]:
    """Carga fila + reconstruye Comprobante y FlujoECF con su historial."""
    conn = db.connect()
    try:
        cur = conn.cursor()
        fila = db.fetchone(cur, "SELECT * FROM factura_ecf"
                                " WHERE id=%s AND empresa_id=%s",
                           (comp_id, empresa_id))
        if not fila:
            raise ValueError("comprobante no encontrado")
        row = dict(fila)
        c = _reconstruir(json.loads(row["datos"]))
        flujo = FlujoECF()
        evs = db.fetchall(cur, "SELECT seq, de_estado, a_estado, actor,"
                               " detalle, hash, timestamp"
                               " FROM factura_ecf_eventos"
                               " WHERE comprobante_id=%s ORDER BY seq",
                          (comp_id,))
        lista = flujo.historial._eventos.setdefault(c.id, [])
        for seq, de, a, actor, detalle, hashv, ts in evs:
            lista.append(Evento(seq=int(seq), comprobante_id=c.id,
                                de=de or "", a=a, actor=actor or "",
                                detalle=detalle or "", timestamp=ts,
                                hash=hashv))
        for ev in lista:
            if ev.a == "pendiente_aprobacion":
                flujo._aprobaciones[c.id] = {
                    "actor": ev.actor, "detalle": ev.detalle,
                    "fecha": ev.timestamp}
                break
        return row, c, flujo
    finally:
        conn.close()


# --- operaciones -------------------------------------------------------------

def crear(empresa_id: int, venta_id: int, tipo: str, actor: str,
          encf_original: str = "") -> dict:
    """Crea un comprobante e-CF (SIMULACIÓN — sin validez fiscal).

    Issue #11: el e-NCF se reserva por emisor fiscal normalizado
    (RNC), no por empresa; el namespace 'mock' nunca consume rango real.
    """
    if tipo not in TIPOS_PERMITIDOS:
        raise ValueError(f"tipo e-CF no habilitado: {tipo!r}")
    if db.es_modo_cloud():
        raise ValueError("e-CF deshabilitado: producto administrativo SIN VALOR FISCAL")
    conn = db.connect()
    try:
        with db.locked(conn):
            emp = _empresa(conn, empresa_id)
            rnc_norm = _rnc_norm(emp.get("rnc") or "")
            modo = emp.get("e_cf_mode") or "disabled"
            if modo not in config.ECF_MODES:
                modo = "disabled"
            espacio = _espacio(modo)
            c = _construir_desde_venta(conn, empresa_id, venta_id, tipo,
                                       encf_original)
            c.e_ncf = _reservar_e_ncf(conn, rnc_norm, tipo, espacio)
            errores = validar_comprobante(
                c, existe_encf=lambda e: _existe_encf(conn, rnc_norm, e))
            if errores:
                raise ValueError("validación e-CF: " + "; ".join(errores))
            flujo = FlujoECF()
            flujo.historial.registrar(c.id, "", "borrador", actor,
                                      "comprobante creado (simulación)")
            cur = conn.cursor()
            comp_id = db.insert_id(
                cur, "INSERT INTO factura_ecf (empresa_id, emisor_rnc_norm,"
                     " e_ncf, tipo, estado, modo, xml, datos, historial,"
                     " creado_en, actualizado_en)"
                     " VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                (empresa_id, rnc_norm, c.e_ncf, c.tipo, c.estado, c.modo,
                 generar_xml(c), json.dumps(c.to_dict(), ensure_ascii=False),
                 "[]", _ahora(), _ahora()))
            _persistir_estado(conn, comp_id, c, flujo)
            cur = conn.cursor()
            db.execute(cur, "UPDATE factura_ventas SET ecf_id=%s"
                            " WHERE id=%s AND empresa_id=%s",
                       (comp_id, venta_id, empresa_id))
        return obtener(empresa_id, comp_id)
    except ErrorECF as exc:
        raise ValueError(str(exc))
    finally:
        conn.close()


def _existe_encf(conn, emisor_rnc_norm: str, e_ncf: str) -> bool:
    """¿Existe ya este e-NCF para el emisor fiscal? (issue #11)."""
    cur = conn.cursor()
    fila = db.fetchone(cur, "SELECT id FROM factura_ecf"
                            " WHERE emisor_rnc_norm=%s AND e_ncf=%s",
                       (emisor_rnc_norm, (e_ncf or "").strip().upper()))
    return bool(fila)


def obtener(empresa_id: int, comp_id: int) -> dict | None:
    try:
        row, c, flujo = _cargar(empresa_id, comp_id)
    except ValueError:
        return None
    row["comprobante"] = c.to_dict()
    row["historial_lista"] = json.loads(row["historial"] or "[]")
    row["historial_ok"] = flujo.verificar_historial(c.id)
    row["transiciones"] = list(TRANSICIONES_PERMITIDAS.get(c.estado, ()))
    row["tipo_nombre"] = TIPOS_ECF.get(c.tipo, {}).get("nombre", c.tipo)
    return row


def listar(empresa_id: int, estado: str | None = None) -> list[dict]:
    conn = db.connect()
    try:
        cur = conn.cursor()
        sql = ("SELECT id, e_ncf, tipo, estado, modo, creado_en"
               " FROM factura_ecf WHERE empresa_id=%s")
        params: list = [empresa_id]
        if estado:
            if estado not in ESTADOS:
                raise ValueError(f"estado inválido: {estado!r}")
            sql += " AND estado=%s"
            params.append(estado)
        sql += " ORDER BY id DESC"
        out = []
        for f in db.fetchall(cur, sql, params):
            out.append({"id": f[0], "e_ncf": f[1], "tipo": f[2],
                        "estado": f[3], "modo": f[4], "creado_en": f[5],
                        "tipo_nombre": TIPOS_ECF.get(f[2], {}).get("nombre",
                                                                   f[2])})
        return out
    finally:
        conn.close()


def _aplicar(empresa_id: int, comp_id: int, accion, actor: str) -> dict:
    if db.es_modo_cloud():
        raise ValueError("e-CF deshabilitado: producto administrativo SIN VALOR FISCAL")
    row, c, flujo = _cargar(empresa_id, comp_id)
    try:
        accion(c, flujo, actor)
    except ErrorECF as exc:
        raise ValueError(str(exc))
    conn = db.connect()
    try:
        with db.tx(conn):
            _persistir_estado(conn, row["id"], c, flujo)
    finally:
        conn.close()
    return obtener(empresa_id, comp_id)


def validar(empresa_id: int, comp_id: int, actor: str) -> dict:
    def _acc(c, flujo, actor):
        errores = validar_comprobante(c)
        if errores:
            raise ErrorECF("validación e-CF: " + "; ".join(errores))
        flujo.transicionar(c, "validado", actor, "validación estructural OK")
    return _aplicar(empresa_id, comp_id, _acc, actor)


def aprobar(empresa_id: int, comp_id: int, actor: str,
            detalle: str = "") -> dict:
    """Aprobación HUMANA obligatoria antes de emitir."""
    def _acc(c, flujo, actor):
        flujo.aprobar(c, actor, detalle or "aprobado por responsable")
    return _aplicar(empresa_id, comp_id, _acc, actor)


def emitir(empresa_id: int, comp_id: int, actor: str) -> dict:
    """Transmite vía MockProvider (bloqueado si modo=disabled)."""
    def _acc(c, flujo, actor):
        xml = generar_xml(c)
        flujo.emitir(c, MockProvider(), actor, xml)
    return _aplicar(empresa_id, comp_id, _acc, actor)


def anular(empresa_id: int, comp_id: int, actor: str, motivo: str) -> dict:
    if not (motivo or "").strip():
        raise ValueError("la anulación exige un motivo")
    def _acc(c, flujo, actor):
        flujo.transicionar(c, "anulado", actor, motivo.strip())
    return _aplicar(empresa_id, comp_id, _acc, actor)


def xml_texto(empresa_id: int, comp_id: int) -> tuple[str, str]:
    row, _, _ = _cargar(empresa_id, comp_id)
    return row["e_ncf"], row["xml"]


def verificar_cadena(empresa_id: int, comp_id: int) -> bool:
    _, c, flujo = _cargar(empresa_id, comp_id)
    return flujo.verificar_historial(c.id)
