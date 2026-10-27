# 2. Firmware bauen und flashen

## Hardware

- Waveshare ESP32-S3-Touch-LCD-4B
- Ziel: `esp32-s3-touch-lcd-4b`
- Sprache: `de-DE`
- Wakeword-Modell: `wn9_jarvis_tts`

Partitionen des bestätigten Geräts:

| Partition | Offset | Größe |
|---|---:|---:|
| NVS | `0x9000` | 16 KiB |
| OTA data | `0xd000` | 8 KiB |
| PHY init | `0xf000` | 4 KiB |
| App OTA 0 | `0x20000` | 4032 KiB |
| App OTA 1 | `0x410000` | 4032 KiB |
| Assets | `0x800000` | 8 MiB |

## Empfohlener Wiederherstellungsweg

Der sichere Standard schreibt nur die App-Partition. WLAN/NVS und Assets bleiben
erhalten:

```bash
./scripts/flash-firmware.sh --port /dev/ttyACM0 --board 4b
```

**Das `--board` ist Pflicht, und zwar aus gutem Grund.** In diesem Projekt liegen
zwei verschiedene Firmware-Stände: das **Touch-LCD-4B** und das
**ESP32-S3-AUDIO-Board**. Beide sind ESP32-S3 mit 16 MB Flash — **der Chip
verrät das Modell nicht**. Wer das falsche Image schreibt, bekommt ein Gerät,
das bootet, aber stumm bleibt (oder gar nicht mehr startet).

Deshalb:

- Das Skript ordnet das Image über seine eingebettete Board-Kennung zu und
  **bricht ab, bevor ein Byte geschrieben wird**, wenn Image und `--board`
  nicht zusammenpassen.
- `--board auto` wird bewusst **abgelehnt**: ohne Boot-Log ist die Zuordnung
  nicht eindeutig.
- Prüfsumme aus `CHECKSUMS.sha256` und die App-Partitionsgröße
  (4032 KiB) werden vor dem Schreiben geprüft, nach dem Schreiben mit
  `verify-flash` gegengeprüft.

### Board bestimmen

Wenn unklar ist, welches Board hängt:

```bash
./scripts/flash-firmware.sh --port /dev/ttyACM0            # zeigt die Anleitung
python3 scripts/read-device-info.py --port /dev/ttyACM0    # Chip, Flash, MAC
```

Oder das Boot-Log lesen (115200 Baud) — die Zeile ist eindeutig:

```text
I (208) Board: UUID=… SKU=esp32-s3-audio-board      -> --board audio
I (208) Board: UUID=… SKU=esp32-s3-touch-lcd-4b     -> --board 4b
```

Wenn `/dev/ttyACM0` verschwindet: USB einmal abziehen/einstecken und erneut prüfen.
Der Port gehört auf den meisten Systemen `root:uucp`; ohne `uucp`-Gruppe braucht
das Lesen/Schreiben `sudo`.

## Vollständiges Rettungsimage

Nur bei leerem oder vollständig beschädigtem Flash:

```bash
./scripts/flash-firmware.sh --port /dev/ttyACM0 --board 4b --full
```

Dieses Image wurde mit **geleertem NVS** (`0x9000`–`0xF000` auf `0xFF`) abgelegt,
enthält also keine WLAN-Zugangsdaten des Referenzgeräts. Nach dem Flashen ist eine
erneute WLAN-Provisionierung erforderlich.

## Aus Quellen bauen

Der zusätzliche 4B-Patch
`firmware/patches/jarvis-4b-restore-brightness.patch` stellt beim Booten den
in NVS gespeicherten Helligkeitswert wieder her. Ohne ihn überschreibt der
Upstream-Konstruktor jeden gespeicherten Wert mit 100 %. Eine per MCP auf 5 %
gesetzte Helligkeit bleibt damit auch nach einem Neustart erhalten.

```bash
./scripts/build-firmware.sh --public-host 192.168.178.143
```

Der Build klont exakt die Pins aus `VERSIONS.md`, wendet beide Patchdateien an,
verwendet ESP-IDF 6.1 im Docker-Container und legt die Artefakte unter `build/` ab.

Die Firmware-Patches enthalten:

- 24/7: kein Display-/CPU-Schlaf, kein PMIC-Poweroff;
- WLAN: gespeicherte Netze endlos erneut versuchen, statt automatisch in
  Provisionierung zu wechseln;
- eindeutiges physisches Wiedergabeende (`playback_drained`);
- Gerätezustände über das XiaoZhi-Protokoll;
- `stream_end`, damit „Sprechen" erst nach geleertem Lautsprecher endet;
- **Wiederherstellungs-Watchdog**, der hängende Gesprächszustände selbstständig
  nach Bereitschaft zurückführt (siehe `docs/02-FIRMWARE.md`);
- board-spezifisches animiertes OpenClaw-Gesicht mit Blinzeln, Blickbewegung,
  Atmung, Emotionsfarben und animierten Mundbalken beim Sprechen;
- Render-Cache für unveränderte Farben, Rotation und Geometrie, entfernte tote
  Lid-Objekte sowie LVGL auf Kern 1 mit 5-ms-Zeitbasis; Zustände und sichtbare
  50-ms-Gesichtsanimation bleiben unverändert;
- IDF-6.1-Kompatibilität für `uart-uhci`;
- lokales OTA-/WebSocket-Ziel.

## Animiertes Gesicht ohne Architekturwechsel

