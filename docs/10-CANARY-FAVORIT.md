# 10 — Canary 180M Flash als Favorit (lokale Erkennung)

Dieses Dokument beschreibt die **bevorzugte** lokale Erkennung dieses
Projekts: **NVIDIA Canary 180M Flash (INT8)** über `sherpa-onnx`, mit
**Silero-VAD** davor. Sie ersetzt im Referenzbetrieb `faster-whisper small`.

> **Kurzfassung:** Canary + VAD versteht kurze deutsche Alltagsbefehle am
> Gerät besser als `faster-whisper small` und ist dabei vergleichbar schnell.
> Der Dienst läuft parallel zu Whisper auf einem eigenen Port — ein Rückweg
> ist ein Einzeiler.

## Warum Canary

| Kriterium | Canary 180M Flash INT8 | faster-whisper small |
|---|---|---|
| Modellgroesse | 180 M Parameter | 244 M Parameter |
| Sprachen | en, es, de, fr (`src_lang`/`tgt_lang`) | mehrsprachig |
| Decoder | NeMo-CTM (AED) | Whisper-Seq2Seq |
| Laufzeit | sherpa-onnx (ONNX Runtime) | CTranslate2 |
| Modell-Init | ~3,1 s (einmalig) | ~2 s |
| Deutsche Alltagsbefehle (Gerät) | **besser** | gut |

Der Vergleich wurde mit identischem PCM gegen beide Dienste gefahren
(`scripts/compare-stt.sh`). Auf dem Referenzhost (Intel i5-8365U, nur CPU):

