"""
ZAYVERO BUSINESS — FASE 1A: prueba con datos sintéticos edge-case.

Genera 12,000 filas con NOMBRES DE COLUMNA ALTERNATIVOS (para probar aliases):
    "Invoice Number" -> Invoice, "Product Code" -> StockCode, etc.
Incluye: nulos, duplicados, fechas inválidas, cantidades negativas,
precios en cero, facturas canceladas (prefijo C), Customer ID faltante.
"""

import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pandas as pd

random.seed(42)

N = 12000
rows = []
for i in range(N):
    # Facturas canceladas: 5% con prefijo C
    inv = f"C{50000 + i}" if random.random() < 0.05 else f"{50000 + i}"
    rows.append({
        # NOMBRES ALTERNATIVOS (prueban el sistema de aliases)
        "Invoice Number": inv,
        "Product Code": random.choice(["A100", "B200", "C300", 999]),  # 999 = tipo mixto
        "Description": random.choice(["Widget", "Gadget", None]),       # nulos
        "Qty": random.choice([1, 2, 5, 10, -3, -1]),                    # negativos
        "Fecha": random.choice([
            "2024-01-15", "2024/03/22", "15-06-2024",
            "fecha-mala", None,                                          # inválidas/nulas
        ]),
        "Precio": random.choice([9.99, 19.50, 0.0, 5.25]),              # cero incluido
        "Cliente": random.choice(["CLI-1", "CLI-2", None]),             # nulos
        "Pais": random.choice(["USA", "Mexico", "España"]),
    })

df = pd.DataFrame(rows)
# Duplicados: 2% de filas repetidas
dups = df.sample(frac=0.02, random_state=7)
df = pd.concat([df, dups], ignore_index=True)

out = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "data", "raw", "test_edge_cases.csv",
)
df.to_csv(out, index=False)
print(f"OK: {len(df)} filas -> {out}")
print("Columnas:", list(df.columns))
