#!/usr/bin/env python3
"""Wyoming-ASR-Server fuer NVIDIA Canary 180M Flash (INT8) via sherpa-onnx.

Dieser Server spricht dasselbe Protokoll wie wyoming-faster-whisper, ist also
fuer die XiaoZhi-Bridge und Home Assistant ein gleichwertiger Ersatz. Er nutzt
aber NICHT faster-whisper, sondern das NeMo-Modell nvidia/canary-180m-flash
(sherpa-onnx-Konvertierung, mehrsprachig en/es/de/fr).

Warum ein eigener Server?
-------------------------
`wyoming-faster-whisper --stt-library sherpa` kann nur Transducer-Modelle
(Parakeet/Zipformer). Canary ist ein NeMo-AED-Modell und braucht
`OfflineRecognizer.from_nemo_canary`.

Protokoll (wie gehabt):
  -> Transcribe(language=...)      optional, waehlt die Sprache
  -> AudioStart/AudioChunk/AudioStop  16 kHz, 16-bit, mono
  <- Transcript(text=...)

Der Transcribe-`language`-Code wird auf Canarys src_lang/tgt_lang abgebildet.
Unterstuetzte Werte: en, es, de, fr. Alles andere -> Default-Sprache.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import logging
import os
import signal
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

import numpy as np
import sherpa_onnx
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStop
from wyoming.event import Event
from wyoming.info import AsrModel, AsrProgram, Attribution, Info
from wyoming.server import AsyncEventHandler, AsyncServer

_LOGGER = logging.getLogger("wyoming_canary")

_RATE = 16000
SUPPORTED_LANGS = ("en", "es", "de", "fr")
_LANG_ALIASES = {
    "english": "en", "german": "de", "deutsch": "de",
    "spanish": "es", "french": "fr",
}


def _norm_lang(value: Optional[str], default: str) -> str:
    if not value:
        return default
    code = value.strip().lower().split("-")[0].split("_")[0]
    code = _LANG_ALIASES.get(code, code)
    return code if code in SUPPORTED_LANGS else default


class SileroVad:
    """Silero-VAD aus sherpa-onnx: trennt Sprache von Nicht-Sprache.

    Canary selbst hat KEIN eingebautes VAD. Ohne Filter transkribiert es
    Rauschen und liefert Artefakte wie "T S F" oder "Copyright WDR 2020".
    """

    def __init__(self, model_path: str, threshold: float = 0.5,
                 min_speech: float = 0.25, min_silence: float = 0.25) -> None:
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = model_path
        cfg.silero_vad.threshold = threshold
        cfg.silero_vad.min_silence_duration = min_silence
        cfg.silero_vad.min_speech_duration = min_speech
        cfg.silero_vad.window_size = 512
        cfg.sample_rate = _RATE
        self._cfg = cfg

    def has_speech(self, samples: np.ndarray) -> bool:
        """Erkennt, ob im Signal ueberhaupt Sprache steckt."""
        pad = (512 - len(samples) % 512) % 512
        padded = np.pad(samples, (0, pad)) if pad else samples
        vad = sherpa_onnx.VoiceActivityDetector(self._cfg, buffer_size_in_seconds=60)
        found = 0
        win = 512
        for i in range(0, len(padded), win):
            vad.accept_waveform(padded[i:i + win].astype(np.float32))
            while not vad.empty():
                found += len(vad.front.samples)
                vad.pop()
        vad.flush()
        while not vad.empty():
            found += len(vad.front.samples)
            vad.pop()
        return found >= int(0.25 * _RATE)


class CanaryEngine:
    """Haelt je Sprachpaar einen geladenen OfflineRecognizer (Modelle warm)."""

    def __init__(self, model_dir: str, default_lang: str, num_threads: int,
                 vad: "SileroVad | None" = None) -> None:
        self.model_dir = Path(model_dir)
        self.default_lang = _norm_lang(default_lang, "en")
        self.num_threads = num_threads
        self.vad = vad
        self._recognizers: Dict[Tuple[str, str], sherpa_onnx.OfflineRecognizer] = {}
        self.encoder = str(self.model_dir / "encoder.int8.onnx")
        self.decoder = str(self.model_dir / "decoder.int8.onnx")
        self.tokens = str(self.model_dir / "tokens.txt")
        for p in (self.encoder, self.decoder, self.tokens):
            if not Path(p).exists():
                raise FileNotFoundError(f"Modelldatei fehlt: {p}")

    def _get(self, lang: str) -> sherpa_onnx.OfflineRecognizer:
        lang = _norm_lang(lang, self.default_lang)
        key = (lang, lang)
        rec = self._recognizers.get(key)
        if rec is None:
            _LOGGER.info("Lade Canary-Modell (src=%s, tgt=%s)", lang, lang)
            rec = sherpa_onnx.OfflineRecognizer.from_nemo_canary(
                encoder=self.encoder,
                decoder=self.decoder,
                tokens=self.tokens,
                src_lang=lang,
                tgt_lang=lang,
                num_threads=self.num_threads,
                provider="cpu",
            )
            self._recognizers[key] = rec
        return rec

    def warmup(self, lang: str) -> None:
        rec = self._get(lang)
        stream = rec.create_stream()
        stream.accept_waveform(_RATE, np.zeros(4000, dtype=np.float32))
        rec.decode_stream(stream)

    def transcribe(self, samples: np.ndarray, lang: str) -> str:
        if self.vad is not None and not self.vad.has_speech(samples):
            _LOGGER.debug("VAD: keine Sprache erkannt")
            return ""
        rec = self._get(lang)
        stream = rec.create_stream()
        stream.accept_waveform(_RATE, samples)
        rec.decode_stream(stream)
        return stream.result.text.strip()


class CanaryHandler(AsyncEventHandler):
    def __init__(self, wyoming_info: Info, engine: CanaryEngine, default_lang: str,
                 *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.wyoming_info_event = wyoming_info.event()
        self.engine = engine
        self.default_lang = default_lang
        self._lang = default_lang
        self._converter = AudioChunkConverter(rate=_RATE, width=2, channels=1)
        self._pcm = bytearray()

    async def handle_event(self, event: Event) -> bool:
        if AudioChunk.is_type(event.type):
            chunk = self._converter.convert(AudioChunk.from_event(event))
            self._pcm.extend(chunk.audio)
            return True

        if Transcribe.is_type(event.type):
            tr = Transcribe.from_event(event)
            self._lang = _norm_lang(tr.language, self.default_lang)
            _LOGGER.debug("Sprache: %s", self._lang)
            return True

        if AudioStop.is_type(event.type):
            if not self._pcm:
                await self.write_event(Transcript(text="").event())
                return False
            samples = np.frombuffer(bytes(self._pcm), dtype=np.int16).astype(np.float32) / 32768.0
            self._pcm = bytearray()
            _LOGGER.info("Audio empfangen: %d Samples (%.2fs)", len(samples), len(samples) / _RATE)
            if os.environ.get("CANARY_DUMP"):
                import wave as _w
                with _w.open(os.environ["CANARY_DUMP"], "wb") as _f:
                    _f.setnchannels(1); _f.setsampwidth(2); _f.setframerate(_RATE)
                    _f.writeframes((samples * 32768).astype(np.int16).tobytes())
            text = await asyncio.to_thread(self.engine.transcribe, samples, self._lang)
            _LOGGER.info("Erkannt (%s): %s", self._lang, text)
            await self.write_event(Transcript(text=text).event())
            self._lang = self.default_lang
            return False

        if event.type == "describe":
            await self.write_event(self.wyoming_info_event)
            return True

        return True


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--uri", required=True, help="z.B. tcp://0.0.0.0:10301")
    parser.add_argument("--model-dir", required=True)
    parser.add_argument("--language", default="de")
    parser.add_argument("--src-lang", default=None)
    parser.add_argument("--tgt-lang", default=None)
    parser.add_argument("--num-threads", type=int, default=4)
    parser.add_argument("--vad-model", default=None,
                        help="Pfad zu silero_vad.onnx (aktiviert VAD-Filter)")
    parser.add_argument("--vad-threshold", type=float, default=0.5)
    parser.add_argument("--vad-min-speech-ms", type=int, default=100)
    parser.add_argument("--vad-min-silence-ms", type=int, default=250)
    parser.add_argument("--zeroconf", nargs="?", const="canary")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    default_lang = _norm_lang(args.src_lang or args.language, "en")
    vad = None
    if args.vad_model:
        vad = SileroVad(args.vad_model, args.vad_threshold,
                        args.vad_min_speech_ms / 1000.0,
                        args.vad_min_silence_ms / 1000.0)
        _LOGGER.info("VAD aktiviert (%s)", args.vad_model)
    engine = CanaryEngine(args.model_dir, default_lang, args.num_threads, vad=vad)
    engine.warmup(default_lang)

    model = AsrModel(
        name="canary-180m-flash-int8",
        attribution=Attribution(name="NVIDIA", url="https://huggingface.co/nvidia/canary-180m-flash"),
        installed=True,
        description="Canary 180M Flash (INT8) via sherpa-onnx",
        version="1.0",
        languages=list(SUPPORTED_LANGS),
    )
    info = Info(asr=[AsrProgram(
        name="wyoming-canary",
        attribution=Attribution(name="sherpa-onnx", url="https://github.com/k2-fsa/sherpa-onnx"),
        installed=True,
        description="NVIDIA Canary 180M Flash speech-to-text",
        version="1.0",
        models=[model],
        supports_transcript_streaming=False,
    )])

    server = AsyncServer.from_uri(args.uri)
    _LOGGER.info("Bereit auf %s (Standardsprache %s)", args.uri, default_lang)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    handler_factory = functools.partial(
        CanaryHandler, info, engine, default_lang
    )
    await server.run(handler_factory)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
