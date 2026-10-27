# 14. KI-Neuaufbau: Entscheidungen, Rückfragen und vollständige Kette

Dieses Kapitel ist für eine KI gedacht, die **nur den GitHub-Link** erhält.
Sie soll nicht raten, sondern den Aufbau schrittweise rekonstruieren und den
Besitzer an den Stellen fragen, an denen Haus, Hardware oder Zugangsdaten
nicht aus dem Repository ableitbar sind.

## Grundregel für die ausführende KI

1. Erst vorhandene Hardware, Host, Netzwerk und ioBroker inventarisieren.
2. Keine Geräteadresse, Raumzuordnung oder Gruppenlogik erraten.
3. Bei Mehrdeutigkeit eine konkrete Frage mit den gefundenen Kandidaten stellen.
4. Keine Lampe, Steckdose, Heizung, Pumpe oder Tür testweise schalten, bevor der
   Besitzer ein ungefährliches Testgerät und den gewünschten Endzustand nennt.
5. Geheimnisse nur lokal einrichten; niemals Tokens, WLAN-Daten, NVS-Dumps oder
   Zertifikatsschlüssel nach GitHub schreiben.
6. „Dateien vorhanden“, „Build grün“ und „am Gerät getestet“ getrennt melden.

---

## 1. Zuerst den Besitzer fragen

Diese Fragen sind kein Zeichen fehlender Intelligenz. Die Antworten existieren
nicht in einem generischen Repository und müssen vom jeweiligen Haus kommen.

### Hardware

- Welche Geräte sind vorhanden?
  - Waveshare Smart-86-Box / ESP32-S3-Touch-LCD-4B mit Display?
  - Waveshare ESP32-S3-AUDIO-Board ohne Display?
  - Wie viele Exemplare von jedem?
- Welches Gerät soll in welchen Raum?
- Hat ein Gerät eine Kamera? Falls ja: welches Sensormodell und welche
  Einbaulage?
- Ist das Board bereits provisioniert oder ist der Flash wirklich leer?
- Darf ein vorhandenes Gerät aktualisiert werden, oder soll zunächst nur gebaut
  und geprüft werden?

### Host und Netzwerk

- Auf welchem Linux-Host sollen Gateway, Bridge, STT und TTS laufen?
- Unter welchem Benutzer? Die Referenzinstallation nutzt `openclaw`.
- Welche LAN-IP hat dieser Host? Sie wird in Firmware-OTA-URL und
  `--public-host` benötigt.
- Sind TCP 8765 (Geräte-WebSocket), 8766 (OTA/Foto) und 18789
  (OpenClaw-Gateway, lokal TLS) erreichbar?
- Wo läuft ioBroker und ist `simple-api` aktiviert? Referenz:
  `http://192.168.178.24:8087`.

### Sprache und Modell

- Soll Audio vollständig lokal bleiben?
- Welche STT-Variante ist gewünscht?
  - Canary 1B v2 über `onnx-asr` (aktiver Referenzstand, Port 10303),
  - Canary 180M Flash INT8 + Silero-VAD (leichtere Alternative, Port 10301),
  - faster-whisper `small` als Rückfall,
  - oder Provider-STT?
- Welche Piper-Stimme soll sprechen? Referenz: `de_DE-kerstin-low`.
- Welcher OpenClaw-Agent bzw. welches LLM soll denken? Das Modell ist
  Hostkonfiguration und **nicht** in der Firmware fest eingebaut.

### Smart Home

- Welche Räume existieren wirklich?
- Welche Geräte gehören in jeden Raum?
- Bedeutet „Wohnzimmer an“ eine einzelne Lampe, eine Zigbee-Gruppe oder mehrere
  getrennte Lampen?
- Gibt es absichtlich gruppierte Geräte, Szenen oder Skripte?
- Welche Aktoren sind gefährlich oder dürfen niemals automatisch getestet
  werden, z. B. Heizung, Wasser, Tür, Alarm, Pumpe oder Rollladen?
- Welches harmlose Gerät darf für den ersten Schreibtest verwendet werden, und
  auf welchen Zustand soll es danach zurückgestellt werden?

