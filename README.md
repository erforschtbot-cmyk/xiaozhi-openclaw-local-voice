# Jarvis: Waveshare XiaoZhi als OpenClaw-Sprachterminal (lokal)

Dieses Repository enthält **alle projektspezifischen Dateien und Anleitungen**,
um ein funktionierendes Jarvis-System auf einem leeren Linux-/OpenClaw-Host
exakt wiederherzustellen — mit **lokaler Erkenung und lokaler
Sprachausgabe**. Das gesprochene Wort wird auf dem Host erkannt
(faster-whisper) und gesprochen (Piper). **Kein Mikrofon-Audio und keine
Stimme geht mehr an einen Cloud-Sprachdienst.**

## Architektur

```text
Waveshare ESP32-S3-Touch-LCD-4B
  ├─ Wakeword (wn9_jarvis_tts), Mikrofon, Lautsprecher, Display, Geräte-MCP
  └─ WebSocket :8765
          ↓
Python-Bridge  (server.py, systemd: jarvis-realtime-bridge.service)
  ├─ Opus-Audio vom Gerät, Nachhör-Fenster, OTA auf :8766
  ├─ startet Helfer: node local-voice.mjs        ← Erkenung + Agent + Text
  │    ├─ Erkennt Aeusserungsgrenzen (Pegel, 700 ms Stille)
  │    ├─ Transkribiert lokal: Wyoming-Whisper (127.0.0.1:10300)
  │    └─ Ruft den OpenClaw-Agenten auf (chat.send)
  └─ vertont den Antworttext lokal: piper_speaker.py (Piper, CPU)
          ↓
OpenClaw Gateway  (wss://127.0.0.1:18789)
  └─ OpenClaw-Agent (Modell, Werkzeuge, Skills)
          ↓
Piper spricht den Text lokal  →  als Opus zurück zum Gerät
```

Lokal sind: Wakeword, Mikrofonweg, Erkenung, Sprachausgabe und die
Audioausgabe am Gerät. Ueber das Gateway laeuft nur noch das **Denken**
(der Agent mit seinem Modell).

**Vier Bausteine, klar getrennt:**

1. `gateway/server.py` — WebSocket-Server auf Port 8765/8766, Taktung und
   Nachhör-Fenster.
2. `gateway/local-voice.mjs` — der lokale Helfer: erkennt Aeusserungen,
   holt den Text vom lokalen Whisper, ruft den Agenten auf und meldet dessen
   Antworttext.
3. `gateway/local_stt.mjs` — Wyoming-Protokoll und Aeusserungsgrenzen.
4. `gateway/piper_speaker.py` — haelt das Piper-Stimmmodell warm und wandelt
   Text in PCM mit der vom Gerät erwarteten Rate (24 kHz).

Details: [`docs/07-LOKALE-STIMME.md`](docs/07-LOKALE-STIMME.md),
[`docs/08-LOKALE-ERKENNUNG.md`](docs/08-LOKALE-ERKENNUNG.md),
[`docs/09-FIRMWARE-WACHWORT.md`](docs/09-FIRMWARE-WACHWORT.md).

## Was hier drin ist

| Bestandteil | Datei(en) |
|---|---|
| Firmware-Patches gegen den gepinnten Upstream | `firmware/patches/` |
| Board-spezifisches animiertes OpenClaw-Gesicht | im XiaoZhi-Patch unter `jarvis_face_display.*` |
| Wiederherstellungs-Watchdog (im Firmware-Patch) | `firmware/patches/xiaozhi-esp32-openclaw.patch` |
| Bestätigte Firmware-Images (Wiederherstellungsanker) | `firmware/prebuilt/` |
| Aufgelöste Abhängigkeiten des Firmware-Builds | `firmware/dependencies.lock` |
| Bridge (Python, WebSocket-Server) | `gateway/server.py` |
| Lokale Erkennung (Node) | `gateway/local-voice.mjs`, `gateway/local_stt.mjs` |
| Lokale Sprachausgabe (Piper) | `gateway/piper_speaker.py` |
| Provider-Helfer (Rückfallweg) | `gateway/openclaw-talk-realtime.mjs` |
| OpenClaw-`dist`-Patches für den Voice-Pfad | `scripts/apply-openclaw-voice-dist-patches.py` |
| Stimmen-Benchmark für die eigene Hardware | `scripts/benchmark-tts.py` |
| Tests (Einheit + Ende-zu-Ende) | `tests/` |
| systemd-Userdienst | `systemd/` |
| Bau-/Flash-/Installationsskripte | `scripts/` |
| Prüfsummen | `CHECKSUMS.sha256` |
| Verbindliche Versionen und Pins | `VERSIONS.md` |

