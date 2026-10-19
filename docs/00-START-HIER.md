# Von leerem USB-Gerät zu OpenClaw — Einstieg und Audit

Stand: 20.10.2026. **Noch kein vollständig abgenommener Nullstart.**
Diese Seite ersetzt frühere pauschale Aussagen „alles vollständig nachbaubar“.
Dateien vorhanden, erfolgreich kompilierbar und auf leerer Hardware getestet sind
unterschiedliche Prüfstufen. Historische Buildberichte sind keine heutige Abnahme.

## Was entspricht hier einer Arduino-`.ino`?

Das Projekt verwendet **ESP-IDF/C++**, nicht Arduino. Die vollständige Firmware
entsteht aus dem exakt gepinnten XiaoZhi-Quellbaum plus dem Board-Patch.
Der Clone ist absichtlich nicht als zweite vollständige Kopie im Repo enthalten.
Die Buildskripte laden ihn, wenden den Patch an und starten die ESP-IDF-Toolchain.
Nach dem Vorbereiten liegen dort `CMakeLists.txt`, `main/main.cc`,
`main/application.cc` und `main/boards/waveshare/<board>/`.
`firmware/audio-board/board/jarvis_ring_led.cc` allein ist **nicht** die Firmware.
Ein `.bin` ist das fertige Flash-Artefakt, nicht der bearbeitbare Quellcode.

| Bestandteil | Aufgabe | Quelle / Aufbau |
|---|---|---|
| 4B / Smart-86-Box | Mikrofon, Wakeword, Lautsprecher, Gesicht | `scripts/build-firmware.sh`, `firmware/patches/xiaozhi-esp32-openclaw.patch` |
| Audio-Board / AI Smart Speaker | Mikrofone, Lautsprecher, Ring, Tasten | `scripts/build-firmware-audio-board.sh`, `firmware/patches/jarvis-audio-board-ring.patch` |
| OTA-Konfigurationsserver | verrät dem Gerät seine WebSocket-Adresse | Bestandteil von `gateway/server.py`, TCP 8766 |
| Audio-Bridge | übersetzt XiaoZhi/Opus zum OpenClaw-Talk-Pfad | `gateway/server.py`, TCP 8765 |
| Gateway-Helfer | authentifizierte Talk-Verbindung | `gateway/openclaw-talk-realtime.mjs` |
| OpenClaw-Gateway | Talk/Provider und Agent/Werkzeuge | separates npm-Paket `openclaw@2026.10.4`, TLS TCP 18789 |
| Raum-/Gesprächsbindung | verhindert Zuordnung zum falschen Gerät | `room-map.json`, `voice-turn-context.mjs`, `scoped-smart-home-router.mjs` |
| Agent-Werkzeug-Plugin | Alexa-Aufrufe des Agenten | `plugins/iobroker-alexa/`, TypeScript, JS, Tests und Lockdatei |
| Smart-Home-Steuerung | erlaubte Einzelbefehle an ioBroker/Alexa | `smart-home-router.mjs`, `room-command.mjs`, Katalogskript und Timer |

```text
USB: nur Bauen/Flashen/Diagnose
Gerät --WLAN--> OTA :8766 --> WebSocket-Audio :8765
      --> Python-Bridge --> Node-Helfer --> OpenClaw WSS :18789
      --> Realtime-Sprache / Agent / Werkzeuge / ioBroker
```

Das ist ein **XiaoZhi-Bridge-Projekt**, kein automatisch gepairter offizieller
OpenClaw-ESP-Node. „OTA-Gateway“ meint hier den Konfigurationsserver in der Bridge.

## Reihenfolge für einen fremden Agenten

1. Hardware anhand Platine/Display/Ring bestimmen. USB-Chip-ID ESP32-S3 allein
   unterscheidet diese beiden Boards nicht. Port und 16-MB-Flash prüfen.
2. Linux-Host mit Node/npm, Python/venv/libopus, Git und Docker vorbereiten.
   Die Referenz erwartet Benutzer `/home/openclaw` und npm unter
   `/home/openclaw/.npm-global`; die Pfade sind derzeit nicht portabel.
3. OpenClaw **2026.10.4** installieren, Gateway konfigurieren, Provider anmelden
   und das Talk-/Consult-Sessionziel `agent:voice:…` prüfen. **Die bisherige Host-Anleitung automatisiert
   diesen Nullstart nicht vollständig.** Sie setzt ein funktionierendes Gateway voraus.
4. TLS und Token bereitstellen: der Helfer erwartet `wss://127.0.0.1:18789`,
   `%h/.openclaw/ssl/gateway.crt` sowie `gateway.authToken` in der lokalen
   `%h/.openclaw/secrets.json`. Token und Gateway-Authentifizierung müssen
   übereinstimmen. Keine Referenz-Secrets kopieren oder auf GitHub hochladen.
   Zertifikatserzeugung, Gateway-Konfigurationsvorlage, Agent-/Provider-Einrichtung
   und sichere Tokenübernahme fehlen noch als ein zusammenhängender Bootstrap.
