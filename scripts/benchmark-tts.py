#!/usr/bin/env python3
"""Misst die Eignung deutscher Piper-Stimmen auf diesem Host.

Erzeugt die in VERSIONS.md dokumentierte Tabelle nachvollziehbar neu.
RTF = Synthesezeit / Audiozeit. Werte unter 1,0 sind schneller als Echtzeit;
fuer einen Sprachassistenten ist deutlich unter 1,0 noetig, weil noch
Netzwerk, Erkennung und Agent dazukommen.

Aufruf:
    python3 scripts/benchmark-tts.py
    python3 scripts/benchmark-tts.py --voice-dir ~/.local/share/piper-voices
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "gateway"))

from piper_speaker import PiperSpeaker, find_default_voice  # noqa: E402

TEXT = (
    "Guten Tag. Das ist ein laengerer deutscher Testsatz, damit die Messung "
    "nicht von der Startzeit dominiert wird, sondern die eigentliche "
    "Sprachsynthese misst."
)

DEFAULT_VOICES = [
    "de_DE-thorsten-medium",
    "de_DE-thorsten-high",
    "de_DE-thorsten-low",
    "de_DE-thorsten_emotional-medium",
    "de_DE-mls-medium",
    "de_DE-kerstin-low",
    "de_DE-eva_k-x_low",
]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--voice-dir",
        default=str(Path.home() / ".local" / "share" / "piper-voices"),
    )
    parser.add_argument("--voices", nargs="*", default=DEFAULT_VOICES)
    args = parser.parse_args()

    print(f"{'Stimme':32s} {'synthese':>9s} {'audio':>7s} {'RTF':>6s} {'rate':>7s} {'MB':>7s}")
    print("-" * 76)
    rows = []
    for name in args.voices:
        try:
            model = find_default_voice(args.voice_dir, name)
        except FileNotFoundError:
            print(f"{name:32s} {'--':>9s} {'--':>7s} {'--':>6s} {'--':>7s} {'fehlt':>7s}")
            continue
        t0 = time.monotonic()
        speaker = PiperSpeaker(model)
        load = time.monotonic() - t0
        t0 = time.monotonic()
        pcm = speaker.synthesize_pcm(TEXT)
        dt = time.monotonic() - t0
        audio = len(pcm) / 2 / speaker.target_rate
        rtf = dt / audio if audio else float("inf")
        size_mb = os.path.getsize(model) / 1024 / 1024
        rows.append((name, dt, audio, rtf, speaker.source_rate, size_mb))
        print(
            f"{name:32s} {dt:8.2f}s {audio:6.2f}s {rtf:6.2f} "
            f"{speaker.source_rate:7d} {size_mb:6.1f}M"
        )
        print(f"{'':32s} (Modell geladen in {load:.2f}s)")

    if not rows:
        print("\nKeine Stimme gefunden. Stimmen holen: siehe docs/07-LOKALE-STIMME.md")
        return 1

    best = min(rows, key=lambda r: r[3])
    print()
    print(f"Schnellste geprüfte Stimme: {best[0]} (RTF {best[3]:.2f})")
    print("Richtwert: RTF < 0,5 lässt im Betrieb genug Zeit für Erkennung und Agent.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
