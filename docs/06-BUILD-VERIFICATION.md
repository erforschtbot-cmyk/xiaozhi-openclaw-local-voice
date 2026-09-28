# 6. Nachgewiesener Neuaufbau

## Firmware-Build

Am 24.09.2026 wurde `scripts/build-firmware.sh` auf einem sauberen Checkout
vollständig ausgeführt.

- Upstream XiaoZhi: `4632dc51f0a5ad26e08542e131e6e48da41e4ff3`
- Upstream uart-uhci: `6ea5f576af84640209aa6b98e27be92b8ce418c5`
- ESP-IDF: `v6.1-803-g94639ab7251`
- 74 Komponenten wurden aufgelöst
- 2.237 Kompilierschritte erfolgreich
- Appgröße: `0x2c5aa0`, 30 % der kleinsten App-Partition frei
- zweiter sauberer Kontrollbuild ebenfalls erfolgreich
- erzeugte `dependencies.lock` ist im Repository hinterlegt

Die Binärdateien enthalten Buildmetadaten und sind deshalb nicht zwingend
byteidentisch zwischen zwei erfolgreichen Neubauten. Maßgeblich sind die gepinnten
Quellen, die Lockdatei, ein fehlerfreier Build und anschließend der Hardware-Test.

## Patch-Anwendung (am 26.09.2026 erneut geprüft)

Beide Firmware-Patches wurden gegen den gepinnten Upstream verifiziert:

```text
xiaozhi-esp32-openclaw.patch
  = byte-identisch zum Ist-Differenzbild des gepinnten Arbeitsbaums
  = wendet sauber auf pristine Checkout an (git apply --check: OK)

uart-uhci-idf61.patch
  = wendet sauber auf uart-uhci@6ea5f576 an
```

## Verifikation der laufenden Firmware (26.09.2026)

Der Zustand des Geräts wurde rein lesend festgestellt. Vor dem Flash war das der
Stand vom 23.09. (ELF `b6bf3e74…`); seit dem Flash läuft der neue Build:

| Quelle | Ergebnis |
|---|---|
| Boot-Log (seriell), nach Flash | `xiaozhi 2.5.0`, compile `Sep 26 2026 19:10:55`, ELF `2d817b23…` |
| App-Deskriptor `read-flash 0x20020`, nach Flash | identisch zum Boot-Log |
| Geräte-MAC | `94:a9:90:cc:8d:b4` |

Damit ist der laufende Stand eindeutig auf die Prebuilt-Images in
`firmware/prebuilt/` abbildbar:

| Datei | Prüfsumme (SHA-256) | Bezug |
|---|---|---|
| `xiaozhi.bin` | `9e8cdc856d57a5f5561842652096eef1a31039d62f59bde5424a41803bd6e739` | App-Partition `ota_0` des Watchdog-Builds |
| `merged-binary.bin` | `637227b6950449ee75b9a2fff16be5a319afe38c91eb46c5c3d22a3f5920d36d` | Volles Image, NVS geleert |
| `previous-no-watchdog/xiaozhi.bin` | `69672c442cdf5166f89e0bfc50e2d26854b0f669afac8c45e573f2b4de1f0fb8` | Vorheriger bewährter Stand `b6bf3e74…` |

Die Prebuilt-Dateien von vor dem 26.09. wurden aus dem Voll-Image vom 25.09.2026
(`full-flash-before-official-openclaw.bin`, SHA-256
`034024a8c7d720c0ffcbe8470f3526b646eb8e3c28013752a5380b1ecdaece05`) extrahiert:
Partition `ota_0` bei `0x20000`, Länge `0x3f0000`, Daten bis zum letzten
Nicht-`0xFF`-Byte getrimmt. Seit dem 26.09. stammen sie direkt aus dem Build.

## Geheimnisprüfung der veröffentlichten Images

Der komplette Flash-Speicher wurde partitionweise auf Zugangsdaten geprüft:

| Partition | Nicht-`FF`/`00`-Bytes | Zugangsdaten |
|---|---:|---|
| `nvs` | 1569 | **ja** (WLAN-SSID/Passwort im Klartext) |
| `otadata` | 6 | nein |
| `phy_init` | 0 | nein |
| `ota_0` (App) | 2532898 | nein |
| `ota_1` (leer) | 0 | nein |
| `assets` | 3623405 | nein |

Daraus folgt:

- `firmware/prebuilt/xiaozhi.bin` (nur `ota_0`) ist **frei** von Zugangsdaten.
- `firmware/prebuilt/merged-binary.bin` wurde mit **geleertem NVS**
  (`0x9000`–`0xF000` = `0xFF`) erzeugt, damit keine WLAN-Zugangsdaten
  mitveröffentlicht werden.

## Bridge-Skripte

Beide Skripte wurden am 26.09.2026 aus dem laufenden Stand übernommen und
byte-genau verifiziert:

| Datei | SHA-256 |
|---|---|
| `gateway/server.py` | `fe11766cb233d2d3687f548512abb77b095924088ba53f1603fe459c1ef3893e` |
| `gateway/openclaw-talk-realtime.mjs` | `430aa9205b5f275552c14e27aefbaa4f58990df892071bd173c3e9c55e1145c6` |

