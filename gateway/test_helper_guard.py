"""Provider/helper failures must return the device to standby.

Die frueher hier geprueste 5-Sekunden-Stille-Regel („Jarvis", dann nichts
gesagt) wurde am 27.10.2026 entfernt: seit dem Silero-VAD im STT-Dienst kommt
Rauschen gar nicht mehr als Text an. Blieb doch einmal ein Wort uebrig, ist
eine Antwort harmlos — die Frist kostete dagegen bei jedem Wecken 5 s.
"""
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
    session.tts_started = False
    session.pending_tool = False
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

    async def test_recognized_word_keeps_the_turn_open(self):
        session, reasons = make_session(ready=True)
        session.transcript_final_at = 1.0
        session.arm_helper_guard(session.process)
        await asyncio.wait_for(session.helper_guard, timeout=2)
        self.assertEqual(reasons, [])

if __name__ == "__main__":
    unittest.main()
