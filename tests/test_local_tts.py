#!/usr/bin/env python3
"""Tests fuer die lokale Sprachausgabe der Bridge.

Ausfuehren:
    python3 tests/test_local_tts.py

Die Tests brauchen `piper` (Sprachausgabe) und `opuslib` (Rahmenkodierung).
Beide sind im Bridge-Venv vorhanden; beim Testen aus einem anderen
Interpreter die Site-Packages des Bridge-Venv per PYTHONPATH ergaenzen.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "gateway"))

from piper_speaker import PiperSpeaker, find_default_voice  # noqa: E402
from server import XiaozhiSession  # noqa: E402

VOICE_DIR = os.environ.get(
    "PIPER_VOICE_DIR", str(Path.home() / ".local" / "share" / "piper-voices")
)
VOICE = os.environ.get("PIPER_VOICE", "de_DE-thorsten-medium")

failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = "OK  " if condition else "FEHL"
    print(f"  [{status}] {name}{(' — ' + detail) if detail else ''}")
    if not condition:
        failures.append(name)


# --------------------------------------------------------------------------
print("=== 1. Satztrennung ===")
cases = [
    ("Einen Moment bitte.", 19),
    ("Der Monitor ist an. Der Befehl ist raus.", 19),
    ("Das ergibt 13.95 Grad.", 22),  # Dezimalpunkt darf nicht trennen
    ("Kein Satzende hier", 0),       # unter 160 Zeichen -> noch nichts
    ("Kurz. Und noch einer! Und ein dritter?", 5),
]
for text, expected in cases:
    got = XiaozhiSession._tts_boundary(text)
    check(f"Grenze {got:3d} (erwartet {expected:3d}) fuer {text[:34]!r}", got == expected)

# Ueberlange Saetze ohne Satzzeichen muessen trotzdem geteilt werden.
long_text = "wort " * 60
check("Ueberlanger Satz wird geteilt", XiaozhiSession._tts_boundary(long_text) > 0)

# --------------------------------------------------------------------------
print()
print("=== 2. Piper-Synthese (lokal, CPU) ===")
try:
    model_path = find_default_voice(VOICE_DIR, VOICE)
    t0 = time.monotonic()
    speaker = PiperSpeaker(model_path)
    load_time = time.monotonic() - t0
    info = speaker.describe()
    check("Stimme geladen", True, f"{Path(model_path).name} in {load_time:.2f}s")
    check("Zielrate ist 24 kHz", info["target_rate"] == 24000, str(info))

    text = "Der Monitor ist an. Der Befehl ist an Alexa rausgegangen."
    t0 = time.monotonic()
    pcm = speaker.synthesize_pcm(text)
    dt = time.monotonic() - t0
    audio_seconds = len(pcm) / 2 / 24000
    rtf = dt / audio_seconds if audio_seconds else float("inf")
    check("PCM erzeugt", len(pcm) > 0, f"{len(pcm)} Bytes")
    check("PCM ist durch 2 teilbar (s16le)", len(pcm) % 2 == 0)
    check("RTF deutlich unter 1.0 (schneller als Echtzeit)", rtf < 0.5,
          f"RTF {rtf:.2f}, {audio_seconds:.2f}s Audio in {dt:.3f}s")
    check("Leerer Text ergibt kein Audio", speaker.synthesize_pcm("   ") == b"")
except FileNotFoundError as exc:
    check("Stimme gefunden", False, str(exc))
    speaker = None

# --------------------------------------------------------------------------
print()
print("=== 3. Echtzeit-Taktung des Sendens ===")
if speaker is not None:
    class FakeWebSocket:
        def __init__(self) -> None:
            self.frames: list[bytes] = []

        async def send(self, data: bytes) -> None:
            self.frames.append(data)

    import opuslib

    class PacedSession:
        """Minimale Nachbildung der Session fuer den Taktungstest."""

        TTS_LEAD_FRAMES = XiaozhiSession.TTS_LEAD_FRAMES
        TTS_FRAME_SECONDS = XiaozhiSession.TTS_FRAME_SECONDS
        send_pcm = XiaozhiSession.send_pcm
        send_pcm_paced = XiaozhiSession.send_pcm_paced

        def __init__(self) -> None:
            self.websocket = FakeWebSocket()
            self.send_lock = asyncio.Lock()
            self.tts_started = True
            self.last_sent_audio_at = 0.0
            self.encoder = opuslib.Encoder(24000, 1, opuslib.APPLICATION_VOIP)
            self.encoder.bitrate = 32000

    async def run_paced() -> tuple[float, int]:
        session = PacedSession()
        pcm = b"\x11\x22" * (24000 * 3)  # genau 3 Sekunden Audio
        loop = asyncio.get_running_loop()
        started = loop.time()
        await session.send_pcm_paced(pcm)
        return loop.time() - started, len(session.websocket.frames)

    elapsed, frames = asyncio.run(run_paced())
    expected_frames = 3 * 1000 // 60  # 50 Rahmen zu 60 ms
    check("Rahmenzahl entspricht 60-ms-Raster", frames == expected_frames,
          f"{frames} Rahmen (erwartet {expected_frames})")
    # Ohne Taktung waere das Senden in unter 0.3 s fertig. Mit Taktung muss
    # es ungefaehr in Echtzeit laufen (3 s minus Vorlauf).
    check("Senden laeuft in Echtzeit, nicht im Schwall", elapsed > 2.0,
          f"{elapsed:.2f}s fuer 3.00s Audio")

# --------------------------------------------------------------------------
print()
if failures:
    print(f"FEHLGESCHLAGEN: {len(failures)} Test(s): {', '.join(failures)}")
    sys.exit(1)
print("Alle Tests bestanden.")
