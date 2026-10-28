"""Allzweck-Karte: Überschrift plus beliebig viele Zeilen.

Damit geht jede Antwort ohne eigenes Layout — Kalender, Einkaufsliste,
Statusmeldungen. Das ist die Vorlage, die man nimmt, wenn man schnell etwas
auf den Bildschirm bringen will.

Daten:
    titel     Überschrift (gross)
    untertitel kleine Zeile darunter
    zeilen    Liste aus Zeichenketten oder [links, rechts]
    fuss      kleine Zeile unten
    akzent    "blau" | "gruen" | "rot" | "gelb" — Farbstreifen oben
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import ImageDraw

import palette as P

AKZENTE = {
    "blau": P.ACCENT,
    "gruen": (74, 222, 128),
    "rot": (248, 113, 113),
    "gelb": (250, 204, 21),
}


def render(data, size, load_font):
    img = P.gradient(size)
    draw = ImageDraw.Draw(img)

    akzent = AKZENTE.get(str(data.get("akzent", "blau")), P.ACCENT)
    draw.rectangle([0, 0, size, 6], fill=akzent)

    pad = 30
    titel = str(data.get("titel", ""))
    untertitel = str(data.get("untertitel", ""))
    fuss = str(data.get("fuss", ""))
    zeilen = data.get("zeilen") or []

    f_titel = load_font(40)
    f_sub = load_font(21)
    f_zeile = load_font(25)
    f_fuss = load_font(17)

    y = 40
    if titel:
        # Lange Titel umbrechen statt abschneiden: eine Karte mit
        # abgeschnittenem Wort ist schlimmer als eine kleinere Schrift.
        worte = titel.split()
        zeile = ""
        for wort in worte:
            probe = f"{zeile} {wort}".strip()
            if f_titel.getlength(probe) <= size - 2 * pad or not zeile:
                zeile = probe
            else:
                P.text(draw, (pad, y), zeile, f_titel, P.TEXT)
                y += 46
                zeile = wort
        if zeile:
            P.text(draw, (pad, y), zeile, f_titel, P.TEXT)
            y += 52

    if untertitel:
        P.text(draw, (pad, y), untertitel, f_sub, P.TEXT_DIM)
        y += 38

    if titel or untertitel:
        P.hairline(draw, pad, y, size - pad)
        y += 22

    platz_unten = 60 if fuss else 24
    for eintrag in zeilen:
        if y > size - platz_unten - 26:
            break
        if isinstance(eintrag, (list, tuple)):
            links = str(eintrag[0]) if len(eintrag) > 0 else ""
            rechts = str(eintrag[1]) if len(eintrag) > 1 else ""
            P.text(draw, (pad, y), links, f_zeile, P.TEXT)
            if rechts:
                P.text(draw, (size - pad, y), rechts, f_zeile, P.TEXT_DIM, anchor="ra")
        else:
            P.text(draw, (pad, y), str(eintrag), f_zeile, P.TEXT)
        y += 44

    if fuss:
        P.text(draw, (pad, size - 36), fuss, f_fuss, P.TEXT_FAINT)

    return img
