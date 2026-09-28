# Verbindliche Versionen

Diese Werte beschreiben den **Ist-Stand vom 2026-09-28** auf dem Referenzhost.
Die Firmware enthaelt gegenueber dem Vorgaengerprojekt **eine** Aenderung:
Das Wachwort-Audio wird nicht mehr an den Server geschickt
(`CONFIG_SEND_WAKE_WORD_DATA=n`, siehe `docs/09-FIRMWARE-WACHWORT.md`).
Erkennung und Stimme laufen lokal auf dem Host.

| Komponente | Version / Pin |
|---|---|
| OpenClaw | `2026.9.6` (`eb377ac`) |
| Node.js | `26.7.0` auf dem Referenzhost |
| Python | `3.14.7` auf dem Referenzhost |
| XiaoZhi Upstream | `78/xiaozhi-esp32@4632dc51f0a5ad26e08542e131e6e48da41e4ff3` |
| uart-uhci Upstream | `78/uart-uhci@6ea5f576af84640209aa6b98e27be92b8ce418c5` |
| ESP-IDF Build-Image | `espressif/idf:v6.1` (Digest `sha256:81893c71bb5e570088901f21def8684c25cd2a9020281bd01b843a7655edb18c`) |
| Board | Waveshare ESP32-S3-Touch-LCD-4B |
| Bridge-Python-Pakete | `websockets==15.0.1`, `opuslib==3.0.1` |
| **Lokale Sprachausgabe** | `piper-tts==1.4.2` (GPL-3.0-or-later), `numpy>=1.26` |
| **Lokale Stimme** | `de_DE-kerstin-low` (16 kHz, 60,2 MB) |
| **Lokale Erkennung** | `faster-whisper` **small**, int8, CPU (Wyoming-Dienst) |
| Erkennungsdienst | `wyoming-faster-whisper`, Port `10300` |
| Realtime-Modell (nur noch Rueckfall) | `gpt-live-1-codex` |
| Provider-Stimme (nur noch Rueckfall) | `cove` |
| Talk-Session-Key | `agent:voice:xiaozhi-realtime-v5` (konstant, ohne UUID-Anhang) |
| Talk-Consult-Key | `agent:allgemein:xiaozhi-realtime-v5` (konstant, ohne UUID-Anhang) |

## Stimmen-Eignung auf dem Referenzhost

Gemessen mit `tests/test_local_tts.py` bzw. `scripts/benchmark-tts.py` auf
Intel i5-8365U (nur CPU, keine NVIDIA). RTF = Synthesezeit / Audiozeit;
Werte unter 1,0 sind schneller als Echtzeit.

| Stimme | Abtastrate | RTF | Bewertung |
|---|---|---|---|
| `de_DE-kerstin-low` | 16 kHz | 0,04 | **Standard** (so gewaehlt) |
| `de_DE-thorsten-medium` | 22,05 kHz | 0,05–0,06 | bester Kompromiss aus Klang/Tempo |
| `de_DE-thorsten-high` | 22,05 kHz | ~0,27 | beste Qualitaet, 5x Rechenzeit |
| `de_DE-thorsten-low` | 16 kHz | ~0,04 | schnell, schmalbandiger |
| `de_DE-thorsten_emotional-medium` | 22,05 kHz | ~0,05 | emotionaler Klang |
| `de_DE-mls-medium` | 22,05 kHz | ~0,06 | 236 Sprecher zur Auswahl |
| `de_DE-eva_k-x_low` | 16 kHz | ~0,04 | weiblich, kleinste Datei |

Hinweis: Alle deutschen Frauenstimmen bei Piper existieren nur als `low`
oder `x_low`; medium/high gibt es nur fuer Thorsten-Stimmen.

## Erkennungs-Eignung auf dem Referenzhost

Gemessen gegen den laufenden Wyoming-Dienst (CPU, int8) mit einem 3,12-s-Satz:

| Modell | Zeit | RTF | Bewertung |
|---|---|---|---|
| `base` | 0,81 s | 0,26 | schnell, verhört sich gelegentlich |
| `small` | 2,18 s | 0,70 | **Standard** — genauer |
| `medium` | 7,3–15,0 s | 2,3–4,8 | **untauglich** fuer Sprache |

Der frueher laufende `medium`-Dienst war 9- bis 18-mal langsamer als `base`.
Gewaehlt wurde `small`, weil die Erkennungsfehler von `base` im Sprachbetrieb
stoerten ("Schalte" -> "Zeite").

## Laufende Firmware (Geräte-Ist-Stand)

| Merkmal | Wert |
|---|---|
| Project | `xiaozhi` |
| Version | `2.5.0` |
| Compile time | `Sep 28 2026 13:08:52` (de-DE, Wachwort-Audio abgeschaltet) |
| ELF-SHA256 | `c30048b5f32545b3d596fb45089a0ab2e0db6b29d94cefdd724f023e11f937cd` |
| ESP-IDF | `v6.1` |
| Geräte-MAC | `94:a9:90:cc:8d:b4` |
| App-Partition | `0x20000`, 4.128.768 Bytes; belegt 2.905.488 (29 % frei) |

Dieser Stand liegt als `firmware/prebuilt/xiaozhi.bin` im Repo und wurde am
2026-09-28 auf das Gerät geflasht; die Prüfsumme nach dem Schreiben wurde
zurückgelesen und stimmte.

### Rückwege (vorherige Stände)

Direkter Rückweg — der Stand **vor** dem Wachwort-Eingriff:
`Sep 27 2026 14:13:36`, ELF `77fd991a4856c6bef3c0dd3aa08d3f3ef650b06c74bd23087bb4aad553c91362`
(`firmware/prebuilt/previous/xiaozhi.bin`).

Watchdog-Stand mit statischem Smiley: `Sep 26 2026 19:10:55`,
ELF `2d817b23a349060d` (`firmware/prebuilt/previous-watchdog-smiley/xiaozhi.bin`).

Ohne Watchdog: `Sep 23 2026 08:43:24`, ELF `b6bf3e74cef7c701`
(`firmware/prebuilt/previous-no-watchdog/xiaozhi.bin`).

**Nicht verwechseln:** Es gibt weitere `xiaozhi 2.5.0`-Builds, u. a. vom
`Sep 22 2026 15:25:48` (ELF `dc80cd8c5e42d81b`). Version und Projektname sind
identisch — nur Compile time und ELF-Hash unterscheiden die Builds.

Neuere Versionen sind nicht automatisch gleichwertig. Erst nach einem vollständigen
End-to-End-Test dürfen Pins aktualisiert werden.