- Kurze, klare Befehle am Gerät: **Canary klar besser** (vom Owner so
  entschieden: „der versteht perfekt").
- Lange, verwaschene WhatsApp-Clips: `faster-whisper small` war teils
  robuster (Canary streut dort gelegentlich Fuellwoerter). Fuer den
  Sprachterminal-Betrieb ist das ohne Belang.

## Aufbau

```text
Geraet --PCM--> Bridge (local-voice.mjs)
                   |
                   +--> Wyoming-Anfrage an 127.0.0.1:10301
                   |       (wyoming_canary.py)
                   |          |
                   |          +-- Silero-VAD: hat das Signal ueberhaupt Sprache?
                   |          +-- Ja           -> komplettes Audio an Canary (NeMo)
                   |          +-- Nein, >=1.5s -> komplettes Audio an Canary (NeMo)
                   |          +-- Nein, <1.5s  -> leerer Text
                   |
                   +--> Transkript -> Agent (chat.send)
```

Warum das VAD **filtert**, aber **nicht zuschneidet**: Wird nur der
VAD-Ausschnitt transkribiert, fehlt regelmaessig der Wortanfang. Deshalb
dient das VAD hier ausschliesslich als **Tor**: Sprache erkannt -> das
komplette Audio an Canary; keine Sprache -> leer. Das entfernt
Halluzinationen auf Rauschen (Canary lieferte sonst z. B. „T S F"), ohne den
Anfang abzuschneiden.

### Das VAD-Tor darf eine echte Aeusserung nicht wegwerfen

Das Tor ist eine **Vor**stufe. Genau deshalb kann es die groesste Staerke von
Canary zunichte machen: leise und aus der Ferne gesprochene erste Saetze.
Meldet Silero dort „keine Sprache", war die Aufnahme verloren, **bevor**
Canary sie ueberhaupt sah — obwohl Canary sie problemlos verstanden haette.
Beobachtet direkt nach einem frischen Kanal: alle Fehlschlaege leise
(peak 60–250), alle Erfolge laut (peak 300–1400).

**Verhalten:**

| VAD-Ergebnis | Aufnahmedauer | Ergebnis |
|---|---|---|
| Sprache erkannt | egal | komplettes Audio an Canary |
| **keine** Sprache | **>= 1,5 s** | **komplettes Audio an Canary** (Canary entscheidet) |
| keine Sprache | < 1,5 s | leer (echtes Rauschen/Klicken) |

Die Logzeile macht den Unterschied sichtbar:

```text
VAD: keine Sprache erkannt, aber 3.90s Audio -> Canary trotzdem
VAD: keine Sprache erkannt (kurz, 0.80s) -> verworfen
```

**Regel:** Ein VAD-Tor darf filtern, aber keine Aufnahme verwerfen, die lang
genug ist, um ein echter Satz zu sein. Die Entscheidung „ist das Text?" gehoert
an Canary, nicht an den VAD.

## Installation

```bash
# 1) Eigenes venv
python3 -m venv ~/wyoming-canary/.venv
~/wyoming-canary/.venv/bin/pip install sherpa-onnx wyoming numpy

# 2) Modell (INT8) + Testdateien
#    Quelle: csukuangfj/sherpa-onnx-nemo-canary-180m-flash-en-es-de-fr-int8
#    Dateien: encoder.int8.onnx (~127 MB), decoder.int8.onnx (~71 MB),
#             tokens.txt, test_wavs/de.wav, test_wavs/en.wav

# 3) Silero-VAD
curl -L -o ~/wyoming-canary/silero_vad.onnx \
  https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/silero_vad.onnx
```

Der Server liegt als `gateway/wyoming_canary.py` im Repo. Start:

```bash
~/wyoming-canary/.venv/bin/python gateway/wyoming_canary.py \
  --uri tcp://0.0.0.0:10301 \
  --model-dir ~/wyoming-canary/model \
  --language de --num-threads 8 \
  --vad-model ~/wyoming-canary/silero_vad.onnx
```

Dauerhafter Dienst: `systemd/wyoming-canary.service.reference`
(Port **10301**). Whisper bleibt auf **10300** parallel.

## Bridge darauf umstellen

In `systemd/jarvis-realtime-bridge.service` nur die STT-URI aendern:

```diff
- --stt-uri 127.0.0.1:10300     # Whisper
+ --stt-uri 127.0.0.1:10301     # Canary  (Favorit)
```

Danach:

```bash
systemctl --user daemon-reload
systemctl --user restart jarvis-realtime-bridge
```

**Rueckweg zu Whisper:** dieselbe Zeile zurueck auf `10300`.

## Protokoll

Der Server spricht dasselbe Wyoming-ASR-Protokoll wie
`wyoming-faster-whisper`:

```text
-> Transcribe(language="de")        optional, waehlt src_lang/tgt_lang
-> AudioStart(rate, width, channels)
-> AudioChunk(...)                  beliebig viele, 16-bit PCM
-> AudioStop()
<- Transcript(text="...")
```

`language` bildet auf Canarys `src_lang`/`tgt_lang` ab; unterstuetzt sind
`en`, `es`, `de`, `fr`. Andere Werte fallen auf die Standardsprache zurueck.

## Messwerte (Referenzhost, i5-8365U, CPU, 8 Threads)

| Signal | Dauer | Latenz | RTF |
|---|---|---|---|
| `de.wav` (Testwav) | 2,75 s | ~1,2–2,1 s | 0,44–0,76 |
| Rauschen | 1,0 s | ~0,04–0,08 s | (leer) |

Die RTF schwankt je nach Systemlast; entscheidend ist der
**VAD-Kurzschluss**: Bei Rauschen faellt die Antwort in Millisekunden aus,
weil gar nicht transkribiert wird.

## Prüfe das Ergebnis selbst

```bash
scripts/compare-stt.sh <audio-datei> [de]
```

Schickt **denselben** PCM an `:10300` (Whisper) und `:10301` (Canary) und
zeigt beide Texte samt Latenz/RTF.

## Fallstricke

- **Kein VAD im Modell.** Canary selbst erkennt keine Sprechpausen. Ohne
  `--vad-model` transkribiert es Rauschen und erzeugt Artefakte.
- **Zuschneiden vermeiden.** Nur den VAD-Ausschnitt zu verwenden kostet den
  Wortanfang (empirisch: „Alles hat ein Ende" -> „hat ein Ende").
- **Erstes Modell pro Sprache wird geladen.** Beim Wechsel der Sprache
  (z. B. `de` -> `en`) laedt ein zweiter Recognizer (~3 s). Pro Sprache
  wird der Recognizer danach warm gehalten.
