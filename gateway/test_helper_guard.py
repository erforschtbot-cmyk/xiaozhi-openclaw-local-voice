"""Provider/helper failures must return the device to standby."""
import asyncio
import unittest
from types import SimpleNamespace

from server import XiaozhiSession


def make_session(*, ready=False, turn_peak=0, state="listening", tts_started=False):
    session = XiaozhiSession.__new__(XiaozhiSession)
    session.helper_guard = None
    session.process_watch_task = None
    session.helper_ready = asyncio.Event()
    if ready:
        session.helper_ready.set()
    session.transcript_final_at = 0.0
    session.input_stopped = False
    session.tag = "[test]"
    session.HELPER_START_TIMEOUT_SECONDS = 0.02
    session.turn_peak = int(turn_peak)
    session.device_state = state
    session.tts_started = tts_started
    session.pending_tool = False
    session.process = SimpleNamespace(returncode=None)
    session.followup_seconds = lambda: 0.05
    session.tts_started = False
    session.pending_tool = False
    session.last_mic_activity_at = 0.0
    session.speech_seen_since_wake = False
    reasons = []

    async def return_to_standby(reason):
        reasons.append(reason)
        session.input_stopped = True

    session.return_to_standby = return_to_standby
    return session, reasons


class HelperGuardBehaviour(unittest.IsolatedAsyncioTestCase):
    async def test_start_timeout_recycles_connection(self):
        session, reasons = make_session()
        session.arm_helper_guard(session.process)
        await asyncio.wait_for(session.helper_guard, timeout=1)
        self.assertEqual(reasons, ["helper_start_timeout"])

    async def test_silence_after_wake_returns_to_standby(self):
        # Genau der Fall vom 22.10.2026: Wake-Wort, dann nichts gesagt. Vorher
        # blieb das Geraet dauerhaft gruen, weil nur das Nachhoerfenster NACH
        # einer Antwort eine Frist hatte.
        session, reasons = make_session(ready=True)
        session.arm_helper_guard(session.process)
        await asyncio.wait_for(session.helper_guard, timeout=2)
        self.assertEqual(reasons, ["silence_after_wake"])

    async def test_detected_speech_stops_the_silence_rule(self):
        # Sprache erkannt (auch ohne Transkript): die Stille-Regel darf nicht
        # mehr greifen. Belegt 22.10.2026: Sprache 22:17:49 peak=1383,
        # Abbruch 22:17:54, weil die Frist weiterlief.
        session, reasons = make_session(ready=True)
        session.speech_seen_since_wake = True
        session.arm_helper_guard(session.process)
        await asyncio.wait_for(session.helper_guard, timeout=2)
        self.assertEqual(reasons, [])

    async def test_recognized_word_keeps_the_turn_open(self):
        session, reasons = make_session(ready=True)
        session.transcript_final_at = 1.0
        session.arm_helper_guard(session.process)
        await asyncio.wait_for(session.helper_guard, timeout=2)
        self.assertEqual(reasons, [])

if __name__ == "__main__":
    unittest.main()
