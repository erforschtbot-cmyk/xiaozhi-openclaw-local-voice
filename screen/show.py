#!/usr/bin/env python3
"""Rendert eine Anzeige und schiebt sie aufs Geraet.

Das ist der eine Befehl, den man im Alltag braucht:

    ./show.py wetter --data '{"titel":"Morgen","temp":18,"lage":"Regen"}'

Ablauf: Vorlage rendern -> RGB565-Rohdaten ueber den Anzeigen-Server
bereitstellen -> URL an die Bridge geben, die sie dem Geraet uebergibt.
PNG ist nur eine lokale Vorschau. Nichts davon landet im Flash des ESP32.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from PIL import Image

BASE = Path(__file__).resolve().parent
RENDER = BASE / "render.py"
OUT = BASE / "out"


def _fit_on_canvas(image, size: int):
    """Ganzes Bild auf die Displayflaeche, ohne Motivverlust.

    Ein quadratischer Zuschnitt aus einem Querformat verliert zwangslaeufig
    die halbe Breite. Belegt: bei einem Brave-Treffer (500x259) lag der
    Elefant rechts aussen, center-crop zeigte Himmel und Baum, und ein
    Versuch mit Kantenenergie waehlte ebenfalls den Baum (das dichte Geaest
    hat mehr Kanten als ein glatter Elefantentroesel). Deshalb kein Zuschnitt
    mehr: das Bild wird vollstaendig eingepasst, der Rest der Flaeche mit
    einem dunklen Farbton aus dem Bild selbst gefuellt.
    """
    image = image.convert("RGB")
    width, height = image.size
    if width <= 0 or height <= 0:
        raise SystemExit("Bild ohne Masse")

    scale = min(size / width, size / height)
    target_w = max(1, int(round(width * scale)))
    target_h = max(1, int(round(height * scale)))
    resized = image.resize((target_w, target_h), Image.LANCZOS)

    # Hintergrundfarbe: dunkelster Bildrand, damit die Balken nicht als
    # Fremdkoerper wirken. Kein Blur noetig, das kostet nur Zeit.
    small = image.copy()
    small.thumbnail((32, 32), Image.BILINEAR)
    pixels = list(small.getdata())
    darkest = min(pixels, key=lambda p: p[0] + p[1] + p[2])
    background = tuple(int(channel * 0.45) for channel in darkest)

    canvas = Image.new("RGB", (size, size), background)
    canvas.paste(resized, ((size - target_w) // 2, (size - target_h) // 2))
    return canvas


def to_raw(source: Path, size: int = 480, preview: bool = True) -> Path:
    """Beliebiges Bild -> RGB565-Rohdaten in Displaygroesse.

    Fremde Bilder (Foto, Screenshot, Bildsuche) sind selten quadratisch. Das
    Geraet zeigt nur Rohdaten mit exakt passender Bytezahl. Das Bild wird
    vollstaendig eingepasst (kein Beschnitt, kein Motivverlust) und nach
    RGB565 little endian gewandelt.
    """
    with Image.open(source) as opened:
        image = _fit_on_canvas(opened, size)

    OUT.mkdir(parents=True, exist_ok=True)
    if preview:
        # Nur zur Sichtpruefung auf dem Host, geht nicht ans Geraet.
        image.save(OUT / f"{source.stem}-preview.png", format="PNG", optimize=True)

    rgb = image.tobytes()
    buffer = bytearray(size * size * 2)
    for index in range(size * size):
        red, green, blue = rgb[index * 3], rgb[index * 3 + 1], rgb[index * 3 + 2]
        # RGB565: 5 Bit Rot, 6 Bit Gruen, 5 Bit Blau.
        value = ((red & 0xF8) << 8) | ((green & 0xFC) << 3) | (blue >> 3)
        buffer[index * 2] = value & 0xFF
        buffer[index * 2 + 1] = (value >> 8) & 0xFF

    target = OUT / f"{source.stem}-{size}x{size}.raw"
    target.write_bytes(bytes(buffer))
    return target


def render(template: str, data: dict, raw: bool = True, size: int = 480) -> Path:
    """Rendert eine Vorlage. Standard ist RGB565-Rohdaten (.raw): das Geraet
    zeigt sie ohne Dekodierschritt an. PNG blieb auf dem 4B schwarz."""
    out = OUT / f"{template}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [sys.executable, str(RENDER), template, "--out", str(out),
           "--size", str(size)]
    if raw:
        cmd.append("--raw")
    if data:
        cmd += ["--data", json.dumps(data, ensure_ascii=False)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise SystemExit((result.stderr or result.stdout).strip())
    return out.with_suffix(".raw") if raw else out


def call_bridge(bridge: str, params: dict, timeout: float = 20.0) -> dict:
    url = f"{bridge.rstrip('/')}/screen?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")
        raise SystemExit(f"Bridge meldet HTTP {exc.code}: {detail}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Bridge nicht erreichbar unter {bridge}: {exc.reason}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("template", nargs="?", default="", help="Vorlagenname, z. B. wetter")
    parser.add_argument("--data", default="{}", help="JSON-Objekt mit den Anzeigedaten")
    parser.add_argument("--device", default="", help="MAC des Geraets, falls mehrere verbunden")
    parser.add_argument("--bridge", default="http://127.0.0.1:8766")
    parser.add_argument(
        "--serve-host",
        default=os.environ.get("JARVIS_SCREEN_PUBLIC_HOST", "127.0.0.1"),
        help=("Adresse, unter der das Geraet den Host erreicht; die Bridge "
              "schreibt 127.0.0.1 automatisch auf ihre LAN-Adresse um"),
    )
    parser.add_argument("--serve-port", type=int, default=8770)
    parser.add_argument("--no-push", action="store_true", help="nur rendern, nicht senden")
    parser.add_argument("--hide", action="store_true", help="Vollbild ausblenden")
    parser.add_argument("--status", action="store_true",
                        help="nur pruefen, ob gerade angezeigt werden kann")
    parser.add_argument("--file", default="",
                        help="beliebiges Bild senden (wird auf 480x480 skaliert)")
    parser.add_argument("--png", action="store_true",
                        help="PNG senden statt RGB565-Rohdaten (Standard: raw)")
    args = parser.parse_args()

    # Das 4B zeigt Vorlagen verlässlich nur als RGB565-Rohdaten. Modelle haben
    # nach einem früheren Fehler eigenmächtig --png ergänzt; beim echten Push
    # wird das deshalb absichtlich ignoriert. PNG bleibt mit --no-push als
    # lokale Layout-Vorschau verfügbar.
    use_png = args.png and args.no_push
    if args.png and not args.no_push:
        print("Hinweis: --png wird beim Geräte-Push ignoriert; sende RGB565-Rohdaten.",
              file=sys.stderr)

    if args.status:
        result = call_bridge(args.bridge, {"action": "status"})
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result.get("can_show") else 1

    if args.hide:
        print(json.dumps(call_bridge(args.bridge,
                                     {"action": "hide", "device": args.device}),
                         ensure_ascii=False))
        return 0

    if args.file:
        path = Path(args.file).expanduser().resolve()
        if not path.is_file():
            raise SystemExit(f"Datei fehlt: {path}")
        # Fremde Bilder niemals unveraendert durchreichen: ein 8-MB-JPEG
        # sprengt den PSRAM des Geraets. Erst hier auf Rohdaten bringen.
        path = to_raw(path)
        name = path.stem
    else:
        if not args.template:
            raise SystemExit("Vorlage fehlt (oder --file angeben)")
        data = json.loads(args.data) if args.data else {}
        if not isinstance(data, dict):
            raise SystemExit("--data muss ein JSON-Objekt sein")
        path = render(args.template, data, raw=not use_png)
        # Rohdaten tragen die Masse im Namen: <name>-480x480.raw
        name = args.template if use_png else f"{args.template}-480x480"

    suffix = ".png" if use_png else ".raw"
    url = f"http://{args.serve_host}:{args.serve_port}/img/{name}{suffix}"
    print(f"Bild:  {path}")
    print(f"URL:   {url}")
    if args.no_push:
        return 0
    result = call_bridge(args.bridge, {"url": url, "device": args.device})
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("shown") else 1


if __name__ == "__main__":
    sys.exit(main())
