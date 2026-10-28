# 0. Dateibestand — Nullstart ohne Vorwissen

Stand: **2026-10-28**, Referenzhost `cachyos-x8664`.

Diese Datei trennt Pflichtdateien, automatisch erzeugte Daten und externe
Voraussetzungen. Sie ist die Inventarliste für einen Agenten, der nur den
GitHub-Link kennt.

## Fester Installationsort

Die Bridge liegt auf dem Referenzhost unter:

```text
/home/openclaw/.local/share/jarvis-realtime-bridge
```

Der OpenClaw-`dist`-Patch importiert `voice-turn-context.mjs` statisch und
`scoped-smart-home-router.mjs` dynamisch per absolutem `file://`-Pfad.
Deshalb ist dieser Ort für den Benutzer `openclaw` bindend. Ein anderer
Benutzername erfordert einen neu erzeugten OpenClaw-Patchsatz.

Der lokale Resolver liegt unter:

```text
/home/openclaw/.local/share/alexa-direct
```

`gateway/local-voice.mjs` leitet den Pfad aus `HOME`/`XDG_DATA_HOME` ab;
`JARVIS_LOCAL_SMARTHOME_DIR` kann ihn explizit überschreiben.

Die TFT-Anzeige liegt unter `~/.local/share/jarvis-screen`; ihre
reproduzierbaren Quellen stehen im Repo unter `screen/`. Der dazugehörige
Agent-Skill wird nach `~/.openclaw/workspace-voice/skills/display-cards/`
installiert.

## Pflichtdateien am Bridge-Ort

| Datei | Rolle / Importeur |
|---|---|
| `server.py` | WebSocket/OTA, systemd-Einstieg |
| `room_command.py` | Raumzuordnung, von `server.py` importiert |
| `piper_speaker.py` | lokale Sprachausgabe, von `server.py` importiert |
| `local-voice.mjs` | lokaler Sprach-/Agentenpfad |
| `local_stt.mjs` | Wyoming-STT, von `local-voice.mjs` importiert |
| `openclaw-talk-realtime.mjs` | Provider-Fallback |
| `openclaw-thin-client.mjs` | Diagnose-/Fallback-Client |
| `photo-consult.mjs` | Bild-Consult |
| `voice-turn-context.mjs` | statischer Import aus dem OpenClaw-Handler |
| `scoped-smart-home-router.mjs` | dynamischer Import aus dem Handler |
| `smart-home-router.mjs`, `room-command.mjs`, `room-router.mjs` | Routerkette |
| `refresh-alexa-catalog.mjs` | erzeugt den Alexa-Katalog |
| `requirements.txt`, `requirements-tts.txt` | Python-Abhängigkeiten |
| `room-map.json` | gepflegte Geräte→Raum-Konfiguration |
| `camera-orientation.json` | gepflegte Kameralage |
| `disable-smart-home-fastpath` | verhindert einen zweiten Ausführer |

`scripts/install-host.sh` kopiert diese Menge und prüft sie. Bestehende
`room-map.json` und `camera-orientation.json` werden nicht überschrieben.

## Pflichtdateien der TFT-Anzeige

| Datei | Rolle / Importeur |
|---|---|
| `show.py` | Alltagsbefehl: rendern/konvertieren und `/screen` aufrufen |
| `render.py`, `palette.py` | Pillow-Renderer und gemeinsames Design |
| `templates/wetter.py`, `templates/karte.py` | Wetter- und Textkarte |
| `image-search.py` | Brave-Bildsuche, Quellenfilter, Konvertierung |
| `serve.py` | HTTP-Auslieferung von `.raw` auf Port 8770 |
| `requirements.txt`, `.venv/` | gepinnte Pillow-Laufzeit |
| `openclaw/skills/display-cards/SKILL.md` | Anleitung für den Voice-Agenten |

Die Firmwareseite liegt in `firmware/patches/jarvis-screen-show.patch`; der
Patch wird von `scripts/build-firmware.sh` nach dem 4B-Basispatch angewandt.

## Pflichtdateien des lokalen Smart-Home-Resolvers

