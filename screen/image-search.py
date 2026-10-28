#!/usr/bin/env python3
"""Sucht ein Bild bei Brave Images und zeigt es auf dem Geraet.

    ./image-search.py "Elefant in der Savanne"
    ./image-search.py "Wetter" --index 2 --no-push

Ablauf: Brave-Images-Abfrage -> Trefferbild laden -> auf 480x480 zuschneiden
und nach RGB565 wandeln -> ueber show.py aufs Display schieben.

Warum nicht einfach die Treffer-URL ans Geraet geben: die Quellbilder sind oft
mehrere MB gross und nicht quadratisch. Das Geraet kann nur Rohdaten mit exakt
480*480*2 Byte anzeigen. Die Umwandlung passiert deshalb hier auf dem Host.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
SHOW = BASE / "show.py"
OUT = BASE / "out"
SECRETS = Path.home() / ".openclaw" / "secrets.json"
BRAVE_ENDPOINT = "https://api.search.brave.com/res/v1/images/search"
# Brave verlangt einen erkennbaren Client; ohne UA antworten manche CDNs mit 403.
USER_AGENT = "OpenClaw-Display/1.0 (personal room display)"


# Freie Quellen: kein Wasserzeichen, Nutzung erlaubt. Diese zuerst.
FREE_SOURCES = (
    "pixabay.com",
    "pexels.com",
    "unsplash.com",
    "wikimedia.org",          # deckt commons.wikimedia.org mit ab
    "openverse.org",
    "nasa.gov",
)

# Bezahl-Stock: geliefert wird die wasserzeichenbehaftete Vorschau. Belegt am
# 28.10.2026 ("Zeige einen Elefanten"): Treffer 1 war ein Dreamstime-Bild mit
# "dreamstime"-Schriftzug quer ueber dem Motiv. Brauchbar nur als Notnagel.
STOCK_SOURCES = (
    "dreamstime.com",
    "istockphoto.com",
    "gettyimages.com",
    "shutterstock.com",
    "alamy.com",
    "stock.adobe.com",
    "adobe.com",
    "123rf.com",
    "depositphotos.com",
    "fotolia.com",
    "colourbox.com",
    "imago-images.de",
    "agefotostock.com",
    "stock.adobe",
)


def source_domain(result: dict) -> str:
    """Domain des Treffers, ohne www und Port."""
    raw = ""
    if isinstance(result, dict):
        raw = str(result.get("source") or "")
        if not raw:
            raw = str(result.get("url") or "")
    raw = raw.strip().lower()
    if "//" in raw:
        raw = raw.split("//", 1)[1]
    raw = raw.split("/", 1)[0].split(":", 1)[0]
    return raw[4:] if raw.startswith("www.") else raw


def brand_of(domain: str) -> str:
    """Markenkern ohne Laenderendung: 'gettyimages.de' -> 'gettyimages'.

    Belegt am 28.10.2026: Brave liefert deutsche Quellen mit .de
    (gettyimages.de), waehrend die Liste .com enthielt. Ein reiner
    Stringvergleich uebersah sie, und ein Getty-Bild mit Wasserzeichen galt
    als "unbekannt". Deshalb wird die Endung vor dem Vergleich entfernt.
    """
    parts = domain.split(".")
    if len(parts) >= 2:
        # "stock.adobe.com" -> "adobe", "de.dreamstime.com" -> "dreamstime"
        return parts[-2] if parts[-1] not in ("uk", "de", "at", "ch") or len(parts) < 3 \
            else parts[-3]
    return domain


def classify_source(domain: str) -> str:
    """'frei', 'stock' oder 'unbekannt'."""
    brand = brand_of(domain)
    for item in FREE_SOURCES:
        if domain == item or domain.endswith("." + item) or brand == item.split(".")[0]:
            return "frei"
    for item in STOCK_SOURCES:
        if domain == item or domain.endswith("." + item) or brand == item.split(".")[0]:
            return "stock"
    return "unbekannt"


def order_results(results: list[dict], allow_stock: bool) -> list[dict]:
    """Freie Quellen nach vorn, Stock nach hinten (oder ganz raus).

    Die Reihenfolge von Brave ist bei allgemeinen Begriffen von bezahlten
    Stockportalen gepraegt: bei "Elefant" standen fuenf von acht Treffern bei
    Dreamstime/iStock/Getty. Alle tragen Wasserzeichen. Deshalb wird
    umsortiert, nicht blind der erste Treffer genommen.
    """
    free: list[dict] = []
    unknown: list[dict] = []
    stock: list[dict] = []
    for result in results:
        kind = classify_source(source_domain(result))
        (free if kind == "frei" else unknown if kind == "unbekannt" else stock).append(result)
    if allow_stock:
        return free + unknown + stock
    return free + unknown


def brave_key() -> str:
    try:
        data = json.loads(SECRETS.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise SystemExit(f"secrets.json nicht lesbar: {exc}")
    entry = data.get("brave")
    key = entry.get("apiKey") if isinstance(entry, dict) else None
    if not key:
        raise SystemExit("Kein Brave-Schluessel unter secrets.json -> brave.apiKey")
    return key


def search(query: str, count: int = 8, safe: bool = True) -> list[dict]:
    params = {"q": query, "count": str(count), "country": "DE"}
    if safe:
        params["safesearch"] = "strict"
    url = BRAVE_ENDPOINT + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Accept-Encoding": "gzip",
        "X-Subscription-Token": brave_key(),
    })
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw = response.read()
            if response.headers.get("Content-Encoding") == "gzip":
                import gzip
                raw = gzip.decompress(raw)
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"Brave meldet HTTP {exc.code}: {body}")
    except urllib.error.URLError as exc:
        raise SystemExit(f"Brave nicht erreichbar: {exc.reason}")
    data = json.loads(raw.decode("utf-8"))
    return data.get("results") or []


def image_candidates(result: dict) -> list[str]:
    """Bild-URLs in Qualitaetsreihenfolge: Original zuerst, Thumbnail danach.

    Belegt am 28.10.2026: `properties.url` lieferte das Original mit 1280x663
    (266 KB), das Thumbnail nur 500x259 (56 KB). Aus 259 px Hoehe auf 480 zu
    skalieren kostet sichtbar Schaerfe. Das Thumbnail bleibt als Rueckfall,
    weil `properties.url` bei manchen Anbietern auf eine HTML-Seite zeigt --
    das faellt erst beim Oeffnen auf, deshalb entscheidet Pillow, nicht die
    Dateiendung.
    """
    candidates: list[str] = []
    properties = result.get("properties") if isinstance(result, dict) else None
    if isinstance(properties, dict):
        url = properties.get("url")
        if isinstance(url, str) and url.startswith("http"):
            candidates.append(url)
    thumbnail = result.get("thumbnail") if isinstance(result, dict) else None
    if isinstance(thumbnail, dict):
        url = thumbnail.get("src")
        if isinstance(url, str) and url.startswith("http") and url not in candidates:
            candidates.append(url)
    return candidates


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=25) as response:
        return response.read()


def download(candidates: list[str]) -> tuple[Path, str]:
    """Laedt den ersten Kandidaten, den Pillow wirklich als Bild erkennt."""
    from PIL import Image
    import io

    errors: list[str] = []
    for url in candidates:
        try:
            raw = _fetch(url)
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__} bei {url[:60]}")
            continue
        try:
            with Image.open(io.BytesIO(raw)) as probe:
                size = probe.size
        except Exception as exc:  # noqa: BLE001
            # Typisch: properties.url war eine HTML-Seite des Anbieters.
            errors.append(f"kein Bild ({type(exc).__name__}) bei {url[:60]}")
            continue
        OUT.mkdir(parents=True, exist_ok=True)
        target = OUT / "suche-quelle.img"
        target.write_bytes(raw)
        print(f"Bild:    {size[0]}x{size[1]}, {len(raw) / 1024:.0f} KB")
        return target, url

    detail = "; ".join(errors) or "keine Kandidaten"
    raise SystemExit(f"Kein verwendbares Bild gefunden: {detail}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("query", help="Suchbegriff, z. B. 'Elefant'")
    parser.add_argument("--index", type=int, default=0,
                        help="welcher Treffer (0 = erster)")
    parser.add_argument("--no-safe", action="store_true",
                        help="SafeSearch aus (nicht empfohlen)")
    parser.add_argument("--no-push", action="store_true",
                        help="nur herunterladen und umwandeln, nicht senden")
    parser.add_argument("--list", action="store_true",
                        help="nur die Trefferliste zeigen")
    parser.add_argument("--any", action="store_true",
                        help="auch Bezahl-Stock zulassen (Wasserzeichen!)")
    known, rest = parser.parse_known_args()

    results = search(known.query, safe=not known.no_safe)
    if not results:
        print("Keine Treffer.", file=sys.stderr)
        return 1

    original_count = len(results)
    results = order_results(results, allow_stock=known.any)
    if not results:
        # Nur Stock gefunden: lieber mit Hinweis ausliefern als gar nichts.
        print("Nur Bezahl-Stock gefunden (Wasserzeichen moeglich); "
              "nutze --any fuer bewusste Wahl.", file=sys.stderr)
        results = order_results(search(known.query, safe=not known.no_safe),
                                allow_stock=True)
    if len(results) < original_count:
        print(f"Quellenfilter: {original_count - len(results)} Stock-Treffer "
              f"uebersprungen (Wasserzeichen)")

    if known.list:
        for number, result in enumerate(results, start=1):
            title = (result.get("title") or "").split("|")[0].strip()
            domain = source_domain(result)
            kind = classify_source(domain)
            mark = {"frei": "frei ", "stock": "STOCK", "unbekannt": "     "}[kind]
            print(f"{number:2}. [{mark}] {title[:60]:60} {domain}")
        return 0

    if not 0 <= known.index < len(results):
        raise SystemExit(f"--index {known.index} liegt ausserhalb (1..{len(results)})")

    result = results[known.index]
    candidates = image_candidates(result)
    if not candidates:
        raise SystemExit("Treffer enthaelt keine Bild-URL.")
    domain = source_domain(result)
    kind = classify_source(domain)
    print(f"Treffer: {(result.get('title') or '')[:70]}")
    print(f"Quelle:  {domain} ({kind})")

    source, used_url = download(candidates)
    print(f"URL:     {used_url[:100]}")
    command = [sys.executable, str(SHOW), "--file", str(source)]
    # show.py kennt --no-push, --serve-host usw.; unbekannte Argumente des
    # Aufrufers werden durchgereicht, damit Host und Port gesetzt werden koennen.
    command += rest
    if known.no_push:
        command.append("--no-push")
    completed = subprocess.run(command, capture_output=True, text=True, timeout=90)
    sys.stdout.write(completed.stdout)
    if completed.returncode != 0:
        sys.stderr.write(completed.stderr)
        return completed.returncode
    return 0


if __name__ == "__main__":
    sys.exit(main())
