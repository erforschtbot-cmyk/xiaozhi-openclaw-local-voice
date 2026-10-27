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
import sherpa_onnx
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


class SileroVad:
    """Silero-VAD aus sherpa-onnx: trennt Sprache von Nicht-Sprache.

    Canary 1B v2 hat KEIN eingebautes VAD. Ohne Filter transkribiert es
    Stille und Hintergrundgeraeusche und liefert dann kurze Fantasiewoerter
    ("Ja.", "Danke.", "Mm."). Belegt 27.10.2026: 20 solcher Transkripte am
    Tag, davon mehrere bei peak=0 (reine Stille) und peak<900 (Rauschen).

    Das VAD wirkt als TOR, nicht als Schere: es entscheidet nur, OB
    gesprochen wurde. Das Audio wird nicht zugeschnitten, sonst geht der
    Wortanfang verloren.
    """

    def __init__(self, model_path: str, threshold: float = 0.3,
                 min_speech: float = 0.15, min_silence: float = 0.25) -> None:
        cfg = sherpa_onnx.VadModelConfig()
        cfg.silero_vad.model = model_path
        cfg.silero_vad.threshold = threshold
        cfg.silero_vad.min_silence_duration = min_silence
        cfg.silero_vad.min_speech_duration = min_speech
        cfg.silero_vad.window_size = 512
        cfg.sample_rate = _RATE
        self._cfg = cfg
        self._min_samples = int(min_speech * _RATE)

    def has_speech(self, samples: np.ndarray) -> bool:
        """True, wenn im Signal mindestens min_speech Sekunden Sprache sind."""
        if samples.size == 0:
            return False
        pad = (512 - samples.size % 512) % 512
        padded = np.pad(samples, (0, pad)) if pad else samples
        vad = sherpa_onnx.VoiceActivityDetector(self._cfg, buffer_size_in_seconds=60)
        found = 0
        for i in range(0, padded.size, 512):
            vad.accept_waveform(padded[i:i + 512].astype(np.float32))
            while not vad.empty():
                found += len(vad.front.samples)
                vad.pop()
        vad.flush()
        while not vad.empty():
            found += len(vad.front.samples)
            vad.pop()
        return found >= self._min_samples


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
                 vad: "SileroVad | None" = None, vad_fallback_seconds: float = 1.5,
                 vad_loose: "SileroVad | None" = None, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.vad = vad
        # Ab dieser Aufnahmedauer wird ein zweiter, empfindlicherer
        # VAD-Durchgang versucht (schuetzt leise/entfernte echte Saetze).
        self.vad_fallback_seconds = vad_fallback_seconds
        self.vad_loose = vad_loose or vad
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
            # Tor: ohne erkannte Sprache gar nicht erst transkribieren.
            # Canary erfindet sonst kurze Woerter fuer Stille und Rauschen.
            #
            # ABER: Lange Aufnahmen trotzdem an Canary geben. Das Tor ist eine
            # Vorstufe und kann leise oder aus der Ferne gesprochene Saetze
            # faelschlich als "keine Sprache" werten - dann waere die
            # Aeusserung verloren, BEVOR Canary sie ueberhaupt sah. Gemessen
            # 27.10.2026: Fehlschlaege alle leise (peak 60-250), Erfolge laut
            # (peak 300-1400). Regel deshalb: kurz + keine Sprache -> Rauschen;
            # lang -> Canary entscheidet (siehe docs/10-CANARY-FAVORIT.md).
            seconds = len(samples) / _RATE
            if self.vad is not None:
                speaks = await asyncio.to_thread(self.vad.has_speech, samples)
                if not speaks and seconds >= self.vad_fallback_seconds:
                    # Zweiter, empfindlicherer Durchgang fuer lange Aufnahmen.
                    # Gemessen 27.10.2026: bei threshold 0.15 wird leise Sprache
                    # noch erkannt (Pegel 983), waehrend Rauschen bis amp 0.04
                    # und 3 s Dauer draussen bleibt. Ohne diesen Durchgang
                    # gingen 2 s Rauschen wieder an Canary.
                    speaks = await asyncio.to_thread(
                        self.vad_loose.has_speech, samples
                    )
                    _LOGGER.info(
                        "VAD: empfindlicher Durchgang (%.2fs) -> %s",
                        seconds, "Sprache" if speaks else "keine Sprache",
                    )
                if not speaks:
                    _LOGGER.info(
                        "Keine Sprache erkannt (VAD, %.2fs); kein Transkript",
                        seconds,
                    )
                    await self.write_event(Transcript(text="").event())
                    self._lang = self.default_lang
                    return False
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
    parser.add_argument("--vad-model", default="",
                        help="Pfad zu silero_vad.onnx; leer = VAD aus")
    parser.add_argument("--vad-threshold", type=float, default=0.3)
    parser.add_argument("--vad-min-speech", type=float, default=0.15)
    parser.add_argument("--vad-fallback-seconds", type=float, default=1.5,
                        help="ab dieser Laenge zweiter, empfindlicherer "
                             "VAD-Durchgang (schuetzt leise Saetze)")
    parser.add_argument("--vad-threshold-loose", type=float, default=0.15,
                        help="Schwelle des zweiten Durchgangs")
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    default_lang = _norm_lang(args.language, "de")
    engine = Canary1bEngine(args.model_name, args.model_path, args.quantization)
    engine.warmup()

    vad = None
    vad_loose = None
    if args.vad_model:
        vad = SileroVad(args.vad_model, threshold=args.vad_threshold,
                        min_speech=args.vad_min_speech)
        vad_loose = SileroVad(args.vad_model, threshold=args.vad_threshold_loose,
                              min_speech=args.vad_min_speech)
        _LOGGER.info("Silero-VAD aktiv: %s (threshold=%.2f, min_speech=%.2fs)",
                     args.vad_model, args.vad_threshold, args.vad_min_speech)
    else:
        _LOGGER.warning("Kein VAD aktiv; Stille und Rauschen werden transkribiert")

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

    handler_factory = functools.partial(
        Canary1bHandler, info, engine, default_lang, vad,
        args.vad_fallback_seconds, vad_loose,
    )
    await server.run(handler_factory)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
