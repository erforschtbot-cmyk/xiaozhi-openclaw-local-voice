#!/usr/bin/env python3
"""Liest den Ist-Stand der Firmware vom Geraet — ausschliesslich lesend.

Es wird KEIN Schreib- oder Loeschvorgang ausgeloest. Gelesen wird nur der
App-Deskriptor (256 Byte bei 0x20020), der Version, Projekt, Compile-Zeit,
ESP-IDF-Version und ELF-SHA256 enthaelt.

Damit laesst sich eindeutig feststellen, welcher Build auf dem Geraet laeuft —
wichtig, weil mehrere Builds dieselbe Version und denselben Projektnamen tragen
und nur ueber Compile-Zeit und ELF-Hash unterscheidbar sind.

Aufruf:
    sudo python3 scripts/read-device-info.py [--port /dev/ttyACM0]
"""
from __future__ import annotations

import argparse
import pathlib
import struct
import subprocess
import sys
import tempfile

DESC_OFFSET = 0x20020
DESC_LENGTH = 0x100


def field(block: bytes, offset: int, length: int) -> str:
    return block[offset:offset + length].split(b"\0")[0].decode("utf-8", "replace")


def find_esptool() -> pathlib.Path:
    candidates = [
        pathlib.Path.home() / ".openclaw/workspace-allgemein/.tmp/jarvis-esptool-venv/bin/esptool",
        pathlib.Path.home() / ".openclaw/workspace-allgemein/.venv-esptool/bin/esptool",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    found = subprocess.run(["bash", "-lc", "command -v esptool || command -v esptool.py"],
                           text=True, capture_output=True)
    if found.returncode != 0:
        raise SystemExit("esptool nicht gefunden. 'pip install esptool==5.4.0' in eine venv.")
    return pathlib.Path(found.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="/dev/ttyACM0")
    args = parser.parse_args()

    esptool = find_esptool()
    with tempfile.TemporaryDirectory() as tmp:
        out = pathlib.Path(tmp) / "appdesc.bin"
        cmd = [str(esptool), "--port", args.port, "--no-stub",
               "read-flash", hex(DESC_OFFSET), hex(DESC_LENGTH), str(out)]
        if hasattr(__import__("os"), "geteuid") and __import__("os").geteuid() != 0:
            cmd = ["sudo"] + cmd
        result = subprocess.run(cmd, text=True, capture_output=True)
        if result.returncode != 0 or not out.is_file():
            sys.stderr.write(result.stdout + result.stderr)
            raise SystemExit("Lesen des App-Deskriptors fehlgeschlagen.")
        block = out.read_bytes()

    magic = struct.unpack("<I", block[0:4])[0]
    if magic != 0xABCD5432:
        raise SystemExit(f"Unerwartete Deskriptor-Magic: {magic:#010x}")

    print("Geraet: Firmware-Ist-Stand (nur gelesen, nichts geschrieben)")
    print(f"  project    : {field(block, 0x30, 32)}")
    print(f"  version    : {field(block, 0x10, 32)}")
    print(f"  compile    : {field(block, 0x50, 16)} {field(block, 0x60, 16)}")
    print(f"  esp-idf    : {field(block, 0x70, 32)}")
    print(f"  elf-sha256 : {block[0x90:0xB0].hex()}")
    print()
    print("Abgleich: gegen firmware/prebuilt/ und VERSIONS.md stellen. "
          "Nur Compile-Zeit und ELF-Hash unterscheiden Builds derselben Version.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