Alle Quellen unter `local-smarthome/` werden nach `~/.local/share/alexa-direct`
installiert. Harte Importkette:

```text
smarthome_cli.py -> control.py
control.py -> catalog.py, fuzzy.py, localmap.py, resolver.py, rooms.py
```

`bridges.json` und `rooms_overrides.json` sind gepflegte Konfiguration und
werden bei Wiederholung des Installers erhalten. Die anderen JSON-Dateien
werden aus ioBroker erzeugt und gehören nicht ins Repository.

## Automatisch erzeugte Daten

| Datei | Erzeuger | Zeitbasis / Verhalten bei Fehlen |
|---|---|---|
| `active-room.json` | Bridge | Sekunden; ohne Datei kein Raumkontext |
| `last-utterance.json` | Talk-Helper | Millisekunden; Direktweg lehnt ab |
| `last-alexa-command.json` | Bridge | Sekunden; nur Dedupe fehlt |
| `voice-bindings/*.json` | Bridge | Millisekunden/TTL; Session-Bindung fehlt |
| `photo-*.jpg` | Gerät/Bridge | Aufnahmezeit; kein Altbild verfügbar |
| `alexa-device-catalog.json` | Katalog-Timer | Sekunden; alter Router fällt zurück |
| `alexa-direct/catalog.json` | `catalog.py` | Sekunden; wird neu aus ioBroker gebaut |
| `alexa-direct/localmap.json` | `localmap.py` | Sekunden; wird neu gebaut |
| `alexa-direct/rooms.json` | `rooms.py` | keine TTL; wird neu gebaut |
| `jarvis-screen/out/*` | Renderer/Bildsuche | letzte Vorschau und RGB565-Datei; wird bei jedem Auftrag neu erzeugt |

## Voraussetzungen außerhalb des Repositories

| Voraussetzung | Referenz / Folge bei Fehlen |
|---|---|
| OpenClaw | exakt `2026.10.7`; Patchsatz ist versionsgebunden |
| Gateway-TLS | `~/.openclaw/ssl/gateway.crt`; Helper verbindet sonst nicht |
| Provider-Anmeldung und Modell | interaktiv im eigenen OpenClaw einrichten |
| Piper-Stimme | `~/.local/share/piper-voices/de_DE-kerstin-low.onnx` |
| Wyoming-STT | Referenz `127.0.0.1:10303` |
| Canary-1B-Laufzeit | `gateway/wyoming_canary1b.py`, `gateway/requirements-canary1b.txt`, `systemd/wyoming-canary1b.service.in` |
| ioBroker Simple API | Referenz `http://192.168.178.24:8087`, per `IOBROKER_SIMPLE_API` änderbar |
| Firmware-Hardware | Waveshare Smart-86-Box/Touch-LCD-4B oder Audio-Board |
| USB-/Seriellrechte | nötig nur zum Flashen |
| Brave Search API | optional für Suchbilder; Key nur lokal unter `secrets.json -> brave.apiKey` |
| Internet/Wetterquelle | `wttr.in/Buchholz%20%28Aller%29?format=j1`; Karten funktionieren auch mit anderweitig gelieferten Daten |

Zertifikate, Tokens, Provider-Credentials, WLAN/NVS, Logs und Fotos werden
bewusst nicht veröffentlicht.

## OpenClaw-Patches

Für `2026.10.7` ist `patches/openclaw-2026.10.7/` der exakte Satz.
`manifest.json` pinnt Vorher-/Nachher-SHA256. Aktiv sind:

- `voice-confirmation-disabled-by-owner-v1`
- `voice-persist-split-v4`
- `jarvis-safe-router-v1`
- `jarvis-direct-completion-v2`

Prüfen, zunächst rein lesend:

```bash
python3 scripts/restore-openclaw-baseline.py \
  --openclaw-root "$(npm root -g)/openclaw"
python3 scripts/verify-openclaw-voice-dist-patches.py
python3 scripts/verify-jarvis-smart-home-router.py
```

Der erste Befehl schreibt erst mit `--apply`. Ein danach nötiger
Gateway-Neustart ist bewusst kein Teil des Skripts.

