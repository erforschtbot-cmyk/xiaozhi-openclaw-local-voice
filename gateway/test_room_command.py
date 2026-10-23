"""Tests fuer die kleine Raum-Regel: nur Licht/Steckdose, nur an/aus."""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from room_command import apply_room, has_room  # noqa: E402

ROOMS = ["Wohnzimmer", "Arbeitszimmer", "Schlafzimmer", "Küche", "Badezimmer"]


class SollUmstellen(unittest.TestCase):
    def check(self, text, expected, room="Wohnzimmer"):
        out, changed, reason = apply_room(text, room, ROOMS)
        self.assertTrue(changed, f"{text!r} haette umgestellt werden muessen ({reason})")
        self.assertEqual(out, expected, text)

    def test_licht_an(self):
        self.check("Licht an", "Wohnzimmer Licht an")

    def test_licht_aus(self):
        self.check("Licht aus", "Wohnzimmer Licht aus")

    def test_klein_geschrieben(self):
        self.check("licht an", "Wohnzimmer Licht an")

    def test_mit_verb(self):
        self.check("Schalte das Licht an", "Wohnzimmer Licht an")

    def test_bitte(self):
        self.check("bitte Licht an", "Wohnzimmer Licht an")

    def test_bitte_und_verb(self):
        self.check("Bitte schalte das Licht aus", "Wohnzimmer Licht aus")

    def test_mit_punkt(self):
        self.check("Mach das Licht an.", "Wohnzimmer Licht an.")

    def test_steckdose_an(self):
        self.check("Steckdose an", "Wohnzimmer Steckdose an")

    def test_steckdose_aus(self):
        self.check("schalte die Steckdose aus", "Wohnzimmer Steckdose aus")

    def test_ein_wird_an(self):
        self.check("Licht ein", "Wohnzimmer Licht an")

    def test_arbeitszimmer(self):
        self.check("Licht an", "Arbeitszimmer Licht an", room="Arbeitszimmer")

    def test_kueche_umlaut(self):
        self.check("Licht aus", "Küche Licht aus", room="Küche")


class SollUnveraendertBleiben(unittest.TestCase):
    def check(self, text, room="Wohnzimmer"):
        out, changed, reason = apply_room(text, room, ROOMS)
        self.assertFalse(changed, f"{text!r} wurde umgestellt: {out!r} ({reason})")
        self.assertEqual(out, text)

    def test_raum_genannt(self):
        self.check("Licht Wohnzimmer an")

    def test_raum_mit_praeposition(self):
        self.check("Schalte das Licht im Wohnzimmer aus")

    def test_anderer_raum(self):
        self.check("Licht im Schlafzimmer an")

    def test_eigener_geraetename(self):
        self.check("Wegbeleuchtung an")

    def test_monitorlicht(self):
        self.check("Monitorlicht an")

    def test_monitor(self):
        self.check("Monitor aus")

    def test_mehrere_aktionen(self):
        self.check("Licht an und Steckdose aus")

    def test_bedingung(self):
        self.check("Licht an wenn ich komme")

    def test_zeit(self):
        self.check("Licht an in 10 Sekunden")

    def test_frage(self):
        self.check("Ist das Licht an?")

    def test_verneinung(self):
        self.check("Licht nicht an")

    def test_kein_befehl(self):
        self.check("Erzähle einen Witz")

    def test_dimmen_noch_nicht(self):
        self.check("Licht 50 %")

    def test_farbe_noch_nicht(self):
        self.check("Licht blau")

    def test_temperatur_noch_nicht(self):
        self.check("Temperatur")


class Randfaelle(unittest.TestCase):
    def test_kein_raum(self):
        out, changed, reason = apply_room("Licht an", "", ROOMS)
        self.assertFalse(changed)
        self.assertEqual(reason, "no-room")

    def test_leer(self):
        out, changed, _ = apply_room("", "Wohnzimmer", ROOMS)
        self.assertFalse(changed)

    def test_idempotent(self):
        once, c1, _ = apply_room("Licht an", "Wohnzimmer", ROOMS)
        self.assertTrue(c1)
        twice, c2, _ = apply_room(once, "Wohnzimmer", ROOMS)
        self.assertFalse(c2)
        self.assertEqual(twice, once)

    def test_has_room(self):
        self.assertTrue(has_room("licht im wohnzimmer an", ROOMS))
        self.assertFalse(has_room("Licht an", ROOMS))

    def test_nur_zielwort(self):
        out, changed, _ = apply_room("Licht", "Wohnzimmer", ROOMS)
        self.assertFalse(changed)

    def test_unbekannte_aktion(self):
        out, changed, reason = apply_room("Licht irgendwas", "Wohnzimmer", ROOMS)
        self.assertFalse(changed)
        self.assertEqual(reason, "unknown-action")


if __name__ == "__main__":
    unittest.main(verbosity=1)
