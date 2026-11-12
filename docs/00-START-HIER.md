# Von leerem USB-Gerät zu OpenClaw — Einstieg und Audit

Wenn eine KI diesen Aufbau ohne Vorwissen übernimmt, liest sie zusätzlich
[`14-KI-NEUAUFBAU-CHECKLISTE.md`](14-KI-NEUAUFBAU-CHECKLISTE.md). Dort stehen
die Rückfragen, die nicht aus Code ableitbar sind, und die vollständige Kette
von beiden Firmwarezielen bis ioBroker, STT und TTS.

Stand: 09.11.2026. **Die lokale Sprachkette (Canary + Piper) ist am Gerät
abgenommen.** Der Host-Aufbau ist durch `scripts/install-host.sh` abgedeckt und
prüft sich selbst; der Gateway-Patchsatz liegt für 2026.10.4 bis 2026.11.2 vor.
Der verbindliche Dateibestand steht in
[`00-DATEIBESTAND.md`](00-DATEIBESTAND.md).

> **Netzadressen nicht kopieren:** `192.168.178.143` und
> `192.168.178.24` beschreiben ausschließlich den dokumentierten
> Referenzaufbau. Auf einem anderen Host sind sie keine auffindbaren Dienste.
> Vor Installation oder Firmware-Bau die Zuordnung und die echten
> Betreiberwerte in [`18-ANDERE-HOSTS.md`](18-ANDERE-HOSTS.md) klären.

Für einen Nachbau ohne Vorwissen gilt: **`docs/01-HOST-INSTALLATION.md` in der
Reihenfolge abarbeiten, nicht querlesen.** Dort steht nach jedem Schritt, was
hätte passieren müssen. Diese Seite hier erklärt den Aufbau und die
Prüfstufen; sie ersetzt die Schritt-für-Schritt-Anleitung nicht.

Dateien vorhanden, erfolgreich kompilierbar und auf leerer Hardware getestet
sind unterschiedliche Prüfstufen. Historische Buildberichte sind keine heutige
Abnahme. Was am 23.10. tatsächlich belegt wurde, steht unter
„Prüfergebnisse“ am Ende dieser Seite.

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
| OpenClaw-Gateway | Talk/Provider und Agent/Werkzeuge | separates npm-Paket `openclaw@2026.11.2`, TLS TCP 18789 |
| Lokaler Smart-Home-Resolver | eindeutige Befehle ohne Agentenlauf | `local-smarthome/`, ioBroker Simple API |
| Geräteverwaltung im Browser | Namen, Räume, Gruppen, Satz-Test unter `:8088` | `local-smarthome/panel.py`, `systemd/geraeteverwaltung.service.in`, `docs/16-WEBOBERFLAECHE.md` |
| Diagnose lokaler Schaltweg | „nicht nutzbar“ vs. 8088/8770, Dry-Run, Logzeilen | `docs/17-SCHNELLWEG-DIAGNOSE.md` |
| Andere Hosts / Container | konfigurierbare Pfade, Gateway-URL, ioBroker-Endpunkt | `docs/18-ANDERE-HOSTS.md` |
| Raum-/Gesprächsbindung | verhindert Zuordnung zum falschen Gerät | `room-map.json`, `voice-turn-context.mjs`, `scoped-smart-home-router.mjs` |
| Foto per Sprache | Kamera auslösen, Bild an den Agenten, Antwort sprechen | `gateway/photo-consult.mjs`, `POST /photo` in `server.py`; `docs/13-FOTO-PER-SPRACHE.md` |
| TFT-Karten/Bilder | Wetter, Textkarte und Bildsuche als RGB565 | `screen/`, `openclaw/skills/display-cards/`, `docs/15-ANZEIGEN.md` |
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
3. OpenClaw **2026.11.2** installieren, Gateway konfigurieren, Provider anmelden
   und das Talk-/Consult-Sessionziel `agent:voice:…` prüfen. **Die Host-Anleitung
   automatisiert diesen Teil nicht** — Schritt 3 bleibt Handarbeit, ist aber in
   `docs/01-HOST-INSTALLATION.md` mit Prüfpunkt beschrieben.
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
   Der Wert ist absichtlich Pflicht; eine KI darf hier weder
   `192.168.178.143` noch die zuerst gefundene Host-IP erraten.
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
11. Beim 4B zusätzlich `docs/15-ANZEIGEN.md` prüfen: Wetter für den korrekten
    Ort, Suchbild, Rückkehr zum Gesicht bei Bereitschaft und ein Gerät ohne TFT.
