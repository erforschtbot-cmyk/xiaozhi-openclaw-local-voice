#!/usr/bin/env python3
"""Lokale Sprachausgabe fuer die XiaoZhi-Bridge mit Piper.

Warum dieses Modul existiert
----------------------------
Die Bridge bekam ihr Audio bisher fertig vom OpenAI-Realtime-Provider und
schob es nur noch durch den Opus-Encoder. Fuer eine lokale Stimme muss die
Bridge den gesprochenen Text selbst in PCM wandeln.

Zwei Eigenschaften sind dafuer entscheidend:

1. **Das Modell bleibt warm.** `PiperVoice.load()` dauert ~1,3 s. Es wird
   genau einmal geladen und danach fuer jede Aeusserung wiederverwendet.
   Ein Aufruf der `piper-tts`-Kommandozeile pro Satz waere um Groessen-
   ordnungen langsamer, weil dort jedes Mal das Modell geladen wird.
2. **Die Abtastrate passt zum Geraet.** Piper liefert die Stimmen mit
   16 kHz oder 22,05 kHz. Das XiaoZhi-Geraet erwartet 24 kHz. Die
   Umsetzung passiert hier, damit der restliche Pfad unveraendert bleibt.

Gemessen auf dem Referenzhost (Intel i5-8365U, nur CPU):
`de_DE-thorsten-medium` erreicht RTF 0.05-0.08, erste Ausgabe nach
60-110 ms.
"""

from __future__ import annotations

import re

import numpy as np

try:  # Piper >= 1.3 (aktuelle piper-tts-Pakete)
    from piper import PiperVoice
except ImportError:  # pragma: no cover - aeltere Installationen
    from piper.voice import PiperVoice  # type: ignore[no-redef]

# Das Geraet erwartet 24 kHz mono, 16 Bit little endian.
DEVICE_SAMPLE_RATE = 24000


# Markdown-Reste, die nicht vorgelesen werden duerfen. Der Agent antwortet
# haeufig formatiert (`**13:28 Uhr**`, Aufzaehlungen, Links); eine
# Sprachausgabe darf davon nichts aussprechen.
_MARKDOWN_RULES = (
    (re.compile(r"!\[([^\]]*)\]\([^)]*\)"), r"\1"),        # Bilder -> Alt-Text
    (re.compile(r"\[([^\]]+)\]\([^)]*\)"), r"\1"),          # Links -> Text
    (re.compile(r"```[^`]*```", re.DOTALL), " "),                 # Codebloecke
    (re.compile(r"`([^`]*)`"), r"\1"),                          # Inline-Code
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),                    # fett
    (re.compile(r"__([^_]+)__"), r"\1"),                        # fett (alt)
    (re.compile(r"(?<![\w])\*([^*\n]+)\*(?![\w])"), r"\1"),     # kursiv
    (re.compile(r"(?<![\w])_([^_\n]+)_(?![\w])"), r"\1"),       # kursiv (alt)
    (re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE), ""),        # Ueberschriften
    (re.compile(r"^\s{0,3}[-*+]\s+", re.MULTILINE), ""),         # Aufzaehlung
    (re.compile(r"^\s{0,3}>\s?", re.MULTILINE), ""),             # Zitat
    (re.compile(r"\s*\|[^\n]*\|"), " "),                       # Tabellenzeilen
)
# Absatzgrenzen: ein Satzzeichen ergaenzen, aber nur wenn noch keines steht.
_PARAGRAPH = re.compile(r"([^.!?:;,\s])\s*\n{2,}\s*")


def strip_markdown(text: str) -> str:
    """Entfernt Formatierungszeichen, die sonst Buchstabe fuer Buchstabe
    vorgelesen wuerden. Der reine Inhalt bleibt erhalten."""
    if not text:
        return ""
    cleaned = text
    for pattern, replacement in _MARKDOWN_RULES:
        cleaned = pattern.sub(replacement, cleaned)
    # Absatzgrenzen werden zu einer Sprechpause mit Satzzeichen.
    cleaned = _PARAGRAPH.sub(r"\1. ", cleaned)
    # Sternchen/Unterstriche, die uebrig blieben, sind Formatierungsreste.
    cleaned = cleaned.replace("*", "").replace("#", "")
    cleaned = re.sub(r"\s+", " ", cleaned)
    # Mehrfache Satzzeichen zusammenziehen (z. B. ".." aus Absatzgrenzen).
    cleaned = re.sub(r"([.!?,;:])\1+", r"\1", cleaned)
    cleaned = re.sub(r"\.\s*\.", ".", cleaned)
    return cleaned.strip()


