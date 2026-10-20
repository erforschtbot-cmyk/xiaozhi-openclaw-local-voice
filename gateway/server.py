#!/usr/bin/env python3
import argparse
import asyncio
import collections
import json
import os
import re
import time
import signal
import uuid
import urllib.parse
import urllib.request
from pathlib import Path

import opuslib
from websockets.asyncio.server import serve

# Raum-Zuordnung: das Geraet schickt seine MAC im Handshake (Device-Id).
# Daraus wird der Raum ermittelt und fuer die Weiche hinterlegt.
from room_command import publish_active_room, room_for_device

# Lokale Sprachausgabe. Das Modul wird nur importiert, wenn der lokale
# TTS-Pfad aktiv ist; so bleibt der Provider-Pfad ohne Piper-Abhaengigkeit
# lauffaehig, falls jemand Piper nicht installiert hat.
try:
    from piper_speaker import PiperSpeaker, find_default_voice
except ImportError:  # pragma: no cover
    PiperSpeaker = None  # type: ignore[assignment]
    find_default_voice = None  # type: ignore[assignment]


class WakeArbiter:
    """Nur das lauteste Geraet fuehrt den Dialog - ohne Zeitfenster.

    Alle Geraete hoeren das Wachwort und schicken ihren verstandenen Satz an
    die Bridge. Hier wird der Mikrofonpegel verglichen: kommt spaeter ein
    Geraet mit hoeherem Pegel, wird das bisher aktive einfach in Bereitschaft
    geschickt. Damit spricht immer das Geraet, das den Nutzer wirklich gehoert
    hat, und ein Befehl kann nicht doppelt ausgefuehrt werden.

    Ohne Konkurrenz gibt es keinerlei Verzoegerung: das erste Geraet gewinnt
    sofort.
    """

    def __init__(self):
        self._sessions = {}
        self._lock = asyncio.Lock()
        self._winner = None
        self._winner_peak = 0

    def register(self, session):
        self._sessions[session.session_id] = session

    def unregister(self, session):
        self._sessions.pop(session.session_id, None)
        if self._winner == session.session_id:
            self._winner = None
            self._winner_peak = 0

    async def claim(self, session, peak):
        """True, wenn dieses Geraet den Dialog fuehren soll.

        Der bisherige Gewinner wird dabei in Bereitschaft geschickt, sobald ein
        Geraet mit hoeherem Pegel dazukommt.
        """
        superseded = None
        previous_peak = 0
        async with self._lock:
            if self._winner == session.session_id:
                # Dieses Geraet fuehrt bereits; Folgeaeusserungen bleiben bei ihm.
                return True
            if self._winner is None or peak > self._winner_peak:
                previous = self._sessions.get(self._winner) if self._winner else None
                previous_peak = self._winner_peak
                self._winner = session.session_id
                self._winner_peak = peak
                if previous is not None and previous is not session:
                    superseded = previous
                print(
                    f"{session.tag} Wake arbitration won (peak={peak}); "
                    f"active devices now: "
                    f"{', '.join(s.tag for s in self._sessions.values() if s is not session) or 'none'}",
                    flush=True,
                )
            else:
                print(
                    f"{session.tag} Wake arbitration lost (peak={peak} < "
                    f"{self._winner_peak}); returning to standby",
                    flush=True,
                )
                return False
        if superseded is not None:
            print(
                f"{superseded.tag} superseded by louder device "
                f"({peak} > {previous_peak}); returning to standby",
                flush=True,
            )
            await superseded.return_to_standby("superseded_by_louder_device")
        return True


