"""Wetterkarte: aktuelle Lage oder Vorhersage fuer morgen.

Daten (alle optional, kommen vom Agenten):
    titel      "Morgen" / "Heute"
    ort        "Arnsberg"
    temp       Zahl in Grad (gross angezeigt)
    lage       "leichter Regen"
    spanne     "6-19 Grad"
    zeilen     Liste aus [Bezeichnung, Temperatur, Zustand]
    symbol     "sonne" | "wolken" | "regen" | "schnee" | "gewitter"
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import ImageDraw

import palette as P

SYMBOLS = {
    "sonne": (P.SUN, P.SUN),
    "wolken": (P.CLOUD_LIGHT, P.CLOUD),
    "regen": (P.CLOUD_LIGHT, P.RAIN),
    "schnee": (P.CLOUD_LIGHT, (222, 236, 250)),
    "gewitter": (P.CLOUD, P.SUN),
}


def _symbol(draw, cx, cy, kind, size):
    primary, secondary = SYMBOLS.get(kind, SYMBOLS["wolken"])
    r = size // 2
    if kind == "sonne":
        draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=primary)
        return
    # Sonne hinter Wolke bei "gewitter", sonst nur Wolke
    if kind == "gewitter":
        draw.ellipse([cx - r, cy - r - 4, cx + r, cy + r - 4], fill=secondary)
    draw.ellipse([cx - r, cy - r // 2, cx + r // 2, cy + r // 2], fill=primary)
    draw.ellipse([cx - r // 3, cy - r, cx + r, cy + r // 3], fill=primary)
    draw.ellipse([cx - r, cy - r // 4, cx + r, cy + r // 2], fill=primary)
    if kind in ("regen", "schnee"):
        for i in range(3):
            x = cx - r // 2 + i * (r // 2)
            draw.line([(x, cy + r // 2 + 6), (x - 4, cy + r // 2 + 20)],
                      fill=secondary, width=3)


def render(data, size, load_font):
    titel = str(data.get("titel", ""))
    ort = str(data.get("ort", ""))
    temp = data.get("temp")
    lage = str(data.get("lage", ""))
    spanne = str(data.get("spanne", ""))
    symbol = str(data.get("symbol", "wolken"))
    zeilen = data.get("zeilen") or []

    img = P.gradient(size)
    draw = ImageDraw.Draw(img)

    f_small = load_font(20)
    f_mid = load_font(26)
    f_huge = load_font(112)
    f_tiny = load_font(17)

    pad = 30
    # Kopfzeile: Ort gehoert zum Titel, nicht in die rechte obere Ecke — dort
    # sitzt das Wettersymbol.
    kopf = titel
    if ort:
        kopf = f"{titel} \u00b7 {ort}" if titel else ort
    if kopf:
        P.text(draw, (pad, 26), kopf, f_small, P.TEXT_DIM)

    if temp is not None:
        P.text(draw, (pad - 4, 46), f"{temp}", f_huge, P.TEXT)
        # Grad-Zeichen kleiner und hochgestellt: die Riesenziffer bleibt die
        # Hauptaussage, das Zeichen nur Zusatz.
        P.text(draw, (pad + f_huge.getlength(f"{temp}") + 4, 62), "\u00b0",
               load_font(34), P.TEXT_DIM)

    _symbol(draw, size - pad - 26, 58, symbol, 56)

    text_x = int(size * 0.52)
    if lage:
        P.text(draw, (text_x, 96), lage, f_mid, P.TEXT)
    if spanne:
        P.text(draw, (text_x, 134), spanne, f_mid, P.TEXT_DIM)

    P.hairline(draw, pad, 186, size - pad)

    row_y = 208
    step = 58
    for eintrag in zeilen[:4]:
        try:
            name, wert, zustand = (list(eintrag) + ["", "", ""])[:3]
        except TypeError:
            continue
        P.text(draw, (pad, row_y), str(name), f_mid, P.TEXT)
        P.text(draw, (int(size * 0.44), row_y), str(wert), f_mid, P.TEXT)
        P.text(draw, (int(size * 0.62), row_y), str(zustand), f_mid, P.TEXT_DIM)
        row_y += step

    if data.get("fuss"):
        P.text(draw, (pad, size - 34), str(data["fuss"]), f_tiny, P.TEXT_FAINT)

    return img
