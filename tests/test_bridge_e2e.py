#!/usr/bin/env python3
"""Ende-zu-Ende-Test der Bridge mit lokaler Sprachausgabe.

Startet `server.py` mit `--tts-local`, verbindet sich als Geraet per
WebSocket, sendet eine kurze Stille-Eingabe und prueft, dass die Bridge:

1. den Nutzer-Transkript weiterleitet (`stt`),
2. den Assistenten-Text als `tts`-Zustand schickt,
3. den Text lokal ueber Piper vertont und Opus-Rahmen liefert,
4. die Antwort mit `stream_end` abschliesst,
5. das Rohaudio des Providers **nicht** weiterreicht.

Ausfuehren mit einem Interpreter, der `websockets`, `opuslib` und `piper`
sieht (siehe Modulkopf von tests/test_local_tts.py).
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "gateway"))

import opuslib  # noqa: E402
import websockets  # noqa: E402

GATEWAY = HERE.parent / "gateway" / "server.py"
FAKE_HELPER = HERE / "fixtures" / "fake-talk-helper.mjs"
VOICE_DIR = os.environ.get(
    "PIPER_VOICE_DIR", str(Path.home() / ".local" / "share" / "piper-voices")
)
VOICE = os.environ.get("PIPER_VOICE", "de_DE-thorsten-medium")
PORT = 8799
OTA_PORT = 8800

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'FEHL'}] {name}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


async def main() -> None:
    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(GATEWAY),
        "--host", "127.0.0.1", "--port", str(PORT), "--ota-port", str(OTA_PORT),
        "--public-host", "127.0.0.1",
        "--session-key", "agent:voice:test-local",
        "--consult-session-key", "",
        "--helper", str(FAKE_HELPER),
        "--tts-local", "--tts-voice", VOICE, "--tts-voice-dir", VOICE_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    # Bridge-Log im Hintergrund einsammeln.
    log_lines: list[str] = []

    async def collect_log() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            log_lines.append(line.decode("utf-8", "replace").rstrip())

    log_task = asyncio.create_task(collect_log())

    try:
        # Auf den Listener warten.
        for _ in range(60):
            await asyncio.sleep(0.1)
            if any("listening on" in line for line in log_lines):
                break
        check("Bridge gestartet und lauscht", any("listening on" in l for l in log_lines))
        check("Piper-Stimme geladen", any("Local voice ready" in l for l in log_lines))

        decoder = opuslib.Decoder(24000, 1)
        states: list[str] = []
        transcripts: list[str] = []
        opus_frames = 0
        pcm_bytes = 0

        async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws:
            await ws.send(json.dumps({
                "type": "hello",
                "features": {},
            }))
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            check("hello beantwortet", hello.get("type") == "hello")

            # 0,5 s stille "Sprache" als Opus-Rahmen senden, damit die Bridge
            # den Helfer startet.
            encoder = opuslib.Encoder(24000, 1, opuslib.APPLICATION_VOIP)
            silence = b"\x00\x00" * (24000 // 2 // 10)  # 100 ms
            for _ in range(5):
                await ws.send(encoder.encode(silence, 240))  # 240 = 10 ms

            deadline = asyncio.get_running_loop().time() + 25
            while asyncio.get_running_loop().time() < deadline:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=2.0)
                except asyncio.TimeoutError:
                    continue
                if isinstance(message, bytes):
                    opus_frames += 1
                    try:
                        pcm = decoder.decode(message, 1440, False)
                        pcm_bytes += len(pcm)
                    except opuslib.OpusError:
                        pass
                    continue
                payload = json.loads(message)
                if payload.get("type") == "tts":
                    state = payload.get("state")
                    states.append(state)
                    if state == "stream_end":
                        break
                elif payload.get("type") == "stt":
                    transcripts.append(payload.get("text", ""))

        await asyncio.sleep(0.5)

        check("Nutzer-Transkript weitergeleitet", transcripts == ["Wie ist der Stand?"],
              str(transcripts))
        check("tts-Start gesendet", "start" in states, str(states[:6]))
        check("tts-stream_end gesendet", "stream_end" in states, str(states[:6]))
        check("Lokales Audio als Opus-Rahmen geliefert", opus_frames > 0,
              f"{opus_frames} Rahmen, {pcm_bytes} PCM-Bytes")

        # Erwartete Audiodauer: der Satz wurde von Piper vertont. Nur pruefen,
        # dass ueberhaupt substanzielles Audio kam (nicht das 1-s-Testaudio
        # des Fake-Helfers, das 48000 Bytes waere).
        expected_min = 24000 * 2 * 2  # mindestens 2 Sekunden Sprache
        check("Audio ist die lokale Stimme, nicht das Provider-Testaudio",
              pcm_bytes >= expected_min,
              f"{pcm_bytes} Bytes ({pcm_bytes/2/24000:.2f}s)")

        check("Provider-Testaudio wurde verworfen (Log)",
              any("Local voice stream complete" in l for l in log_lines))
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
        await asyncio.gather(log_task, return_exceptions=True)

    print()
    if failures:
        print(f"FEHLGESCHLAGEN: {len(failures)}: {', '.join(failures)}")
        print()
        print("--- Bridge-Log ---")
        print("\n".join(log_lines[-40:]))
        sys.exit(1)
    print("Ende-zu-Ende-Test bestanden.")


if __name__ == "__main__":
    asyncio.run(main())
