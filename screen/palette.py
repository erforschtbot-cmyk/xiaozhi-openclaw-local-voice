"""Gemeinsame Farben und Zeichenhilfen fuer alle Anzeigen.

Eine Aenderung hier wirkt auf alle Vorlagen — das ist der Punkt: das Aussehen
bleibt ueber alle Karten hinweg zusammenhaengend.
"""

BG_TOP = (16, 24, 42)
BG_BOTTOM = (9, 13, 24)
TEXT = (238, 243, 252)
TEXT_DIM = (150, 168, 194)
TEXT_FAINT = (104, 122, 148)
ACCENT = (96, 165, 250)
SUN = (255, 196, 64)
CLOUD = (214, 222, 235)
CLOUD_LIGHT = (238, 243, 250)
RAIN = (125, 176, 235)
LINE = (44, 58, 88)


def gradient(size: int, top=BG_TOP, bottom=BG_BOTTOM):
    from PIL import Image
    img = Image.new("RGB", (size, size), top)
    px = img.load()
    for y in range(size):
        t = y / max(1, size - 1)
        px_row = (
            int(top[0] + (bottom[0] - top[0]) * t),
            int(top[1] + (bottom[1] - top[1]) * t),
            int(top[2] + (bottom[2] - top[2]) * t),
        )
        for x in range(size):
            px[x, y] = px_row
    return img


def text(draw, xy, value, font, fill=TEXT, anchor=None):
    draw.text(xy, str(value), font=font, fill=fill, anchor=anchor)


def hairline(draw, x0, y, x1, fill=LINE, width=2):
    draw.line([(x0, y), (x1, y)], fill=fill, width=width)


def rounded_panel(draw, box, radius=18, fill=None, outline=None, width=2):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)
