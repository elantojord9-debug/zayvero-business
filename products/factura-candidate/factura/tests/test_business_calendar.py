from datetime import date
from decimal import Decimal
from unittest.mock import patch
from test_factura import BaseFactura
from factura_core import clock, pagos, reportes, saas
from factura_persist import db
from factura_web.templates import fecha_do


class TestBusinessCalendar(BaseFactura):
    def seed(self, company, timestamp, count=1):
        conn = db.connect()
        try:
            with db.tx(conn):
                for n in range(count):
                    db.execute(conn.cursor(), "INSERT INTO factura_ventas (empresa_id,numero,tipo,total,creada) VALUES (%s,%s,'factura','1',%s)", (company, timestamp + str(n), timestamp))
        finally:
            conn.close()

    def test_evening_sale_belongs_to_dominican_day(self):
        eid = self._empresa()
        self.seed(eid, '2026-10-29T01:00:00+00:00')
        with patch.object(clock, 'today_business', return_value=date(2026,10,28)):
            self.assertEqual(reportes.ventas_por_dia(eid, 1), [{'dia':'2026-10-28','cantidad':1,'total':'1'}])
        self.assertEqual(len(reportes._ventas_rango(eid, '2026-10-28', '2026-10-28')), 1)
        self.assertEqual(reportes._ventas_rango(eid, '2026-10-29', '2026-10-29'), [])

    def test_month_boundary_uses_business_calendar(self):
        eid = self._empresa()
        self.seed(eid, '2026-11-01T02:00:00+00:00')
        with patch.object(clock, 'today_business', return_value=date(2026,10,31)):
            self.assertEqual(saas.contar_docs_mes(eid), 1)
        with patch.object(clock, 'today_business', return_value=date(2026,11,1)):
            self.assertEqual(saas.contar_docs_mes(eid), 0)

    def test_timestamp_display_and_date_only(self):
        self.assertEqual(fecha_do('2026-10-29T01:00:00+00:00'), '28/10/2026 21:00')
        self.assertEqual(fecha_do('2026-10-29'), '29/10/2026')

    def test_receivables_includes_more_than_500_and_isolates_tenants(self):
        eid = self._empresa()
        other = self._empresa('Other', '999999999')
        self.seed(eid, '2026-10-29T01:00:00+00:00', 501)
        self.seed(other, '2026-10-29T01:00:00+00:00')
        self.assertEqual(len(pagos.cxc_pendientes(eid)), 501)
        self.assertEqual(pagos.total_cxc(eid), Decimal('501'))
