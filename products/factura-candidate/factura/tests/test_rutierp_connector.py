import unittest
from unittest.mock import patch, MagicMock
from factura_ecf.rutierp_connector import RutiERPConnector, RutiERPError

class RutiERPConnectorTests(unittest.TestCase):
    def test_https_required(self):
        with self.assertRaises(ValueError):
            RutiERPConnector("http://example.test", "key")

    def test_missing_key(self):
        with self.assertRaises(ValueError):
            RutiERPConnector("https://example.test", "")

    def test_send_disabled_by_default(self):
        client = RutiERPConnector("https://example.test", "key")
        with self.assertRaises(PermissionError):
            client.send({"ncf": "E310000019000"})

    def test_send_requires_sequence_expiration(self):
        client = RutiERPConnector("https://example.test", "key")
        with self.assertRaises(ValueError):
            client.send({"ncf": "E310000019000", "items": [{"name": "Test"}]}, approved=True)

    @patch("urllib.request.urlopen")
    def test_auth_header_and_endpoint(self, opener):
        response = MagicMock()
        response.read.return_value = b'{"code":200,"data":{"state":"active"}}'
        opener.return_value.__enter__.return_value = response
        client = RutiERPConnector("https://example.test", "private-key")
        self.assertEqual(client.authenticate()["code"], 200)
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/api/ecf/auth")
        self.assertEqual(request.get_header("Authorization"), "private-key")
        self.assertEqual(request.get_method(), "POST")

    @patch("urllib.request.urlopen")
    def test_send_with_approval(self, opener):
        response = MagicMock()
        response.read.return_value = b'{"code":200,"data":{"ecf_status":"sent"}}'
        opener.return_value.__enter__.return_value = response
        client = RutiERPConnector("https://example.test", "private-key")
        client.send({"ncf":"E310000019000","invoice_date_due":"2027-12-31",
                     "items":[{"name":"Servicio","quantity":1,"price_unit":100}]}, approved=True)
        self.assertEqual(opener.call_args.args[0].get_method(), "POST")

if __name__ == "__main__":
    unittest.main()
