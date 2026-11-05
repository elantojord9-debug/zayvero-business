"""Rutiverso Sandbox with explicit per-company server configuration."""
import json
import hashlib
import os
from decimal import Decimal
from urllib.request import Request, build_opener, HTTPRedirectHandler
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET

from factura_core import ventas
from factura_persist import db
from factura_persist.uow import company_transaction

BASE = 'https://sistema.rutiversotech.com/api/ecf'
START = {'31': 19001, '32': 19101}
END = 19500


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def configuration(company):
    """Resolve only server-owned secrets; never accept connector keys from a sale."""
    configured = os.getenv('FACTURA_RUTIVERSO_SANDBOX_CONFIG')
    if configured:
        entries = json.loads(configured)
        entry = entries.get(str(company))
        if not entry:
            raise ValueError('Sandbox sin habilitar para esta empresa')
        token_env = entry['token_env']
        if not token_env.startswith('FACTURA_RUTIVERSO_TOKEN_'):
            raise ValueError('Referencia de credencial inválida')
        token = os.environ[token_env]
        starts = {kind: int(entry['inicio'][kind]) for kind in START}
        due = entry['vencimiento']
        from datetime import date
        date.fromisoformat(due)
    else:
        if str(company) != os.getenv('FACTURA_RUTIVERSO_SANDBOX_EMPRESA', ''):
            raise ValueError('Sandbox sin habilitar para esta empresa')
        token = os.environ['FACTURA_RUTIVERSO_SANDBOX_TOKEN']
        starts = {kind: int(os.environ['FACTURA_RUTIVERSO_E' + kind + '_INICIO']) for kind in START}
        due = '2028-12-31'
    if not token or any(not START[kind] <= starts[kind] <= END for kind in START):
        raise ValueError('Credencial o rango Sandbox inválido')
    return {'token': token, 'inicio': starts, 'vencimiento': due}


def enabled(company):
    try:
        configuration(company)
        return True
    except (ValueError, KeyError, TypeError, AttributeError):
        return False


def request(path, payload=None, *, company):
    headers = {'Authorization': configuration(company)['token'],
               'Content-Type': 'application/json'}
    body = None if payload is None else json.dumps(payload).encode()
    with build_opener(NoRedirect()).open(Request(BASE + path, data=body,
                                                headers=headers), timeout=20) as response:
        raw = response.read(2_000_001)
    if len(raw) > 2_000_000:
        raise ValueError('respuesta demasiado grande')
    return raw.decode('utf-8')


def _json(path, payload=None, *, company):
    result = json.loads(request(path, payload, company=company))
    if result.get('code', 200) != 200:
        raise ValueError('el proveedor rechazó la solicitud')
    return result.get('data') if 'data' in result else result


def _row(conn, company, sale):
    row = db.fetchone(conn.cursor(), 'SELECT * FROM factura_sandbox WHERE empresa_id=%s AND venta_id=%s', (company, sale))
    return dict(row) if row else None


def _payload(sale, kind, ncf, config):
    if sale['estado'] == 'anulada' or Decimal(sale.get('descuento') or '0'):
        raise ValueError('Prueba disponible para ventas vigentes sin descuentos')
    items = []
    for item in sale['items']:
        if Decimal(item.get('descuento') or '0'):
            raise ValueError('Prueba disponible para líneas sin descuentos')
        subtotal = Decimal(item['subtotal'])
        tax = Decimal(item['itbis'])
        exempt = item['itbis_exento']
        expected = Decimal('0') if exempt else (subtotal * Decimal('.18')).quantize(Decimal('.01'))
        if tax != expected:
            raise ValueError('Esta prueba admite ITBIS 18% o exento')
        items.append({'name': item['nombre'], 'quantity': float(item['cantidad']),
                      'price_unit': float(item['precio']), 'tax_ids': [1 if exempt else 3]})
    total = sum((Decimal(i['subtotal']) + Decimal(i['itbis']) for i in sale['items']), Decimal(0))
    if total != Decimal(sale['total']):
        raise ValueError('El total de la venta no coincide con sus líneas')
    payload = {'ncf': ncf, 'invoice_date_due': config['vencimiento'],
               'external_ref': 'ZAYVERO-POS-SANDBOX-' + ncf,
               'income_type': '01', 'payment_type': '1', 'wait_dgii': False,
               'client_name': 'ZAYVERO PRUEBA SANDBOX', 'items': items}
    if kind == '31':
        payload.update(client_name='GRUPO VILLAR SRL', client_vat='131118003')
    return payload


def _connector_fingerprint(company):
    return hashlib.sha256(configuration(company)['token'].encode()).hexdigest()


def _check_connector(company, row):
    fingerprint = json.loads(row['payload']).get('_zayvero_connector')
    if fingerprint and fingerprint != _connector_fingerprint(company):
        raise ValueError('El comprobante pertenece a otra credencial; revise la configuración')
    if not fingerprint and os.getenv('FACTURA_RUTIVERSO_SANDBOX_CONFIG'):
        raise ValueError('Comprobante anterior a la configuración por empresa; requiere conciliación')