5. Bridge-Dateien installieren; **vor einem Gateway-Neustart** müssen die Module
   unter `/home/openclaw/.local/share/jarvis-realtime-bridge` liegen.
6. Exakte Gateway-Baseline wiederherstellen (unten). Gateway-Start/Neustart
   führt der Betreiber aus; kein Skript dieser neuen Wiederherstellung tut das.
7. Eigene Netzadresse einsetzen: Firmware-OTA-URL und `--public-host` müssen
   vom Geräte-WLAN erreichbar sein. Referenz-IP-Adressen nicht blind übernehmen.
8. Geräte-Firmware bauen und das richtige Image flashen (unten).
9. WLAN über den Einrichtungs-AP `Xiaozhi-…` provisionieren; Adresse/Anweisungen
   aus serieller Ausgabe bzw. Gerätedisplay verwenden. Gespeicherte NVS-OTA-URL
   hat Vorrang vor dem einkompilierten Wert. Eine fremde NVS nicht übernehmen.
10. Eigene MAC-Adressen in `room-map.json` den Räumen zuordnen. Für Smart Home
    ioBroker-Adresse, Echo-Datenpunkt und erlaubte Geräte anpassen. Referenz:
    `gateway/smart-home-router.mjs` (ENDPOINT), `refresh-alexa-catalog.mjs` (base).
    Katalog erzeugen und Timer aus `systemd/jarvis-alexa-catalog.*` installieren;
    der Cloud-Installer liefert die Units jetzt mit, aktiviert sie jedoch erst
    nach eigener Konfiguration über `systemctl --user enable --now jarvis-alexa-catalog.timer`.
11. `docs/04-TESTPLAN.md` real durchführen: Boot, WLAN, Wakeword, lange Äußerung,
    hörbare Antwort, Rückkehr zum Zuhören, Transkript, Werkzeug, zwei Räume.
    Syntax, Prüfsummen oder offene Ports ersetzen diesen Test nicht.

## Exakter Gateway-Wiederaufbau — neues geprüftes Skript

Die alten Apply-Skripte waren nicht deckungsgleich mit dem Live-Code: falscher
Routerimport, fehlende Gesprächsbindung und ältere Persistenzlogik.
Die neue Baseline enthält Diffs **gegen das originale npm-Paket** und SHA-256
für Vorher/Nachher. Beide Dateien wurden isoliert rekonstruiert, mit Node auf
Syntax geprüft und bytegleich mit dem Referenzhost verglichen.

```bash
# Nur lesen/prüfen:
python3 scripts/restore-openclaw-baseline.py   --openclaw-root "$(npm root -g)/openclaw"
# Erst auf der neu aufzubauenden Installation schreiben:
python3 scripts/restore-openclaw-baseline.py   --openclaw-root "$(npm root -g)/openclaw" --apply
```

Es prüft alle Ausgangsdateien vor dem Schreiben, legt Backups an und verweigert
unbekannte/teilgepatchte Dateien oder andere Versionen. Kein Dienstneustart.
Enthalten: Sprachbestätigung aus (Referenzverhalten), Persistenz, Schnellweg,
Direktabschluss **und** Raum-/Turn-Bindung. C/D sind nicht enthalten.
Der Import von `voice-turn-context.mjs` ist **statisch**: fehlt das Modul, kann
bereits das Laden des Handlers scheitern! Nur der dynamische Routerimport besitzt
Fallback. Die frühere pauschale Aussage „fehlende Module sind harmlos“ war falsch.
Nach einem OpenClaw-Upgrade diese versionsgebundene Baseline nicht blind anwenden.

## Leeres Gerät ist nicht dasselbe wie Firmware-Update

**App-only bei `0x20000` reicht auf leerem Flash nicht.** Bootloader,
Partitionstabelle und Assets/Wakeword-Daten fehlen dann weiterhin.

| Gerät | Bereits eingerichtet | Wirklich leer |
|---|---|---|
| 4B | geprüftes App-Image `firmware/prebuilt/xiaozhi.bin` | vollständiges boardpassendes Image, z. B. `firmware/prebuilt/merged-binary.bin`; Hostadresse/NVS beachten |
| Audio-Board | `firmware/prebuilt/audio-board/xiaozhi.bin` | **kein Audio-Board-Vollimage im Repo**; mit dessen Buildskript `build/firmware/merged-binary.bin` erzeugen |

Build-Aufrufe (jeweils eigener frischer Repo-Clone, da alte Skripte denselben
`source/`- und `build/`-Ordner verwenden und den Quellbaum löschen):

```bash
# 4B:
bash scripts/build-firmware.sh --public-host HOST_IM_GERAETENETZ
# ODER Audio-Board, nicht beide im selben Arbeitsbaum gleichzeitig:
bash scripts/build-firmware-audio-board.sh HOST_IM_GERAETENETZ
```

Beide Patches sind am 20.10. auf frischem Upstream-Commit `4632dc51…` anwendbar.
Das bisher fehlende Audio-Board-Lockfile wurde beim erfolgreichen frischen Build
gesichert und wird nun vom Buildskript aus
`firmware/audio-board/dependencies.lock` übernommen.