12. `docs/04-TESTPLAN.md` real durchführen: Boot, WLAN, Wakeword, lange Äußerung,
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
| 4B | geprüftes App-Image `firmware/prebuilt/xiaozhi.bin` (inkl. TFT-Anzeige) | `firmware/prebuilt/merged-binary.bin`; erfolgreich gebaut, nicht auf dem eingerichteten Referenzgerät vollgeflasht; Hostadresse/NVS beachten |
| Audio-Board | `firmware/prebuilt/audio-board/xiaozhi.bin` | `firmware/prebuilt/audio-board/merged-binary.bin`; App am 09.11. vom Gerät zurückgelesen und bytegleich, Vollimage aus demselben Build |

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

## Prüfergebnisse und unvermeidbare Betreiberangaben

- Referenzhost und Repo enthalten bytegleichen Bridge- und Resolver-Quellcode.
- OpenClaw 2026.11.2: Handler exakt zum veröffentlichten Manifest-Hash;
  Scope-Prüfung sowie 145/145 Gateway-Routertests grün.
- Lokaler Resolver: 34/34 Offline-Fälle grün.
- Die veralteten Gesprächs-Timing- und TTS-Testattrappen sind auf den aktuellen
  ereignisgesteuerten Stand nachgezogen und grün.
- Isolierter Installerlauf unter einem leeren temporären HOME: Importkette,
  Units, Bridge, Anzeige und Resolver vollständig erzeugt.
- Die Weboberfläche antwortet über LAN auf `http://<HOST>:8088/`; Port,
  ioBroker-Basis und Raumanzahl werden in der Seite angezeigt.
- Audio-Board `90:70:69:eb:1c:0c`: 16-MB-Flash vollständig lokal gesichert;
  veröffentlichte App (2.866.736 Byte) bei `0x20000` zurückgelesen und
  bytegleich. Der übrige App-Slot war vollständig `0xFF`.
- Fertige Vollimages liegen für 4B und Audio-Board vor. Ihre NVS-Bereiche sind
  vollständig `0xFF`; WLAN-Daten und individuelle Kalibrierung werden nicht
  veröffentlicht.

Nicht automatisierbar sind nur fremde Zugangsdaten und Entscheidungen: WLAN,
OpenClaw-Provider/Token, konkrete ioBroker-/Echo-Adresse, Geräte-IDs und Räume.
Der fremde Agent muss diese Werte vom Betreiber erfragen; sie dürfen nicht aus
dem Referenzhost übernommen oder in GitHub eingecheckt werden. Ein physischer
Hör-/Mikrofon-/Raumtest bleibt nach der Installation Pflicht.

## Zusätzlich gefunden: Agent-Werkzeug-Plugin

Das aktive `iobroker-alexa`-Plugin fehlte bisher in beiden Repos. Es ist jetzt
unter `plugins/iobroker-alexa/` einschließlich Bau- und Einbauanleitung enthalten.
Mit `--iobroker-alexa-endpoint` installiert der Bridge-Installer es automatisch;
alternativ steht `scripts/install-iobroker-plugin.sh` einzeln bereit.

Plugin-Nachweis: `npm ci`, TypeScript-Build und 10/10 Tests erfolgreich am
09.11.2026; Laufzeit-Abhängigkeiten (`npm audit --omit=dev`) ohne bekannte
Schwachstelle. Der nötige Gateway-Neustart bleibt Betreiberaufgabe.

## Lesender Host-Check

`python3 scripts/check-new-host.py` prüft Programme, Referenzpfade, TLS-Datei,
Tokenvorhandensein (ohne Ausgabe des Werts), Bridge-Module und Dienststatus.
Exit 1 benennt fehlende Voraussetzungen. Es installiert nichts, schaltet nichts
und ersetzt keinen Audio-/Provider-Test. Das `agent:voice:`-Sessionpräfix darf
nicht ohne Prüfung mit einem statischen Agent-Eintrag gleichgesetzt werden.

## Audio-Board-Komplettbuild und Hardware-Anker, 09.11.2026

Gepinnter Upstream, ESP-IDF v6.1, Deutsch, `wn9_jarvis_tts` und die eigene
Audio-Board-Lockdatei. Das Buildskript entfernt den Upstream-Ausschluss der
`dependencies.lock` aus `.dockerignore`; ohne diesen Schritt würde der Container
trotz vorhandener Lockdatei tagesaktuell neu auflösen. App und Vollimage
einschließlich Bootloader, Partitionstabelle und Assets liegen fertig unter
`firmware/prebuilt/audio-board/`. Die App ist der bytegleich zurückgelesene
Hardware-Anker; ein Vollimage ist nur für leeren/defekten Flash gedacht und
erfordert danach WLAN-Provisionierung beziehungsweise das Zurückspielen der
vorher gesicherten individuellen NVS.
