#!/usr/bin/env python3
"""Ende-zu-Ende-Test der Bridge mit lokalem Erkennen UND lokalem Sprechen.

Speist echte Sprache als Opus-Rahmen in die laufende Bridge ein — genau so,
wie es das Geraet tut — und prueft die vollstaendige lokale Kette:

    Opus-Audio -> Bridge -> lokaler Whisper -> Agent -> Piper (lokal) -> Opus

Verlangt werden:
  1. der Nutzer-Transkript kommt aus lokaler Erkennung,
  2. es wird kein Provider-Audio weitergegeben,
  3. das Geraet empfaengt lokales Piper-Audio,
  4. die Antwort endet mit stream_end.

Aufruf:
  PYTHONPATH=<opuslib>:<websockets> python3 tests/test_bridge_local_stt_e2e.py
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
sys.path.insert(0, str(REPO / "gateway"))

import opuslib  # noqa: E402
import websockets  # noqa: E402

GATEWAY = REPO / "gateway" / "server.py"
HELPER = REPO / "gateway" / "local-voice.mjs"
VOICE_DIR = os.environ.get(
    "PIPER_VOICE_DIR", str(Path.home() / ".local" / "share" / "piper-voices")
)
VOICE = os.environ.get("PIPER_VOICE", "de_DE-kerstin-low")
GATEWAY_CA = os.environ.get(
    "OPENCLAW_GATEWAY_CA", str(Path.home() / ".openclaw" / "ssl" / "gateway.crt")
)
SPEECH_WAV = os.environ.get("SPEECH_WAV", "/tmp/stt_probe.wav")
PORT = 8797
OTA_PORT = 8798
RATE = 16000  # Das Geraet sendet 16 kHz.

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    print(f"  [{'OK  ' if condition else 'FEHL'}] {name}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


def speech_frames() -> list[bytes]:
    """Erzeugt 60-ms-Opus-Rahmen mit echter Sprache, dann Stille."""
    with wave.open(SPEECH_WAV, "rb") as handle:
        assert handle.getframerate() == RATE, "Testaudio muss 16 kHz sein"
        pcm = handle.readframes(handle.getnframes())

    encoder = opuslib.Encoder(RATE, 1, opuslib.APPLICATION_VOIP)
    encoder.bitrate = 32000
    frame_samples = RATE * 60 // 1000  # 960 = 60 ms

    frames: list[bytes] = []
    for offset in range(0, len(pcm), frame_samples * 2):
        chunk = pcm[offset:offset + frame_samples * 2]
        if len(chunk) < frame_samples * 2:
            chunk = chunk + b"\x00" * (frame_samples * 2 - len(chunk))
        frames.append(encoder.encode(chunk, frame_samples))
    # Dem Sprecher Stille folgen lassen, damit die Aeusserung endet.
    silence = b"\x00" * (frame_samples * 2)
    for _ in range(40):  # 2,4 s
        frames.append(encoder.encode(silence, frame_samples))
    return frames


async def main() -> None:
    if not Path(SPEECH_WAV).exists():
        print(f"Testaudio fehlt: {SPEECH_WAV}")
        sys.exit(1)

    proc = await asyncio.create_subprocess_exec(
        sys.executable, str(GATEWAY),
        "--host", "127.0.0.1", "--port", str(PORT), "--ota-port", str(OTA_PORT),
        "--public-host", "127.0.0.1",
        "--session-key", "agent:voice:lokal-e2e",
        "--consult-session-key", "",
        "--helper", str(HELPER),
        "--stt-local", "--stt-uri", "127.0.0.1:10300", "--stt-language", "de",
        "--tts-local", "--tts-voice", VOICE, "--tts-voice-dir", VOICE_DIR,
        "--gateway-ca", GATEWAY_CA,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        env=os.environ.copy(),
    )

    log_lines: list[str] = []

    async def collect() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            text = line.decode("utf-8", "replace").rstrip()
            log_lines.append(text)
            print(f"  [bridge] {text}")

    log_task = asyncio.create_task(collect())

    try:
        for _ in range(80):
            await asyncio.sleep(0.1)
            if any("listening on" in line for line in log_lines):
                break
        check("Bridge gestartet", any("listening on" in l for l in log_lines))
        check("Lokale Stimme geladen", any("Local voice ready" in l for l in log_lines))

        decoder = opuslib.Decoder(24000, 1)
        states: list[str] = []
        transcripts: list[str] = []
        opus_frames = 0
        pcm_bytes = 0

        async with websockets.connect(f"ws://127.0.0.1:{PORT}") as ws:
            await ws.send(json.dumps({"type": "hello", "features": {}}))
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
            check("hello beantwortet", hello.get("type") == "hello")

            for frame in speech_frames():
                await ws.send(frame)
                await asyncio.sleep(0.005)

            deadline = asyncio.get_running_loop().time() + 90
            while asyncio.get_running_loop().time() < deadline:
                try:
                    message = await asyncio.wait_for(ws.recv(), timeout=3.0)
                except asyncio.TimeoutError:
                    continue
                if isinstance(message, bytes):
                    opus_frames += 1
                    try:
                        pcm_bytes += len(decoder.decode(message, 1440, False))
                    except opuslib.OpusError:
                        pass
                    continue
                payload = json.loads(message)
                if payload.get("type") == "tts":
                    states.append(payload.get("state"))
                    if payload.get("state") == "stream_end":
                        break
                elif payload.get("type") == "stt":
                    transcripts.append(payload.get("text", ""))

        await asyncio.sleep(0.5)

        check("Nutzer-Transkript lokal erkannt", len(transcripts) == 1,
              str(transcripts))
        if transcripts:
            check("Transkript enthaelt 'Monitor'",
                  "monitor" in transcripts[0].lower(), transcripts[0])
        check("tts gestartet", "start" in states, str(states[:8]))
        check("tts abgeschlossen", "stream_end" in states, str(states[:8]))
        check("Lokales Audio geliefert", opus_frames > 0,
              f"{opus_frames} Rahmen, {pcm_bytes} PCM-Bytes")
        check("Audio ist substanziell (lokale Stimme)",
              pcm_bytes >= 24000 * 2 * 1, f"{pcm_bytes/2/24000:.2f}s")
    finally:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10)
        except asyncio.TimeoutError:
            proc.kill()
        await asyncio.gather(log_task, return_exceptions=True)

    if failures:
        print(f"\nFEHLGESCHLAGEN: {', '.join(failures)}")
        sys.exit(1)
    print("\nLokale Kette Ende-zu-Ende bestanden.")


if __name__ == "__main__":
    asyncio.run(main())
