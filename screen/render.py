#!/usr/bin/env python3
"""Rendert eine Anzeige-Vorlage als PNG in Displaygroesse.

Aufruf:
    render.py <vorlage> [--arg schluessel=wert ...] [--out PFAD] [--size 480]

Jede Vorlage liegt als templates/<vorlage>.py und stellt genau eine Funktion
`render(data, size) -> PIL.Image` bereit. Damit ist eine neue Anzeige eine
neue Datei — am Geraet aendert sich nichts.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE = Path(__file__).resolve().parent
TEMPLATES = BASE / "templates"
PALETTE = BASE / "palette.py"

FONT_CANDIDATES = [
    "/usr/share/fonts/noto/NotoSans-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/TTF/DejaVuSans.ttf",
]


def font_path() -> str:
    for candidate in FONT_CANDIDATES:
        if Path(candidate).is_file():
            return candidate
    raise SystemExit("Keine geeignete Schrift gefunden")


def load_font(size: int) -> ImageFont.FreeTypeFont:
    return ImageFont.truetype(font_path(), size)


def load_template(name: str):
    safe = "".join(c for c in name if c.isalnum() or c in "_-")
    if safe != name:
        raise SystemExit(f"Ungueltiger Vorlagenname: {name!r}")
    path = TEMPLATES / f"{safe}.py"
    if not path.is_file():
        known = ", ".join(sorted(p.stem for p in TEMPLATES.glob("*.py")))
        raise SystemExit(f"Vorlage {name!r} fehlt. Vorhanden: {known}")
    spec = importlib.util.spec_from_file_location(f"vorlage_{safe}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, "render"):
        raise SystemExit(f"Vorlage {name!r} hat keine render()-Funktion")
    return module


def parse_args(raw: list[str]) -> dict:
    data: dict = {}
    for item in raw:
        if "=" not in item:
            raise SystemExit(f"Erwartet schluessel=wert, bekam {item!r}")
        key, value = item.split("=", 1)
        try:
            data[key] = json.loads(value)
        except json.JSONDecodeError:
            data[key] = value
    return data


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("template")
    parser.add_argument("--arg", action="append", default=[])
    parser.add_argument("--data", default="", help="JSON-Objekt statt --arg")
    parser.add_argument("--out", default="")
    parser.add_argument("--size", type=int, default=480)
    parser.add_argument("--raw", action="store_true",
                        help="RGB565-Rohdaten (.raw) statt PNG schreiben")
    args = parser.parse_args()

    data = parse_args(args.arg)
    if args.data:
        extra = json.loads(args.data)
        if not isinstance(extra, dict):
            raise SystemExit("--data muss ein JSON-Objekt sein")
        data.update(extra)

    module = load_template(args.template)
    image = module.render(data, args.size, load_font)
    if image.size != (args.size, args.size):
        image = image.resize((args.size, args.size), Image.LANCZOS)

    out = Path(args.out) if args.out else BASE / "out" / f"{args.template}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    if args.raw:
        # Rohdaten statt PNG: RGB565 little endian, wie das Geraet sie ohne
        # Dekodierschritt direkt anzeigen kann. Genau dieser Weg ist auf dem
        # Board erprobt (Kameravorschau); PNG im Zeichenpfad blieb schwarz.
        # Masse gehoeren in den Namen: die Bridge leitet daraus die
        # Bildgroesse ab, ohne dass das Geraet raten muss.
        raw = out.parent / f"{out.stem}-{args.size}x{args.size}.raw"
        # Pillow kann kein RGB565; die RGB-Bytes selbst umrechnen.
        rgb = image.convert("RGB").tobytes()
        buf = bytearray(len(rgb) // 3 * 2)
        for i in range(len(rgb) // 3):
            r, g, b = rgb[i * 3], rgb[i * 3 + 1], rgb[i * 3 + 2]
            v = ((r & 0xF8) << 8) | ((g & 0xFC) << 3) | (b >> 3)
            buf[i * 2] = v & 0xFF
            buf[i * 2 + 1] = (v >> 8) & 0xFF
        raw.write_bytes(bytes(buf))
        print(raw)
        return 0
    image.save(out, format="PNG", optimize=True)
    print(out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
