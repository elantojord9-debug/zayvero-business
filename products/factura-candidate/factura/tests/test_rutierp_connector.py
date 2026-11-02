import unittest
from unittest.mock import patch, MagicMock
from factura_ecf.rutierp_connector import RutiERPConnector

class RutiERPConnectorTests(unittest.TestCase):
    def test_https_required(self):
        with self.assertRaises(ValueError):
            RutiERPConnector("http://example.test", "key")
    def test_send_disabled_by_default(self):
        client = RutiERPConnector("https://example.test", "key")
        with self.assertRaises(PermissionError):
            client.request("/send", {"ncf": "E310000019000"})
    @patch("urllib.request.urlopen")
    def test_auth_header_and_endpoint(self, opener):
        response = MagicMock()
        response.read.return_value = b'{"code":200,"data":{"state":"active"}}'
        opener.return_value.__enter__.return_value = response
        client = RutiERPConnector("https://example.test", "private-key")
        self.assertEqual(client.request("/auth")["code"], 200)
        request = opener.call_args.args[0]
        self.assertEqual(request.full_url, "https://example.test/api/ecf/auth")
        self.assertEqual(request.get_header("Authorization"), "private-key")
        self.assertEqual(request.get_method(), "POST")
if __name__ == "__main__":
    unittest.main()