## In Kurzform

| Stufe | Umsetzung | Messwert auf dem Referenzhost |
|---|---|---|
| Erkennung | faster-whisper `small`, int8, CPU | ~2,2 s für 3,1 s Sprache |
| Stimme | Piper `de_DE-kerstin-low` | RTF 0,04, erster Ton ~70 ms |
| Aeusserungsende | 700 ms Stille | vorher 3000 ms beim Provider |
| Sprechgrenze | `--tts-split` (Komma/Satz/ganz) | Standard: Satzende

```bash
./scripts/install-host.sh --public-host 192.168.178.143
# → lokale Erkennung + lokale Stimme

./scripts/install-host.sh --public-host 192.168.178.143 --provider-stt --provider-voice
# → Rueckweg: alles wieder wie zuvor ueber den Provider
```

Voraussetzungen: laufender Wyoming-Whisper-Dienst und die Piper-Stimme:

```bash
python3 -m piper.download_voices de_DE-kerstin-low
```

## Dauerhafte Talk-Sitzung (Ist-Stand)

`server.py` setzt seit dem 27.09.2026 **konstante** Schlüssel:

```python
OPENCLAW_TALK_SESSION_KEY    = self.args.session_key        # agent:voice:xiaozhi-realtime-v5
OPENCLAW_TALK_CONSULT_SESSION_KEY = self.args.consult_session_key  # agent:allgemein:xiaozhi-realtime-v5
```

Vorher hing an beiden Schlüsseln eine UUID. Damit wurde für **jede** Äußerung
beziehungsweise **jeden** Werkzeug-Aufruf eine frische Session aufgebaut.

Folgen des Wechsels (belegt im Betrieb):

- **Werkzeug-Antworten kommen spürbar schneller** (näher an Alexa): die Sitzung
  bleibt warm, der Prompt-Cache greift (`cacheRead`), der Aufbau einer neuen
  Session pro Frage entfällt.
- **Kein `SqliteTranscriptMutationConflictError` mehr** auf frischen Transkripten.
- **Kein Wachstum an Sessions pro Werkzeugfrage** durch den Session-Schlüssel.

Zum Zurücksetzen: in `server.py` die beiden Zuweisungen wieder auf
`f"{self.args.session_key}-{self.turn_id}"` und
`f"{self.args.consult_session_key}-{self.session_id}"` setzen.

Ergänzend wurde `is_interim_text()` von einem Präfix-Vergleich (`startswith`) auf
eine **geschlossene Liste reiner Zwischenansagen** umgestellt. Eine zu einer
Nachricht verwachsene Zwischenansage+Antwort („I'll check that request. Es ist 01:02
Uhr.“) erhält dadurch korrekt `stream_end`; siehe `docs/05-FEHLERSUCHE.md`.

Die Firmware enthält **keinen OpenAI-Schlüssel**. Anmeldung und Schlüssel bleiben
auf dem OpenClaw-Host.

## Neuinstallation in richtiger Reihenfolge

1. [`docs/01-HOST-INSTALLATION.md`](docs/01-HOST-INSTALLATION.md)
2. [`docs/02-FIRMWARE.md`](docs/02-FIRMWARE.md)
3. [`docs/03-OPENCLAW-PATCH.md`](docs/03-OPENCLAW-PATCH.md)
4. [`docs/04-TESTPLAN.md`](docs/04-TESTPLAN.md)
5. [`docs/05-FEHLERSUCHE.md`](docs/05-FEHLERSUCHE.md)
6. [`docs/06-BUILD-VERIFICATION.md`](docs/06-BUILD-VERIFICATION.md)
7. [`docs/07-LOKALE-STIMME.md`](docs/07-LOKALE-STIMME.md)
8. [`docs/08-LOKALE-ERKENNUNG.md`](docs/08-LOKALE-ERKENNUNG.md)
9. [`docs/09-FIRMWARE-WACHWORT.md`](docs/09-FIRMWARE-WACHWORT.md)

Für einen bereits eingerichteten Host genügt typischerweise:

```bash
./scripts/install-host.sh --public-host 192.168.178.143
./scripts/apply-openclaw-voice-dist-patches.py
./scripts/verify-host.sh
python3 tests/test_local_tts.py
node tests/test_local_stt.mjs
python3 tests/test_bridge_local_stt_e2e.py
systemctl --user restart openclaw-gateway.service
```

