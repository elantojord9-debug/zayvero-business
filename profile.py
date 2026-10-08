"""
ZAYVERO BUSINESS — FASE 1B: Business Data Profiling Engine.

`profile.py`: construye el BusinessDatasetProfile desde un Parquet
normalizado de FASE 1A y lo guarda como JSON.

Uso:
    .venv/bin/python profile.py data/processed/demo-retail/online_retail_II.parquet --company demo-retail

Salida:
    data/profiles/<company_id>/<base>_profile.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from profiling import build_profile  # noqa: E402
from profiling.builder import save_profile  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ZAYVERO BUSINESS — FASE 1B: construir BusinessDatasetProfile"
    )
    parser.add_argument("parquet", help="Parquet normalizado de FASE 1A")
    parser.add_argument("--company", required=True, help="company_id (obligatorio)")
    args = parser.parse_args()

    profile = build_profile(args.parquet, args.company)
    base = os.path.splitext(os.path.basename(args.parquet))[0]
    out_path = save_profile(profile, args.company, base)

    dq = profile["data_quality"]
    sales = profile["sales"]
    print(f"OK: perfil guardado en {out_path}")
    print(f"  Filas: {profile['dataset_metadata']['n_rows']}")
    print(f"  Quality score: {dq['score']}/100 ({dq['grade']})")
    print(f"  Facturación BRUTA: {sales['gross_revenue']}")
    print(f"  Facturación CANCELADA: {sales['cancelled_revenue']}")
    print(f"  Facturación NETA: {sales['net_revenue']}")
    print(f"  Explicación: {dq['explanation'][:200]}...")


if __name__ == "__main__":
    main()
