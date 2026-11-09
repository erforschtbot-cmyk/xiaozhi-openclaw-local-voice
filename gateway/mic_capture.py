#!/usr/bin/env python3
"""Mitschnitt des Mikrofonstroms aus der Jarvis-Bridge.

Die Bridge dekodiert jedes Opus-Paket des Geraets bereits zu PCM
(16 kHz, mono, int16). Dieses Modul schreibt diesen Strom als WAV mit.

Aktivierung: Datei `capture.enabled` im Bridge-Verzeichnis anlegen.
Ohne diese Datei wird nichts geschrieben und nichts importiert.

Crash-Festigkeit: Der WAV-Header wird beim Oeffnen mit 0xFFFFFFFF
vorgefuellt ("Streaming-WAV"). ffmpeg/sox lesen bis EOF, ein hart
abgebrochener Prozess hinterlaesst also eine lesbare Datei. Beim
regulaeren Schliessen werden die echten Groessen nachgetragen.
"""
from __future__ import annotations

import os
import re
import struct
import time
from pathlib import Path

RATE = 16000
CHANNELS = 1
BITS = 16

_HERE = Path(__file__).resolve().parent
FLAG_FILE = _HERE / "capture.enabled"
BASE_DIR = Path(os.environ.get("JARVIS_CAPTURE_DIR", _HERE / "captures"))

# 5 Minuten pro Datei, dann weiterrollen.
MAX_FRAMES = RATE * 300


def _safe(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_.-]", "_", str(name or "unknown"))


def enabled() -> bool:
    return FLAG_FILE.exists()


class Capture:
    """Schreibt einen PCM-Strom eines Geraets in rollierende WAV-Dateien."""

    def __init__(self, device_id: str, base_dir: Path | None = None) -> None:
        self.device_id = _safe(device_id)
        self.base_dir = Path(base_dir or BASE_DIR) / self.device_id
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self._fh = None
        self._frames = 0
        self._index = 0
        self._started_at = 0.0
        self.path: Path | None = None

    # -- Dateiverwaltung ---------------------------------------------------
    def _open(self) -> None:
        stamp = time.strftime("%Y-%m-%dT%H-%M-%S")
        cand = self.base_dir / f"{stamp}_{self._index:03d}.wav"
        while cand.exists():
            self._index += 1
            cand = self.base_dir / f"{stamp}_{self._index:03d}.wav"
        self._fh = open(cand, "wb")
        # Streaming-Header: Groessen auf 0xFFFFFFFF, wird beim Schliessen
        # korrigiert.
        self._fh.write(
            b"RIFF" + struct.pack("<I", 0xFFFFFFFF) + b"WAVE"
            + b"fmt " + struct.pack("<IHHIIHH", 16, 1, CHANNELS, RATE,
                                    RATE * CHANNELS * BITS // 8,
                                    CHANNELS * BITS // 8, BITS)
            + b"data" + struct.pack("<I", 0xFFFFFFFF)
        )
        self._frames = 0
        self._started_at = time.time()
        self.path = cand

    def _finalize(self) -> None:
        if not self._fh:
            return
        data_bytes = self._frames * CHANNELS * BITS // 8
        try:
            self._fh.seek(4)
            self._fh.write(struct.pack("<I", 36 + data_bytes))
            self._fh.seek(40)
            self._fh.write(struct.pack("<I", data_bytes))
        except OSError:
            pass
        self._fh.close()
        self._fh = None
        if self.path:
            print(f"[capture] {self.device_id}: {self.path} "
                  f"({self._frames / RATE:.1f} s)", flush=True)

    # -- Daten -------------------------------------------------------------
    def write(self, pcm: bytes) -> None:
        if not pcm:
            return
        if self._fh is None:
            self._open()
        self._fh.write(pcm)
        self._fh.flush()
        self._frames += len(pcm) // (CHANNELS * BITS // 8)
        if self._frames >= MAX_FRAMES:
            self._finalize()

    def close(self) -> None:
        self._finalize()
