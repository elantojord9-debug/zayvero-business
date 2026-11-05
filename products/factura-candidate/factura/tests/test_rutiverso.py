import json
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import test_wsgi_gate
from test_factura import BaseFactura
from factura_ecf import rutiverso
from factura_core import ventas
from factura_persist import db


class TestRutiverso(BaseFactura):
    request = test_wsgi_gate.TestWsgiGate.request
    payload = test_wsgi_gate.TestWsgiGate.payload
    def setUp(self):
        super().setUp()
        from factura_auth import auth
        from factura_core import inventario, caja
        self.eid = self._empresa()
        self.user = auth.crear_usuario(self.eid, "wsgi@test.local", "Synthetic-User-Password-123!", "admin")
        self.cookie = auth.crear_sesion(self.user)
        self.csrf = auth.csrf_token(auth.leer_sesion(self.cookie))
        self.product = inventario.crear(self.eid, "WSGI", "Producto", 100, stock=5)
        caja.abrir(self.eid, self.user["id"], 0)
        self.environment = patch.dict(os.environ, {
            'FACTURA_RUTIVERSO_SANDBOX_EMPRESA': str(self.eid),
            'FACTURA_RUTIVERSO_SANDBOX_TOKEN': 'synthetic-test-key',
            'FACTURA_RUTIVERSO_E31_INICIO': '19001',
            'FACTURA_RUTIVERSO_E32_INICIO': '19101'})
        self.environment.start()
        self.addCleanup(self.environment.stop)
        status, body = self.request('/api/pos/cobrar', self.payload())
        self.assertEqual(status, 200)
        self.sale = json.loads(body)['id']

    def action(self, action='emitir', kind='32', sale=None, **kwargs):
        return self.request('/api/pos/sandbox', {'venta_id': sale or self.sale,
                            'tipo': kind, 'accion': action}, **kwargs)

    def result(self, ncf='E320000019101', state='success'):
        return {'invoice_id': 987, 'ncf': ncf, 'amount_total': 118,
                'ecf_status': state,
                'qr_link': 'https://fc.dgii.gov.do/TesteCF/ConsultaTimbreFC'}

    def test_timeout_restart_recovers_without_second_send(self):
        with patch.object(rutiverso, 'request', side_effect=TimeoutError):
            status, body = self.action()
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)['estado'], 'pendiente')
        db.reset_backend_cache()
        with patch.object(rutiverso, 'request') as call:
            self.action()
            call.assert_not_called()
        responses = [json.dumps({'data': [{'id': 987, 'l10n_do_fiscal_number': 'E320000019101'}]}),
                     json.dumps(self.result()), '<ECF><eNCF>E320000019101</eNCF></ECF>']
        with patch.object(rutiverso, 'request', side_effect=responses):
            status, body = self.action('consultar')
        self.assertEqual(json.loads(body)['estado'], 'aceptado')
        self.assertIn('<ECF>', json.loads(body)['xml'])
        self.assertEqual(len(ventas.listar(self.eid)), 1)

    def test_repeated_and_concurrent_send_allocates_once(self):
        with patch.object(rutiverso, 'request', return_value=json.dumps(self.result())) as call:
            with ThreadPoolExecutor(4) as pool:
                rows = list(pool.map(lambda _: rutiverso.operate(self.eid,self.sale,'32','emitir'), range(4)))
            self.assertEqual(call.call_count, 1)
        self.assertEqual({r['ncf'] for r in rows}, {'E320000019101'})

    def test_foreign_sale_and_csrf_cannot_send(self):
        other = self._empresa('B', '222222222')
        sale = ventas.crear_venta(other, 'factura', None, [{'nombre':'B','cantidad':1,'precio':100}])
        with patch.object(rutiverso, 'request') as call:
            self.assertEqual(self.action(sale=sale['id'])[0], 400)
            self.assertEqual(self.action(csrf=False)[0], 403)
            call.assert_not_called()

    def test_sandbox_disabled_by_default(self):
        with patch.dict(os.environ, {'FACTURA_RUTIVERSO_SANDBOX_EMPRESA': ''}):
            self.assertEqual(self.action()[0], 400)

    def test_e31_recipient_and_number(self):
        with patch.object(rutiverso,'request',return_value=json.dumps(self.result('E310000019001'))) as call:
            self.assertEqual(self.action(kind='31')[0],200)
            payload=call.call_args.args[1]
            self.assertEqual(payload['client_vat'],'131118003')
            self.assertEqual(payload['ncf'],'E310000019001')
        self.assertEqual(self.action(kind='32')[0],400)

    def test_provider_rejection_overrides_sent(self):
        result=self.result(state='sent')
        result['message_dgii']='DGII HTTP 400 Rechazado'
        with patch.object(rutiverso,'request',return_value=json.dumps(result)):
            self.assertEqual(json.loads(self.action()[1])['estado'],'rechazado')

    def test_receipt_reaches_sandbox_controls(self):
        status,html=self.request('/facturas/'+str(self.sale))
        self.assertEqual(status,200)
        self.assertIn(b'/api/pos/sandbox',html)
        self.assertNotIn(b'synthetic-test-key',html)

    def test_exhausted_range_and_production_qr_are_blocked(self):
        with patch.dict(os.environ, {'FACTURA_RUTIVERSO_E32_INICIO': '19501'}):
            with patch.object(rutiverso, 'request') as call:
                self.assertEqual(self.action()[0],400)
                call.assert_not_called()
        result=self.result()
        result['qr_link']='https://fc.dgii.gov.do/Produccion/ConsultaTimbreFC'
        with patch.object(rutiverso, 'request', return_value=json.dumps(result)):
            data=json.loads(self.action()[1])
            self.assertEqual(data['estado'],'pendiente')
            self.assertEqual(data['qr'],'')

    def test_cashier_cannot_send_sandbox(self):
        from factura_auth import auth
        auth.cambiar_rol(self.eid,self.user['id'],'cajero')
        with patch.object(rutiverso,'request') as call:
            self.assertEqual(self.action()[0],403)
            call.assert_not_called()

    def test_company_configuration_does_not_fall_back_to_shared_key(self):
        mapping = {str(self.eid): {'token_env': 'FACTURA_RUTIVERSO_TOKEN_A',
                   'inicio': {'31': 19003, '32': 19103}, 'vencimiento': '2028-12-31'}}
        with patch.dict(os.environ, {'FACTURA_RUTIVERSO_SANDBOX_CONFIG': json.dumps(mapping),
                                   'FACTURA_RUTIVERSO_TOKEN_A': 'company-a-secret'}):
            self.assertEqual(rutiverso.configuration(self.eid)['token'], 'company-a-secret')
            self.assertFalse(rutiverso.enabled(self.eid + 100))
            with patch.object(rutiverso, 'request', return_value=json.dumps(self.result('E320000019103'))) as call:
                self.assertEqual(self.action()[0], 200)
                self.assertEqual(call.call_args.kwargs['company'], self.eid)
                self.assertNotIn('_zayvero_connector', call.call_args.args[1])
            with patch.dict(os.environ, {'FACTURA_RUTIVERSO_TOKEN_A': 'another-connector'}):
                with patch.object(rutiverso, 'request') as call:
                    self.assertEqual(self.action('consultar')[0], 400)
                    call.assert_not_called()

    def test_invalid_company_mapping_fails_closed(self):
        for mapping in ['not-json', '[]', json.dumps({str(self.eid): {'token_env': 'HOME'}})]:
            with patch.dict(os.environ, {'FACTURA_RUTIVERSO_SANDBOX_CONFIG': mapping}):
                with patch.object(rutiverso, 'request') as call:
                    self.assertEqual(self.action()[0], 400)
                    call.assert_not_called()

    def test_wrong_type_consult_does_not_call_provider(self):
        with patch.object(rutiverso, 'request', return_value=json.dumps(self.result())):
            self.action()
        with patch.object(rutiverso, 'request') as call:
            self.assertEqual(self.action('consultar', kind='31')[0], 400)
            call.assert_not_called()

    def test_empty_provider_list_remains_pending_without_resend(self):
        with patch.object(rutiverso, 'request', side_effect=TimeoutError):
            self.action()
        with patch.object(rutiverso, 'request', return_value=json.dumps({'data': []})) as call:
            data = json.loads(self.action('consultar')[1])
            self.assertEqual(data['estado'], 'pendiente')
            self.assertEqual(call.call_args.args[0], '/invoices?limit=100')
            self.assertEqual(call.call_count, 1)

    def test_http_uses_each_company_secret(self):
        mapping = {str(c): {'token_env': 'FACTURA_RUTIVERSO_TOKEN_' + str(c),
                   'inicio': {'31': 19003, '32': 19103}, 'vencimiento': '2028-12-31'} for c in (10,20)}
        env = {'FACTURA_RUTIVERSO_SANDBOX_CONFIG': json.dumps(mapping),
               'FACTURA_RUTIVERSO_TOKEN_10': 'secret-a', 'FACTURA_RUTIVERSO_TOKEN_20': 'secret-b'}
        with patch.dict(os.environ, env), patch.object(rutiverso, 'build_opener') as build:
            build.return_value.open.return_value.__enter__.return_value.read.return_value = b'{}'
            for company, token in [(10,'secret-a'), (20,'secret-b')]:
                rutiverso.request('/auth', {}, company=company)
                req = build.return_value.open.call_args.args[0]
                self.assertEqual(req.get_header('Authorization'), token)
                self.assertEqual(req.full_url, rutiverso.BASE + '/auth')
