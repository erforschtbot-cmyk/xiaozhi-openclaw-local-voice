"""Conversation idle deadlines: exercise production methods without devices."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import struct
import unittest
from server import XiaozhiSession

class ConversationTiming(unittest.TestCase):
    def setUp(self):
        self.s = XiaozhiSession.__new__(XiaozhiSession)
        self.s.followup_open = True
        self.s.followup_audio_guard_until = 0
        self.s.followup_last_activity = 0

    def audio(self, amplitude, now):
        self.s.note_followup_audio(struct.pack('<960h', *([amplitude] * 960)), now)

    def test_silence_does_not_keep_connection_alive(self):
        self.audio(0, 9)
        self.assertEqual(self.s.followup_idle_remaining(10), 0)

    def test_low_noise_does_not_extend_window(self):
        self.audio(50, 9)
        self.assertEqual(self.s.followup_idle_remaining(10), 0)

    def test_long_followup_exceeds_original_deadline(self):
        for now in range(8, 69):
            self.audio(600, now)
            self.assertEqual(self.s.followup_idle_remaining(now), 10)
        self.assertEqual(self.s.followup_idle_remaining(77), 1)
        self.assertEqual(self.s.followup_idle_remaining(78), 0)

    def test_pause_during_followup(self):
        self.audio(600, 9)
        self.assertEqual(self.s.followup_idle_remaining(10.5), 8.5)
        self.audio(600, 10.5)
        self.assertEqual(self.s.followup_idle_remaining(11), 9.5)

    def test_playback_guard_ignores_audio(self):
        self.s.followup_audio_guard_until = 12
        self.audio(1000, 9)
        self.assertEqual(self.s.followup_last_activity, 0)

    def test_finalized_turn_not_extended(self):
        self.s.followup_open = False
        self.audio(1000, 9)
        self.assertEqual(self.s.followup_last_activity, 0)

    def test_empty_pcm(self):
        self.s.note_followup_audio(b'', 9)
        self.assertEqual(self.s.followup_last_activity, 0)

class TimerIntegration(unittest.IsolatedAsyncioTestCase):
    async def test_active_audio_postpones_actual_close(self):
        s = XiaozhiSession.__new__(XiaozhiSession)
        s.followup_task = None
        s.followup_open = True
        s.followup_audio_guard_until = 0
        s.followup_last_activity = 0
        s.tag = "[test]"
        s.followup_audio_packets = s.followup_forwarded_packets = s.followup_peak = 0
        s.websocket = SimpleNamespace(close=AsyncMock())
        clock = SimpleNamespace(now=0)
        calls = []
        async def sleep(delay):
            calls.append(delay)
            clock.now += delay
            if len(calls) <= 3:
                # Input near the deadline must keep the real timer running.
                s.note_followup_audio(struct.pack('<960h', *([600] * 960)), clock.now - 1)
                s.websocket.close.assert_not_awaited()
        with patch('server.asyncio.sleep', sleep), patch('server.asyncio.get_running_loop', return_value=SimpleNamespace(time=lambda: clock.now)):
            s.schedule_followup_timeout()
            await s.followup_task
        self.assertGreater(clock.now, 30)
        s.websocket.close.assert_awaited_once()

if __name__ == '__main__':
    unittest.main()
