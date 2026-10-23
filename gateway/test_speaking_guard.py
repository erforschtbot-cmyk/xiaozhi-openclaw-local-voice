"""Speaking guard: real asyncio timing, no global mocking, no devices."""
import asyncio
import unittest
from types import SimpleNamespace
from server import XiaozhiSession


def make_session(**overrides):
    s = XiaozhiSession.__new__(XiaozhiSession)
    s.speaking_guard = None
    s.stream_end_sent = False
    s.tts_started = False
    s.pending_tool = False
    s.pending_tool_since = 0.0
    s.last_sent_audio_at = 0.0
    s.input_stopped = False
    s.tag = "[test]"
    s.SPEAKING_TOOL_MAX_SECONDS = 0.1
    s.SPEAKING_STALL_SECONDS = 0.1
    sent = []

    async def send_json(payload):
        sent.append(payload)

    s.send_json = send_json
    s.websocket = SimpleNamespace(transport=SimpleNamespace(abort=lambda: None))
    for key, value in overrides.items():
        setattr(s, key, value)
    return s, sent


class GuardBehaviour(unittest.IsolatedAsyncioTestCase):
    async def test_tool_without_audio_is_closed(self):
        s, sent = make_session(pending_tool=True, pending_tool_since=-5.0)
        s.arm_speaking_guard()
        await asyncio.wait_for(s.speaking_guard, timeout=5)
        self.assertTrue(s.input_stopped, "microphone must not stay blocked")
        self.assertEqual(sent, [], "nothing spoken, so no tts stop")

    async def test_idle_round_without_tool_exits_quietly(self):
        s, sent = make_session()
        s.arm_speaking_guard()
        await asyncio.wait_for(s.speaking_guard, timeout=5)
        self.assertFalse(s.input_stopped)
        self.assertEqual(sent, [])

    async def test_spoken_round_gets_tts_stop(self):
        s, sent = make_session(tts_started=True,
                               last_sent_audio_at=asyncio.get_running_loop().time() - 5)
        s.arm_speaking_guard()
        await asyncio.wait_for(s.speaking_guard, timeout=5)
        self.assertEqual(sent, [{"type": "tts", "state": "stop"}])
        self.assertTrue(s.input_stopped)

    async def test_stream_end_stops_guard(self):
        s, sent = make_session(tts_started=True,
                               last_sent_audio_at=asyncio.get_running_loop().time() - 5)
        s.stream_end_sent = True
        s.arm_speaking_guard()
        await asyncio.wait_for(s.speaking_guard, timeout=5)
        self.assertEqual(sent, [])
        self.assertFalse(s.input_stopped)


if __name__ == "__main__":
    unittest.main()
