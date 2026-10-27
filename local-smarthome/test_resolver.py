"""Offline-Tests: viele deutsche Formulierungen gegen den echten Katalog."""
import sys
import catalog
import resolver

DEVICES = catalog.load()["devices"]

CASES = [
    # (Satz, erwartetes Geraet oder None, erwartete Aktion)
    ("schalte doch mal bitte den monitor an", "Monitor", "on"),
    ("Monitor an", "Monitor", "on"),
    ("monitor aus", "Monitor", "off"),
    ("mach das monitorlicht aus", "Monitorlicht", "off"),
    ("kannst du bitte das monitorlicht einschalten", "Monitorlicht", "on"),
    ("dreh die deckenstrahler links runter", "Deckenstrahler links", "percent"),
    ("deckenstrahler links auf 30 prozent", "Deckenstrahler links", "percent"),
    ("stell die lampe im schlafzimmer auf 20 prozent", "Lampe Schlafzimmer", "percent"),
    ("lampe schlafzimmer an", "Lampe Schlafzimmer", "on"),
    ("schalte lampe küche ein", "Lampe Küche", "on"),
    ("mach die küche hell", None, None),
    ("steckdose sofa aus", "Steckdose Sofa", "off"),
    ("schalte die steckdose am sofa ab", "Steckdose Sofa", "off"),
    ("ventilator arbeitszimmer an", "Ventilator Arbeitszimmer", "on"),
    ("garten steckdose an", "Garten Steckdose", "on"),
    ("schalte gartenwasser ein", "Gartenwasser", "on"),
    ("hast du den fernseher an", None, None),
    ("ist das licht im arbeitszimmer an", None, None),
    ("warum ist die lampe im bad aus", None, None),
    ("schalte das licht im arbeitszimmer nicht aus", None, None),
    ("mach die jalousie runter", "Jalousie", "off"),
    ("heizung badezimmer an", "Badezimmer Heizung", "on"),
    ("stell die temperatur klimaanlage auf 22 grad", "Temperatur Klimaanlage", "temperature"),
    ("temperatur klima schlafzimmer auf 22 grad", None, None),
    ("schalte den beamer an und das licht aus", None, None),
    ("licht an", None, None),
    ("alles an", None, None),
    ("schalte alle lampen an", "Alle Lampen", "on"),
    ("mach den computer aus", "Computer", "off"),
    ("waschmaschine an", "Waschmaschine", "on"),
    ("starte den kino pc", "KinoPC", "on"),
    ("schalte kinopc ein", "KinoPC", "on"),
    ("monitorlich an", "Monitorlicht", "on"),
    ("schalte doch mal bitte den monitor an!", "Monitor", "on"),
]

def main():
    fails = 0
    for text, want_dev, want_action in CASES:
        r = resolver.resolve(text, DEVICES)
        got_dev = r.device if r.ok else None
        got_act = r.action if r.ok else None
        ok = (got_dev == want_dev) and (got_act == want_action)
        if not ok:
            fails += 1
        mark = "OK " if ok else "FAIL"
        print(f"{mark} {text[:48]:50} -> dev={got_dev!r} act={got_act} ({r.reason})"
              + ("" if ok else f"  ERWARTET {want_dev!r}/{want_action} {r.candidates}"))
    print(f"\n{len(CASES)-fails}/{len(CASES)} korrekt")
    return 1 if fails else 0

if __name__ == "__main__":
    sys.exit(main())
