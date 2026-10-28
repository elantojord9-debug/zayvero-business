#!/usr/bin/env python3
"""FASE 6 — Ensambla el video demostrativo desde las capturas reales.

Uso:
    ensamblar_video.py [--capturas demo/capturas] [--salida demo/video-demo.mp4]

Cada captura se muestra ~3s con fundido; títulos en español quemados.
Requiere ffmpeg en el PATH.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

TITULOS = {
    "01-landing": "TALENTO — página comercial",
    "02-login": "Acceso seguro por empresa",
    "03-panel": "Panel con indicadores del negocio",
    "04-pos": "Punto de venta rápido",
    "05-caja": "Control de caja: apertura y cierre por turno",
    "06-factura-nueva": "Nueva factura con ITBIS automático",
    "07-facturas": "Historial de facturas",
    "08-devoluciones": "Devoluciones sin alterar el original",
    "09-clientes": "Clientes con RNC",
    "10-inventario": "Inventario en tiempo real",
    "11-sucursales": "Sucursales por plan",
    "12-cxc": "Cuentas por cobrar",
    "13-ecf": "e-CF en simulación — sin validez fiscal",
    "14-reportes": "Reportes y exportaciones",
    "15-auditoria": "Auditoría de acciones críticas",
    "16-config": "Configuración por empresa",
}

import os

# Fuente del sistema para quemar texto en los segmentos; configurable por
# entorno (requiere el paquete de fuentes DejaVu instalado en el sistema).
FONT = os.environ.get(
    "FACTURA_FONT_BOLD",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
W, H = 1280, 720
SEG = 3.0  # segundos por escena
XFADE = 0.5


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--capturas", default=os.path.join(BASE, "demo", "capturas"))
    ap.add_argument("--salida", default=os.path.join(BASE, "demo",
                                                    "video-demo.mp4"))
    args = ap.parse_args()

    pngs = sorted(f for f in os.listdir(args.capturas) if f.endswith(".png"))
    if not pngs:
        print("sin capturas en", args.capturas)
        return 1
    print(f"{len(pngs)} capturas")

    tmp = os.path.join(BASE, "demo", "_segs")
    os.makedirs(tmp, exist_ok=True)
    segs = []
    for i, png in enumerate(pngs):
        slug = os.path.splitext(png)[0]
        titulo = TITULOS.get(slug, slug)
        # Escapar para drawtext: : ' % \
        titulo = (titulo.replace("\\", "\\\\").replace(":", "\\:")
                        .replace("'", "\\'").replace("%", "\\%"))
        seg = os.path.join(tmp, f"seg{i:02d}.mp4")
        # Escala/recorta a 1280x720, título quemado abajo
        vf = (f"scale=1280:720:force_original_aspect_ratio=increase,"
              f"crop=1280:720,"
              f"drawbox=y=ih-64:c=0x0B1F3A:t=fill,"
              f"drawtext=fontfile={FONT}:text='{titulo}':"
              f"fontcolor=white:fontsize=26:x=(w-text_w)/2:y=h-44")
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-loop", "1",
                        "-i", os.path.join(args.capturas, png),
                        "-vf", vf, "-t", str(SEG), "-pix_fmt", "yuv420p",
                        "-r", "30", seg], check=True)
        segs.append(seg)

    # Concatena con fundidos encadenados
    if len(segs) == 1:
        subprocess.run(["ffmpeg", "-y", "-v", "error", "-i", segs[0],
                        "-c", "copy", args.salida], check=True)
    else:
        cmd = ["ffmpeg", "-y", "-v", "error"]
        for s in segs:
            cmd += ["-i", s]
        fc = []
        total = SEG
        prev = "[0:v]"
        for i in range(1, len(segs)):
            out = f"[x{i}]"
            off = total - XFADE
            fc.append(f"{prev}[{i}:v]xfade=transition=fade:"
                      f"duration={XFADE}:offset={off}{out}")
            prev = out
            total += SEG - XFADE
        cmd += ["-filter_complex", ";".join(fc), "-map", prev,
                "-pix_fmt", "yuv420p", "-r", "30", "-t", str(total),
                args.salida]
        subprocess.run(cmd, check=True)

    # Limpieza de segmentos
    for s in segs:
        os.remove(s)
    try:
        os.rmdir(tmp)
    except OSError:
        pass
    tam = os.path.getsize(args.salida)
    print(f"video: {args.salida} ({tam/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