---

## 2. Gesamtaufbau

```text
Touch-LCD-4B oder Audio-Board
  ├─ Wakeword, Mikrofon, Lautsprecher
  ├─ optional Display/Kamera bzw. RGB-Ring
  └─ WebSocket ws://HOST:8765
          │
          ▼
jarvis-realtime-bridge.service
  ├─ server.py: Geräteprotokoll, Opus, OTA :8766, Fotos
  ├─ local-voice.mjs: lokales STT -> Text -> OpenClaw-Agent
  ├─ local_stt.mjs: Wyoming-Protokoll
  ├─ piper_speaker.py: Text -> lokales PCM
  └─ local-smarthome/: eindeutige Befehle ohne Agentenlauf
          │
          ▼
OpenClaw Gateway wss://127.0.0.1:18789
  ├─ Agent/LLM, Werkzeuge und Sessions
  ├─ versionsgebundener Jarvis-dist-Patch
  └─ optional iobroker-alexa Werkzeug-Plugin
          │
          ▼
ioBroker simple-api :8087
  ├─ direkte lokale Adapter: zigbee, wled, yeelight, shelly, sonoff …
  ├─ alias.0 / devices.0 / javascript.0
  └─ alexa2 nur dort, wo kein lokaler Aktor existiert
```

---

## 3. Firmware für beide Geräte

### Gemeinsame Basis

- Upstream: `78/xiaozhi-esp32`
- Commit: siehe `VERSIONS.md`
- Toolchain: `espressif/idf:v6.1` mit dokumentiertem Digest
- Sprache: explizit `de-DE`
- Wakeword: `wn9_jarvis_tts`
- OTA-/Bridge-Adresse: beim Build über `--public-host` setzen
- App-Offset bei vorhandenem Gerät: `0x20000`
- Leerer Flash: passendes `merged-binary.bin` ab `0x0`, anschließend
  Provisionierung bzw. gesicherte NVS zurückspielen

### Smart-86-Box / Touch-LCD-4B

Build:

```bash
./scripts/build-firmware.sh --public-host <LAN-IP-DES-HOSTS>
```

Verwendete Patches:

- `xiaozhi-esp32-openclaw.patch`: Gesicht, Watchdog, Dauerbetrieb,
  Geräte-/Protokollfunktionen, Wakeword-Audio aus
- `jarvis-4b-restore-brightness.patch`: gespeicherte Helligkeit beim Booten
  wiederherstellen statt hart 100 % zu setzen
- `uart-uhci-idf61.patch`: gepinnte lokale Komponente für IDF 6.1

Geräteeinstellungen, die eine KI sonst leicht vergisst:

- Displayhelligkeit wird in NVS gespeichert. 5 % bleiben nur mit dem
  Helligkeitspatch nach einem Neustart erhalten.
- Lautsprecherlautstärke ist eine andere Einstellung als Mikrofonverstärkung.
- Mikrofon-Codec-Gain ist Firmware-/Codec-Konfiguration, kein Prozentregler im
  Host. Hoher Gain bedeutet nicht automatisch Clipping.
- Kameralage steht in `gateway/camera-orientation.json`.
- Geräte→Raum steht in `gateway/room-map.json`.
- Das Board bleibt absichtlich wach; CPU-/Display-Schlaf ist deaktiviert.
- Das kurze Aktivierungsgeräusch hängt am Firmwarepfad ohne Wakeword-Audio.

### ESP32-S3-AUDIO-Board

Build:

```bash
./scripts/build-firmware-audio-board.sh <LAN-IP-DES-HOSTS> [wakeword]
```

Verwendete Bestandteile:

- `jarvis-audio-board-ring.patch`: sieben LEDs, Zustandsfarben, Tasten,
  Lautstärkeanzeige und Geräteprotokoll
- `jarvis-audio-board-camera-psram.patch` und
  `jarvis-audio-board-camera-esp32camera.patch`: aktueller Kamerapfad
- eigenes `firmware/audio-board/dependencies.lock`

Zu prüfen bzw. zu erfragen:

