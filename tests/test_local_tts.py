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
    ("Es ist 13:52 Uhr.", 17),       # Uhrzeit darf nicht trennen
]
for text, expected in cases:
    got = XiaozhiSession._tts_boundary(text)
    check(f"Grenze {got:3d} (erwartet {expected:3d}) fuer {text[:34]!r}", got == expected)

# Zahlenschutz: Zahlzeichen duerfen nie eine Sprechgrenze sein. Ein Schnitt
# dort ergaebe "Es ist 13:" — das klingt hoerbar abgehackt.
for text in ("Es ist 13:52 Uhr.", "Es sind 13.95 Grad.", "Um 9:30 Uhr."):
    for index, char in enumerate(text):
        if char not in ".:":
            continue
        left = index > 0 and text[index - 1].isdigit()
        right = index + 1 < len(text) and text[index + 1].isdigit()
        if left and right:
            cut = XiaozhiSession._tts_boundary(text)
            check(
                f"Zahl {text!r} nicht vor Zeichen {index} getrennt",
                cut != index + 1,
                f"Schnitt bei {cut}",
            )

# Zerlegungs-Modi
mode_cases = [
    ("clause", "Es ist 13:52 Uhr, und der Monitor ist an.", 17),
    ("sentence", "Es ist 13:52 Uhr, und der Monitor ist an.", 41),
    ("whole", "Es ist 13:52 Uhr, und der Monitor ist an.", 0),
    ("whole", "Erster Satz. Zweiter Satz.", 0),
]
for mode, text, expected in mode_cases:
    got = XiaozhiSession._tts_boundary(text, mode)
    check(f"Modus {mode:8s} Grenze {got:3d} (erwartet {expected:3d})",
          got == expected)

# `clause` muss frueher trennen als `sentence`.
text = "Es ist 13:52 Uhr, und der Monitor ist an. Morgen wird es kaelter."
clause = XiaozhiSession._tts_boundary(text, "clause")
sentence = XiaozhiSession._tts_boundary(text, "sentence")
check("clause trennt frueher als sentence", 0 < clause < sentence,
      f"clause={clause}, sentence={sentence}")

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
