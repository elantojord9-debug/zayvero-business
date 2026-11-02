"""Pruebas offline: nunca hacen llamadas al integrador ni consumen secuencias."""
import unittest
from factura_ecf.rangos_externos import GRUPO_VILLAR_SANDBOX, RangoExterno

class TestRangosExternos(unittest.TestCase):
    def test_extremos(self):
        e31 = GRUPO_VILLAR_SANDBOX["31"]
        e32 = GRUPO_VILLAR_SANDBOX["32"]
        self.assertEqual(e31.encf(19000), "E310000019000")
        self.assertEqual(e31.encf(19500), "E310000019500")
        self.assertEqual(e32.encf(19100), "E320000019100")
        self.assertEqual(e32.encf(19500), "E320000019500")
        self.assertEqual((e31.cantidad, e32.cantidad), (501, 401))

    def test_fuera_de_rango(self):
        for tipo, numero in (("31", 18999), ("31", 19501), ("32", 19099), ("32", 19501)):
            with self.assertRaises(ValueError):
                GRUPO_VILLAR_SANDBOX[tipo].encf(numero)

    def test_config_invalida(self):
        with self.assertRaises(ValueError):
            RangoExterno("131118003", "31", 200, 100)
        with self.assertRaises(ValueError):
            RangoExterno("131118003", "33", 1, 2)

if __name__ == "__main__":
    unittest.main()