- Hat dieses konkrete Gerät überhaupt einen angeschlossenen Kamerasensor?
- Sind sieben LEDs sichtbar und stimmen Rot/Grün/Cyan? Eine vertauschte
  R/G-Reihenfolge ist Hardware-Farbordnung, kein Fehlerzustand.
- Welche Tasten sollen Lautstärke steuern?
- Wie hell soll der Ring im Ruhezustand sein?
- Zwei Geräte können dieselbe Bridge nutzen, benötigen aber unterschiedliche
  Device-IDs und Raumzuordnungen.

### Vor jedem Flash

- Boardziel anhand Boardkennung/Manifest belegen.
- Bei einem vorhandenen Gerät vollständigen Flash bzw. mindestens NVS sichern.
- App-only verwenden, wenn WLAN, UUID und Kalibrierung erhalten bleiben sollen.
- Niemals 4B-Image auf Audio-Board oder umgekehrt schreiben.
- Nach dem Schreiben Hash/Read-back und Bootlog prüfen.

---

## 4. OpenClaw selbst

Die Referenzversion steht in `VERSIONS.md`. Ein Update ändert die
minifizierten `dist`-Dateinamen und macht den Patchsatz versionsgebunden.

Reihenfolge:

```bash
openclaw --version
python3 scripts/restore-openclaw-baseline.py \
  --openclaw-root "$(npm root -g)/openclaw"
# Nur wenn EXACT bzw. der richtige Patchsatz festgestellt wurde:
python3 scripts/restore-openclaw-baseline.py \
  --openclaw-root "$(npm root -g)/openclaw" --apply
python3 scripts/verify-openclaw-voice-dist-patches.py
python3 scripts/verify-jarvis-smart-home-router.py
```

Der Patch bewirkt unter anderem:

- sichere Bindung des gesprochenen Satzes an Gerät/Raum/Turn,
- genau einen Ausführer für direkte Schaltbefehle,
- korrekten Abschluss auch ohne Agentenlauf,
- keine zusätzliche gesprochene Bestätigungsfrage,
- korrekte Persistenz von Frage und Antwort.

Der Gateway-Neustart ist bewusst ein eigener Besitzer-Schritt:

```bash
systemctl --user restart openclaw-gateway.service
```

Danach beide Verify-Skripte erneut ausführen und das Gateway-Journal prüfen.

---

## 5. Gateway und Geräteverbindung

Pflicht:

- `openclaw-gateway.service` aktiv, TLS auf Port 18789
- `jarvis-realtime-bridge.service` aktiv
- Zertifikat: `~/.openclaw/ssl/gateway.crt`
- Gateway-Token lokal in `~/.openclaw/secrets.json`
- Bridge-`--public-host` entspricht der aus dem Gerätenetz erreichbaren IP
- Firmware-OTA-URL zeigt auf `http://<public-host>:8766/xiaozhi/ota/`
- Firewall erlaubt 8765/8766 aus dem Gerätenetz

Prüfen:

```bash
python3 scripts/check-new-host.py
systemctl --user status openclaw-gateway.service
systemctl --user status jarvis-realtime-bridge.service
ss -ltn | grep -E ':18789|:8765|:8766'
journalctl --user -u jarvis-realtime-bridge.service -n 100
```

Wenn ein Gerät nicht verbindet, zuerst fragen/prüfen:

1. Hat es WLAN?
2. Welche IP/MAC/Device-ID meldet es?
3. Welche OTA-/Bridge-Adresse steckt in Firmware oder NVS?
4. Ist 8765 vom Gerätenetz erreichbar?
5. Stimmen TLS-Datei und Gateway-Token zwischen Helper und Gateway?
6. Ist das richtige Boardimage geflasht?

---

## 6. Lokale Sprache: verwendete Software

### STT

Aktiver Referenzstand:

- NVIDIA Canary 1B v2
- Laufzeit: `onnx-asr`
- Wyoming-Dienst: `gateway/wyoming_canary1b.py`
- Port: `10303`
- gepinnte Pakete: `gateway/requirements-canary1b.txt`
- Unit: `systemd/wyoming-canary1b.service.in`
- Modellname: `nemo-canary-1b-v2`
- Modellordner enthält rund 3,7 GB und wird deshalb nicht auf GitHub gespeichert

