#!/usr/bin/env python3
import argparse
import asyncio
import json
import os
import re
import signal
import uuid
from pathlib import Path

import opuslib
from websockets.asyncio.server import serve

# Lokale Sprachausgabe. Das Modul wird nur importiert, wenn der lokale
# TTS-Pfad aktiv ist; so bleibt der Provider-Pfad ohne Piper-Abhaengigkeit
# lauffaehig, falls jemand Piper nicht installiert hat.
try:
    from piper_speaker import PiperSpeaker, find_default_voice
except ImportError:  # pragma: no cover
    PiperSpeaker = None  # type: ignore[assignment]
    find_default_voice = None  # type: ignore[assignment]


class XiaozhiSession:
    def __init__(self, websocket, args):
        self.websocket = websocket
        self.args = args
        self.session_id = str(uuid.uuid4())
        self.turn_id = str(uuid.uuid4())
        self.decoder = opuslib.Decoder(16000, 1)
        self.encoder = opuslib.Encoder(24000, 1, opuslib.APPLICATION_VOIP)
        self.encoder.bitrate = 32000
        self.process = None
        self.stdout_task = None
        self.stderr_task = None
        self.tts_started = False
        self.pcm_buffer = bytearray()
        self.send_lock = asyncio.Lock()
        self.finish_task = None
        self.followup_task = None
        self.input_stopped = False
        self.last_sent_audio_at = 0.0
        self.pcm_bytes_received = 0
        self.audio_progress_event = asyncio.Event()
        self.stream_end_sent = False
        self.followup_open = False
        self.followup_audio_packets = 0
        self.followup_forwarded_packets = 0
        self.followup_peak = 0
        self.mcp_request_id = 0
        self.mcp_pending = {}
        self.mcp_ready = asyncio.Event()

        # --- Lokale Sprachausgabe (Piper) -------------------------------
        # `tts_local` schaltet den Pfad um: der Provider liefert dann nur
        # noch Erkenung und Text; das gehoerte Audio wird verworfen und die
        # Stimme entsteht lokal. Der Sprecher ist pro Prozess warm und wird
        # von main() hereingereicht.
        self.speaker = getattr(args, "speaker", None)
        self.tts_local = self.speaker is not None
        self.stt_local = getattr(args, "stt_local", False)
        self.tts_text_buffer = ""
        self.tts_queue = None
        self.tts_worker_task = None
        self.tts_pending = 0
        self.tts_drain_event = asyncio.Event()
        self.tts_drain_event.set()
        self.local_pcm_bytes_sent = 0

    async def send_mcp_request(self, method, params, timeout=3.0):
        self.mcp_request_id += 1
        request_id = self.mcp_request_id
        response = asyncio.get_running_loop().create_future()
        self.mcp_pending[request_id] = response
        await self.send_json({
            "type": "mcp",
            "payload": {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            },
        })
        try:
            return await asyncio.wait_for(response, timeout=timeout)
        finally:
            self.mcp_pending.pop(request_id, None)

    async def initialize_mcp(self):
        try:
            response = await self.send_mcp_request(
                "initialize", {"capabilities": {}}, timeout=3.0
            )
            if "error" in response:
                raise RuntimeError(response["error"])
            self.mcp_ready.set()
            print("Device MCP initialized", flush=True)
        except Exception as exc:
            print(f"Device MCP initialization failed: {exc}", flush=True)

    async def call_device_tool(self, name, arguments):
        await asyncio.wait_for(self.mcp_ready.wait(), timeout=3.0)
        try:
            response = await self.send_mcp_request(
                "tools/call",
                {"name": name, "arguments": arguments},
                timeout=3.0,
            )
        except asyncio.TimeoutError:
            # Setting an absolute value is idempotent. One retry handles an
            # occasional lost first MCP response without changing the result.
            response = await self.send_mcp_request(
                "tools/call",
                {"name": name, "arguments": arguments},
                timeout=3.0,
            )
        if "error" in response:
            raise RuntimeError(response["error"])
        result = response.get("result", {})
        if result.get("isError"):
            raise RuntimeError(result)
        return result

    async def handle_device_voice_command(self, transcript):
        normalized = " ".join(transcript.lower().replace("%", " prozent ").split())
        number_words = {
            "null": 0, "zehn": 10, "zwanzig": 20, "dreißig": 30,
            "dreissig": 30, "vierzig": 40, "fünfzig": 50,
            "fuenfzig": 50, "sechzig": 60, "siebzig": 70,
            "achtzig": 80, "neunzig": 90, "hundert": 100,
        }
        value_pattern = (
            r"(\d{1,3}|null|zehn|zwanzig|dreißig|dreissig|vierzig|"
            r"fünfzig|fuenfzig|sechzig|siebzig|achtzig|neunzig|hundert)"
        )
        commands = (
            (
                rf"(?:lautstärke|lautstaerke)(?:\s+auf)?\s+{value_pattern}(?:\s+prozent)?",
                "self.audio_speaker.set_volume", "volume", "volume",
            ),
            (
                rf"(?:(?:bildschirm|display)[-\s]*)?helligkeit(?:\s+auf)?\s+{value_pattern}(?:\s+prozent)?",
                "self.screen.set_brightness", "brightness", "brightness",
            ),
        )
        for pattern, tool_name, argument_name, label in commands:
            match = re.search(pattern, normalized)
            if not match:
                continue
            raw_value = match.group(1)
            value = int(raw_value) if raw_value.isdigit() else number_words[raw_value]
            if not 0 <= value <= 100:
                print(f"Rejected device {label} outside 0-100: {value}", flush=True)
                return True
            try:
                await self.call_device_tool(tool_name, {argument_name: value})
                print(f"Device {label} set to {value}% via MCP", flush=True)
                display_label = "Lautstärke" if label == "volume" else "Helligkeit"
                await self.send_json({
                    "type": "stt",
                    "text": f"{display_label}: {value} %",
                })
                await asyncio.sleep(1.0)
            except Exception as exc:
                print(f"Device {label} MCP call failed: {exc!r}", flush=True)
            return True
        return False

    async def send_json(self, payload):
        payload.setdefault("session_id", self.session_id)
        async with self.send_lock:
            await self.websocket.send(json.dumps(payload, ensure_ascii=False))

    async def start_realtime(self):
        if self.process and self.process.returncode is None:
            # XiaoZhi keeps sending microphone packets while the provider is
            # preparing the answer. They belong to the completed utterance and
            # must not replace its still-running helper. A follow-up helper is
            # allowed only after finish_after_grace_period reaps this process.
            return

        self.turn_id = str(uuid.uuid4())
        self.tts_started = False
        self.pcm_buffer = bytearray()
        self.input_stopped = False
        self.last_sent_audio_at = 0.0
        self.pcm_bytes_received = 0
        self.audio_progress_event = asyncio.Event()
        self.stream_end_sent = False

        env = os.environ.copy()
        env.update(
            # TEST (2026-09-27, dauerhafte Instanz): Der Session-Key ist bewusst
            # KONSTANT. Alle Utterances/Verbindungen landen in derselben Session,
            # damit ein zweites "Jarvis" in derselben Instanz weiterläuft.
            # Zum Zurücksetzen: die beiden f-Strings wieder mit -{uuid} anhängen.
            OPENCLAW_TALK_SESSION_KEY=self.args.session_key,
            OPENCLAW_TALK_CONSULT_SESSION_KEY=(
                self.args.consult_session_key
                if self.args.consult_session_key
                else ""
            ),
            OPENCLAW_REALTIME_MODEL=self.args.model,
            OPENCLAW_REALTIME_VOICE=self.args.voice,
            OPENCLAW_WHISPER_URI=getattr(self.args, "stt_uri", "127.0.0.1:10300"),
            OPENCLAW_WHISPER_LANGUAGE=getattr(self.args, "stt_language", "de"),
            NODE_EXTRA_CA_CERTS=self.args.gateway_ca,
        )
        self.process = await asyncio.create_subprocess_exec(
            "node", self.args.helper, "16000", "2", "1",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        process = self.process
        self.stdout_task = asyncio.create_task(self.read_audio(process))
        self.stderr_task = asyncio.create_task(self.read_events(process))

    async def stop_input(self, process=None):
        process = process or self.process
        if process and process.stdin and not process.stdin.is_closing():
            process.stdin.close()
            await process.stdin.wait_closed()

    async def read_audio(self, process):
        frame_bytes = 24000 * 60 // 1000 * 2
        try:
            while True:
                chunk = await process.stdout.read(8192)
                if not chunk:
                    break
                if self.tts_local:
                    # Lokale Sprachausgabe: das Audio des Providers wird nicht
                    # mehr zum Geraet geschickt. Der Textpfad (read_events)
                    # erzeugt die Stimme ueber Piper. Der Provider-Audiostrom
                    # wird trotzdem vollstaendig gelesen, damit sich der
                    # Kindprozess nicht an einem vollen Rohr blockiert.
                    continue
                # stream_end already told the ESP32 that this spoken response
                # is complete. GPT-Live can still emit trailing silence/audio;
                # consume it locally so playback_drained is not delayed while
                # the Talk session remains open for a follow-up utterance.
                if self.stream_end_sent:
                    continue
                self.pcm_bytes_received += len(chunk)
                self.audio_progress_event.set()
                self.pcm_buffer.extend(chunk)
                while len(self.pcm_buffer) >= frame_bytes:
                    pcm = bytes(self.pcm_buffer[:frame_bytes])
                    del self.pcm_buffer[:frame_bytes]
                    # Forward the first provider audio frame immediately.
                    # Do not wait for transcript text or segment classification.
                    await self.send_pcm(pcm)
        except Exception as exc:
            print(f"audio relay failed: {exc}", flush=True)

    async def send_pcm(self, pcm):
        if not self.tts_started:
            await self.send_json({"type": "tts", "state": "start"})
            self.tts_started = True
        packet = self.encoder.encode(pcm, 1440)
        async with self.send_lock:
            await self.websocket.send(packet)
        self.last_sent_audio_at = asyncio.get_running_loop().time()

    @staticmethod
    def is_interim_text(text):
        # Nur eine REINE Zwischenansage darf den stream_end unterdruecken.
        # Vorher reichte ein Praefix ("startswith"), dadurch galt auch
        # "I'll check that request. Es ist 01:02 Uhr." als Zwischenansage —
        # das Gerät bekam nie stream_end und blieb im Sprechen-Zustand.
        normalized = " ".join(text.strip().lower().replace("’", "'").split())
        normalized = normalized.strip(" .,!?;:…-–—")
        return normalized in {
            "i'll check",
            "i'll check that",
            "i'll check that request",
            "i will check",
            "i will check that request",
            "let me check",
            "let me check that",
            "let me check that for you",
            "let me check that for you please",
            "one moment",
            "one moment please",
            "einen moment",
            "einen moment bitte",
            "einen moment, ich prüfe das",
            "einen moment ich prüfe das",
            "augenblick",
            "augenblick bitte",
            "moment, ich prüfe das",
            "moment ich prüfe das",
            "ich prüfe das",
            "ich prüfe das kurz",
            "ich schaue",
            "ich schaue nach",
            "ich schaue kurz nach",
        }

    def cancel_session_finish(self):
        current_task = asyncio.current_task()
        if (
            self.finish_task
            and self.finish_task is not current_task
            and not self.finish_task.done()
        ):
            self.finish_task.cancel()
        if self.finish_task is not current_task:
            self.finish_task = None

    def cancel_followup_timeout(self):
        if self.followup_task and not self.followup_task.done():
            self.followup_task.cancel()
        self.followup_task = None

    def schedule_followup_timeout(self):
        self.cancel_followup_timeout()

        async def close_after_followup_window():
            print("Follow-up window open for 10 seconds", flush=True)
            await asyncio.sleep(10.0)
            print(
                "Follow-up window expired; returning to standby "
                f"(audio packets received={self.followup_audio_packets}, "
                f"forwarded={self.followup_forwarded_packets}, "
                f"peak={self.followup_peak})",
                flush=True,
            )
            try:
                await asyncio.wait_for(
                    self.websocket.close(code=1000, reason="follow-up timeout"),
                    timeout=2.0,
                )
            except asyncio.TimeoutError:
                # XiaoZhi does not always return a WebSocket close frame. Do
                # not let the library's default 10-second close timeout keep
                # the display in listening state.
                self.websocket.transport.abort()

        self.followup_task = asyncio.create_task(close_after_followup_window())

    def schedule_stream_end(self, process):
        self.cancel_session_finish()

        async def finish_after_trailing_frames():
            # assistant_done is the semantic end of this spoken segment.
            # GPT-Live may emit one final audio frame a few milliseconds later,
            # but it can also keep emitting silence indefinitely. Therefore do
            # not wait for the PCM byte counter to become idle.
            await asyncio.sleep(0.12)
            await self.finish_audio_stream(process, self.pcm_bytes_received)

        self.finish_task = asyncio.create_task(finish_after_trailing_frames())

    async def finish_audio_stream(self, process, expected_pcm_bytes):
        if self.stream_end_sent:
            return
        # This function is normally called by finish_task itself. Do not let
        # cancel_session_finish() cancel the currently executing task before
        # it can send stream_end to the device.
        self.cancel_session_finish()

        async def wait_until_stdout_is_consumed():
            while self.pcm_bytes_received < expected_pcm_bytes:
                self.audio_progress_event.clear()
                if self.pcm_bytes_received >= expected_pcm_bytes:
                    break
                await self.audio_progress_event.wait()

        try:
            await asyncio.wait_for(wait_until_stdout_is_consumed(), timeout=3.0)
        except asyncio.TimeoutError:
            print(
                f"PCM relay stalled at {self.pcm_bytes_received}/{expected_pcm_bytes} bytes",
                flush=True,
            )
            await self.send_json({"type": "tts", "state": "stop"})
            return

        if not self.tts_started:
            return

        self.stream_end_sent = True
        await self.send_json({"type": "tts", "state": "stream_end"})
        print(
            f"Audio stream complete at {expected_pcm_bytes} PCM bytes; "
            "device will switch after physical playback drain",
            flush=True,
        )
        # End only this spoken response. Keep the helper and its OpenClaw Talk
        # session alive so a follow-up confirmation uses the same voiceSessionId.

    # ------------------------------------------------------------------
    # Lokale Sprachausgabe (Piper)
    # ------------------------------------------------------------------
    #
    # Der Provider liefert nur noch Erkennung und Text. Den Text wandelt
    # Piper lokal in PCM. Zwei Dinge muessen dabei stimmen:
    #
    # 1. **Taktung.** Das Geraet nimmt 60-ms-Opus-Rahmen in eine Warteschlange
    #    von 1,2 s (`MAX_DECODE_PACKETS_IN_QUEUE`). Wird die Warteschlange
    #    voll, verwirft es weitere Rahmen *ohne Sperre* (`wait=False`). Ein
    #    kompletter Satz in einem Schwall wuerde also hoerbare Luecken
    #    erzeugen. Deshalb wird hier in Echtzeit gesendet, mit einem kleinen
    #    Vorlauf, damit der Anfang nicht unterlaeuft.
    # 2. **Zerlegung.** Text wird an Satzgrenzen gesprochen, nicht als
    #    ganzer Absatz. So beginnt die Stimme frueher.

    TTS_LEAD_FRAMES = 3
    TTS_FRAME_SECONDS = 0.060

    def start_local_tts(self):
        """Startet den Sprach-Arbeiter fuer eine Antwort."""
        if self.tts_worker_task and not self.tts_worker_task.done():
            return
        self.tts_text_buffer = ""
        self.tts_pending = 0
        self.local_pcm_bytes_sent = 0
        self.tts_drain_event.clear()
        self.tts_queue = asyncio.Queue()
        self.tts_worker_task = asyncio.create_task(self._local_tts_worker())

    async def _local_tts_worker(self):
        loop = asyncio.get_running_loop()
        try:
            while True:
                text = await self.tts_queue.get()
                if text is None:
                    break
                try:
                    pcm = await loop.run_in_executor(
                        None, self.speaker.synthesize_pcm, text
                    )
                except Exception as exc:  # noqa: BLE001
                    print(f"local TTS failed: {exc!r}", flush=True)
                    pcm = b""
                if pcm:
                    await self.send_pcm_paced(pcm)
                    self.local_pcm_bytes_sent += len(pcm)
        finally:
            self.tts_drain_event.set()

    async def send_pcm_paced(self, pcm):
        """Sendet PCM in 60-ms-Rahmen, an der Wanduhr getaktet."""
        frame_bytes = 24000 * 60 // 1000 * 2
        loop = asyncio.get_running_loop()
        started = loop.time()
        index = 0
        for offset in range(0, len(pcm), frame_bytes):
            chunk = pcm[offset:offset + frame_bytes]
            if len(chunk) < frame_bytes:
                chunk = chunk + b"\x00" * (frame_bytes - len(chunk))
            await self.send_pcm(chunk)
            index += 1
            if index > self.TTS_LEAD_FRAMES:
                target = started + (index - self.TTS_LEAD_FRAMES) * self.TTS_FRAME_SECONDS
                delay = target - loop.time()
                if delay > 0:
                    await asyncio.sleep(delay)

    def feed_tts_text(self, text):
        """Sammelt Text und schiebt fertige Saetze an den Arbeiter."""
        if not self.tts_local or not text:
            return
        self.tts_text_buffer += text
        while True:
            boundary = self._tts_boundary(self.tts_text_buffer)
            if boundary <= 0:
                break
            phrase = self.tts_text_buffer[:boundary].strip()
            self.tts_text_buffer = self.tts_text_buffer[boundary:]
            if phrase:
                self.tts_pending += 1
                self.tts_queue.put_nowait(phrase)

    @staticmethod
    def _tts_boundary(text):
        """Index hinter dem naechsten Satzende, sonst bei Ueberlaenge."""
        for index, char in enumerate(text):
            if char in ".!?;:…":
                # Beim Punkt sicherstellen, dass nicht gerade eine Abkuerzung
                # oder Zahl mit Dezimaltrennzeichen getrennt wird.
                if char == "." and index + 1 < len(text) and text[index + 1].isdigit():
                    continue
                return index + 1
        if len(text) >= 160:
            space = text.rfind(" ", 0, 160)
            return space + 1 if space > 0 else 160
        return 0

    async def finish_local_tts(self, process):
        """Spricht den Rest und schliesst die Antwort ab."""
        if self.stream_end_sent:
            return
        self.cancel_session_finish()
        rest = self.tts_text_buffer.strip()
        self.tts_text_buffer = ""
        if rest:
            self.tts_pending += 1
            self.tts_queue.put_nowait(rest)
        if self.tts_queue is not None:
            self.tts_queue.put_nowait(None)
        try:
            await asyncio.wait_for(self.tts_drain_event.wait(), timeout=30.0)
        except asyncio.TimeoutError:
            print("local TTS drain timed out", flush=True)
        self.tts_worker_task = None
        if not self.tts_started:
            # Nichts zu sprechen (leere Antwort). Kein stream_end noetig.
            return
        self.stream_end_sent = True
        await self.send_json({"type": "tts", "state": "stream_end"})
        print(
            f"Local voice stream complete at {self.local_pcm_bytes_sent} PCM bytes; "
            "device will switch after physical playback drain",
            flush=True,
        )

    def schedule_local_stream_end(self, process):
        self.cancel_session_finish()
        self.finish_task = asyncio.create_task(self.finish_local_tts(process))

    async def read_events(self, process):
        try:
            while True:
                line = await process.stderr.readline()
                if not line:
                    break
                text = line.decode("utf-8", "replace").strip()
                try:
                    event = json.loads(text)
                except json.JSONDecodeError:
                    print(text, flush=True)
                    continue
                kind = event.get("type")
                if kind == "user_transcript":
                    self.cancel_followup_timeout()
                    self.followup_open = False
                    transcript = event.get("text", "")
                    # A finalized new utterance starts the next response. Only
                    # now may provider audio be forwarded again; until here,
                    # trailing frames from the previous response stay dropped.
                    self.stream_end_sent = False
                    print(f"User transcript: {transcript}", flush=True)
                    await self.send_json({"type": "stt", "text": transcript})
                    device_command_handled = await self.handle_device_voice_command(transcript)
                    if device_command_handled:
                        # Device-local commands are complete after the MCP
                        # response. Do not let GPT-Live delegate the same text
                        # to the general agent and manufacture an approval turn.
                        if not self.input_stopped:
                            self.input_stopped = True
                            await self.stop_input(process)
                        if process.returncode is None:
                            process.terminate()
                            await process.wait()
                        if self.process is process:
                            self.process = None
                        await self.send_json({"type": "tts", "state": "stop"})
                        print(
                            "Device command complete; returning directly to standby",
                            flush=True,
                        )
                        self.websocket.transport.abort()
                        return
                    # Pause microphone forwarding while the assistant speaks,
                    # but keep the pipe and Talk session open for the next turn.
                    if not self.input_stopped:
                        self.input_stopped = True
                    if self.tts_local:
                        self.start_local_tts()
                elif kind == "assistant_delta" and event.get("text"):
                    await self.send_json({
                        "type": "tts",
                        "state": "sentence_start",
                        "text": event["text"],
                    })
                    # Lokale Stimme: den Text sofort an Piper weitergeben,
                    # damit die Sprache frueh beginnt und nicht erst nach dem
                    # vollstaendigen Transkript.
                    if self.tts_local:
                        self.feed_tts_text(event["text"])
                elif kind == "audio_done":
                    print(
                        "OpenClaw realtime event: "
                        + json.dumps(event, ensure_ascii=False),
                        flush=True,
                    )
                    if self.tts_local:
                        # Das Provider-Audio wird verworfen; der Abschluss
                        # folgt aus dem Text (assistant_done).
                        pass
                    else:
                        await self.finish_audio_stream(
                            process,
                            int(event.get("audioByteCount") or 0),
                        )
                elif kind == "error":
                    print(f"OpenClaw realtime error: {event.get('message')}", flush=True)
                elif kind == "tool_call":
                    self.cancel_session_finish()
                    print(
                        "OpenClaw realtime event: "
                        + json.dumps(event, ensure_ascii=False),
                        flush=True,
                    )
                elif kind == "audio_after_assistant_done":
                    print(
                        "OpenClaw realtime event: "
                        + json.dumps(event, ensure_ascii=False),
                        flush=True,
                    )
                elif kind == "assistant_done":
                    print(
                        "OpenClaw realtime event: "
                        + json.dumps(event, ensure_ascii=False),
                        flush=True,
                    )
                    if event.get("text") and not self.is_interim_text(event["text"]):
                        if self.tts_local:
                            self.schedule_local_stream_end(process)
                        else:
                            self.schedule_stream_end(process)
                elif kind in {"ready", "tool_result"}:
                    print(
                        "OpenClaw realtime event: "
                        + json.dumps(event, ensure_ascii=False),
                        flush=True,
                    )
        except Exception as exc:
            print(f"event relay failed: {exc}", flush=True)

    async def feed_opus(self, packet):
        await self.start_realtime()
        if self.followup_open:
            self.followup_audio_packets += 1
            if self.followup_audio_packets == 1:
                print("First follow-up microphone packet received", flush=True)
        try:
            pcm = self.decoder.decode(packet, 960, False)
        except opuslib.OpusError as exc:
            print(f"invalid Opus packet: {exc}", flush=True)
            return
        if self.input_stopped:
            return
        if self.followup_open and pcm:
            samples = memoryview(pcm).cast("h")
            self.followup_peak = max(
                self.followup_peak,
                max((abs(sample) for sample in samples), default=0),
            )
        if self.process.stdin and not self.process.stdin.is_closing():
            if self.followup_open:
                self.followup_forwarded_packets += 1
            self.process.stdin.write(pcm)
            await self.process.stdin.drain()

    async def close(self, abort=False):
        self.cancel_session_finish()
        self.cancel_followup_timeout()
        # Den Sprach-Arbeiter sauber beenden, damit kein Piper-Thread
        # beim Herunterfahren haengen bleibt.
        if self.tts_queue is not None:
            try:
                self.tts_queue.put_nowait(None)
            except asyncio.QueueFull:  # pragma: no cover
                pass
        if self.tts_worker_task and not self.tts_worker_task.done():
            try:
                await asyncio.wait_for(self.tts_worker_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self.tts_worker_task.cancel()
        await self.stop_input()
        if self.process:
            if abort and self.process.returncode is None:
                self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), timeout=5 if abort else 125)
            except asyncio.TimeoutError:
                self.process.terminate()
                await self.process.wait()
        for task in (self.stdout_task, self.stderr_task):
            if task:
                await asyncio.gather(task, return_exceptions=True)
        for pending in self.mcp_pending.values():
            if not pending.done():
                pending.cancel()
        self.mcp_pending.clear()

    async def run(self):
        try:
            async for message in self.websocket:
                if isinstance(message, bytes):
                    await self.feed_opus(message)
                    continue
                payload = json.loads(message)
                kind = payload.get("type")
                if kind == "hello":
                    await self.send_json({
                        "type": "hello",
                        "transport": "websocket",
                        "audio_params": {
                            "format": "opus",
                            "sample_rate": 24000,
                            "channels": 1,
                            "frame_duration": 60,
                        },
                    })
                    if payload.get("features", {}).get("mcp"):
                        asyncio.create_task(self.initialize_mcp())
                elif kind == "mcp":
                    mcp_payload = payload.get("payload", {})
                    request_id = mcp_payload.get("id")
                    pending = self.mcp_pending.get(request_id)
                    if pending and not pending.done():
                        pending.set_result(mcp_payload)
                elif kind == "listen" and payload.get("state") == "start":
                    await self.start_realtime()
                elif kind == "listen" and payload.get("state") == "stop":
                    # This device-state notification may arrive after
                    # playback_drained. The finalized user transcript already
                    # pauses forwarding at the correct boundary, so acting on
                    # this late event would immediately block the follow-up.
                    pass
                elif (
                    kind == "device"
                    and payload.get("event") == "playback_drained"
                ):
                    print(
                        "Physical playback drained; opening follow-up listening window",
                        flush=True,
                    )
                    await self.send_json({"type": "tts", "state": "stop"})
                    self.tts_started = False
                    self.pcm_buffer = bytearray()
                    self.pcm_bytes_received = 0
                    self.audio_progress_event = asyncio.Event()
                    self.input_stopped = False
                    self.followup_open = True
                    self.followup_audio_packets = 0
                    self.followup_forwarded_packets = 0
                    self.followup_peak = 0
                    # Lokale Stimme: Textpuffer und Ergebniszaehler fuer die
                    # naechste Antwort zuruecksetzen.
                    self.tts_text_buffer = ""
                    self.local_pcm_bytes_sent = 0
                    await self.start_realtime()
                    self.schedule_followup_timeout()
                elif kind == "abort":
                    break
        finally:
            await self.close(abort=True)


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--ota-port", type=int, default=8766)
    parser.add_argument("--public-host", default="192.168.178.143")
    parser.add_argument("--model", default="gpt-live-1-codex")
    parser.add_argument("--voice", default="cove")
    parser.add_argument("--session-key", default="agent:voice:xiaozhi-realtime")
    parser.add_argument(
        "--consult-session-key",
        default="",
    )
    parser.add_argument(
        "--gateway-ca",
        default=str(Path.home() / ".openclaw" / "ssl" / "gateway.crt"),
    )
    parser.add_argument(
        "--helper",
        default=None,
        help="Pfad zum Sprachhelfer (Standard je nach Modus)",
    )
    parser.add_argument(
        "--stt-local",
        action="store_true",
        help="Spracherkennung lokal ueber Wyoming-Whisper statt ueber OpenAI",
    )
    parser.add_argument(
        "--stt-uri",
        default="127.0.0.1:10300",
        help="Adresse des Wyoming-Whisper-Dienstes",
    )
    parser.add_argument(
        "--stt-language",
        default="de",
        help="Sprache fuer die lokale Erkennung",
    )
    parser.add_argument(
        "--tts-local",
        action="store_true",
        help="Sprachausgabe lokal ueber Piper statt ueber den Provider",
    )
    parser.add_argument(
        "--tts-voice",
        default="de_DE-thorsten-medium",
        help="Piper-Stimme fuer die lokale Sprachausgabe",
    )
    parser.add_argument(
        "--tts-voice-dir",
        default=str(Path.home() / ".local" / "share" / "piper-voices"),
        help="Verzeichnis der Piper-Stimmen",
    )
    parser.add_argument(
        "--tts-model-path",
        default="",
        help="Direkter Pfad zu einer .onnx-Stimme (uebersteuert --tts-voice)",
    )
    args = parser.parse_args()

    # Lokale Erkennung bedeutet zwangslaeufig lokale Sprachausgabe: der
    # lokale Helfer liefert gar kein Audio, es gaebe sonst nichts zu spielen.
    if args.stt_local:
        args.tts_local = True

    if args.helper is None:
        args.helper = str(
            Path(__file__).resolve().with_name(
                "local-voice.mjs" if args.stt_local else "openclaw-talk-realtime.mjs"
            )
        )

    if args.tts_local:
        if PiperSpeaker is None:
            raise SystemExit(
                "--tts-local verlangt das Modul 'piper'. Installation:\n"
                "  pip install piper-tts\n"
                "oder das Piper-Paket der Distribution verwenden."
            )
        if args.tts_model_path:
            model_path = args.tts_model_path
        else:
            model_path = find_default_voice(args.tts_voice_dir, args.tts_voice)
        print(f"Local voice enabled: loading {model_path}", flush=True)
        args.speaker = PiperSpeaker(model_path)
        print(f"Local voice ready: {args.speaker.describe()}", flush=True)
    else:
        args.speaker = None

    async def handler(websocket):
        print(f"XiaoZhi connected from {websocket.remote_address}", flush=True)
        try:
            await XiaozhiSession(websocket, args).run()
        except Exception as exc:
            print(f"XiaoZhi session failed: {exc}", flush=True)

    stop = asyncio.get_running_loop().create_future()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set_result, None)
    async def ota_handler(reader, writer):
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            content_length = 0
            for line in header.decode("latin1").split("\r\n"):
                if line.lower().startswith("content-length:"):
                    content_length = int(line.split(":", 1)[1].strip())
            if content_length:
                await reader.readexactly(content_length)
            body = json.dumps({
                "websocket": {
                    "url": f"ws://{args.public_host}:{args.port}",
                    "token": "local-openclaw",
                    "version": 1,
                }
            }).encode()
            writer.write(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                + f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode()
                + body
            )
            await writer.drain()
        except Exception as exc:
            print(f"OTA request failed: {exc}", flush=True)
        finally:
            writer.close()
            await writer.wait_closed()

    ota_server = await asyncio.start_server(ota_handler, args.host, args.ota_port)
    async with ota_server, serve(
        handler,
        args.host,
        args.port,
        max_size=2**20,
        ping_interval=20,
        close_timeout=1,
    ):
        print(f"XiaoZhi OpenClaw gateway listening on ws://{args.host}:{args.port}", flush=True)
        print(f"XiaoZhi OTA configuration on http://{args.host}:{args.ota_port}/xiaozhi/ota/", flush=True)
        await stop


if __name__ == "__main__":
    asyncio.run(main())