class XiaozhiSession:
    ALEXA_TEXT_COMMAND_ENDPOINT = (
        "http://192.168.178.24:8087/set/"
        "alexa2.0.Echo-Devices.G6G2MM12449402WK.Commands.textCommand?value="
    )
    # Gemeinsame Dedupe-Zustandsdatei mit dem Agent-Wrapper
    # (openclaw-voice-skill-action): derselbe Schaltbefehl wird innerhalb des
    # Fensters nur einmal an Alexa geschickt, egal welcher Pfad zuerst kommt.
    ALEXA_DEDUPE_STATE_PATH = (
        "/home/openclaw/.local/share/jarvis-realtime-bridge/last-alexa-command.json"
    )
    ALEXA_DEDUPE_WINDOW_SECONDS = 8.0
    # Modus-Schalter: liegt diese Datei vor, ist der Software-Direktpfad AUS
    # und Smart-Home-Befehle gehen den offiziellen KI-Weg (Agent-Consult).
    FASTPATH_DISABLE_FLAG = (
        "/home/openclaw/.local/share/jarvis-realtime-bridge/disable-smart-home-fastpath"
    )

    def __init__(self, websocket, args):
        self.websocket = websocket
        self.args = args
        # Geraete-Kennung fuer das Log. Zwei Geraete schreiben sonst wortgleich
        # ins Log und sind nicht auseinanderzuhalten.
        try:
            peer = websocket.remote_address
            peer_ip = str(peer[0]) if peer else "?"
        except Exception:
            peer_ip = "?"
        self.peer_ip = peer_ip
        self.tag = f"[{peer_ip.split('.')[-1]}]"
        self.arbiter = getattr(args, "arbiter", None)
        # Pegel des laufenden Befehls: wird beim Wecken zurueckgesetzt und
        # bis zum fertigen Transkript hochgehalten. Grundlage des Vergleichs,
        # welches Geraet den Nutzer wirklich gehoert hat.
        self.turn_peak = 0
        self.awaiting_command = False
        self.drained_watchdog = None
        self.speaking_guard = None
        self.pending_tool = False
        self.pending_tool_since = 0.0
        self.session_id = str(uuid.uuid4())
        # Erst nach dem Setzen der Session-ID registrieren - der Schiedsrichter
        # merkt sich die Sitzung unter dieser Kennung.
        if self.arbiter is not None:
            self.arbiter.register(self)
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
        self.followup_audio_guard_until = 0.0
        self.mcp_request_id = 0
        self.mcp_pending = {}
        self.mcp_ready = asyncio.Event()
        # Diagnosezaehler fuer den Mikrofonpegel (siehe feed_opus).
        self.audio_packets = 0
        self.audio_peak = 0
        # Vorwaermen: der Sprachhelfer (Cloud-Talk-Sitzung) braucht vom Start
        # bis "ready" rund 3 Sekunden. Wird er erst beim ersten Mikrofonpaket
        # gestartet, ist der Nutzer mit seinem Satz laengst durch, bevor
        # ueberhaupt zugehoert wird. Deshalb startet er schon beim
        # Verbindungsaufbau (hello). `helper_starting` verhindert, dass zwei
        # gleichzeitige Aufrufe (hello und erstes Paket) zwei Helfer starten.
        # --- Kamera / Foto -----------------------------------------------
        # Die Firmware kann bereits Fotos: Sie meldet dem Server per MCP einen
        # vision-Endpunkt, schiesst ein JPEG und laedt es per multipart hoch.
        # Ohne diesen Endpunkt meldet das Geraet "Image explain URL or token
        # is not set" und das Werkzeug self.camera.take_photo scheitert.
        self.photo_pending = None      # Future auf das naechste JPEG
        self.photo_path = None         # zuletzt empfangenes Foto
        self.photo_question = ""
        self.photo_budget = 0          # >0 = ein Foto wird erwartet
        # Waehrend einer Foto-Runde darf kein Provider-Audio dazwischenreden:
        # die Antwort kommt aus dem Bild-Consult und wird lokal gesprochen.
        self.photo_speaking = False
        self._photo_voice = None

        self.helper_ready = asyncio.Event()
        self.helper_starting = False
        self.helper_ready_at = 0.0
        self.helper_started_at = 0.0

        # --- Diagnose: Wann kommt Provider-Audio relativ zum Transkript? ---
        # Ohne diese Zeitmarken laesst sich nicht unterscheiden, ob die
        # echte Antwort zu frueh beginnt oder ein Filler das Mikrofon
        # abschaltet (tts start -> Firmware geht auf speaking).
        self.transcript_final_at = 0.0
        self.first_audio_at = 0.0
        self.first_audio_bytes = 0
        self.audio_before_transcript_bytes = 0
        self.audio_diag_bytes = 0
        self.audio_diag_blocks = 0
        self.audio_diag_peak = 0
        # Rueckhaltefenster fuer den Sprechbeginn. GPT-Live haelt den
        # Ausgabestrom durchgehend offen und sendet ab dem Verbindungsaufbau
        # reine Stille (gemessen: 3826 Bytes, Peak 0). Diese Stille darf kein
        # `tts start` ausloesen, sonst schaltet die Firmware auf "Sprechen"
        # und beendet die Sprachverarbeitung mitten im Satz des Nutzers.
        self.lead_silence = bytearray()

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

    def photo_endpoint(self):
        """HTTP-Adresse, unter der das Geraet sein JPEG hochlaedt.

        Das Geraet erreicht uns nur im eigenen Netz. Deshalb die Adresse
        verwenden, ueber die die WebSocket-Verbindung hereinkam.
        """
        host = None
        try:
            local = getattr(self.websocket, "local_address", None)
            if local and local[0] and local[0] not in ("0.0.0.0", "::"):
                host = local[0]
        except Exception:
            host = None
        if not host:
            host = self.args.public_host
        return f"http://{host}:{self.args.ota_port}/photo"

    async def initialize_mcp(self):
        try:
            # capabilities.vision ist der einzige Weg, dem Geraet die
            # Upload-Adresse mitzuteilen (McpServer::ParseCapabilities).
            response = await self.send_mcp_request(
                "initialize",
                {"capabilities": {"vision": {
                    "url": self.photo_endpoint(),
                    "token": "local-openclaw",
                }}},
                timeout=3.0,
            )
            if "error" in response:
                raise RuntimeError(response["error"])
            self.mcp_ready.set()
            print(
                f"{self.tag} Device MCP initialized (vision -> {self.photo_endpoint()})",
                flush=True,
            )
        except Exception as exc:
            print(f"{self.tag} Device MCP initialization failed: {exc}", flush=True)

    async def call_device_tool(self, name, arguments, timeout=3.0):
        await asyncio.wait_for(self.mcp_ready.wait(), timeout=3.0)
        try:
            response = await self.send_mcp_request(
                "tools/call",
                {"name": name, "arguments": arguments},
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # Setting an absolute value is idempotent. One retry handles an
            # occasional lost first MCP response without changing the result.
            response = await self.send_mcp_request(
                "tools/call",
                {"name": name, "arguments": arguments},
                timeout=timeout,
            )
        if "error" in response:
            raise RuntimeError(response["error"])
        result = response.get("result", {})
        if result.get("isError"):
            raise RuntimeError(result)
        return result

    @staticmethod
    def _normalize_alexa_command(command):
        collapsed = " ".join(command.strip().casefold().split())
        return collapsed.strip(" .,!?;:…-–—")

    def _alexa_already_sent(self, key):
        if not key:
            return False
        try:
            with open(self.ALEXA_DEDUPE_STATE_PATH, encoding="utf-8") as handle:
                state = json.load(handle)
        except (OSError, ValueError):
            return False
        if not isinstance(state, dict) or state.get("key") != key:
            return False
        try:
            sent_at = float(state.get("ts"))
        except (TypeError, ValueError):
            return False
        elapsed = time.time() - sent_at
        return 0.0 <= elapsed < self.ALEXA_DEDUPE_WINDOW_SECONDS

    def _alexa_record_sent(self, key):
        if not key:
            return
        try:
            os.makedirs(os.path.dirname(self.ALEXA_DEDUPE_STATE_PATH), exist_ok=True)
            with open(self.ALEXA_DEDUPE_STATE_PATH, "w", encoding="utf-8") as handle:
                json.dump({"key": key, "ts": time.time()}, handle)
        except OSError:
            pass

    @staticmethod
    def is_smart_home_fast_path(transcript):
        # Modus "ki": Direktpfad bewusst abgeschaltet lassen.
        if os.path.exists(
            "/home/openclaw/.local/share/jarvis-realtime-bridge/disable-smart-home-fastpath"
        ):
            return False
        normalized = " ".join(transcript.lower().replace("%", " prozent ").split())
        # German compounds are common device names ("Deckenventilator",
        # "Kuechenlampe"). Match the target as a word SUFFIX so prefixed
        # compounds hit the fast path instead of the slow cloud consult.
        smart_home_target = (
            r"[a-z\u00e4\u00f6\u00fc\u00df-]*"
            r"(?:monitor|licht|lampe|steckdose|jalousie|rollladen|"
            r"heizung|thermostat|ventilator)"
        )
        smart_home_action = (
            r"an|aus|ein|einschalten|ausschalten|hoch|runter|öffnen|"
            r"oeffnen|schließen|schliessen|dimmen|stelle|stell|mach|"
            r"schalte"
        )
        return bool(
            re.search(rf"(?:{smart_home_target})\b", normalized)
            and re.search(rf"\b(?:{smart_home_action})\b", normalized)
            and not re.search(r"\b(?:ob|warum|wieso|weshalb)\b", normalized)
            and not re.match(
                r"^(?:ist|sind|war|waren|steht|stehen|läuft|laeuft|geht)\b",
                normalized,
            )
        )

    # Foto-Wunsch in natuerlicher Sprache. Bewusst eng: nur ausdrueckliche
    # Foto-/Bild-Absichten, damit "mach das Licht an" nicht hier landet.
    PHOTO_PATTERN = re.compile(
        r"\b(?:mach|mache|nimm|nehme|schiess|schieße|knips|schick|schicke)\b"
        r"[^.]{0,40}\b(?:foto|bild)\b"
        r"|\b(?:foto|bild)\b[^.]{0,40}\b(?:mach|machen|aufnehmen|schiessen|schießen|schicken|schick)\b"
        r"|\bwas\s+(?:siehst|erkennst)\s+du\b"
        r"|\bschau\s+(?:mal|dir)\b"
        r"|\bsieh\s+dir\s+(?:das|mal)\s+an\b"
    )

    def _photo_speaker(self):
        """Piper-Stimme fuer die Foto-Antwort, lazily geladen.

        Der Realtime-Weg hat keine lokale Stimme; fuer die Bildantwort wird
        die installierte Piper-Stimme bei Bedarf geladen. Schlaegt das fehl,
        bleibt der Text sichtbar und es wird nichts gesprochen.
        """
        if self._photo_voice is not None:
            return self._photo_voice
        if PiperSpeaker is None or find_default_voice is None:
            return None
        try:
            model_path = find_default_voice(
                self.args.tts_voice_dir, self.args.tts_voice
            )
            self._photo_voice = PiperSpeaker(model_path)
            print(
                f"{self.tag} Photo voice ready: {self._photo_voice.describe()}",
                flush=True,
            )
        except Exception as exc:  # noqa: BLE001
            print(f"{self.tag} Photo voice failed: {exc!r}", flush=True)
            return None
        return self._photo_voice

    @staticmethod
    def _photo_answer_text(result):
        """Antworttext aus einem MCP-Tool-Ergebnis ziehen."""
        def walk(node):
            if isinstance(node, str):
                return node
            if isinstance(node, dict):
                for key in ("content", "result", "text"):
                    if key in node:
                        found = walk(node[key])
                        if found:
                            return found
                return ""
            if isinstance(node, list):
                for item in node:
                    found = walk(item)
                    if found:
                        return found
            return ""

        return walk(result).strip()

    async def speak_photo_answer(self, text):
        """Zeigt und spricht die Bildantwort, dann regulaerer Abschluss."""
        text = (text or "").strip() or "Ich konnte auf dem Bild nichts erkennen."
        await self.send_json({"type": "stt", "text": text})
        speaker = self._photo_speaker()
        if speaker is None:
            self.photo_speaking = False
            return
        try:
            pcm = await asyncio.to_thread(speaker.synthesize_pcm, text)
        except Exception as exc:  # noqa: BLE001
            print(f"{self.tag} Photo TTS failed: {exc!r}", flush=True)
            self.photo_speaking = False
            return
        if not pcm:
            self.photo_speaking = False
            return
        self.tts_started = False
        self.stream_end_sent = False
        await self.send_pcm_paced(pcm)
        self.stream_end_sent = True
        self.cancel_speaking_guard()
        await self.send_json({"type": "tts", "state": "stream_end"})
        self.schedule_drained_watchdog(len(pcm))
        # Schutz wieder aufheben: sonst würden alle weiteren Provider-Audio-
        # frames dauerhaft verworfen und die naechste normale Antwort bliebe
        # stumm.
        self.photo_speaking = False
        print(
            f"{self.tag} Photo answer spoken ({len(pcm)} PCM bytes)",
            flush=True,
        )

    async def handle_photo_request(self, transcript):
        """Kamera auslösen. Die Auswertung läuft getrennt (siehe /photo).

        Das Gerät besitzt das Werkzeug self.camera.take_photo bereits. Es
        schießt das JPEG und lädt es auf /photo hoch. Dieser Aufruf wartet
        nur bis das Gerät den Upload bestätigt hat — nicht auf den Agenten.
        """
        if not self.mcp_ready.is_set():
            print(f"{self.tag} Photo requested but MCP is not ready", flush=True)
            await self.speak_photo_answer(
                "Die Kamera ist noch nicht bereit. Bitte noch einmal versuchen."
            )
            return "photo"
        question = transcript.strip() or "Was ist auf dem Bild zu sehen?"
        print(f"{self.tag} Photo requested: {question!r}", flush=True)
        self.photo_speaking = True
        try:
            await self.call_device_tool(
                "self.camera.take_photo", {"question": question}, timeout=30.0
            )
        except Exception as exc:  # noqa: BLE001
            print(f"{self.tag} Photo tool failed: {exc!r}", flush=True)
            await self.speak_photo_answer(
                "Das Foto hat nicht geklappt. Bitte noch einmal versuchen."
            )
        # Die eigentliche Antwort kommt aus dem Upload-Handler, sobald das
        # JPEG angekommen ist. Hier bewusst nicht darauf warten.
        return "photo"

    async def run_photo_agent(self, path, question):
        """Agenten mit dem Bild befragen und die Antwort sprechen."""
        script = Path(__file__).resolve().parent / "photo-consult.mjs"
        if not script.is_file():
            print(f"{self.tag} photo-consult.mjs missing", flush=True)
            await self.speak_photo_answer("Die Bildauswertung fehlt auf dem Host.")
            return
        session_key = self.args.consult_session_key or self.args.session_key
        env = os.environ.copy()
        env.setdefault(
            "OPENCLAW_NPM_ROOT", "/home/openclaw/.npm-global/lib/node_modules"
        )
        env.setdefault("NODE_EXTRA_CA_CERTS", str(self.args.gateway_ca))
        process = None
        try:
            process = await asyncio.create_subprocess_exec(
                "node", str(script), session_key, str(path), "image/jpeg", question,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            out, err = await asyncio.wait_for(process.communicate(), timeout=90.0)
        except asyncio.TimeoutError:
            if process is not None and process.returncode is None:
                process.kill()
            print(f"{self.tag} Photo consult timed out", flush=True)
            await self.speak_photo_answer(
                "Das Foto ist da, die Auswertung hat zu lange gedauert."
            )
            return
        except Exception as exc:  # noqa: BLE001
            print(f"{self.tag} Photo consult failed: {exc!r}", flush=True)
            await self.speak_photo_answer(
                "Das Foto ist da, die Auswertung ist fehlgeschlagen."
            )
            return
        if process.returncode != 0:
            detail = (err or b"").decode("utf-8", "replace").strip()[:300]
            print(
                f"{self.tag} Photo consult exit={process.returncode} {detail}",
                flush=True,
            )
            await self.speak_photo_answer(
                "Das Foto ist da, die Auswertung ist fehlgeschlagen."
            )
            return
        answer = (out or b"").decode("utf-8", "replace").strip()
        print(f"{self.tag} Photo answer: {answer[:200]!r}", flush=True)
        try:
            await self.speak_photo_answer(answer)
        finally:
            self.photo_speaking = False

    async def handle_device_voice_command(self, transcript):
        normalized = " ".join(transcript.lower().replace("%", " prozent ").split())

        # Foto-Wunsch: Kamera ausloesen, Bild auswerten, Antwort sprechen.
        # Bewusst als Task: Der MCP-Aufruf wartet auf eine Antwort, die ueber
        # den WebSocket-Loop hereinkommt. Wuerde dieser Event-Leser darauf
        # warten, koennte er waehrenddessen keine Helfer-Events mehr abholen
        # und die stderr-Pipe volllaufen lassen.
        if self.PHOTO_PATTERN.search(normalized):
            self.photo_speaking = True
            asyncio.create_task(self.handle_photo_request(transcript))
            return "photo"

        # Smart-home fast path: clear imperative commands must not wait for an
        # agent consult. Forward the original transcript once to the same fixed
        # Alexa ioBroker datapoint as the iobroker_alexa plugin. Questions and
        # ambiguous statements deliberately fall through to the cloud agent.
        if self.is_smart_home_fast_path(transcript):
            command = transcript.strip()
            dedupe_key = self._normalize_alexa_command(command)
            if self._alexa_already_sent(dedupe_key):
                print(
                    f"{self.tag} Smart-home fast path deduplicated (Agent already sent): {command!r}",
                    flush=True,
                )
                return True

            def send_alexa_command():
                url = self.ALEXA_TEXT_COMMAND_ENDPOINT + urllib.parse.quote(
                    command, safe=""
                )
                request = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(request, timeout=3.0) as response:
                    response.read(1)
                    return response.status

            try:
                status = await asyncio.wait_for(
                    asyncio.to_thread(send_alexa_command), timeout=4.0
                )
                if status != 200:
                    raise RuntimeError(f"ioBroker HTTP {status}")
                self._alexa_record_sent(dedupe_key)
                print(
                    f"{self.tag} Smart-home fast path accepted by Alexa: {command!r} http={status}",
                    flush=True,
                )
            except Exception as exc:
                print(f"{self.tag} Smart-home fast path failed: {exc!r}", flush=True)
                return False
            return True

        number_words = {
            "null": 0, "eins": 1, "ein": 1, "zwei": 2, "drei": 3,
            "vier": 4, "fünf": 5, "fuenf": 5, "sechs": 6,
            "sieben": 7, "acht": 8, "neun": 9, "zehn": 10,
            "zwanzig": 20, "dreißig": 30, "dreissig": 30,
            "vierzig": 40, "fünfzig": 50, "fuenfzig": 50,
            "sechzig": 60, "siebzig": 70, "achtzig": 80,
            "neunzig": 90, "hundert": 100,
        }
        # Mehrsilbige Zahlwoerter zuerst, damit "fuenfzig" nicht als "fuenf"
        # gelesen wird. Die Einer (fuenf, acht, ...) fehlten zuvor ganz -
        # dadurch fiel "Helligkeit fuenf Prozent" durch.
        value_pattern = (
            r"(\d{1,3}|hundert|zwanzig|dreißig|dreissig|vierzig|"
            r"fünfzig|fuenfzig|sechzig|siebzig|achtzig|neunzig|"
            r"zehn|null|eins|ein|zwei|drei|vier|fünf|fuenf|"
            r"sechs|sieben|acht|neun)"
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
                print(f"{self.tag} Rejected device {label} outside 0-100: {value}", flush=True)
                return True
            try:
                await self.call_device_tool(tool_name, {argument_name: value})
                print(f"{self.tag} Device {label} set to {value}% via MCP", flush=True)
                display_label = "Lautstärke" if label == "volume" else "Helligkeit"
                await self.send_json({
                    "type": "stt",
                    "text": f"{display_label}: {value} %",
                })
                await asyncio.sleep(1.0)
            except Exception as exc:
                print(f"{self.tag} Device {label} MCP call failed: {exc!r}", flush=True)
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
        if self.helper_starting:
            # Der Start laeuft bereits (Vorwaermen beim hello). Ein zweiter
            # Aufruf aus dem ersten Mikrofonpaket darf keinen zweiten Helfer
            # erzeugen.
            return
        self.helper_starting = True
        try:
            await self._start_realtime_process()
        finally:
            self.helper_starting = False

    async def prewarm_realtime(self):
        """Oeffnet die Talk-Sitzung schon beim Verbindungsaufbau.

        Ohne diesen Schritt entsteht der Helfer erst mit dem ersten
        Mikrofonpaket - also nach dem Wachwort, waehrend der Nutzer bereits
        spricht. Die Anlaufzeit der Sitzung faellt dann in den gesprochenen
        Satz und die ersten Woerter gehen verloren.
        """
        loop = asyncio.get_running_loop()
        self.helper_started_at = loop.time()
        print(f"{self.tag} Prewarm: starting talk helper before first microphone packet", flush=True)
        try:
            await self.start_realtime()
        except Exception as exc:  # noqa: BLE001
            print(f"{self.tag} Prewarm failed: {exc!r}", flush=True)

    async def _start_realtime_process(self):
        self.turn_id = str(uuid.uuid4())
        self.tts_started = False
        self.pcm_buffer = bytearray()
        self.input_stopped = False
        self.last_sent_audio_at = 0.0
        self.pcm_bytes_received = 0
        self.audio_progress_event = asyncio.Event()
        self.stream_end_sent = False
        self.transcript_final_at = 0.0
        self.first_audio_at = 0.0
        self.first_audio_bytes = 0
        self.audio_before_transcript_bytes = 0
        self.audio_diag_bytes = 0
        self.audio_diag_blocks = 0
        self.audio_diag_peak = 0
        self.lead_silence = bytearray()

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
            OPENCLAW_VOICE_DEVICE_ID=getattr(self, "device_id", "") or "",
            OPENCLAW_VOICE_ROOM=getattr(self, "device_room", "") or "",
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
                if self.photo_speaking:
                    # Waehrend einer Foto-Runde entsteht die Antwort aus dem
                    # Bild-Consult. Ein zwischengesprochenes "Einen Moment"
                    # des Realtime-Modells wuerde das Foto uebertoenen.
                    continue
                loop_now = asyncio.get_running_loop().time()
                if self.first_audio_at <= 0:
                    self.first_audio_at = loop_now
                    self.first_audio_bytes = len(chunk)
                    lead = (
                        loop_now - self.transcript_final_at
                        if self.transcript_final_at > 0
                        else None
                    )
                    print(
                        f"{self.tag} DIAG first provider audio bytes={len(chunk)} "
                        f"peak={self._pcm_peak(chunk)} "
                        f"transcript_final={'yes' if lead is not None else 'NO'} "
                        f"lead={f'{lead:+.3f}s' if lead is not None else 'n/a'}",
                        flush=True,
                    )
                self.audio_diag_blocks += 1
                self.audio_diag_peak = max(self.audio_diag_peak, self._pcm_peak(chunk))
                self.audio_diag_bytes += len(chunk)
                if self.transcript_final_at <= 0:
                    self.audio_before_transcript_bytes += len(chunk)
                self.pcm_bytes_received += len(chunk)
                self.audio_progress_event.set()
                self.pcm_buffer.extend(chunk)
                while len(self.pcm_buffer) >= frame_bytes:
                    pcm = bytes(self.pcm_buffer[:frame_bytes])
                    del self.pcm_buffer[:frame_bytes]
                    # Forward the first provider audio frame immediately.
                    # Do not wait for transcript text or segment classification.
                    if not self.tts_started:
                        peak = self._pcm_peak(pcm)
                        if peak < self.PROVIDER_SPEECH_PEAK:
                            # Noch kein Sprachbeginn: Stille verwerfen, aber
                            # einen kurzen Ansatz als Rueckhaltefenster
                            # behalten, damit die erste Silbe nicht fehlt.
                            self.lead_silence.extend(pcm)
                            if len(self.lead_silence) > self.LEAD_LOOKBACK_BYTES:
                                del self.lead_silence[
                                    : len(self.lead_silence) - self.LEAD_LOOKBACK_BYTES
                                ]
                            continue
                        if self.lead_silence:
                            lookback = bytes(self.lead_silence)
                            self.lead_silence.clear()
                            print(
                                f"{self.tag} Provider speech onset peak={peak}; "
                                f"flushing {len(lookback)} lookback bytes",
                                flush=True,
                            )
                            # send_pcm kodiert genau einen 60-ms-Rahmen
                            # (1440 Samples). Den Rueckhalteblock deshalb
                            # rahmenweise ausliefern, nie am Stueck.
                            for offset in range(0, len(lookback), frame_bytes):
                                part = lookback[offset:offset + frame_bytes]
                                if len(part) < frame_bytes:
                                    part = part + b"\x00" * (frame_bytes - len(part))
                                await self.send_pcm(part)
                    await self.send_pcm(pcm)
        except Exception as exc:
            print(f"{self.tag} audio relay failed: {exc}", flush=True)

    @staticmethod
    def _pcm_peak(pcm):
        """Hoechster Absolutwert eines 16-Bit-Mono-Blocks (0..32767)."""
        from array import array

        if len(pcm) < 2:
            return 0
        samples = array("h")
        samples.frombytes(pcm[: len(pcm) - (len(pcm) % 2)])
        if samples.itemsize != 2:  # pragma: no cover
            return 0
        return max((abs(s) for s in samples), default=0)

    # Ab diesem Pegel gilt ein Provider-Block als Sprache. Gemessen: reine
    # Stille liegt bei Peak 0, normale Sprache bei 700..17000. Der Wert ist
    # bewusst niedrig, damit ein leiser Sprechbeginn nicht abgeschnitten wird.
    PROVIDER_SPEECH_PEAK = 60
    # So viel Ton wird vor dem erkannten Sprechbeginn zurueckgehalten, damit
    # der erste Silbenansatz nicht verloren geht (120 ms bei 24 kHz/16 Bit).
    LEAD_LOOKBACK_BYTES = 24000 * 2 * 120 // 1000

    async def send_pcm(self, pcm):
        if not self.tts_started:
            loop_now = asyncio.get_running_loop().time()
            lead = (
                loop_now - self.transcript_final_at
                if self.transcript_final_at > 0
                else None
            )
            print(
                f"{self.tag} DIAG tts start (device goes speaking) "
                f"transcript_final={'yes' if lead is not None else 'NO'} "
                f"lead={f'{lead:+.3f}s' if lead is not None else 'n/a'} "
                f"audio_before_transcript_bytes={self.audio_before_transcript_bytes} "
                f"diag_bytes={self.audio_diag_bytes} diag_blocks={self.audio_diag_blocks} "
                f"diag_peak={self.audio_diag_peak}",
                flush=True,
            )
            await self.send_json({"type": "tts", "state": "start"})
            self.tts_started = True
            self.arm_speaking_guard()
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

    def cancel_drained_watchdog(self):
        if self.drained_watchdog is not None and not self.drained_watchdog.done():
            self.drained_watchdog.cancel()
        self.drained_watchdog = None

    # Nach dieser Zeit ohne neues Audio gilt eine Sprachausgabe als haengend.
    SPEAKING_STALL_SECONDS = 45.0
    # Ein laufendes Werkzeug pausiert die Pruefung. Kommt aber selbst nach
    # dieser Zeit kein Ergebnis (abgebrochener Consult), wird trotzdem beendet.
    SPEAKING_TOOL_MAX_SECONDS = 150.0

    def cancel_speaking_guard(self):
        if self.speaking_guard is not None and not self.speaking_guard.done():
            self.speaking_guard.cancel()
        self.speaking_guard = None

    def arm_speaking_guard(self):
        """Verhindert, dass ein Geraet dauerhaft auf 'Sprechen' stehen bleibt.

        Die Bridge sendet 'tts start', sobald das erste Audioframe rausgeht.
        Kommt danach kein sauberes 'tts stream_end' (z. B. weil eine Sitzung
        ohne Abschluss endet), wartete das Geraet frueher unbegrenzt weiter -
        es blieb auf 'Sprechen' stehen und befreite sich erst, wenn der Nutzer
        etwas Neues sagte.

        Dieser Waechter schliesst die Sprachausgabe, wenn laenger kein Audio
        mehr fliesst und kein Ende gesetzt wurde. Waehrend ein Werkzeug
        (Agent-Consult) laeuft, pausiert die Pruefung bewusst, damit eine
        laengere Werkzeuglaufzeit die Antwort nicht abschneidet.
        """
        self.cancel_speaking_guard()

        async def guard():
            loop = asyncio.get_running_loop()
            while True:
                await asyncio.sleep(1.0)
                if self.stream_end_sent or not self.tts_started:
                    return
                if self.pending_tool:
                    tool_age = loop.time() - self.pending_tool_since
                    if tool_age < self.SPEAKING_TOOL_MAX_SECONDS:
                        continue
                    print(
                        f"{self.tag} Tool still pending after {tool_age:.0f}s; "
                        "closing this speaking round anyway",
                        flush=True,
                    )
                if self.last_sent_audio_at <= 0:
                    continue
                idle = loop.time() - self.last_sent_audio_at
                if idle >= self.SPEAKING_STALL_SECONDS:
                    print(
                        f"{self.tag} Speaking stalled {idle:.0f}s without "
                        "stream_end; sending tts stop so the device returns "
                        "to standby",
                        flush=True,
                    )
                    try:
                        await self.send_json({"type": "tts", "state": "stop"})
                    except Exception:
                        pass
                    # Stop stale provider output and force an idle transition.
                    # A timed-out round must not keep the microphone blocked.
                    self.input_stopped = True
                    self.websocket.transport.abort()
                    return

        self.speaking_guard = asyncio.create_task(guard())

    async def win_command_arbitration(self):
        """Nur das lauteste Geraet fuehrt den Dialog - Vergleich der Pegel.

        Kein Zeitfenster: das erste Geraet gewinnt sofort. Kommt spaeter ein
        Geraet mit hoeherem Pegel dazu, wird das bisher aktive in Bereitschaft
        geschickt (siehe WakeArbiter).
        """
        if self.arbiter is None:
            return True
        peak = self.turn_peak
        self.awaiting_command = False
        won = await self.arbiter.claim(self, peak)
        if not won:
            await self.return_to_standby("wake_arbitration_lost")
        return won

    async def return_to_standby(self, reason="standby"):
        """Ein Geraet sofort in Bereitschaft schicken, ohne Dialog."""
        self.cancel_followup_timeout()
        self.cancel_session_finish()
        self.cancel_drained_watchdog()
        self.input_stopped = True
        try:
            await asyncio.wait_for(
                self.websocket.close(code=1000, reason=reason),
                timeout=2.0,
            )
        except Exception:
            try:
                self.websocket.transport.abort()
            except Exception:
                pass

    def schedule_drained_watchdog(self, playback_bytes=0):
        """Geraete ohne playback_drained trotzdem in Bereitschaft holen.

        Das Audio-Board meldet das Ende der Wiedergabe nicht. Ohne diesen
        Wecker bliebe es nach der Antwort im Sprech-/Zuhoerzustand stehen.
        Geraete, die das Ereignis senden (der 4B), sind hier laengst weiter -
        dann ist der Wecker ein wirkungsloser No-Op.

        Gewartet wird die geschaetzte Wiedergabedauer (24 kHz, 16 Bit, mono
        = 48000 Byte/s) plus Reserve, damit die Antwort nicht abgeschnitten
        wird.
        """
        self.cancel_drained_watchdog()
        playback_seconds = max(0.0, playback_bytes / 48000.0)

        async def fallback_after_playback():
            await asyncio.sleep(playback_seconds + 0.8)
            if not self.followup_open:
                print(
                    f"{self.tag} No playback_drained from device; "
                    f"opening follow-up window anyway (waited "
                    f"{playback_seconds + 0.8:.1f}s)",
                    flush=True,
                )
                await self.open_followup_window()

        self.drained_watchdog = asyncio.create_task(fallback_after_playback())

    def schedule_followup_timeout(self):
        self.cancel_followup_timeout()

        async def close_after_followup_window():
            print(f"{self.tag} Follow-up window open for 10 seconds", flush=True)
            await asyncio.sleep(10.0)
            print(
                f"{self.tag} Follow-up window expired; returning to standby "
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

    async def open_followup_window(self, audio_guard_seconds=0.0):
        """Return the still-open device session to listening for 10 seconds."""
        await self.send_json({"type": "tts", "state": "stop"})
        self.tts_started = False
        self.pcm_buffer = bytearray()
        self.pcm_bytes_received = 0
        # Rueckhaltefenster der vorigen Runde verwerfen: sonst koennte ein
        # Rest in die naechste Antwort bluten und den Sprechbeginn verfaelschen.
        self.lead_silence = bytearray()
        self.audio_progress_event = asyncio.Event()
        self.input_stopped = False
        self.followup_open = True
        self.followup_audio_packets = 0
        self.followup_forwarded_packets = 0
        self.followup_peak = 0
        self.followup_audio_guard_until = (
            asyncio.get_running_loop().time() + audio_guard_seconds
        )
        self.tts_text_buffer = ""
        self.local_pcm_bytes_sent = 0
        await self.start_realtime()
        self.schedule_followup_timeout()

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

    def schedule_fast_path_stream_end(self, process):
        """Close a fast-path acknowledgement even if no final text arrives."""
        self.cancel_session_finish()

        async def finish_after_audio_idle():
            loop = asyncio.get_running_loop()
            deadline = loop.time() + 8.0
            while loop.time() < deadline:
                if (
                    self.tts_started
                    and self.last_sent_audio_at > 0
                    and loop.time() - self.last_sent_audio_at >= 0.45
                ):
                    await self.finish_audio_stream(process, self.pcm_bytes_received)
                    return
                await asyncio.sleep(0.05)
            # No usable acknowledgement arrived. Never leave the firmware in
            # speaking forever; end this command deterministically.
            await self.send_json({"type": "tts", "state": "stop"})
            self.websocket.transport.abort()

        self.finish_task = asyncio.create_task(finish_after_audio_idle())

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
                f"{self.tag} PCM relay stalled at {self.pcm_bytes_received}/{expected_pcm_bytes} bytes",
                flush=True,
            )
            await self.send_json({"type": "tts", "state": "stop"})
            return

        if not self.tts_started:
            # Kein Sprachbeginn erkannt: Der Provider hat fuer diese Runde nur
            # Stille geliefert (gemessen: Peak 0). Ohne Abschluss bliebe das
            # Geraet im Zuhoeren stehen, waehrend die Bridge das Mikrofon
            # bereits verwirft. Deshalb kontrolliert ins Nachhoerfenster
            # wechseln, damit der naechste Satz wieder ankommt.
            print(
                f"{self.tag} No provider speech in this round "
                f"(diag_peak={self.audio_diag_peak}); "
                "reopening listening window",
                flush=True,
            )
            await self.open_followup_window()
            return

        self.stream_end_sent = True
        self.cancel_speaking_guard()
        await self.send_json({"type": "tts", "state": "stream_end"})
        self.schedule_drained_watchdog(expected_pcm_bytes)
        print(
            f"{self.tag} Audio stream complete at {expected_pcm_bytes} PCM bytes; "
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
                    print(f"{self.tag} local TTS failed: {exc!r}", flush=True)
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
        mode = getattr(self.args, "tts_split", "sentence")
        if mode == "whole":
            # Erst am Ende der Antwort sprechen (siehe finish_local_tts).
            self.tts_text_buffer += text
            return
        self.tts_text_buffer += text
        while True:
            boundary = self._tts_boundary(self.tts_text_buffer, mode)
            if boundary <= 0:
                break
            phrase = self.tts_text_buffer[:boundary].strip()
            self.tts_text_buffer = self.tts_text_buffer[boundary:]
            if phrase:
                self.tts_pending += 1
                self.tts_queue.put_nowait(phrase)

    @staticmethod
    def _tts_boundary(text, mode="sentence"):
        """Index hinter der naechsten Sprechgrenze, sonst bei Ueberlaenge.

        `mode` steuert, wo getrennt wird:

        - `clause`:  schon am Komma. Die Stimme beginnt am fruehesten,
          klingt aber zerstueckelter (kurze Stuecke, mehr Nahtstellen).
        - `sentence` (Standard): am Satzende (`. ! ? ; : …`). Guter
          Kompromiss — frueher Beginn, wenige Nahtstellen.
        - `whole`: nicht trennen. Die ganze Antwort wird in einem Stueck
          gesprochen; die Stimme beginnt spaet, klingt dafuer am ruhigsten.
        """
        if mode == "whole":
            return 0
        breaks = ".!?;:…" if mode == "sentence" else ".!?;:…,"
        for index, char in enumerate(text):
            if char not in breaks:
                continue
            # Steht links und rechts eine Ziffer, trennt das Zeichen keine
            # Saetze, sondern gehoert zur Zahl: Uhrzeit (13:52), Dezimalzahl
            # (13.95) oder Tausendertrennung. Ein Schnitt dort ergaebe
            # "Es ist 13:" — hoerbar abgehackt mitten in der Zahl.
            left_digit = index > 0 and text[index - 1].isdigit()
            right_digit = index + 1 < len(text) and text[index + 1].isdigit()
            if left_digit and right_digit:
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
            print(f"{self.tag} local TTS drain timed out", flush=True)
        self.tts_worker_task = None
        if not self.tts_started:
            # Nichts zu sprechen (leere Antwort). Kein stream_end noetig.
            return
        self.stream_end_sent = True
        self.cancel_speaking_guard()
        await self.send_json({"type": "tts", "state": "stream_end"})
        self.schedule_drained_watchdog(self.local_pcm_bytes_sent)
        print(
            f"{self.tag} Local voice stream complete at {self.local_pcm_bytes_sent} PCM bytes; "
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
                    print(f"{self.tag} {text}", flush=True)
                    continue
                kind = event.get("type")
                if kind == "user_transcript":
                    self.cancel_followup_timeout()
                    self.followup_open = False
                    transcript = event.get("text", "")
                    # Nur das lauteste Geraet darf antworten. Ein Geraet mit
                    # hoeherem Pegel uebernimmt und schickt dieses hier in
                    # Bereitschaft - so kann kein Befehl doppelt laufen.
                    if not await self.win_command_arbitration():
                        self.input_stopped = True
                        continue
                    # A finalized new utterance starts the next response. Only
                    # now may provider audio be forwarded again; until here,
                    # trailing frames from the previous response stay dropped.
                    self.stream_end_sent = False
                    # Re-arm per utterance even when provider audio preceded
                    # the final transcript and the previous guard already exited.
                    self.arm_speaking_guard()
                    self.transcript_final_at = asyncio.get_running_loop().time()
                    print(
                        f"{self.tag} User transcript: {transcript} "
                        f"[diag first_audio={'yes' if self.first_audio_at > 0 else 'NO'} "
                        f"audio_before_transcript_bytes={self.audio_before_transcript_bytes}]",
                        flush=True,
                    )
                    # Jetzt ist klar, welches Geraet den Nutzer gehoert hat.
                    # Erst hier den Raum hinterlegen, damit bei zwei Geraeten
                    # nicht das zuletzt verbundene gewinnt.
                    if getattr(self, "device_room", None):
                        publish_active_room(self.device_id, self.device_room)
                    await self.send_json({"type": "stt", "text": transcript})
                    device_command_handled = await self.handle_device_voice_command(transcript)
                    if device_command_handled == "photo":
                        # Die Foto-Runde spricht ihre Antwort selbst und sendet
                        # dabei ihr eigenes stream_end. Kein Fast-Path-Abschluss:
                        # der wuerde die laufende Wiedergabe abschneiden.
                        self.input_stopped = True
                        print(
                            f"{self.tag} Photo turn started; awaiting camera result",
                            flush=True,
                        )
                        continue
                    if device_command_handled:
                        # The direct HTTP action is already complete. Keep the
                        # realtime helper alive so its spoken acknowledgement
                        # can finish cleanly. The helper suppresses the general
                        # agent consult for this exact fast-path utterance, so
                        # no second Alexa command can be emitted. Playback drain
                        # will open the normal follow-up listening window only
                        # after the acknowledgement has physically finished.
                        self.input_stopped = True
                        print(
                            f"{self.tag} Device command complete; waiting for spoken acknowledgement",
                            flush=True,
                        )
                        self.schedule_fast_path_stream_end(process)
                        continue
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
                        f"{self.tag} OpenClaw realtime event: "
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
                    print(f"{self.tag} OpenClaw realtime error: {event.get('message')}", flush=True)
                elif kind == "tool_call":
                    if not self.pending_tool:
                        self.pending_tool_since = asyncio.get_running_loop().time()
                    self.pending_tool = True
                    self.cancel_session_finish()
                    print(
                        f"{self.tag} OpenClaw realtime event: "
                        + json.dumps(event, ensure_ascii=False),
                        flush=True,
                    )
                elif kind == "audio_after_assistant_done":
                    print(
                        f"{self.tag} OpenClaw realtime event: "
                        + json.dumps(event, ensure_ascii=False),
                        flush=True,
                    )
                elif kind == "assistant_done":
                    print(
                        f"{self.tag} OpenClaw realtime event: "
                        + json.dumps(event, ensure_ascii=False),
                        flush=True,
                    )
                    if event.get("text") and not self.is_interim_text(event["text"]):
                        if self.photo_speaking:
                            # Eine Foto-Runde spricht ihre Antwort selbst. Das
                            # generische stream_end des Realtime-Modells wuerde
                            # die laufende Wiedergabe abschneiden.
                            print(
                                f"{self.tag} Photo turn active; suppressing provider stream_end",
                                flush=True,
                            )
                        elif self.tts_local:
                            self.schedule_local_stream_end(process)
                        else:
                            self.schedule_stream_end(process)
                elif kind in {"ready", "tool_result"}:
                    if kind == "tool_result":
                        self.pending_tool = False
                    if kind == "ready":
                        self.helper_ready.set()
                        if self.helper_started_at:
                            self.helper_ready_at = asyncio.get_running_loop().time()
                            print(
                                f"{self.tag} Prewarm: helper ready after "
                                f"{self.helper_ready_at - self.helper_started_at:.2f}s",
                                flush=True,
                            )
                    print(
                        f"{self.tag} OpenClaw realtime event: "
                        + json.dumps(event, ensure_ascii=False),
                        flush=True,
                    )
        except Exception as exc:
            print(f"{self.tag} event relay failed: {exc}", flush=True)

    async def feed_opus(self, packet):
        # A finalized utterance is already being answered. In particular, do
        # not spawn a replacement helper after a fast-path helper exits while
        # its acknowledgement is still playing.
        if self.input_stopped:
            return
        await self.start_realtime()
        if self.followup_open:
            self.followup_audio_packets += 1
            if self.followup_audio_packets == 1:
                print(f"{self.tag} First follow-up microphone packet received", flush=True)
        try:
            pcm = self.decoder.decode(packet, 960, False)
        except opuslib.OpusError as exc:
            print(f"{self.tag} invalid Opus packet: {exc}", flush=True)
            return
        if pcm:
            # Pegel mitschreiben: ein zu hoher Schwellwert in der
            # Aeusserungserkennung faellt sonst nicht auf.
            samples = memoryview(pcm).cast("h")
            peak = max((abs(sample) for sample in samples), default=0)
            if self.awaiting_command:
                # Laeuft der laufende Befehl, wird der hoechste Pegel seit dem
                # Wecken festgehalten. Das ist die Grundlage des Vergleichs.
                self.turn_peak = max(self.turn_peak, peak)
            self.audio_packets += 1
            self.audio_peak = max(self.audio_peak, peak)
            if self.audio_packets % 25 == 0:
                print(
                    f"{self.tag} Mic audio: packets={self.audio_packets} "
                    f"peak={self.audio_peak} followup={self.followup_open}",
                    flush=True,
                )
                self.audio_peak = 0
        if self.followup_open and pcm:
            samples = memoryview(pcm).cast("h")
            self.followup_peak = max(
                self.followup_peak,
                max((abs(sample) for sample in samples), default=0),
            )
        if (
            self.process
            and self.process.stdin
            and not self.process.stdin.is_closing()
        ):
            if (
                self.followup_open
                and asyncio.get_running_loop().time()
                < self.followup_audio_guard_until
            ):
                return
            if self.followup_open:
                self.followup_forwarded_packets += 1
            self.process.stdin.write(pcm)
            await self.process.stdin.drain()

    async def close(self, abort=False):
        self.cancel_session_finish()
        self.cancel_followup_timeout()
        self.cancel_drained_watchdog()
        self.cancel_speaking_guard()
        if self.arbiter is not None:
            self.arbiter.unregister(self)
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
                    # Vorwaermen: Die Talk-Sitzung des Providers braucht rund
                    # 3 Sekunden bis sie zuhoert. Hier ist der einzige
                    # Zeitpunkt, der frueh genug liegt: das Geraet hat gerade
                    # den Kanal geoeffnet und der Nutzer hat das Wachwort noch
                    # nicht gesprochen. Bewusst als Task, damit der
                    # hello-Austausch nicht darauf wartet.
                    asyncio.create_task(self.prewarm_realtime())
                elif kind == "mcp":
                    mcp_payload = payload.get("payload", {})
                    request_id = mcp_payload.get("id")
                    pending = self.mcp_pending.get(request_id)
                    if pending and not pending.done():
                        pending.set_result(mcp_payload)
                elif kind == "listen" and payload.get("state") == "start":
                    # Neuer Befehl: Pegelmessung neu starten und den Dialog
                    # aufnehmen. Wer wirklich gehoert wurde, entscheidet sich
                    # erst beim fertigen Transkript (Pegelvergleich).
                    self.turn_peak = 0
                    self.awaiting_command = True
                    await self.start_realtime()
                elif (
                    kind == "device"
                    and payload.get("event") == "state"
                ):
                    # Die Firmware meldet bei jedem Zustandswechsel ihren
                    # echten Zustand. Bisher wurde das ignoriert; die Bridge
                    # musste den Geraetezustand aus dem Timing erraten. Wir
                    # protokollieren ihn, damit sich Anzeige und Wirklichkeit
                    # bei Bedarf abgleichen lassen.
                    print(
                        f"{self.tag} Device state: {payload.get('state')}",
                        flush=True,
                    )
                    # In the follow-up window, "speaking" means the device
                    # has accepted a new utterance and is waiting for its
                    # result. Do not let the original idle timer close the
                    # socket while that utterance is still being processed.
                    if (
                        self.followup_open
                        and payload.get("state") == "speaking"
                    ):
                        self.cancel_followup_timeout()
                        self.followup_open = False
                        print(
                            f"{self.tag} Follow-up utterance accepted; standby timeout cancelled",
                            flush=True,
                        )
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
                    self.cancel_drained_watchdog()
                    print(
                        f"{self.tag} Physical playback drained; opening follow-up listening window",
                        flush=True,
                    )
                    await self.open_followup_window()
                elif kind == "abort":
                    break
        finally:
            await self.close(abort=True)


ACTIVE_SESSIONS = {}


def _split_multipart(body, boundary):
    """Felder eines multipart/form-data-Rumpfs als {name: bytes}.

    Das Geraet sendet genau zwei Teile: `question` (Text) und `file`
    (JPEG, Dateiname camera.jpg). Der JPEG-Teil wird binaer belassen; es
    wird nur die eine Trennzeile am Ende entfernt, die zum naechsten
    Boundary gehoert.
    """
    marker = ("--" + boundary).encode()
    parts = {}
    for chunk in body.split(marker):
        if chunk.startswith(b"--"):
            break
        chunk = chunk.lstrip(b"\r\n")
        if not chunk:
            continue
        head, sep, payload = chunk.partition(b"\r\n\r\n")
        if not sep:
            continue
        name = None
        for line in head.decode("latin1", "replace").split("\r\n"):
            if line.lower().startswith("content-disposition:"):
                found = re.search(r'name="([^"]+)"', line)
                if found:
                    name = found.group(1)
        if payload.endswith(b"\r\n"):
            payload = payload[:-2]
        if name:
            parts[name] = payload
    return parts

async def _read_chunked(reader):
    """Liest einen HTTP/1.1-chunked-Rumpf vollstaendig."""
    data = bytearray()
    while True:
        line = await reader.readuntil(b"\r\n")
        size_text = line.strip().split(b";")[0]
        if not size_text:
            break
        try:
            size = int(size_text, 16)
        except ValueError:
            break
        if size == 0:
            try:
                await reader.readuntil(b"\r\n")
            except Exception:
                pass
            break
        data.extend(await reader.readexactly(size))
        try:
            await reader.readexactly(2)
        except Exception:
            break
    return bytes(data)



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
    parser.add_argument(
        "--tts-split",
        choices=("clause", "sentence", "whole"),
        default="sentence",
        help=(
            "Wo der Sprechtext getrennt wird: clause = am Komma (fruehester "
            "Beginn), sentence = am Satzende (Standard), whole = die ganze "
            "Antwort in einem Stueck (spaetester Beginn, am ruhigsten)"
        ),
    )
    # Feinregler der Stimme (Piper). Weggelassen = Pipers Standard.
    parser.add_argument(
        "--tts-length-scale",
        type=float,
        default=None,
        help="Tempo: 1.0 normal, kleiner = schneller, groesser = langsamer",
    )
    parser.add_argument(
        "--tts-noise-scale",
        type=float,
        default=None,
        help="Aussprache-Variation: kleiner = gleichmaessiger",
    )
    parser.add_argument(
        "--tts-noise-w-scale",
        type=float,
        default=None,
        help="Lautlaengen-Variation: kleiner = gleichmaessiger",
    )
    parser.add_argument(
        "--tts-volume",
        type=float,
        default=None,
        help="Lautstaerke der Synthese (0.0-1.0, nicht die Geraetelautstaerke)",
    )
    args = parser.parse_args()

    # Ein gemeinsamer Schiedsrichter fuer alle Geraete dieser Bridge: nur das
    # Geraet mit dem hoechsten Mikrofonpegel fuehrt einen Dialog.
    args.arbiter = WakeArbiter()

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
        args.speaker = PiperSpeaker(
            model_path,
            length_scale=args.tts_length_scale,
            noise_scale=args.tts_noise_scale,
            noise_w_scale=args.tts_noise_w_scale,
            volume=args.tts_volume,
        )
        print(f"Local voice ready: {args.speaker.describe()}", flush=True)
        print(f"Local voice split mode: {args.tts_split}", flush=True)
    else:
        args.speaker = None

    async def handler(websocket):
        peer_ip = websocket.remote_address[0] if websocket.remote_address else "?"
        dev_tag = f"[{peer_ip.split('.')[-1]}]"
        print(f"{dev_tag} XiaoZhi connected from {websocket.remote_address}", flush=True)
        # Raum des Geraets aus der Device-Id (MAC) im Handshake ermitteln.
        # Bewusst nur merken, NICHT veroeffentlichen: bei zwei verbundenen
        # Geraeten wuerde sonst das zuletzt verbundene gewinnen. Veroeffentlicht
        # wird erst beim Sprechen, nach der Weckwort-Absprache.
        device_id = None
        room = None
        try:
            headers = websocket.request.headers if websocket.request else {}
            device_id = headers.get("Device-Id") or headers.get("device-id")
            room = room_for_device(device_id)
            if room:
                print(f"{dev_tag} Raum aus Device-Id {device_id}: {room}", flush=True)
            else:
                print(f"{dev_tag} Kein Raum hinterlegt fuer Device-Id {device_id!r}", flush=True)
        except Exception as exc:
            print(f"{dev_tag} Raum-Ermittlung fehlgeschlagen: {exc!r}", flush=True)
        try:
            session = XiaozhiSession(websocket, args)
            session.device_id = device_id
            session.device_room = room
            if device_id:
                ACTIVE_SESSIONS[device_id.upper()] = session
            try:
                await session.run()
            finally:
                if device_id and ACTIVE_SESSIONS.get(device_id.upper()) is session:
                    ACTIVE_SESSIONS.pop(device_id.upper(), None)
        except Exception as exc:
            print(f"{dev_tag} XiaoZhi session failed: {exc}", flush=True)

    stop = asyncio.get_running_loop().create_future()
    for sig in (signal.SIGTERM, signal.SIGINT):
        asyncio.get_running_loop().add_signal_handler(sig, stop.set_result, None)
    async def handle_photo(body, boundary, device_id, peer):
        """Nimmt das JPEG an und startet die Auswertung im Hintergrund.

        Rueckgabe geht sofort an das Gerät: Es bestätigt den Upload und
        beendet damit seinen HTTP-Aufruf. Der Agent braucht deutlich länger
        als eine HTTP-Antwort zulässt — würde hier gewartet, liefe das Gerät
        in seinen Timeout und das Werkzeug meldete "Failed to upload photo".
        Die Antwort wird stattdessen über die offene WebSocket-Session
        gesprochen, sobald sie vorliegt.
        """
        fields = _split_multipart(body, boundary)
        image = fields.get("file") or b""
        if not image:
            print(f"Photo from {peer}: no image payload", flush=True)
            return "Das Foto ist leer angekommen."
        question = fields.get("question", b"").decode("utf-8", "replace").strip()
        if not question:
            question = "Was ist auf dem Bild zu sehen?"

        stamp = time.strftime("%Y%m%d-%H%M%S")
        safe_id = re.sub(r"[^0-9A-Za-z]", "", device_id or "device")
        target = Path(
            f"/home/openclaw/.local/share/jarvis-realtime-bridge/photo-{safe_id}-{stamp}.jpg"
        )
        try:
            target.write_bytes(image)
        except OSError as exc:
            print(f"Photo from {peer}: cannot save {exc!r}", flush=True)
            target = None
        print(
            f"Photo from {peer} device={device_id}: {len(image)} bytes, "
            f"question={question!r}, saved={target}",
            flush=True,
        )
        if not image.startswith(b"\xff\xd8"):
            return "Das Bild kam nicht als JPEG an."

        if target is None:
            return "Ich konnte das Foto nicht speichern."

        session = ACTIVE_SESSIONS.get((device_id or "").upper())
        if session is None:
            print(
                f"Photo from {peer}: no active session for device {device_id!r}",
                flush=True,
            )
            return "Das Foto ist da, aber keine Sprachverbindung aktiv."

        # Auswertung getrennt starten; der Upload wird sofort bestätigt.
        asyncio.create_task(session.run_photo_agent(target, question))
        return "Einen Moment, ich schaue mir das Bild an."

    async def ota_handler(reader, writer):
        try:
            header = await reader.readuntil(b"\r\n\r\n")
            lines = header.decode("latin1").split("\r\n")
            method = (lines[0].split(" ")[0] if lines else "GET").upper()
            path = lines[0].split(" ")[1] if len(lines[0].split(" ")) > 1 else "/"
            content_length = 0
            chunked = False
            boundary = ""
            device_id = None
            for line in lines:
                low = line.lower()
                if low.startswith("content-length:"):
                    content_length = int(line.split(":", 1)[1].strip())
                elif low.startswith("transfer-encoding:") and "chunked" in low:
                    chunked = True
                elif low.startswith("content-type:") and "boundary=" in low:
                    boundary = line.split("boundary=", 1)[1].strip().strip('"')
                elif low.startswith("device-id:"):
                    device_id = line.split(":", 1)[1].strip()

            # --- Foto-Upload des Geraets -------------------------------
            if path.startswith("/photo"):
                body = b""
                if chunked:
                    body = await _read_chunked(reader)
                elif content_length:
                    body = await reader.readexactly(content_length)
                answer = await handle_photo(
                    body, boundary or "----ESP32_CAMERA_BOUNDARY",
                    device_id, writer.get_extra_info("peername"),
                )
                payload = answer.encode("utf-8")
                writer.write(
                    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain; charset=utf-8\r\n"
                    + f"Content-Length: {len(payload)}\r\nConnection: close\r\n\r\n".encode()
                    + payload
                )
                await writer.drain()
                return

            if chunked:
                await _read_chunked(reader)
            elif content_length:
                await reader.readexactly(content_length)
            # Das Geraet kann uns aus einem anderen Subnetz erreichen
            # (z. B. 192.168.68.0/24 statt 192.168.178.0/24). Wir antworten
            # darum mit der lokalen Adresse, ueber die die OTA-Anfrage
            # hereinkam: so liegt die WebSocket-URL immer im selben Netz wie
            # das Geraet. --public-host dient nur noch als Rueckfall.
            local_ip = None
            try:
                sockname = writer.get_extra_info("sockname")
                if sockname and sockname[0]:
                    local_ip = str(sockname[0])
            except Exception:
                local_ip = None
            if not local_ip or local_ip in ("0.0.0.0", "::"):
                local_ip = args.public_host
            print(
                f"OTA: handing out ws://{local_ip}:{args.port} "
                f"to {writer.get_extra_info('peername')}",
                flush=True,
            )
            body = json.dumps({
                "websocket": {
                    "url": f"ws://{local_ip}:{args.port}",
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
