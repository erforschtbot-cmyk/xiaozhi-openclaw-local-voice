"""Regressionstest fuer die Weckwort-Schiedsrichter-Logik.

Belegter Fehler (24.10.2026, 01:36 im Betrieb): Das Wohnzimmer-Geraet gewann
mit peak=15611 und verschwand danach ohne sauberes close() (keepalive timeout).
Der Peak blieb stehen. Jeder spaetere Anspruch des Schlafzimmers (9185-14998)
verlor gegen diesen toten Gewinner, obwohl das Wohnzimmer laengst nichts mehr
tat. Ergebnis: "Wake arbitration lost" und ein verworfener Befehl.

Der Test haelt beide Ursachen fest:
  1. Ein beendeter Dialog gibt den Gewinner frei (release).
  2. Ein Gewinner, der sich lange nicht meldet, blockiert nicht ewig (stale).
"""
import asyncio
import unittest

from server import WakeArbiter


class FakeSession:
    def __init__(self, name):
        self.session_id = f"session-{name}"
        self.tag = f"[{name}]"


class WakeArbiterRegression(unittest.IsolatedAsyncioTestCase):
    async def test_louder_device_wins(self):
        arbiter = WakeArbiter()
        loud = FakeSession("loud")
        quiet = FakeSession("quiet")
        self.assertTrue(await arbiter.claim(loud, 15000))
        self.assertFalse(await arbiter.claim(quiet, 9000))

    async def test_release_frees_dialog_for_other_device(self):
        """Der belegte Fall: Gewinner beendet den Dialog, leiseres Geraet darf."""
        arbiter = WakeArbiter()
        loud = FakeSession("loud")
        quiet = FakeSession("quiet")
        arbiter.register(loud)
        arbiter.register(quiet)

        self.assertTrue(await arbiter.claim(loud, 15611))
        self.assertFalse(await arbiter.claim(quiet, 9185))

        # Dialog des lauten Geraets endet -> Gewinner freigegeben.
        arbiter.release(loud)

        self.assertTrue(
            await arbiter.claim(quiet, 9185),
            "Nach dem Dialogende muss das leisere Geraet gewinnen duerfen",
        )

    async def test_release_of_non_winner_keeps_current(self):
        """Ein Verlierer, der in Bereitschaft geht, darf den Gewinner nicht kippen."""
        arbiter = WakeArbiter()
        loud = FakeSession("loud")
        quiet = FakeSession("quiet")
        arbiter.register(loud)
        arbiter.register(quiet)

        self.assertTrue(await arbiter.claim(loud, 15000))
        arbiter.release(quiet)  # Verlierer raeumt auf

        # Gewinner bleibt: gleiches Geraet gewinnt weiterhin, auch leise.
        self.assertTrue(await arbiter.claim(loud, 100))

    async def test_stale_winner_does_not_block_forever(self):
        """Verschwundener Gewinner ohne close() darf nicht dauerhaft sperren."""
        arbiter = WakeArbiter()
        ghost = FakeSession("ghost")
        other = FakeSession("other")
        arbiter.register(ghost)
        arbiter.register(other)

        self.assertTrue(await arbiter.claim(ghost, 15611))

        # Kein release, kein unregister: Geraet ist einfach weg.
        arbiter._winner_at -= (arbiter.STALE_SECONDS + 1)

        self.assertTrue(
            await arbiter.claim(other, 9185),
            "Ein toter Gewinner darf nach STALE_SECONDS nicht mehr blockieren",
        )

    async def test_unregister_releases_winner(self):
        arbiter = WakeArbiter()
        gone = FakeSession("gone")
        other = FakeSession("other")
        arbiter.register(gone)
        arbiter.register(other)

        self.assertTrue(await arbiter.claim(gone, 20000))
        arbiter.unregister(gone)

        self.assertTrue(await arbiter.claim(other, 5000))

    async def test_superseded_device_is_sent_to_standby(self):
        """Kommt ein lauteres Geraet, wird der alte Gewinner in Bereitschaft geschickt."""
        arbiter = WakeArbiter()

        class Tracking(FakeSession):
            def __init__(self, name):
                super().__init__(name)
                self.standby_reasons = []

            async def return_to_standby(self, reason="standby"):
                self.standby_reasons.append(reason)

        first = Tracking("first")
        louder = Tracking("louder")
        arbiter.register(first)
        arbiter.register(louder)

        self.assertTrue(await arbiter.claim(first, 8000))
        self.assertTrue(await arbiter.claim(louder, 20000))

        self.assertEqual(first.standby_reasons, ["superseded_by_louder_device"])


if __name__ == "__main__":
    unittest.main()