## Nullstart-Reihenfolge

1. `docs/00-START-HIER.md` lesen und Hardwarevariante bestimmen.
2. OpenClaw 2026.10.7 installieren, TLS/Provider/Gateway selbst einrichten.
3. `scripts/install-host.sh --public-host <LAN-IP> --no-restart` ausführen.
4. lokalen Resolver gemäß `local-smarthome/README.md` erzeugen und trocken testen.
5. OpenClaw-Patch rein lesend prüfen, dann bewusst anwenden und Gateway durch
   den Owner neu starten.
6. Firmware für das richtige Board bauen oder den passenden App-Anker flashen.
   Leerer Flash braucht das Vollimage; App-only bei `0x20000` setzt Bootloader,
   Partitionstabelle und Assets voraus.
7. `scripts/check-new-host.py`, `scripts/verify-host.sh` und
   `docs/04-TESTPLAN.md` vollständig abarbeiten.

## Verifizierungsstand 28.10.2026

- OpenClaw 2026.10.7: beide gepatchten `dist`-Dateien exakt zu den
  Manifest-`after`-Hashes.
- Gateway-Routertests: 149/149 grün (fünf Suiten, gemessen 27.10.2026).
- Lokaler Resolver: 34/34 Offline-Fälle grün.
- 4B-Basis-, Helligkeits- und TFT-Patch: vollständiger Container-Build mit
  `espressif/idf:v6.1` erfolgreich; laufende App über 2.909.840 Byte
  zurückgelesen und bytegleich zum veröffentlichten App-Anker.
- Wetterkarte und Suchbild: Owner-Abnahme am Gerät („funzt super. und schnell“);
  Pushformat RGB565 480×480, 460.800 Byte.
- Bestehender Bridge-End-to-End-Test mit lokaler Piper-Ausgabe: grün.
- Nicht grün sind zwei ältere Testhüllen: `test_conversation_timing.py`
  referenziert die entfernte Methode `note_followup_audio`;
  `test_local_tts.py` besitzt im Taktungs-Stub noch kein `_pcm_peak`.
  Das sind offen dokumentierte Testdrifts, keine bestandenen Tests.
- Kein vollständiger 4B-Neubau und kein Leerhost-/Leerflash-Hardwaretest in
  diesem Audit. Der Buildweg ist vorhanden, aber diese Prüfstufe bleibt offen.

## Ergänzende Dokumente im Quellbaum

Diese Dateien lagen bisher nur im Live-Verzeichnis `~/.local/share/alexa-direct/`
und sind jetzt versioniert:

| Datei | Inhalt |
|---|---|
| `local-smarthome/SCRIPTE.md` | alle 78 ioBroker-JavaScript-/Blockly-Skripte, was sie verknüpfen |
| `local-smarthome/UNSTIMMIGKEITEN.md` | Alias-Analyse, offene Lücken, bewusst unvollständig |
| `local-smarthome/ALEXA-NLU-ANALYSE.md` | Untersuchung vom 26.10.2026: warum `powerState` über Amazons Cloud läuft (`alexa-remote2`, `PUT /api/phoenix/state`) |
| `local-smarthome/alias-beispiele/` | sechs echte `alias.0`-Objekte als Vorlage für eigene Anlagen |

`ALEXA-NLU-ANALYSE.md` ist die frühere Fassung von `local-smarthome/README.md`
unter anderem Titel. Beide bleiben: die README beschreibt die Bedienung, die
Analyse die Herleitung und Messwerte.

## Bewusst nicht im Repository

- `.venv/`, `node_modules/`, `__pycache__/`
- `*.bak-*`, `*.broken-*`, alte Unit-Kopien
- Kataloge, Logs, Fotos, Voice-Bindings und Zeitstempeldateien
- `~/.openclaw/secrets.json`, Zertifikatsschlüssel, WLAN/NVS
- gerätespezifische Flash-Backups

Die veröffentlichten Firmware-Anker sind projektbezogene App-/Build-Artefakte;
ihre Prüfsummen stehen in `CHECKSUMS.sha256` und `VERSIONS.md`.
