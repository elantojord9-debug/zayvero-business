"""Configuración de rangos de prueba externos, SIN emisión ni reserva de e-NCF.

No conectar a producción ni al contador de secuencias interno.
Los rangos aportados por el usuario deben cotejarse con el integrador.
"""
from dataclasses import dataclass

@dataclass(frozen=True)
class RangoExterno:
    rnc: str
    tipo: str
    inicio: int
    fin: int

    def __post_init__(self):
        if not (self.rnc.isdigit() and len(self.rnc) == 9):
            raise ValueError("RNC debe tener 9 dígitos")
        if self.tipo not in ("31", "32"):
            raise ValueError("Solo E31/E32")
        if not (1 <= self.inicio <= self.fin <= 9999999999):
            raise ValueError("Rango inválido")

    def encf(self, numero: int) -> str:
        if not self.inicio <= numero <= self.fin:
            raise ValueError("Número fuera del rango configurado")
        return f"E{self.tipo}{numero:010d}"

    @property
    def cantidad(self) -> int:
        return self.fin - self.inicio + 1


# Datos reportados para sandbox; NO prueba de autorización DGII.
GRUPO_VILLAR_SANDBOX = {
    "31": RangoExterno("131118003", "31", 19000, 19500),
    "32": RangoExterno("131118003", "32", 19100, 19500),
}
