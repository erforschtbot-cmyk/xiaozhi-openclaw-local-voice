#!/usr/bin/env python3
"""Wyoming-ASR-Server fuer NVIDIA Canary 1B v2 (ONNX, via onnx-asr).

Protokoll identisch zu wyoming-faster-whisper / wyoming-parakeet:
  -> Transcribe(language=...)          optional
  -> AudioStart/AudioChunk/AudioStop   16 kHz, 16-bit, mono
  <- Transcript(text=...)

Canary 1B v2 ist ein NeMo-AED-Modell (nicht Transducer), deshalb ueber
`onnx_asr.load_model('nemo-canary-1b-v2')`. Mehrsprachig (25 Sprachen inkl.
Deutsch), CC-BY-4.0.

Eigener Port (Standard 10303); der laufende Parakeet-Dienst (10302) und
quicker-whisper (10300) bleiben unberuehrt.
"""

from __future__ import annotations

import argparse
import asyncio
import functools
import logging
import signal
from pathlib import Path
from typing import Optional

import numpy as np
import onnx_asr
from wyoming.asr import Transcribe, Transcript
from wyoming.audio import AudioChunk, AudioChunkConverter, AudioStop
from wyoming.event import Event
from wyoming.info import AsrModel, AsrProgram, Attribution, Info
from wyoming.server import AsyncEventHandler, AsyncServer

_LOGGER = logging.getLogger("wyoming_canary1b")

_RATE = 16000
SUPPORTED_LANGS = ("de", "en")


def _norm_lang(value: Optional[str], default: str = "de") -> str:
    if not value:
        return default
    code = value.strip().lower().split("-")[0].split("_")[0]
    return code if code in SUPPORTED_LANGS else default


class Canary1bEngine:
    """Haelt das ONNX-Modell warm und transkribiert PCM-Float-Samples."""

    def __init__(self, model_name: str, model_path: str, quantization: Optional[str] = None) -> None:
        self.model_name = model_name
        self.model_path = model_path
        self.quantization = quantization
        self._model = None

    def _get(self):
        if self._model is None:
            _LOGGER.info("Lade %s aus %s (quantization=%s)", self.model_name, self.model_path, self.quantization)
            self._model = onnx_asr.load_model(
                self.model_name, path=self.model_path, quantization=self.quantization
            )
        return self._model

    def warmup(self) -> None:
        model = self._get()
        model.recognize(np.zeros(4000, dtype=np.float32), sample_rate=_RATE, language="de")

    def transcribe(self, samples: np.ndarray, lang: str) -> str:
        model = self._get()
        text = model.recognize(samples, sample_rate=_RATE, language=lang)
        return (text or "").strip()


class Canary1bHandler(AsyncEventHandler):
    def __init__(self, wyoming_info: Info, engine: Canary1bEngine, default_lang: str,
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
            return True

        if AudioStop.is_type(event.type):
            if not self._pcm:
                await self.write_event(Transcript(text="").event())
                return False
            samples = np.frombuffer(bytes(self._pcm), dtype=np.int16).astype(np.float32) / 32768.0
            self._pcm = bytearray()
            _LOGGER.info("Audio empfangen: %d Samples (%.2fs)", len(samples), len(samples) / _RATE)
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
    parser.add_argument("--uri", required=True, help="z.B. tcp://0.0.0.0:10303")
    parser.add_argument("--model-name", default="nemo-canary-1b-v2")
    parser.add_argument("--model-path", default="/home/openclaw/.cache/jarvis-stt-cand5-canary1b-onnx")
    parser.add_argument("--quantization", default=None, help="z.B. int8 oder None")
    parser.add_argument("--language", default="de")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    default_lang = _norm_lang(args.language, "de")
    engine = Canary1bEngine(args.model_name, args.model_path, args.quantization)
    engine.warmup()

    model = AsrModel(
        name="canary-1b-v2-onnx",
        attribution=Attribution(name="NVIDIA", url="https://huggingface.co/nvidia/canary-1b-v2"),
        installed=True,
        description="Canary 1B v2 (ONNX) via onnx-asr",
        version="1.0",
        languages=list(SUPPORTED_LANGS),
    )
    info = Info(asr=[AsrProgram(
        name="wyoming-canary1b",
        attribution=Attribution(name="onnx-asr", url="https://github.com/istupakov/onnx-asr"),
        installed=True,
        description="NVIDIA Canary 1B v2 speech-to-text",
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

    handler_factory = functools.partial(Canary1bHandler, info, engine, default_lang)
    await server.run(handler_factory)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
