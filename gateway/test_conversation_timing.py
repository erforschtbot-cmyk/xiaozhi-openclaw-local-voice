"""Conversation idle deadlines for the event-driven follow-up window."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch
import unittest

from server import XiaozhiSession


class FollowupDeadlineTests(unittest.IsolatedAsyncioTestCase):
    def session(self, *, now=100.0, opened=100.0):
        s = XiaozhiSession.__new__(XiaozhiSession)
        s.followup_task = None
        s.followup_opened_at = opened
        s.followup_audio_packets = 0
        s.followup_forwarded_packets = 0
        s.followup_peak = 0
        s.tag = "[test]"
        s.persistent_standby = False
        s.websocket = SimpleNamespace(close=AsyncMock(), transport=SimpleNamespace(abort=lambda: None))
        s._test_now = now
        return s

    async def scheduled_delay(self, s):
        delays = []

        async def fake_sleep(delay):
            delays.append(delay)

        loop = SimpleNamespace(time=lambda: s._test_now)
        with patch("server.asyncio.sleep", fake_sleep), patch(
            "server.asyncio.get_running_loop", return_value=loop
        ):
            s.schedule_followup_timeout(quiet=True)
            await s.followup_task
        return delays[0]

    async def test_fresh_window_uses_normal_ten_seconds(self):
        self.assertEqual(await self.scheduled_delay(self.session()), 10.0)

    async def test_refresh_cannot_exceed_hard_twenty_second_limit(self):
        s = self.session(now=117.0, opened=100.0)
        self.assertEqual(await self.scheduled_delay(s), 3.0)

    async def test_missing_start_time_is_initialized(self):
        s = self.session(now=50.0, opened=0.0)
        self.assertEqual(await self.scheduled_delay(s), 10.0)
        self.assertEqual(s.followup_opened_at, 50.0)


if __name__ == "__main__":
    unittest.main()