class PiperSpeaker:
    """Haelt ein Piper-Modell geladen und liefert PCM fuer das Geraet.

    Die Feinregler (`length_scale`, `noise_scale`, `noise_w_scale`, `volume`)
    kommen von Piper selbst:

    - `length_scale`: Tempo. 1,0 ist normal, groesser = langsamer, kleiner =
      schneller. Fuer Sprachausgabe ist leicht unter 1 oft verstaendlicher.
    - `noise_scale`: Variation der Aussprache. Hoeher = lebendiger, aber
      unruhiger; niedriger = gleichmaessiger.
    - `noise_w_scale`: Variation der Lautlaengen. Achnlich, feiner.
    - `volume`: Lautstaerke der Synthese (nicht der Geraetelautstaerke).

    `None` bedeutet jeweils: Pipers Standardwert unveraendert lassen.
    """

    def __init__(
        self,
        model_path: str,
        target_rate: int = DEVICE_SAMPLE_RATE,
        length_scale: float | None = None,
        noise_scale: float | None = None,
        noise_w_scale: float | None = None,
        volume: float | None = None,
    ):
        self.model_path = model_path
        self.target_rate = target_rate
        self.voice = PiperVoice.load(model_path)
        self.source_rate = int(self.voice.config.sample_rate)
        self.length_scale = length_scale
        self.noise_scale = noise_scale
        self.noise_w_scale = noise_w_scale
        self.volume = volume
        self._syn_config = self._build_config()
        # Ein Satz Piper-Audio wird in einem Stueck umgesetzt. Kleine
        # Puffergroessen waeren hier kuenstlich; der Aufrufer zerlegt das
        # Ergebnis ohnehin in 60-ms-Opus-Rahmen.
        self._rng = np.arange(0, 1, dtype=np.float64)

    def _build_config(self):
        """Baut die Piper-Syntheseeinstellungen, falls Feinregler gesetzt sind.

        Wichtig: Nur gesetzte Werte uebergeben. Ein explizites
        `volume=None` wuerde Pipers Standard (1.0) ueberschreiben und die
        Synthese mit einem Typfehler abbrechen (`float * None`).
        """
        overrides = {}
        for name in ("length_scale", "noise_scale", "noise_w_scale", "volume"):
            value = getattr(self, name)
            if value is not None:
                overrides[name] = value
        if not overrides:
            return None
        from piper import SynthesisConfig

        return SynthesisConfig(**overrides)

    def describe(self) -> dict:
        info = {
            "model": self.model_path,
            "source_rate": self.source_rate,
            "target_rate": self.target_rate,
        }
        # Nur gesetzte Feinregler anzeigen, damit die Startzeile kurz bleibt.
        for name in ("length_scale", "noise_scale", "noise_w_scale", "volume"):
            value = getattr(self, name)
            if value is not None:
                info[name] = value
        return info

    def _resample(self, samples: np.ndarray) -> np.ndarray:
        """Lineare Umsetzung von source_rate auf target_rate.

        Das Verhaeltnis ist klein (16/22,05 kHz -> 24 kHz), deshalb ist
        lineare Interpolation hier ausreichend und bleibt deterministisch.
        """
        if self.source_rate == self.target_rate or samples.size == 0:
            return samples
        out_length = int(round(samples.size * self.target_rate / self.source_rate))
        if out_length <= 0:
            return np.zeros(0, dtype=np.int16)
        source_index = np.arange(samples.size, dtype=np.float64)
        target_index = np.linspace(0.0, samples.size - 1, out_length)
        resampled = np.interp(target_index, source_index, samples.astype(np.float64))
        return np.clip(np.rint(resampled), -32768, 32767).astype(np.int16)

    def synthesize_pcm(self, text: str) -> bytes:
        """Wandelt Text in PCM (s16le, mono, target_rate).

        Gibt einen leeren Bytes-String zurueck, wenn der Text nichts
        Sprechbares enthaelt.
        """
        text = (text or "").strip()
        if not text:
            return b""
        parts: list[np.ndarray] = []
        for chunk in self.voice.synthesize(text, syn_config=self._syn_config):
            raw = chunk.audio_int16_bytes
            if not raw:
                continue
            parts.append(np.frombuffer(raw, dtype=np.int16))
        if not parts:
            return b""
        samples = np.concatenate(parts)
        resampled = self._resample(samples)
        return resampled.tobytes()


def find_default_voice(voice_dir: str, voice_name: str) -> str:
    """Loest einen Stimmen-Namen wie `de_DE-thorsten-medium` zum Pfad auf."""
    from pathlib import Path

    base = Path(voice_dir)
    candidates = [
        base / f"{voice_name}.onnx",
        # Piper-Voices liegen haeufig unter <dir>/de/de_DE/<name>.onnx
        base / voice_name.split("_")[0] / voice_name.split("-")[0] / f"{voice_name}.onnx",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    # Letzter Versuch: rekursiv suchen.
    for candidate in base.rglob(f"{voice_name}.onnx"):
        return str(candidate)
    raise FileNotFoundError(
        f"Piper-Stimme {voice_name!r} nicht gefunden unter {voice_dir!r}"
    )
