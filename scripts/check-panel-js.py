#!/usr/bin/env python3
"""Prüft das ausgelieferte JavaScript der Geräteverwaltung.

Warum es das gibt: Zweimal (27.10. und 28.10.2026) blieb die Seite leer, weil
im Python-String ein einzelnes \\n stand. Python hat daraus beim Ausliefern
einen ECHTEN Zeilenumbruch mitten im JavaScript-String gemacht - die Datei war
syntaktisch kaputt, render() lief nie, der Nutzer sah nur Kopfzeile und
Formular.

Der Fehler war jedes Mal unsichtbar, weil `node --check` gegen die QUELLE lief,
nicht gegen das, was der Browser bekommt. Diese Prüfung nimmt genau den Text,
den der Server ausliefert.

Aufruf:
    python3 scripts/check-panel-js.py
Rückgabe 0 = in Ordnung, 1 = Fehler gefunden.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ZIEL = os.path.join(HERE, "..", "local-smarthome")


def js_aus_seite(seite: str) -> str:
    if "<script>" not in seite or "</script>" not in seite:
        raise SystemExit("FEHLER: kein <script>-Block in der Seite")
    return seite.split("<script>", 1)[1].split("</script>", 1)[0]


def main() -> int:
    sys.path.insert(0, os.path.abspath(ZIEL))
    try:
        import panel  # noqa: PLC0415
    except Exception as exc:  # noqa: BLE001
        print(f"FEHLER: panel.py nicht importierbar: {exc!r}")
        return 1

    seite = panel.PAGE
    js = js_aus_seite(seite)
    print(f"Seite: {len(seite)} Zeichen, JavaScript: {len(js)} Zeichen")

    fehler = 0

    # 1) Einzelne Escape-Sequenzen im Quelltext als HINWEIS melden.
    #
    #    Daraus entstand der Fehler zweimal: im Python-String stand \n
    #    (einfach), Python machte beim Ausliefern einen echten Zeilenumbruch
    #    daraus, und das JavaScript war mitten im String zerrissen.
    #    Ein Hinweis genügt hier - die verbindliche Prüfung ist node --check
    #    weiter unten, weil nur sie Regex-Literale und maskierte
    #    Anführungszeichen korrekt unterscheidet.
    #
    #    Eigene Zeichenscanner haben hier zweimal Fehlalarme geliefert
    #    (28.10.2026: zehn gemeldete Fehler bei gültiger Datei). Deshalb
    #    bewusst kein zweiter Parser.
    quelle = os.path.join(os.path.abspath(ZIEL), "panel.py")
    try:
        with open(quelle, encoding="utf-8") as handle:
            roh = handle.read()
        block = roh[roh.index("<script>") + 8: roh.index("</script>")]
        einzel = [m for m in re.finditer(r"(?<!\\)\\(n|t|r)", block)]
        if einzel:
            print(f"  HINWEIS: {len(einzel)} einzelne Escape-Sequenz(en) im "
                  "Quelltext:")
            for m in einzel[:5]:
                qz = block[:m.start()].count("\n") + 1
                print(f"           Quellzeile {qz}: {m.group(0)!r} "
                      "(wird beim Ausliefern zu einem echten Zeichen)")
    except (OSError, ValueError) as exc:
        print(f"  HINWEIS: Quelltext nicht prüfbar: {exc!r}")

    # 2) VERBINDLICH: Syntaxprüfung gegen den AUSGELIEFERTEN Text.
    #    Das ist der Text, den der Browser bekommt - nicht die Quelle.
    #    Genau diese Verwechslung hat den Fehler zweimal übersehen lassen.
    node = shutil.which("node")
    if node is None:
        print("  HINWEIS: node fehlt, Syntaxprüfung übersprungen")
    else:
        fd, pfad = tempfile.mkstemp(suffix=".js")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(js)
            ergebnis = subprocess.run([node, "--check", pfad],
                                      capture_output=True, text=True)
            if ergebnis.returncode != 0:
                print("  FEHLER: ausgelieferte JavaScript-Syntax ist kaputt")
                print("         " + (ergebnis.stderr or "").strip().split("\n")[0])
                fehler += 1
            else:
                print("  ausgelieferte JavaScript-Syntax: gültig")
        finally:
            try:
                os.unlink(pfad)
            except OSError:
                pass

    if fehler:
        print(f"\n{fehler} Problem(e) gefunden - die Seite bliebe beim Nutzer leer.")
        return 1
    print("\nIn Ordnung.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