Leichtere Alternative:

- NVIDIA Canary 180M Flash INT8
- Laufzeit: `sherpa-onnx`
- Silero-VAD als Pflicht-Tor
- Wyoming-Dienst: `gateway/wyoming_canary.py`, Port `10301`

Rückfall:

- `wyoming-faster-whisper`
- Modell `small`, CPU/int8, Sprache Deutsch

Die Bridge erhält PCM/Opus vom Gerät; `local_stt.mjs` spricht Wyoming mit dem
STT-Dienst. Modellverzeichnisse sind groß und werden nicht ins Repo kopiert.
Quelle, Modellname, Port und Startparameter müssen dokumentiert werden.

### TTS

- Engine: `piper-tts==1.4.2`
- Standardstimme: `de_DE-kerstin-low`
- Modell besteht aus `.onnx` plus `.onnx.json`
- `piper_speaker.py` hält das Modell geladen und erzeugt lokales PCM
- `server.py` taktet es in 60-ms-Rahmen und setzt beim ersten Satz 180 ms
  Vorlaufstille

### LLM/Agent

STT und TTS sind nicht das „Denken“. Der Text wird an den konfigurierten
OpenClaw-Agenten geschickt. Welcher Provider und welches Modell verwendet
werden, wird auf dem neuen Host eingerichtet und darf nicht als Geheimnis oder
Kontodump im Repository liegen.

---

## 7. Echtzeit schalten: drei Wege auseinanderhalten

### A. Lokaler Resolver — bevorzugt für eindeutige Befehle

`local-smarthome/` löst Satz, Raum, Aktion und lokalen Datenpunkt auf. Er kann
direkt `zigbee.0`, `wled.0`, `yeelight-2.0`, `shelly.0`, `sonoff.0`,
`alias.0` oder bestätigte Gruppen schreiben. Unbekannt/mehrdeutig bedeutet
**kein Schreiben**, sondern Rückfall zum Agenten.

### B. Gateway-Router

`scoped-smart-home-router.mjs` sitzt im OpenClaw-Consultpfad und bindet den
Satz an den unabhängig beobachteten Voice-Turn. Er verhindert, dass Text aus
einer anderen Session oder einem anderen Gerät direkt schaltet.

### C. Agent-Werkzeug-Plugin

`plugins/iobroker-alexa/` stellt Werkzeuge für den normalen Agentenweg bereit.
Das ist wichtig für freie Fragen und komplexere Aufträge, aber langsamer als
der lokale Resolver. Installation und OpenClaw-Einbindung stehen in dessen
`README.md`.

Es darf immer nur **ein** direkter Weg denselben Satz ausführen. Die Datei
`disable-smart-home-fastpath` schaltet den alten Bridge-Direktpfad aus.

---

## 8. Wo eine KI in ioBroker nachsehen kann

Das sind Beispiele, keine universellen Wahrheiten. Vor Schreibzugriffen erst
lesen und den Besitzer fragen.

### Geräte und lokale Aktoren

- `zigbee.0.*`: Zigbee-Geräte, Gruppen, `.state`, `.brightness`,
  Verfügbarkeit und Linkqualität
- `wled.0.*`: LED-Controller, `.on`, Helligkeit, Segmente
- `yeelight-2.0.*`: Lampen und Deckenleuchten
- `shelly.0.*`, `sonoff.0.*`, `tuya.0.*`: lokale/adaptergebundene Aktoren
- `0_userdata.0.*`: vom Besitzer gepflegte Zustände und Auslöser

### Namen und logische Zuordnung

- `alias.0.<Raum>.<Gerät>.*`: menschliche Struktur und Zielverweise
- `devices.0`: durch den Geräteadapter gepflegte Sicht
- `common.name`, `common.smartName`, `common.alias.id`, `common.role`,
  `common.read`, `common.write`: Namen, Ziel und Fähigkeit

### Gruppen, Szenen und Automationen