## Bestätigte Firmware (Ist-Stand)

Aus dem Boot-Log und dem App-Deskriptor des Geräts gelesen:

| Merkmal | Wert |
|---|---|
| Project | `xiaozhi` |
| Version | `2.5.0` |
| Compile time | **Sep 28 2026 13:08:52** |
| ELF-SHA256 | `c30048b5f32545b3d596fb45089a0ab2e0db6b29d94cefdd724f023e11f937cd` |
| ESP-IDF | `v6.1` |
| Board | `esp32-s3-touch-lcd-4b` |
| Sprache | `de-DE` |

Dieser Stand enthält den **Wiederherstellungs-Watchdog**, das animierte
board-spezifische OpenClaw-Gesicht und den Eingriff, dass das **Wachwort-Audio
nicht mehr an den Server** geschickt wird (`CONFIG_SEND_WAKE_WORD_DATA=n`,
siehe [`docs/09-FIRMWARE-WACHWORT.md`](docs/09-FIRMWARE-WACHWORT.md)). Mikrofon,
Erkennung und Mithören sind davon nicht betroffen.

Der Stand **vor** diesem Eingriff liegt als `firmware/prebuilt/previous/xiaozhi.bin`
bereit (`Sep 27 2026 14:13:36`, ELF `77fd991a…`) — ein Befehl als Rückweg.

Verwechslungsgefahr: Es existieren weitere `xiaozhi 2.5.0`-Builds, u. a. der
vorherige bewährte Stand **23.09. 08:43** (ELF `b6bf3e74…`) und ein weiterer vom
**22.09. 15:25** (ELF `dc80cd8c…`). Weder Version noch Projektname unterscheiden
sie — nur Compile-Zeit und ELF-Hash.

## Wiederherstellungsanker

`firmware/prebuilt/` enthält den aktuellen, am Gerät bestätigten Stand:

| Datei | Inhalt | Schreibziel |
|---|---|---|
| `xiaozhi.bin` | Nur App-Partition (`ota_0`) des laufenden Builds (Wachwort-Audio abgeschaltet) | `0x20000` |
| `merged-binary.bin` | Vollständiges Image, **NVS auf `0xFF` geleert** | `0x0` (nur Rettungsfall) |
| `previous/xiaozhi.bin` | Stand **vor** dem Wachwort-Eingriff (`77fd991a…`) | `0x20000` |
| `previous-watchdog-smiley/xiaozhi.bin` | Vorheriger Watchdog-Stand mit statischem Smiley (`2d817b23…`) | `0x20000` |
| `previous-no-watchdog/xiaozhi.bin` | Vorheriger bewährter Stand (`b6bf3e74…`), ohne Watchdog | `0x20000` |
| `previous-face-v2-30fps/xiaozhi.bin` | Direkter Rückweg zum Gesicht vor der finalen LVGL-Optimierung | `0x20000` |

Das NVS im Vollimage wurde bewusst geleert: Es enthält WLAN-Zugangsdaten im
Klartext. Ein geflashtes Gerät muss danach neu provisoniert werden.

## Sicherheitsgrenzen

- `firmware/prebuilt/xiaozhi.bin` wird bei `0x20000` geschrieben (nur App). NVS,
  WLAN und Assets bleiben erhalten.
- `firmware/prebuilt/merged-binary.bin` überschreibt das gesamte Flash und wird
  deshalb nur mit dem ausdrücklichen Schalter `--full-image-i-understand` geflasht.
- **Niemals** Zugangsdaten, `secrets.json`, OpenAI-/GitHub-Tokens oder
  WLAN-Passwörter in dieses Repository eintragen.
- Ein vollständiges Geräteimage enthält das NVS mit WLAN-Zugangsdaten. Vor jeder
  Veröffentlichung eines Rohimages prüfen: NVS-Bereich `0x9000`–`0xF000`.

## Referenzprüfsummen

Siehe [`CHECKSUMS.sha256`](CHECKSUMS.sha256).

## Herkunft

Der Aufbau entstand aus einer funktionierenden Basis; frühere vollständige
Neubauten konnten Mikrofon/Audio verlieren. Deshalb sind die Prebuilt-Images der
Recovery-Anker, und ein Neuaufbau muss erst den vollständigen Testplan bestehen.
Details: [`docs/06-BUILD-VERIFICATION.md`](docs/06-BUILD-VERIFICATION.md).
