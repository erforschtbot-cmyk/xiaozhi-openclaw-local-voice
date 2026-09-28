# Jarvis: Waveshare XiaoZhi als OpenClaw-Sprachterminal (lokale Stimme)

Dieses Repository enthält **alle projektspezifischen Dateien und Anleitungen**,
um ein funktionierendes Jarvis-System auf einem leeren Linux-/OpenClaw-Host
exakt wiederherzustellen — mit **lokaler deutscher Sprachausgabe**.

**Der einzige Unterschied zum Vorgängerprojekt** ist der Sprechpfad: Die Stimme
entsteht nicht mehr bei OpenAI, sondern lokal auf dem Host mit **Piper**. Das
Hören, der Agent, die Werkzeuge und die Firmware bleiben unverändert.

## Architektur

```text
Waveshare ESP32-S3-Touch-LCD-4B
  ├─ Wakeword (wn9_jarvis_tts), Mikrofon, Lautsprecher, Display, Geräte-MCP
  └─ WebSocket :8765
          ↓
Python-Bridge  (server.py, systemd: jarvis-realtime-bridge.service)
  ├─ Opus-Audio vom Gerät, Nachhör-Fenster, OTA auf :8766
  ├─ startet Helfer: node openclaw-talk-realtime.mjs (konstante Session-Schlüssel)
  └─ vertont den Antworttext lokal: piper_speaker.py (Piper, CPU)
          ↓
OpenClaw Gateway  (wss://127.0.0.1:18789)
  └─ Talk / OpenAI GPT-Live (gpt-live-1-codex)
       ├─ ERKENNT die Sprache und liefert den Gesprächstext
       ├─ ruft bei Werkzeugbedarf den OpenClaw-Agenten auf
       └─ dessen gesprochenes Audio wird VERWORFEN
          ↓
Piper spricht den Text lokal  →  als Opus zurück zum Gerät
```

**Drei Skripte, klar getrennt:**

1. `gateway/server.py` — dauerhafter WebSocket-Server auf Port 8765/8766.
2. `gateway/openclaw-talk-realtime.mjs` — Kindprozess von `server.py`. Pro
   Äußerung wird ein Helfer gestartet/weiterverwendet; er verbindet zum Gateway.
   Der **Talk-Session-Schlüssel ist konstant**, sodass alle Äußerungen in
   **derselben** OpenClaw-Session landen.
3. `gateway/piper_speaker.py` — hält das Piper-Stimmmodell warm und wandelt
   Text in PCM mit der vom Gerät erwarteten Rate (24 kHz).

Details zur lokalen Stimme: [`docs/07-LOKALE-STIMME.md`](docs/07-LOKALE-STIMME.md).

## Was hier drin ist

| Bestandteil | Datei(en) |
|---|---|
| Firmware-Patches gegen den gepinnten Upstream | `firmware/patches/` |
| Board-spezifisches animiertes OpenClaw-Gesicht | im XiaoZhi-Patch unter `jarvis_face_display.*` |
| Wiederherstellungs-Watchdog (im Firmware-Patch) | `firmware/patches/xiaozhi-esp32-openclaw.patch` |
| Bestätigte Firmware-Images (Wiederherstellungsanker) | `firmware/prebuilt/` |
| Aufgelöste Abhängigkeiten des Firmware-Builds | `firmware/dependencies.lock` |
| Bridge-Skript (Python, WebSocket-Server) | `gateway/server.py` |
| **Lokale Sprachausgabe (Piper)** | `gateway/piper_speaker.py` |
| Bridge-Skript (Node, Gateway-Helfer) | `gateway/openclaw-talk-realtime.mjs` |
| OpenClaw-`dist`-Patches für den Voice-Pfad | `scripts/apply-openclaw-voice-dist-patches.py` |
| Stimmen-Benchmark für die eigene Hardware | `scripts/benchmark-tts.py` |
| Tests (Einheit + Ende-zu-Ende) | `tests/` |
| systemd-Userdienst | `systemd/` |
| Bau-/Flash-/Installationsskripte | `scripts/` |
| Prüfsummen | `CHECKSUMS.sha256` |
| Verbindliche Versionen und Pins | `VERSIONS.md` |

## Lokale Stimme in Kurzform

Standardstimme ist **`de_DE-thorsten-medium`** (22,05 kHz). Gemessen auf dem
Referenzhost (Intel i5-8365U, nur CPU): **RTF 0,05–0,06**, erster Ton nach
~70 ms. Die Stimme ist frei wählbar (siehe `VERSIONS.md` für die Eignungstabelle
und `docs/07-LOKALE-STIMME.md` für Auswahl und Prüfung).

```bash
./scripts/install-host.sh --public-host 192.168.178.143
# → installiert Piper ins Bridge-Venv und aktiviert --tts-local

./scripts/install-host.sh --public-host 192.168.178.143 --provider-voice
# → laesst alles wie zuvor und spielt die OpenAI-Stimme ab
```

Stimmen müssen vorliegen:

```bash
python3 -m piper.download_voices de_DE-thorsten-medium
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

Für einen bereits eingerichteten Host genügt typischerweise:

```bash
./scripts/install-host.sh --public-host 192.168.178.143
./scripts/apply-openclaw-voice-dist-patches.py
./scripts/verify-host.sh
python3 tests/test_local_tts.py
python3 tests/test_bridge_e2e.py
systemctl --user restart openclaw-gateway.service
```

## Bestätigte Firmware (Ist-Stand)

Aus dem Boot-Log und dem App-Deskriptor des Geräts gelesen:

| Merkmal | Wert |
|---|---|
| Project | `xiaozhi` |
| Version | `2.5.0` |
| Compile time | **Sep 27 2026 14:13:36** |
| ELF-SHA256 | `77fd991a4856c6bef3c0dd3aa08d3f3ef650b06c74bd23087bb4aad553c91362` |
| ESP-IDF | `v6.1` |
| Board | `esp32-s3-touch-lcd-4b` |
| Sprache | `de-DE` |

Dieser Stand enthält den **Wiederherstellungs-Watchdog** und das animierte,
board-spezifische OpenClaw-Gesicht. Der komplette Sprach-, Wakeword-, Netzwerk-
und Bridge-Pfad ist gegenüber dem vorherigen Watchdog-Build unverändert.

Verwechslungsgefahr: Es existieren weitere `xiaozhi 2.5.0`-Builds, u. a. der
vorherige bewährte Stand **23.09. 08:43** (ELF `b6bf3e74…`) und ein weiterer vom
**22.09. 15:25** (ELF `dc80cd8c…`). Weder Version noch Projektname unterscheiden
sie — nur Compile-Zeit und ELF-Hash.

## Wiederherstellungsanker

`firmware/prebuilt/` enthält den aktuellen, am Gerät bestätigten Stand:

| Datei | Inhalt | Schreibziel |
|---|---|---|
| `xiaozhi.bin` | Nur App-Partition (`ota_0`) des laufenden Watchdog-Builds | `0x20000` |
| `merged-binary.bin` | Vollständiges Image, **NVS auf `0xFF` geleert** | `0x0` (nur Rettungsfall) |
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