- `zigbee.0.group_*`: native Zigbee-Gruppen
- `javascript.0.scriptEnabled.*`: aktive/deaktivierte Skripte
- `javascript.0.scriptProblem.*`: fehlerhafte Skripte
- Skriptquellen: prüfen, ob ein Datenpunkt weitere Geräte schaltet oder einen
  Befehl überschreibt
- `scene.0.*` bzw. installierter Szenenadapter

### Alexa als letzter Rückfall

- `alexa2.0.Smart-Home-Devices.*`: Alexa-Smart-Home-Geräte
- `alexa2.0.Echo-Devices.*.Commands.textCommand`: Echo-/NLU-Weg

Alexa-`powerState` ist trotz ioBroker-Datenpunkt häufig ein Cloud-Aufruf.
Wenn ein lokaler Zigbee/WLED/Yeelight/Shelly-Datenpunkt existiert, ist dieser
für Echtzeit und Ausfallsicherheit vorzuziehen.

### So fragt die KI sinnvoll nach

Nicht:

> „Wo ist das Licht?“

Sondern:

> „Ich finde für Wohnzimmer fünf schreibbare Lichtziele und zusätzlich
> `zigbee.0.group_1.state`. Soll ‚Wohnzimmer an‘ die Zigbee-Gruppe oder alle
> fünf Ziele schalten? Welches Ziel darf ich gefahrlos testen?“

Oder:

> „Für ‚Ventilator‘ existieren zwei Alexa-Namen, aber nur ein lokaler
> Zigbee-Schalter. Gehören sie zum selben Gerät, und soll der lokale Schalter
> Vorrang bekommen?“

Jede bestätigte Antwort gehört anschließend als nachvollziehbare Konfiguration
in `rooms_overrides.json`, `bridges.json`, `room-map.json` oder die passende
Dokumentation — nicht nur ins Gedächtnis des ausführenden Agenten.

---

## 9. Häufig vergessene Punkte

- Helligkeit und Lautstärke sind persistente Geräteeinstellungen; Bootcode darf
  sie nicht hart überschreiben.
- Mikrofon-Gain, tatsächlicher PCM-Pegel und Lautsprecherlautstärke sind drei
  verschiedene Größen.
- Raumzuordnung benötigt die echte Device-ID jedes Boards.
- Kameraorientierung und vorhandener Sensortyp sind gerätespezifisch.
- Ein App-Image reicht nicht auf leerem Flash.
- NVS enthält WLAN, UUID und möglicherweise Kalibrierung; nicht veröffentlichen.
- STT-Modell, TTS-Stimme und Zertifikate liegen außerhalb des Repos.
- OpenClaw-Patches müssen nach jedem Upgrade versionsgenau neu erzeugt werden.
- Plugin-Code allein genügt nicht; Load-Pfad, Enable-Eintrag und Tool-Policy
  müssen ebenfalls konfiguriert sein.
- systemd-Profilkopien dürfen nicht auf alte Session-Keys zurückschalten.
- Mehrere Geräte benötigen Arbitration, damit nur das lauteste antwortet.
- Ein erfolgreicher HTTP-Write beweist nicht, dass der physische Aktor reagiert.
- Nach jedem Test den vorherigen Gerätezustand wiederherstellen.
- Uhrzeit, Zeitzone und Sekunden/Millisekunden-Zeitstempel nicht verwechseln.

---

## 10. Abschlusskriterium

Ein Neuaufbau ist erst vollständig, wenn:

- beide gewünschten Firmwareziele aus gepinnten Quellen bauen,
- das jeweils richtige Image am richtigen Board bootet,
- Geräte die Bridge erreichen,
- STT einen gesprochenen Satz korrekt liefert,
- der Agent antwortet,
- Piper hörbar und ohne doppelten/abgeschnittenen Anfang spricht,
- Raum- und Gerätezuordnung stimmen,
- ein ungefährlicher lokaler Schaltbefehl genau einmal ausgeführt wird,
- ein unbekannter oder mehrdeutiger Befehl **nicht** rät,
- OpenClaw-Verifies, Routertests und Prüfsummen grün sind,
- und alle verbleibenden Annahmen offen dokumentiert wurden.
