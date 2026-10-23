"""Device lifecycle regression, entirely offline, no gateway/provider."""
import asyncio
import json
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock
from server import XiaozhiSession


def session():
    s = XiaozhiSession.__new__(XiaozhiSession)
    s.tag = '[offline]'
    s.persistent_standby = True
    s.followup_task = None
    s.followup_open = True
    s.followup_audio_packets = s.followup_forwarded_packets = s.followup_peak = 0
    s.process = None
    s.helper_ready = asyncio.Event()
    s.stdout_task = s.stderr_task = s.process_watch_task = None
    s.send_json = AsyncMock()
    s.publish_ring_ready = AsyncMock()
    for name in ('cancel_session_finish', 'cancel_drained_watchdog',
                 'cancel_helper_guard', 'cancel_speaking_guard', 'cancel_interim_watchdog'):
        setattr(s, name, Mock())
    s.websocket = SimpleNamespace(close=AsyncMock(), transport=SimpleNamespace(abort=Mock()))
    return s


class PersistentStandby(unittest.IsolatedAsyncioTestCase):
    async def test_silent_provider_frames_do_not_extend_activity(self):
        s=session()
        s.tts_started=True
        s.last_sent_audio_at=123.0
        s.send_lock=asyncio.Lock()
        s.encoder=SimpleNamespace(encode=lambda pcm,n:b'opus')
        s.websocket.send=AsyncMock()
        await s.send_pcm(bytes(2880))
        self.assertEqual(s.last_sent_audio_at,123.0)
        await s.send_pcm(b'\xff\x0f'*1440)
        self.assertGreater(s.last_sent_audio_at,123.0)

    async def test_standby_stops_child_without_closing_device(self):
        s = session()
        child = SimpleNamespace(returncode=None, terminate=Mock(), wait=AsyncMock(return_value=0))
        s.process = child
        await s.return_to_standby('test')
        child.terminate.assert_called_once()
        child.wait.assert_awaited_once()
        s.websocket.close.assert_not_awaited()
        s.websocket.transport.abort.assert_not_called()
        self.assertIsNone(s.process)
        self.assertTrue(s.input_stopped)
        s.send_json.assert_awaited_with({'type': 'standby', 'reason': 'test'})

    async def test_five_seconds_idle_retains_transport(self):
        s = session()
        s.schedule_followup_timeout()
        await asyncio.wait_for(s.followup_task, 6)
        s.websocket.close.assert_not_awaited()
        s.send_json.assert_awaited_with({'type': 'standby', 'reason': 'follow-up timeout'})

    async def test_three_wakes_on_one_connection_no_cloud_on_hello(self):
        s = session()
        s.close = AsyncMock()
        s.prewarm_realtime = AsyncMock()
        s.start_realtime = AsyncMock()
        async def messages():
            yield json.dumps({'type':'hello', 'features':{'persistent_standby':True}})
            s.start_realtime.assert_not_awaited()
            self.assertTrue(s.input_stopped)
            for count in range(1,4):
                yield json.dumps({'type':'listen', 'state':'start'})
                self.assertEqual(s.start_realtime.await_count, count)
                self.assertFalse(s.input_stopped)
                await s.return_to_standby('test')
        class Socket:
            def __aiter__(self): return messages()
        s.websocket = Socket()
        await s.run()
        s.prewarm_realtime.assert_not_awaited()
        self.assertEqual(s.start_realtime.await_count, 3)

    async def test_speech_onset_refreshes_idle_deadline(self):
        # Frueher wurde hier die Frist geloescht und followup_open=False
        # gesetzt. Danach existierte keine Frist mehr, und der Ring blieb gruen,
        # bis der naechste Satz kam (belegt 22.10.2026: 105 s). Korrekt ist:
        # Sprachaktivitaet haelt das Fenster offen, indem sie die Frist neu
        # startet; das Fenster bleibt dabei offen.
        s=session()
        s.close=AsyncMock()
        s.schedule_followup_timeout=Mock()
        s.cancel_followup_timeout=Mock()
        async def messages():
            yield json.dumps({'type':'device','event':'speech_activity','active':True})
        class Socket:
            def __aiter__(self): return messages()
        s.websocket=Socket()
        await s.run()
        s.schedule_followup_timeout.assert_called_once()
        s.cancel_followup_timeout.assert_not_called()
        self.assertTrue(s.followup_open)

    async def test_speech_activity_outside_window_is_ignored(self):
        # Ohne offenes Nachhoerfenster darf die Meldung nichts anfassen.
        s=session()
        s.close=AsyncMock()
        s.followup_open=False
        s.schedule_followup_timeout=Mock()
        async def messages():
            yield json.dumps({'type':'device','event':'speech_activity','active':True})
        class Socket:
            def __aiter__(self): return messages()
        s.websocket=Socket()
        await s.run()
        s.schedule_followup_timeout.assert_not_called()

if __name__ == '__main__': unittest.main()