Für das Waveshare-4B wird statt `RgbLcdDisplay` die davon abgeleitete Klasse
`JarvisFaceDisplay` instanziiert. Sie ersetzt ausschließlich die zentrale
Emoji-Darstellung durch LVGL-Objekte. Statusleiste, Untertitel, Touch, Helligkeit
und alle Gerätefunktionen bleiben die vorhandenen XiaoZhi-Komponenten.

Die Anzeige reagiert auf die bereits vorhandenen Aufrufe `SetStatus()` und
`SetEmotion()`:

- Bereitschaft: ruhiges Atmen, zufällige Blickbewegungen und Blinzeln;
- Zuhören: größere, aufmerksame Augen;
- Verbinden/Denken: violette, nachdenkliche Darstellung;
- Sprechen: drei animierte Mundbalken;
- Emotionen wie `happy`, `excited`, `sleepy` und `sad`: weiche Übergänge von
  Farbe, Augenform und Mundkurve.

Es wurden dafür keine Audio-, Wakeword-, Protokoll-, WLAN-, OTA- oder
Bridge-Funktionen verändert. Der Code liegt im Firmware-Patch als
`main/boards/waveshare/esp32-s3-touch-lcd-4b/jarvis_face_display.{h,cc}`.

## Bekannte Timer der Firmware (Ist-Stand)

Diese Werte sind aus dem gepinnten Quellbaum gelesen und erklären Hängeverhalten:

| Timer/Wert | Ort | Bedeutung |
|---|---|---|
| `kTimeoutSeconds = 120` | `main/protocols/protocol.cc` | Kanal gilt als tot, wenn 120 s nichts eingeht |
| Server-Hello 10 s | `main/protocols/websocket_protocol.cc` | Kein Server-Hello binnen 10 s → `SERVER_TIMEOUT` |
| Clock-Tick 1 s | `main/application.cc` | Statusleiste, Heap-Log **und der Wiederherstellungs-Watchdog** |
| Watchdog speaking 60 s | `HandleStuckWatchdog` | Kein Serveraudio seit 60 s im Zustand „Sprechen“ → Bereitschaft |
| Watchdog listening 120 s | `HandleStuckWatchdog` | Keine Serveraktivität seit 120 s im Zustand „Zuhören“ → Bereitschaft |
| Watchdog connecting 15 s | `HandleStuckWatchdog` | Audio-Kanal öffnet nicht binnen 15 s → Bereitschaft |

Der Upstream hat **keinen** Watchdog und keinen Keepalive. Ohne den Patch bleibt ein
hängender Zustand stehen, bis ein Socket-Abbruch von außen kommt. Mit dem Patch
kehrt das Gerät selbstständig nach Bereitschaft zurück.

### Wie der Watchdog arbeitet

`Protocol` merkt sich bei **jedem** eingehenden Paket (JSON *und* Audio) einen
Zeitstempel. Der Watchdog läuft im vorhandenen 1-Sekunden-Takt und wertet diesen
Zeitstempel aus:

- **Zustand „Sprechen“:** Antwortet der Server 60 s nicht, wird das Sprechen
  abgebrochen (`AbortSpeaking`) und der Kanal geschlossen → Bereitschaft.
- **Zustand „Zuhören“:** Seit 120 s keine Serveraktivität → Kanal schließen →
  Bereitschaft. Ein laufender Werkzeug-Consult streamt zwar nichts, hält die
  Verbindung aber offen; 120 s sind bewusst großzügig gewählt.
- **Zustand „Verbinden“:** Öffnet der Audio-Kanal nicht binnen 15 s → Bereitschaft.

Nicht angefasst werden `idle`, `wifi_configuring`, `activating`, `upgrading`,
`notifying` und `fatal_error`. Der Watchdog kann also keine Provisionierung oder
Oberflächen stören.

Jeder Eingriff wird geloggt:

```text
W (...) Application: Stuck watchdog: state=speaking in_state=61s idle=60s (no server audio while speaking) -> idle
```

**Schwellen anpassen:** Die Werte stehen als `constexpr` in
`Application::HandleStuckWatchdog` (`kSpeakingIdleTimeoutS`,
`kListeningIdleTimeoutS`, `kConnectingTimeoutS`). Nach jeder Anpassung den
vollständigen Testplan durchlaufen.

## Warum das Prebuilt-Image erhalten bleibt

Der bestätigte funktionierende Komplettstand wurde aus einer funktionierenden
Basis aufgebaut. Frühere vollständige Neubauten konnten Mikrofon/Audio verlieren.
Darum ist das verifizierte Image der Recovery-Anker; neue Builds müssen erst den
vollständigen Testplan bestehen.

Deshalb gilt bei **jeder** Firmware-Änderung:

1. Vorher den Ist-Stand vom Gerät sichern
   (`scripts/backup-device-firmware.sh`) — der Voll-Dump ist der Rückweg.
2. Das bisherige `firmware/prebuilt/xiaozhi.bin` nach
   `firmware/prebuilt/previous/` verschieben, bevor es ersetzt wird.
3. Im Patch `--check` prüfen, ob er gegen die gepinnte Revision sauber
   anwendbar ist, **bevor** gebaut wird.
4. Nach dem Flashen den App-Deskriptor zurücklesen und mit dem erwarteten
   ELF-Hash vergleichen (`scripts/read-device-info.py`).

Der Wachwort-Eingriff vom 2026-09-28 ist ein Beispiel: Der Vorgängerstand liegt
unter `firmware/prebuilt/previous/`, und der neue Hash ist in `VERSIONS.md`
vermerkt. Details zur Änderung und ihrer Risikoabgrenzung:
[`docs/09-FIRMWARE-WACHWORT.md`](09-FIRMWARE-WACHWORT.md).
