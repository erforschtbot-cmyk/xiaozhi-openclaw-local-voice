#!/usr/bin/env bash
# A/B-Vergleich: denselben PCM an Whisper (:10300) UND Canary (:10301) schicken.
# Aufruf: compare-stt.sh <audio-datei> [sprache]
set -euo pipefail
IN="${1:?Audiodatei fehlt}"
LANG_CODE="${2:-de}"
PY=/home/openclaw/wyoming-stt/.venv/bin/python
WAV=$(mktemp /tmp/stt-ab-XXXX.wav)
trap 'rm -f "$WAV"' EXIT
ffmpeg -v error -y -i "$IN" -ar 16000 -ac 1 -c:a pcm_s16le "$WAV"

"$PY" - "$WAV" "$LANG_CODE" <<'PYEOF'
import asyncio, sys, wave, time
from wyoming.client import AsyncTcpClient
from wyoming.audio import AudioChunk, AudioStart, AudioStop
from wyoming.asr import Transcribe, Transcript

wav, lang = sys.argv[1], sys.argv[2]

async def ask(uri):
    with wave.open(wav, "rb") as w:
        sr = w.getframerate(); raw = w.readframes(w.getnframes())
    t0 = time.perf_counter()
    async with AsyncTcpClient.from_uri(uri) as c:
        await c.write_event(Transcribe(language=lang).event())
        await c.write_event(AudioStart(rate=sr, width=2, channels=1).event())
        step = sr // 10 * 2
        for i in range(0, len(raw), step):
            await c.write_event(AudioChunk(rate=sr, width=2, channels=1, audio=raw[i:i+step]).event())
        await c.write_event(AudioStop().event())
        while True:
            ev = await c.read_event()
            if ev is None: break
            if Transcript.is_type(ev.type):
                return Transcript.from_event(ev).text, time.perf_counter() - t0
    return "", 0.0

async def main():
    dur = None
    with wave.open(wav, "rb") as w:
        dur = w.getnframes() / w.getframerate()
    wt, wl = await ask("tcp://127.0.0.1:10300")
    ct, cl = await ask("tcp://127.0.0.1:10301")
    print(f"Audio: {dur:.2f}s  ({wav})")
    print(f"  Whisper small (RTF {wl/dur:.2f}, {wl:.2f}s): {wt!r}")
    print(f"  Canary 180M   (RTF {cl/dur:.2f}, {cl:.2f}s): {ct!r}")

asyncio.run(main())
PYEOF