def _reserve(company, sale_id, kind):
    with company_transaction(company) as conn:
        sale = ventas.obtener(company, sale_id)
        if not sale:
            raise ValueError('Venta no encontrada')
        existing = _row(conn, company, sale_id)
        if existing:
            _check_connector(company, existing)
            if existing['tipo'] != kind:
                raise ValueError('La venta ya tiene otro tipo de comprobante de prueba')
            return existing, False
        # The company lock serializes allocation, unique e-NCF also guards reuse.
        last = db.fetchone(conn.cursor(), 'SELECT MAX(secuencia) FROM factura_sandbox WHERE tipo=%s', (kind,))[0]
        config = configuration(company)
        start = config['inicio'][kind]
        if not START[kind] <= start <= END:
            raise ValueError('Inicio fuera del rango reservado para pruebas')
        sequence = max(start, (last or 0) + 1)
        if sequence > END:
            raise ValueError('Rango Sandbox agotado')
        ncf = 'E' + kind + str(sequence).zfill(10)
        payload = _payload(sale, kind, ncf, config)
        payload['_zayvero_connector'] = _connector_fingerprint(company)
        db.execute(conn.cursor(), 'INSERT INTO factura_sandbox (empresa_id,venta_id,tipo,secuencia,ncf,payload,estado) VALUES (%s,%s,%s,%s,%s,%s,%s)',
                   (company, sale_id, kind, sequence, ncf, json.dumps(payload), 'pendiente'))
        return _row(conn, company, sale_id), True


def _save(company, sale, **fields):
    with company_transaction(company) as conn:
        sql = ', '.join(key + '=%s' for key in fields)
        db.execute(conn.cursor(), 'UPDATE factura_sandbox SET ' + sql + ' WHERE empresa_id=%s AND venta_id=%s', (*fields.values(), company, sale))


def _accept(company, sale, row, result):
    if result.get('ncf', result.get('encf')) != row['ncf']:
        raise ValueError('Respuesta de otro comprobante')
    if Decimal(str(result.get('amount_total'))) != Decimal(ventas.obtener(company, sale)['total']):
        raise ValueError('Total del proveedor diferente de la venta')
    invoice_id = int(result['invoice_id'])
    if invoice_id <= 0 or (row['invoice_id'] and row['invoice_id'] != invoice_id):
        raise ValueError('Identificador de proveedor inconsistente')
    state = result.get('ecf_status')
    message = result.get('message_dgii') or result.get('dgii_message') or ''
    # Provider can return sent/init while embedding a DGII rejection.
    if 'Rechazado' in message:
        state = 'failure'
    state = {'success': 'aceptado', 'conditional': 'condicional', 'failure': 'rechazado'}.get(state, 'pendiente')
    qr = result.get('qr_link') or result.get('qr_url') or ''
    parts = urlsplit(qr)
    if qr and not (parts.scheme == 'https' and parts.hostname in ('fc.dgii.gov.do', 'ecf.dgii.gov.do') and '/TesteCF/' in parts.path):
        raise ValueError('QR fuera del ambiente de pruebas')
    _save(company, sale, invoice_id=invoice_id, estado=state, qr=qr,
          respuesta=json.dumps(result, ensure_ascii=False), mensaje=message)


def operate(company, sale, kind, action):
    if not enabled(company):
        raise ValueError('Sandbox sin habilitar para esta empresa')
    if kind not in START or action not in ('emitir', 'consultar'):
        raise ValueError('Operación Sandbox inválida')
    if action == 'emitir':
        row, fresh = _reserve(company, sale, kind)
        if fresh:
            try:
                payload = json.loads(row['payload'])
                payload.pop('_zayvero_connector', None)
                _accept(company, sale, row, _json('/send', payload, company=company))
            except Exception:
                _save(company, sale, mensaje='Envío pendiente de confirmar. Consulte el estado con el mismo comprobante.')
    else:
        conn = db.connect()
        try:
            row = _row(conn, company, sale)
        finally:
            conn.close()
        if not row:
            raise ValueError('Primero emita la prueba de esta venta')
        if row['tipo'] != kind:
            raise ValueError('Seleccione el tipo del comprobante emitido')
        _check_connector(company, row)
        try:
            invoice_id = row['invoice_id']
            if not invoice_id:
                matches = [v for v in _json('/invoices?limit=100', company=company) if v.get('l10n_do_fiscal_number') == row['ncf']]
                if not matches:
                    raise ValueError('Sin resultado en las últimas 100 facturas')
                invoice_id = int(matches[0]['id'])
            _accept(company, sale, row, _json('/invoice/' + str(invoice_id) + '/status', company=company))
            xml = request('/get/' + str(invoice_id) + '/xml', company=company)
            root = ET.fromstring(xml)
            if root.findtext('.//eNCF') != row['ncf']:
                raise ValueError('XML de otro comprobante')
            _save(company, sale, xml=xml)
        except Exception:
            _save(company, sale, mensaje='Consulta incompleta. Vuelva a consultar o solicite al proveedor el resultado de este e-NCF.')
    conn = db.connect()
    try:
        row = _row(conn, company, sale)
        return {key: row[key] for key in ('ncf', 'estado', 'mensaje', 'qr', 'xml', 'invoice_id')}
    finally:
        conn.close()