## OpenClaw-`dist`-Patches

Beide Patches wurden am 26.09.2026 als aktiv verifiziert:

| Marker | Datei |
|---|---|
| `voice-confirmation-disabled-by-owner-v1` | `dist/agent-tools.before-tool-call-*.mjs` |
| `voice-test-suppress-assistant-persist-v1` | `dist/handlers-*.mjs` |

`scripts/apply-openclaw-voice-dist-patches.py` ist idempotent geprüft (zweiter
Lauf meldet `already`, ohne eine Sicherung anzulegen).

## Neuaufbau mit Watchdog und Flash (26.09.2026)

Der Build wurde nach dem Watchdog-Patch erneut vollständig ausgeführt:

| Feld | Wert |
|---|---|
| Start / Ende | 19:07:38 / 19:13:58 UTC |
| Status | `succeeded`, `exit_code` 0 |
| Board / Sprache / Wakeword | `esp32-s3-touch-lcd-4b` / `de-DE` / `wn9_jarvis_tts` |
| ESP-IDF | `v6.1-803-g94639ab7251` |
| Compile time | `Sep 26 2026 19:10:55` |
| ELF-SHA256 | `2d817b23a349060d…` |

Artefakte des Builds:

| Datei | Größe | SHA-256 |
|---|---|---|
| `xiaozhi.bin` | 2.907.200 B | `9e8cdc856d57a5f5561842652096eef1a31039d62f59bde5424a41803bd6e739` |
| `merged-binary.bin` | 11.374.535 B | `637227b6950449ee75b9a2fff16be5a319afe38c91eb46c5c3d22a3f5920d36d` |

Der Watchdog ist im Binary nachweisbar:

```text
strings xiaozhi.bin | grep "Stuck watchdog"   -> 1 Treffer
```

## Flash und Betrieb (26.09.2026, 21:36)

Geschrieben wurde **nur die App-Partition** (`0x20000`). NVS, WLAN-Zugangsdaten und
Assets blieben erhalten, eine erneute Provisionierung war nicht nötig.

```text
Wrote 2907200 bytes (1713472 compressed) at 0x00020000 in 21.2 seconds
Verifying written data... Hash of data verified.
```

Nach dem Flash rein lesend vom Gerät bestätigt (`read-flash 0x20020`):

| Feld | Wert |
|---|---|
| project | `xiaozhi` |
| version | `2.5.0` |
| compile | `19:10:55 Sep 26 2026` |
| elf | `2d817b23a349060d` — identisch zum Buildartefakt |

Betriebsbeobachtung seither: Gerät bootet, verbindet sich mit dem LAN und der Bridge
und versteht Sprache. **Kein Fehler, kein Watchdog-Eingriff** bislang — der Watchdog
greift erst, wenn ein Gesprächszustand tatsächlich hängen bleibt.

## Rückwege

Drei unabhängige Kopien der vorherigen Firmware (`b6bf3e74…`, ohne Watchdog):

| Ort | Datei |
|---|---|
| Live-Sicherung vom Gerät | `~/.openclaw/backups/firmware-BEFORE-watchdog-flash-20260926/app-before-watchdog.bin` |
| Verifizierte Kopie | `~/.openclaw/backups/firmware-ACTIVE-xiaozhi-2.5.0-20260923/` |
| Repository | `firmware/prebuilt/` |

## Was ein Neuaufbau noch beweisen muss

Die neu gebauten Images sind ein **Buildnachweis**, aber nicht automatisch der
Recovery-Anker. Das Gerät läuft mit den unter `firmware/prebuilt/` abgelegten,
praktisch bestätigten Images. Ein neu gebautes Image wird erst nach dem vollständigen
Hardware-Testplan (`04-TESTPLAN.md`) zum neuen Recovery-Anker.

## Finale deutsche Performance-Baseline (27.09.2026)

Der aktuelle Patch wurde mit dem gepinnten Image `espressif/idf:v6.1`
(Digest `sha256:81893c71bb5e570088901f21def8684c25cd2a9020281bd01b843a7655edb18c`)
vollständig gebaut. Der Build endete mit 2182/2182 Schritten erfolgreich.

| Feld | Wert |
|---|---|
| Sprache | `de-DE` (`CONFIG_LANGUAGE_DE_DE=y`) |
| App-Größe | 2.906.160 B, 30 % Partition frei |
| App-SHA256 | `4325094cd59b6dc7c5e615e7236ab3318aa69ed39d698972d9e23337a138010e` |
| ELF-SHA256 | `77fd991a4856c6bef3c0dd3aa08d3f3ef650b06c74bd23087bb4aad553c91362` |
| Vollimage-SHA256 | `9799d8533f419a745b1062dfbbf546a394945bcbd67ba5feda4961c207eab662` |

Geflasht wurde ausschließlich die App-Partition bei `0x20000`. Der anschließende
Read-back über exakt 2.906.160 Bytes war byteidentisch (`cmp=0`, gleicher SHA256).
Der Owner bestätigte anschließend Funktion und keine bemerkbare Verzögerung.