Nach erfolgreichem Build Manifest/Board/Checksummen kontrollieren. Zum Flashen
`esptool==5.4.0` in separatem venv installieren. Nur am identifizierten **leeren**
16-MB-ESP32-S3, mit dem **zum Board gehörenden** Vollimage:

```bash
python3 -m venv .venv-esptool
.venv-esptool/bin/pip install esptool==5.4.0
.venv-esptool/bin/esptool --chip esp32s3 --port /dev/ttyACM0 flash-id
.venv-esptool/bin/esptool --chip esp32s3 --port /dev/ttyACM0   write-flash 0x0 build/firmware/merged-binary.bin
.venv-esptool/bin/esptool --chip esp32s3 --port /dev/ttyACM0   verify-flash 0x0 build/firmware/merged-binary.bin
```

Port ist ein Beispiel, nicht eine Geräteerkennung. Bei vorhandenen Daten zuerst
Flash/NVS sichern (enthält Geheimnisse und ggf. individuelle Kalibrierung).
Vollimage überschreibt diese Daten. Das 4B-Vollimage **nicht** für das Audio-Board
nehmen. Diese Prüfung hat kein USB-Gerät geflasht und keinen neuen Hardware-
End-to-End-Test durchgeführt.

## Prüfergebnisse und verbleibende Lücken

- GitHub-Remote vor Audit: Cloud `043f446`, Lokal `25c9bd3`, jeweils identisch zu HEAD.
- Bestehende Prüfsummen: Cloud 77/77, Lokal 75/75 korrekt.
- Gemeinsame Firmware-Dateien beider Repos: bytegleich.
- Cloud: 28 vorhandene Bridge-Dateien bytegleich zum Live-Verzeichnis;
  kein dort vorhandenes Python-/MJS-Modul fehlt im Cloud-Repo.
- Lokal: vier abweichende Dateien und elf fehlende Live-Python-/MJS-Dateien
  (einschließlich Tests). Das ist **kein** aktuelles Spiegelbild des Cloud-Hosts.
- JavaScript-Routertests Cloud: 145 bestanden, 0 fehlgeschlagen.
- Python-Tests Cloud mit Bridge-venv: 45 ausgeführt, 8 Fehler und 1 Fehlschlag;
  u. a. referenziert `test_conversation_timing.py` eine nicht vorhandene Methode
  `note_followup_audio`. Kein Beleg für einen kaputten laufenden Dienst,
  aber auch keine grüne Testbasis für einen Neubau.
- Behoben im Cloud-Installer: Routertests und Katalog-Units werden jetzt
  mitgeliefert; bestehende Raumzuordnungen bleiben erhalten, falsche Installations-
  pfade werden abgewiesen. Die lokale Variante ist damit noch nicht angeglichen.
- Host-Bootstrap, portable Konfiguration und beide Firmware-/Hardware-Abnahmen
  sind noch nicht vollständig nachgewiesen. **Keine pauschale Vollständigkeitszusage.**

## Zusätzlich gefunden: Agent-Werkzeug-Plugin

Das aktive `iobroker-alexa`-Plugin fehlte bisher in beiden Repos. Es ist jetzt
unter `plugins/iobroker-alexa/` einschließlich Bau- und Einbauanleitung enthalten.
Der Bridge-Installer installiert es nicht automatisch.

Plugin-Nachweis: `npm ci`, TypeScript-Build und 10/10 Tests erfolgreich am
20.10.2026 im separaten Repository-Verzeichnis; kein Live-Plugin geändert.

## Lesender Host-Check

`python3 scripts/check-new-host.py` prüft Programme, Referenzpfade, TLS-Datei,
Tokenvorhandensein (ohne Ausgabe des Werts), Bridge-Module und Dienststatus.
Exit 1 benennt fehlende Voraussetzungen. Es installiert nichts, schaltet nichts
und ersetzt keinen Audio-/Provider-Test. Das `agent:voice:`-Sessionpräfix darf
nicht ohne Prüfung mit einem statischen Agent-Eintrag gleichgesetzt werden.

## Frischer Audio-Board-Komplettbuild, 20.10.2026

Erfolgreich (Exit 0): frischer Upstream-Checkout plus veröffentlichter Board-Patch,
ESP-IDF v6.1, Deutsch und `wn9_jarvis_tts`. App und Vollimage einschließlich
Bootloader, Partitionstabelle und Assets erzeugt. Nachweis mit Größen und SHA-256:
`firmware/audio-board/build-proof-20261020.json`. Generierte Konfiguration bestätigt
Audio-Board und Deutsch. Die Abhängigkeitsauflösung des Builds liegt jetzt separat
für das Audio-Board vor. **Kein Flash-/Audio-Test dieses neuen Images.** Der bisher
bestätigte Prebuilt-Anker wurde nicht ersetzt. Der 4B-Vollbuild wurde in diesem
Audit nicht wiederholt (dessen Patch-Anwendung wurde geprüft).
